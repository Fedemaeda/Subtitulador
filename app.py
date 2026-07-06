"""Subtitulador - Transcribe, edit and burn subtitles."""

import os
os.environ["FLASK_SKIP_DOTENV"] = "1"

import uuid
import json
import time
import tempfile
import subprocess
import re
import threading
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template, abort
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

# ---- Model cache ----
from faster_whisper import WhisperModel
_models = {}
_models_lock = threading.Lock()


def get_model(name="base"):
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


_HALLUCINATION_PATTERNS = [
    re.compile(r"thank you|thanks|subtitles|captions", re.I),
    re.compile(r"^\.{2,}$"),
    re.compile(r"thank you for watching|please subscribe|thanks for watching", re.I),
]


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


# ---- Session storage ----
SESSIONS_DIR = Path(tempfile.gettempdir()) / "subtitulador_sessions"
SESSIONS_DIR.mkdir(exist_ok=True)
_sessions = {}
_sessions_lock = threading.Lock()


def _create_session(video_path, ext, segments):
    sid = str(uuid.uuid4())[:8]
    sdir = SESSIONS_DIR / sid
    sdir.mkdir(exist_ok=True)
    # Copy video to session dir
    import shutil
    final_video = str(sdir / f"input.{ext}")
    shutil.copy2(video_path, final_video)
    # Save segments
    (sdir / "segments.json").write_text(json.dumps(segments, ensure_ascii=False), encoding="utf-8")
    with _sessions_lock:
        _sessions[sid] = {
            "dir": str(sdir),
            "video": final_video,
            "ext": ext,
            "created": time.time(),
        }
    return sid


def _get_session(sid):
    with _sessions_lock:
        return _sessions.get(sid)


def _cleanup_old_sessions(max_age_hours=2):
    cutoff = time.time() - (max_age_hours * 3600)
    to_remove = []
    with _sessions_lock:
        for sid, info in _sessions.items():
            if info["created"] < cutoff:
                to_remove.append(sid)
        for sid in to_remove:
            import shutil
            shutil.rmtree(_sessions[sid]["dir"], ignore_errors=True)
            del _sessions[sid]


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
    """Transcribe video and return session ID + segments for editing."""
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

    _cleanup_old_sessions()

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

        # Create session (copies video + saves segments)
        sid = _create_session(video_path, ext, segments)

        return jsonify({
            "session_id": sid,
            "segments": segments,
            "language": detected_lang,
            "ext": ext,
            "filename": file.filename,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.route("/api/session/<sid>/segments", methods=["GET"])
def get_segments(sid):
    """Get current segments for a session."""
    session = _get_session(sid)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    seg_path = Path(session["dir"]) / "segments.json"
    if not seg_path.exists():
        return jsonify({"error": "No segments"}), 404
    return jsonify(json.loads(seg_path.read_text(encoding="utf-8")))


@app.route("/api/session/<sid>/segments", methods=["PUT"])
def save_segments(sid):
    """Save edited segments."""
    session = _get_session(sid)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json()
    if not data or "segments" not in data:
        return jsonify({"error": "No segments provided"}), 400
    seg_path = Path(session["dir"]) / "segments.json"
    seg_path.write_text(json.dumps(data["segments"], ensure_ascii=False), encoding="utf-8")
    return jsonify({"ok": True})


@app.route("/api/session/<sid>/video")
def get_video(sid):
    """Get the original video for preview."""
    session = _get_session(sid)
    if not session:
        abort(404)
    return send_file(session["video"])


@app.route("/api/session/<sid>/srt")
def get_srt(sid):
    """Download the current SRT file."""
    session = _get_session(sid)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    seg_path = Path(session["dir"]) / "segments.json"
    if not seg_path.exists():
        return jsonify({"error": "No segments"}), 404
    segments = json.loads(seg_path.read_text(encoding="utf-8"))
    srt_content = generate_srt(segments)
    srt_path = Path(session["dir"]) / "subtitles.srt"
    srt_path.write_text(srt_content, encoding="utf-8")
    return send_file(str(srt_path), as_attachment=True,
                     download_name="subtitles.srt", mimetype="text/plain")


@app.route("/api/session/<sid>/burn", methods=["POST"])
def burn_subtitles(sid):
    """Burn edited subtitles into video and return the result."""
    session = _get_session(sid)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    font_size = request.json.get("font_size", 22) if request.is_json else 22
    margin_v = request.json.get("margin_v", 30) if request.is_json else 30

    try:
        seg_path = Path(session["dir"]) / "segments.json"
        segments = json.loads(seg_path.read_text(encoding="utf-8"))
        srt_content = generate_srt(segments)

        srt_path = Path(session["dir"]) / "subtitles.srt"
        srt_path.write_text(srt_content, encoding="utf-8")

        ext = session["ext"]
        output_path = Path(session["dir"]) / f"output_subtitled.{ext}"
        video_path = session["video"]

        escaped_srt = str(srt_path).replace("\\", "/").replace(":", "\\:")
        subprocess.run(
            ["ffmpeg", "-i", video_path,
             "-vf", f"subtitles='{escaped_srt}':force_style='FontSize={font_size},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV={margin_v}'",
             "-c:a", "copy", str(output_path), "-y"],
            check=True, capture_output=True)

        return jsonify({"file": str(output_path), "ext": ext, "name": f"subtitled.{ext}"})

    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"FFmpeg: {e.stderr.decode('utf-8', errors='replace')[:500]}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    get_model("base")
    app.run(host="127.0.0.1", port=5000, debug=False)
