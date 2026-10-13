"""Download what Clip Studio needs (one time, ~830 MB), then run the setup check.

Models go to the folder in engine/paths.py: <repo>/models, or CLIPSTUDIO_MODELS if you set it.
Fonts ship in assets/fonts; any that are missing are fetched from Google Fonts' GitHub.
Safe to re-run: finished files are skipped, half-finished downloads are thrown away.
"""
import os
import sys
import tarfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import paths  # noqa: E402

RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
MODELS = [
    (paths.SILERO, RELEASE + "silero_vad.onnx"),
    (paths.YUNET, "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
                  "face_detection_yunet/face_detection_yunet_2023mar.onnx"),
    (paths.U2NET, "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx"),
]
PARAKEET = RELEASE + "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2"
FONT_URLS = {
    "Anton-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
    "Archivo.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/archivo/Archivo%5Bwdth%2Cwght%5D.ttf",
    "Poppins-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Bold.ttf",
    "Poppins-Black.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Black.ttf",
    "Poppins-ExtraBold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-ExtraBold.ttf",
}


def fetch(url, dest):
    """Download to dest.partial, then rename, so an interrupted download never looks finished."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".partial"
    print("downloading", os.path.basename(dest))
    urllib.request.urlretrieve(url, tmp)
    if os.path.getsize(tmp) < 1000:
        os.remove(tmp)
        raise SystemExit(f"{url} came back nearly empty; try again later.")
    os.replace(tmp, dest)


def main():
    for dest, url in MODELS:
        if not os.path.isfile(dest):
            fetch(url, dest)
    if not all(os.path.isfile(f) for f in paths.MODEL_FILES["speech recognition"]):
        archive = os.path.join(paths.MODELS, "parakeet.tar.bz2")
        fetch(PARAKEET, archive)
        print("unpacking speech model")
        with tarfile.open(archive) as t:
            t.extractall(paths.MODELS)
        os.remove(archive)
    for name, url in FONT_URLS.items():
        dest = os.path.join(paths.FONTS, name)
        if not os.path.isfile(dest):
            fetch(url, dest)

    print("\nSetup check:")
    bad = 0
    for c in paths.check():
        print(("  ok    " if c["ok"] else "  FIX   ") + c["item"] + ("" if c["ok"] else "  ->  " + c["fix"]))
        bad += not c["ok"] and c["item"] != "Claude API key"
    print("\nModels are in", paths.MODELS)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
