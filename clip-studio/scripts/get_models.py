"""Download the models Clip Studio needs into models/ (one time, ~830 MB)."""
import os, tarfile, urllib.request
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
FILES = {
    "silero_vad.onnx": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
    "parakeet.tar.bz2": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2",
    "yunet.onnx": "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "u2net_human_seg.onnx": "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx",
}
os.makedirs(ROOT, exist_ok=True)
for name, url in FILES.items():
    dest = os.path.join(ROOT, name)
    if name == "parakeet.tar.bz2" and os.path.isdir(os.path.join(ROOT, "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")):
        continue
    if not os.path.exists(dest):
        print("downloading", name); urllib.request.urlretrieve(url, dest)
    if name.endswith(".tar.bz2"):
        tarfile.open(dest).extractall(ROOT); os.remove(dest)
print("models ready in", ROOT)
