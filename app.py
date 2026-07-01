"""Subtitulador - Transcribe audio from video and burn subtitles."""

import os
os.environ["FLASK_SKIP_DOTENV"] = "1"

import tempfile
import subprocess
import re
import threading
from flask import Flask, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB

ALLOWED_EXTENSIONS = {"mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v"}

# ---- GPU detection ----
import torch
GPU_AVAILABLE = torch.cuda.is_available()
COMPUTE_TYPE = "float16" if GPU_AVAILABLE else "int8"
DEVICE = "cuda" if GPU_AVAILABLE else "cpu"

print(f"Device: {DEVICE} | GPU: {GPU_AVAILABLE} | Compute: {COMPUTE_TYPE}")

# ---- Model cache (loaded on demand) ----
from faster_whisper import WhisperModel
_models = {}
_models_lock = threading.Lock()


def get_model(name="base"):
    """Load model on demand, cache for reuse."""
    with _models_lock:
        if name not in _models:
            print(f"Loading Whisper model '{name}' on {DEVICE}...")
            _models[name] = WhisperModel(name, device=DEVICE, compute_type=COMPUTE_TYPE)
            print(f"Model '{name}' ready.")
        return _models[name]


# ---- Translation ----
from deep_translator import GoogleTranslator

_CJK_RE = re.compile(r"[\u3000-\u9fff\u3040-\u309f\u30a0-\u30ff\uff00-\uffef\u2e80-\u9fff]+")
_LANG_MAP = {"zh": "zh-CN"}


def _contains_cjk(text):
    return bool(_CJK_RE.search(text))


def _translate_chunk(text, source_lang, target_lang):
    text = text.strip()
    if not text or len(text) < 2:
        return text
    try:
        src = _LANG_MAP.get(source_lang, source_lang)
        return GoogleTranslator(source=src, target=target_lang).translate(text) or text
    except Exception:
        return text


def translate_text_smart(text, source_lang, target_lang):
    if not text.strip():
        return text
    if not _contains_cjk(text):
        return _translate_chunk(text, source_lang, target_lang)
    try:
        result = GoogleTranslator(source="auto", target=target_lang).translate(text)
        if result and result.strip():
            return result
    except Exception:
        pass
    return _translate_chunk(text, source_lang, target_lang)


# ---- Silence detection ----
def detect_silence(audio_path, noise_threshold="-30dB", duration=0.5):
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", audio_path, "-af",
             f"silencedetect=noise={noise_threshold}:d={duration}",
             "-f", "null", "-"],
            capture_output=True, text=True)
        silences, starts = [], []
        for line in result.stderr.split("\n"):
            if "silence_start" in line:
                parts = line.split("silence_start:")
                if len(parts) > 1:
                    try: starts.append(float(parts[1].strip().split()[0]))
                    except: pass
            elif "silence_end" in line and starts:
                parts = line.split("silence_end:")
                if len(parts) > 1:
                    try:
                        silences.append((starts.pop(), float(parts[1].strip().split()[0])))
                    except: pass
        return silences
    except Exception:
        return []


def _is_in_silence(start, end, silences):
    dur = end - start
    if dur <= 0:
        return False
    for s, e in silences:
        overlap = max(0, min(end, e) - max(start, s))
        if overlap / dur > 0.6:
            return True
    return False


# ---- Hallucination filter ----
_HALLUCINATION_PATTERNS = [
    re.compile(r"thank you|thanks|subtitles|captions", re.I),
    re.compile(r"^\.{2,}$"),
    re.compile(r"thank you for watching|please subscribe|thanks for watching", re.I),
]


def _is_hallucination(segments, min_duration=0.4, max_repeats=3):
    cleaned, seen = [], {}
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if (seg["end"] - seg["start"]) < min_duration:
            continue
        lower = text.lower()
        if any(p.search(lower) for p in _HALLUCINATION_PATTERNS):
            continue
        seen[lower] = seen.get(lower, 0) + 1
        if seen[lower] > max_repeats:
            continue
        cleaned.append(seg)
    return cleaned


def _tighten_timings(segments, silences):
    if not silences:
        return segments
    result = []
    for seg in segments:
        if _is_in_silence(seg["start"], seg["end"], silences):
            continue
        new = dict(seg)
        for s, _ in silences:
            if new["start"] < s < new["end"]:
                trim = s - 0.1
                if trim > new["start"] + 0.3:
                    new["end"] = trim
                break
        result.append(new)
    return result


