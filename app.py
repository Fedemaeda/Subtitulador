"""Subtitulador - Transcribe audio from video and burn subtitles."""

import os

os.environ["FLASK_SKIP_DOTENV"] = "1"  # ponytail: skip .env loading to avoid encoding issues

import tempfile
import subprocess
import re
import whisper
from deep_translator import GoogleTranslator
from flask import Flask, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB limit

# Allowed video extensions
ALLOWED_EXTENSIONS = {"mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v"}

# Load whisper model once at startup (base model is a good balance)
print("Loading Whisper model (base)... this may take a moment on first run.")
model = whisper.load_model("base")
print("Whisper model loaded.")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ---- Silence detection ----

def detect_silence(audio_path, noise_threshold="-30dB", duration=0.5):
    """Use ffmpeg silencedetect to find silent periods in audio.
    Returns list of (start, end) tuples in seconds."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", audio_path,
                "-af", f"silencedetect=noise={noise_threshold}:d={duration}",
                "-f", "null", "-"
            ],
            capture_output=True,
            text=True,
        )
        # Parse silencedetect output
        silences = []
        lines = result.stderr.split("\n")
        starts = []
        for line in lines:
            if "silence_start" in line:
                # Extract: silence_start: 1.234
                parts = line.split("silence_start:")
                if len(parts) > 1:
                    try:
                        starts.append(float(parts[1].strip().split()[0]))
                    except (ValueError, IndexError):
                        pass
            elif "silence_end" in line and starts:
                parts = line.split("silence_end:")
                if len(parts) > 1:
                    try:
                        end = float(parts[1].strip().split()[0])
                        silences.append((starts.pop(), end))
                    except (ValueError, IndexError):
                        pass
        return silences
    except Exception:
        return []


def _is_in_silence(start, end, silences):
    """Check if a segment overlaps significantly with silent periods."""
    seg_duration = end - start
    if seg_duration <= 0:
        return False

    for sil_start, sil_end in silences:
        # Calculate overlap
        overlap_start = max(start, sil_start)
        overlap_end = min(end, sil_end)
        overlap = max(0, overlap_end - overlap_start)

        # If segment is mostly within silence (>60% overlap), skip it
        if overlap / seg_duration > 0.6:
            return True
    return False


# ---- Hallucination filtering ----

# Common Whisper hallucination patterns (repeated phrases, filler, etc.)
_HALLUCINATION_PATTERNS = [
    re.compile(r"thank you|thanks|subtitles|captions", re.I),
    re.compile(r"^\.{2,}$"),                          # just dots
    re.compile(r"^\s*$"),                              # empty or whitespace
    re.compile(r"thank you for watching|please subscribe|thanks for watching", re.I),
]

_HALLUCINATION_REPEATS = 3  # same text repeated N times = hallucination
_MIN_SEGMENT_DURATION = 0.4  # segments shorter than this are likely noise


def _is_hallucination(segments):
    """Detect and remove common Whisper hallucination segments."""
    if not segments:
        return segments

    cleaned = []
    seen_texts = {}  # text -> count for repeat detection

    for seg in segments:
        text = seg["text"].strip()

        # Skip empty
        if not text:
            continue

        # Skip very short segments
        if (seg["end"] - seg["start"]) < _MIN_SEGMENT_DURATION:
            continue

        # Skip hallucination patterns (case-insensitive, ignore whitespace)
        text_lower = text.lower().strip()
        if any(p.search(text_lower) for p in _HALLUCINATION_PATTERNS):
            continue

        # Skip repeated text (same subtitle appearing many times)
        seen_texts[text_lower] = seen_texts.get(text_lower, 0) + 1
        if seen_texts[text_lower] > _HALLUCINATION_REPEATS:
            continue

        cleaned.append(seg)

    return cleaned


def _tighten_timings(segments, silences):
    """Adjust subtitle timings to avoid silent gaps.
    - Trim end time to before silence starts
    - Skip segments that are entirely in silence
    - Don't let subtitles extend into long silences"""
    if not segments or not silences:
        return segments

    result = []
    for seg in segments:
        # Skip segments entirely in silence
        if _is_in_silence(seg["start"], seg["end"], silences):
            continue

        new_seg = seg.copy()

        # Trim end time if it extends into silence
        for sil_start, sil_end in silences:
            if new_seg["start"] < sil_start < new_seg["end"]:
                # Silence starts during this subtitle - trim to silence start
                # Add small buffer so text doesn't cut abruptly
                trim_point = sil_start - 0.1
                if trim_point > new_seg["start"] + 0.3:
                    new_seg["end"] = trim_point
                break

        result.append(new_seg)

    return result


