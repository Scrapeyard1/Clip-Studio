"""One place for every file location Clip Studio depends on, on Mac, Windows and Linux.

Override with environment variables when needed:
  CLIPSTUDIO_MODELS    folder holding the downloaded models   (default: <repo>/models)
  CLIPSTUDIO_PROJECTS  folder holding projects                 (default: <repo>/projects)
  CLIPSTUDIO_FFMPEG / CLIPSTUDIO_FFPROBE   full paths if FFmpeg isn't on PATH
"""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = os.environ.get("CLIPSTUDIO_MODELS", os.path.join(ROOT, "models"))
FONTS = os.path.join(ROOT, "assets", "fonts")
PROJECTS = os.environ.get("CLIPSTUDIO_PROJECTS", os.path.join(ROOT, "projects"))

PARAKEET_DIR = os.path.join(MODELS, "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")
MODEL_FILES = {
    "speech recognition": [os.path.join(PARAKEET_DIR, f) for f in
                           ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")],
    "voice detection": [os.path.join(MODELS, "silero_vad.onnx")],
    "face detection": [os.path.join(MODELS, "yunet.onnx")],
    "thumbnail cutouts": [os.path.join(MODELS, "u2net_human_seg.onnx")],
}
SILERO = MODEL_FILES["voice detection"][0]
YUNET = MODEL_FILES["face detection"][0]
U2NET = MODEL_FILES["thumbnail cutouts"][0]

CAPTION_FONT = os.path.join(FONTS, "Poppins-Bold.ttf")
FONT_FILES = ["Anton-Regular.ttf", "Archivo.ttf", "Poppins-Bold.ttf", "Poppins-Black.ttf", "Poppins-ExtraBold.ttf"]


def ffmpeg():
    return os.environ.get("CLIPSTUDIO_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def ffprobe():
    return os.environ.get("CLIPSTUDIO_FFPROBE") or shutil.which("ffprobe") or "ffprobe"


def ffmpeg_filter_path(p):
    """A path as FFmpeg's filter syntax needs it (ass=, fontsdir=): forward slashes, escaped drive colon."""
    return p.replace("\\", "/").replace(":", "\\:")


def check(api_key=None):
    """Everything the app needs, as a list of {item, ok, fix}. Shown at startup and on the home screen."""
    out = []
    for name, fn in (("FFmpeg", ffmpeg), ("FFprobe", ffprobe)):
        found = shutil.which(fn()) or os.path.isfile(fn())
        out.append({"item": name, "ok": bool(found),
                    "fix": "Install FFmpeg (Mac: brew install ffmpeg, Windows: winget install ffmpeg), then restart the app."})
    for name, files in MODEL_FILES.items():
        missing = [f for f in files if not os.path.isfile(f)]
        out.append({"item": f"Model: {name}", "ok": not missing,
                    "fix": "Run: python scripts/get_models.py" + (f"  (expected in {MODELS})" if missing else "")})
    missing_fonts = [f for f in FONT_FILES if not os.path.isfile(os.path.join(FONTS, f))]
    out.append({"item": "Fonts", "ok": not missing_fonts,
                "fix": "Missing from assets/fonts: " + ", ".join(missing_fonts) + ". Run: python scripts/get_models.py"})
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    out.append({"item": "Claude API key", "ok": bool(key),
                "fix": "Add your key in Settings. It's needed to find conversations and write titles."})
    return out