# ---- SRT generation ----
def generate_srt(segments):
    lines = []
    def fmt(sec):
        h = int(sec // 3600); m = int((sec % 3600) // 60)
        s = int(sec % 60); ms = int((sec - int(sec)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    for i, seg in enumerate(segments, 1):
        lines.append(f"{i}\n{fmt(seg['start'])} --> {fmt(seg['end'])}\n{seg['text'].strip()}\n")
    return "\n".join(lines)


# ---- Routes ----
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/models")
def list_models():
    return jsonify(["tiny", "base", "small", "medium", "large-v3"])


@app.route("/gpu")
def gpu_info():
    return jsonify({"gpu": GPU_AVAILABLE, "device": DEVICE, "compute_type": COMPUTE_TYPE})


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "video" not in request.files:
        return jsonify({"error": "No video"}), 400

    file = request.files["video"]
    if file.filename == "" or not ("." in file.filename and
            file.filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS):
        return jsonify({"error": "Invalid file type"}), 400

    model_name = request.form.get("model", "base")
    language = request.form.get("language") or None
    target_language = request.form.get("target_language") or None
    noise_threshold = request.form.get("noise_threshold", "-30dB")
    min_duration = float(request.form.get("min_duration", 0.4))
    max_repeats = int(request.form.get("max_repeats", 3))

    tmpdir = tempfile.mkdtemp()
    try:
        ext = file.filename.rsplit(".", 1)[1].lower()
        video_path = os.path.join(tmpdir, f"input.{ext}")
        file.save(video_path)

        # Extract audio
        audio_path = os.path.join(tmpdir, "audio.wav")
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", audio_path, "-y"],
            check=True, capture_output=True)

        # Silence detection
        silences = detect_silence(audio_path, noise_threshold=noise_threshold)

        # Transcribe with faster-whisper
        model = get_model(model_name)
        raw_segments, info = model.transcribe(
            audio_path, language=language,
            word_timestamps=True, vad_filter=True)

        segments = []
        for seg in raw_segments:
            segments.append({
                "start": seg.start, "end": seg.end, "text": seg.text
            })

        detected_lang = info.language

        # Filter
        segments = _is_hallucination(segments, min_duration, max_repeats)
        segments = _tighten_timings(segments, silences)

        # Translate
        if target_language and detected_lang != target_language:
            for seg in segments:
                seg["text"] = translate_text_smart(seg["text"], detected_lang, target_language)

        # Save SRT for later
        srt_content = generate_srt(segments)
        srt_path = os.path.join(tmpdir, "subtitles.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)

        # Burn subtitles into video
        output_path = os.path.join(tmpdir, f"output_subtitled.{ext}")
        escaped_srt = srt_path.replace("\\", "/").replace(":", "\\:")
        subprocess.run(
            ["ffmpeg", "-i", video_path,
             "-vf", f"subtitles='{escaped_srt}':force_style='FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV=30'",
             "-c:a", "copy", output_path, "-y"],
            check=True, capture_output=True)

        return send_file(
            output_path, as_attachment=True,
            download_name=f"{os.path.splitext(secure_filename(file.filename))[0]}_subtitled.{ext}")

    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"FFmpeg: {e.stderr.decode('utf-8', errors='replace')[:500]}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.route("/transcribe-srt", methods=["POST"])
def transcribe_srt():
    """Same as /transcribe but returns SRT file instead of video."""
    if "video" not in request.files:
        return jsonify({"error": "No video"}), 400

    file = request.files["video"]
    if file.filename == "" or not ("." in file.filename and
            file.filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS):
        return jsonify({"error": "Invalid file type"}), 400

    model_name = request.form.get("model", "base")
    language = request.form.get("language") or None
    target_language = request.form.get("target_language") or None
    noise_threshold = request.form.get("noise_threshold", "-30dB")
    min_duration = float(request.form.get("min_duration", 0.4))
    max_repeats = int(request.form.get("max_repeats", 3))

    tmpdir = tempfile.mkdtemp()
    try:
        ext = file.filename.rsplit(".", 1)[1].lower()
        video_path = os.path.join(tmpdir, f"input.{ext}")
        file.save(video_path)

        audio_path = os.path.join(tmpdir, "audio.wav")
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", audio_path, "-y"],
            check=True, capture_output=True)

        silences = detect_silence(audio_path, noise_threshold=noise_threshold)

        model = get_model(model_name)
        raw_segments, info = model.transcribe(
            audio_path, language=language,
            word_timestamps=True, vad_filter=True)

        segments = [{"start": s.start, "end": s.end, "text": s.text} for s in raw_segments]
        detected_lang = info.language

        segments = _is_hallucination(segments, min_duration, max_repeats)
        segments = _tighten_timings(segments, silences)

        if target_language and detected_lang != target_language:
            for seg in segments:
                seg["text"] = translate_text_smart(seg["text"], detected_lang, target_language)

        srt_content = generate_srt(segments)
        srt_path = os.path.join(tmpdir, "subtitles.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)

        return send_file(
            srt_path, as_attachment=True,
            download_name=f"{os.path.splitext(secure_filename(file.filename))[0]}.srt",
            mimetype="text/plain")

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    # Preload default model
    get_model("base")
    app.run(host="127.0.0.1", port=5000, debug=False)