# ---- Smart mixed-language translation ----

# Regex: splits text into CJK chunks vs Latin chunks vs other
_CJK_RE = re.compile(r"[\u3000-\u9fff\u3040-\u309f\u30a0-\u30ff\uff00-\uffef\u2e80-\u9fff]+")
_LATIN_RE = re.compile(r"[a-zA-Z]+")
_OTHER_RE = re.compile(r"[\d\s\W]+")

# Map whisper lang codes → Google Translate codes
_LANG_MAP = {"zh": "zh-CN"}


def _google_src(code):
    return _LANG_MAP.get(code, code)


def _contains_cjk(text):
    """Check if text contains CJK characters."""
    return bool(_CJK_RE.search(text))


def _split_by_script(text):
    """Split text into (script_type, chunk) pairs.
    script_type: 'cjk', 'latin', 'other'
    """
    result = []
    pos = 0
    while pos < len(text):
        # Try CJK
        m = _CJK_RE.match(text, pos)
        if m:
            result.append(("cjk", m.group()))
            pos = m.end()
            continue
        # Try Latin
        m = _LATIN_RE.match(text, pos)
        if m:
            result.append(("latin", m.group()))
            pos = m.end()
            continue
        # Other (spaces, punctuation, digits)
        m = _OTHER_RE.match(text, pos)
        if m:
            result.append(("other", m.group()))
            pos = m.end()
            continue
        # Single char fallback
        result.append(("other", text[pos]))
        pos += 1
    return result


def _translate_chunk(translator, text, source_lang, target_lang):
    """Translate a single chunk with explicit source language."""
    text = text.strip()
    if not text or len(text) < 2:
        return text
    try:
        src = _google_src(source_lang)
        chunk_translator = GoogleTranslator(source=src, target=target_lang)
        result = chunk_translator.translate(text)
        return result if result else text
    except Exception:
        return text


def translate_text_smart(text, source_lang, target_lang, translator):
    """Translate text that may contain mixed languages (e.g. Japanese + English).
    
    Strategy:
    - If no CJK: translate whole text as source_lang→target_lang
    - If CJK present: translate whole text with auto-detect (Google handles
      mixed scripts better as a single input than as fragments)
    """
    if not text.strip():
        return text

    # Fast path: no mixed scripts - use explicit source language
    if not _contains_cjk(text):
        return _translate_chunk(translator, text, source_lang, target_lang)

    # Mixed script: Google Translate handles mixed JA+EN better as a whole
    # than when we split it into fragments. Send with auto-detect.
    try:
        auto_translator = GoogleTranslator(source="auto", target=target_lang)
        result = auto_translator.translate(text)
        if result and result.strip():
            return result
    except Exception:
        pass

    # Fallback: split by script and translate each part
    parts = _split_by_script(text)
    if len(parts) <= 1:
        return _translate_chunk(translator, text, source_lang, target_lang)

    # Merge adjacent same-type chunks
    merged = []
    for stype, chunk in parts:
        if merged and merged[-1][0] == stype:
            merged[-1] = (stype, merged[-1][1] + chunk)
        else:
            merged.append([stype, chunk])

    translated_parts = []
    for stype, chunk in merged:
        if stype == "other":
            translated_parts.append(chunk)
        elif stype == "cjk":
            src = source_lang if source_lang in ("ja", "zh", "ko") else "ja"
            translated = _translate_chunk(translator, chunk, src, target_lang)
            translated_parts.append(translated)
        elif stype == "latin":
            latin_langs = {"en", "es", "pt", "fr", "de", "it", "ru", "ar"}
            src = source_lang if source_lang in latin_langs else "en"
            if src == target_lang:
                translated_parts.append(chunk)
            else:
                translated = _translate_chunk(translator, chunk, src, target_lang)
                translated_parts.append(translated)

    return "".join(translated_parts)


def translate_segments(segments, source_lang, target_lang):
    """Translate segment texts, handling mixed-language segments intelligently."""
    if source_lang == target_lang:
        return segments

    tgt = _LANG_MAP.get(target_lang, target_lang)
    translator = GoogleTranslator(source="auto", target=tgt)

    translated_segs = []
    for seg in segments:
        text = seg["text"].strip()
        new_seg = seg.copy()

        try:
            new_seg["text"] = translate_text_smart(text, source_lang, tgt, translator)
        except Exception:
            try:
                new_seg["text"] = translator.translate(text) or text
            except Exception:
                new_seg["text"] = text

        translated_segs.append(new_seg)

    return translated_segs


def generate_srt(segments):
    """Convert whisper segments to SRT format string."""
    srt_lines = []
    for i, seg in enumerate(segments, 1):
        start = seg["start"]
        end = seg["end"]
        text = seg["text"].strip()

        def fmt(seconds):
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds - int(seconds)) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        srt_lines.append(f"{i}")
        srt_lines.append(f"{fmt(start)} --> {fmt(end)}")
        srt_lines.append(text)
        srt_lines.append("")
    return "\n".join(srt_lines)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "video" not in request.files:
        return jsonify({"error": "No video file uploaded"}), 400

    file = request.files["video"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": f"File type not allowed. Use: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    language = request.form.get("language", None)  # None = auto-detect
    if language == "":
        language = None

    target_language = request.form.get("target_language", None)  # translate to this language
    if target_language == "":
        target_language = None

    tmpdir = tempfile.mkdtemp()
    try:
        # Save uploaded video
        ext = file.filename.rsplit(".", 1)[1].lower()
        video_path = os.path.join(tmpdir, f"input.{ext}")
        file.save(video_path)

        # Extract audio to WAV for whisper
        audio_path = os.path.join(tmpdir, "audio.wav")
        subprocess.run(
            [
                "ffmpeg", "-i", video_path,
                "-vn", "-acodec", "pcm_s16le",
                "-ar", "16000", "-ac", "1",
                audio_path, "-y"
            ],
            check=True,
            capture_output=True,
        )

        # Detect silent periods in audio
        silences = detect_silence(audio_path)

        # Transcribe with whisper (use word timestamps for better timing)
        transcribe_opts = {"word_timestamps": True}
        if language:
            transcribe_opts["language"] = language

        result = model.transcribe(audio_path, **transcribe_opts)
        detected_lang = result.get("language", "unknown")
        segments = result["segments"]

        # Filter out hallucinations (fake subtitles in silence)
        segments = _is_hallucination(segments)

        # Tighten timings: trim into silent gaps, skip segments in silence
        segments = _tighten_timings(segments, silences)

        # Translate if target language is different from detected source
        if target_language and detected_lang != target_language:
            segments = translate_segments(segments, detected_lang, target_language)

        # Generate SRT
        srt_content = generate_srt(segments)
        srt_path = os.path.join(tmpdir, "subtitles.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)

        # Burn subtitles into video
        output_path = os.path.join(tmpdir, f"output_subtitled.{ext}")
        # Use ass filter for better rendering, fallback to subtitles
        escaped_srt = srt_path.replace("\\", "/").replace(":", "\\:")
        subprocess.run(
            [
                "ffmpeg", "-i", video_path,
                "-vf", f"subtitles='{escaped_srt}':force_style='FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV=30'",
                "-c:a", "copy",
                output_path, "-y"
            ],
            check=True,
            capture_output=True,
        )

        # Return the subtitled video
        return send_file(
            output_path,
            as_attachment=True,
            download_name=f"{os.path.splitext(secure_filename(file.filename))[0]}_subtitled.{ext}",
            mimetype=f"video/{ext}",
        )

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        return jsonify({"error": f"FFmpeg error: {error_msg[:500]}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        # Cleanup temp files
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
