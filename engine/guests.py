"""Guest library: each caller's photo and @handle, saved once and reused on every live.

Stored in <projects>/_guests/<slug>/:
  original.<ext>   the photo exactly as you added it
  photo.png        the enhanced, square, face-centred version used in shorts
  guest.json       {"name", "handle"}

Photo enhancement (all local, no upload):
  1. small photos are upscaled 4x (Real-ESRGAN)
  2. the face is restored (GFPGAN 1.4): sharper eyes, skin and hair from blurry screenshots
  3. square crop around the head and shoulders, centred on the face
Each step is skipped (with a lower-quality fallback) if its model isn't downloaded.
"""
import json
import os
import re

import cv2
import numpy as np

import media
import paths

SIZE = 1024
# where the eyes, nose and mouth corners sit in a 512x512 FFHQ-aligned face (what GFPGAN expects)
FFHQ_512 = np.array([[0.37691676, 0.46864664], [0.62285697, 0.46912813], [0.50123859, 0.61331904],
                     [0.39308822, 0.72541100], [0.61150205, 0.72490465]], np.float32) * 512


def root():
    return os.path.join(paths.PROJECTS, "_guests")


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "guest"


def folder(name):
    return os.path.join(root(), slug(name))


def get(name):
    """{"name", "handle", "photo" (path or None), "slug", "version"} for a guest, or a blank entry."""
    d = folder(name)
    info = media.load_json(os.path.join(d, "guest.json")) if os.path.exists(os.path.join(d, "guest.json")) else {}
    photo = os.path.join(d, "photo.png")
    has = os.path.isfile(photo)
    return {"name": info.get("name") or name, "handle": info.get("handle", ""), "slug": slug(name),
            "photo": photo if has else None,
            "version": f"{os.path.getmtime(photo):.0f}-{os.path.getsize(photo)}" if has else None}


def all_guests():
    if not os.path.isdir(root()):
        return []
    out = []
    for s in sorted(os.listdir(root())):
        j = os.path.join(root(), s, "guest.json")
        if os.path.isfile(j):
            out.append(get(media.load_json(j).get("name") or s))
    return out


def set_info(name, handle=None):
    d = folder(name)
    os.makedirs(d, exist_ok=True)
    cur = get(name)
    h = cur["handle"] if handle is None else handle.strip()
    if h and not h.startswith("@"):
        h = "@" + h
    media.write_json(os.path.join(d, "guest.json"), {"name": name.strip(), "handle": h[:40]})
    return get(name)


def save_photo(name, data, ext):
    """Keep the original, then build the enhanced photo. Returns get(name)."""
    d = folder(name)
    os.makedirs(d, exist_ok=True)
    ext = ext.lower().lstrip(".") if ext else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp", "bmp"):
        raise ValueError("Use a JPG, PNG or WebP photo.")
    for f in os.listdir(d):
        if f.startswith("original."):
            os.remove(os.path.join(d, f))
    orig = os.path.join(d, "original." + ext)
    with open(orig, "wb") as f:
        f.write(data)
    img = cv2.imread(orig, cv2.IMREAD_COLOR)
    if img is None:
        os.remove(orig)
        raise ValueError("That file isn't a photo Clip Studio can read.")
    out = enhance(img)
    final = os.path.join(d, "photo.png")
    tmp = media.tmp_name(final)
    cv2.imwrite(tmp, out)
    media.commit(tmp, final)
    if not os.path.exists(os.path.join(d, "guest.json")):
        set_info(name, "")
    return get(name)


# ---------------- enhancement ----------------
def _run_model(path, feed):
    """Load, run once, release. These models are big (GFPGAN ~2 GB in memory) and only run when a
    photo is added, so they're never kept loaded."""
    import gc
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.enable_cpu_mem_arena = False
    sess = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
    try:
        return sess.run(None, feed)[0]
    finally:
        del sess
        gc.collect()


def upscale(img):
    """4x with Real-ESRGAN when the photo is small; otherwise (or without the model) high-quality resize."""
    h, w = img.shape[:2]
    if max(h, w) >= SIZE:
        return img
    if os.path.isfile(paths.ESRGAN):
        if max(h, w) > 400:          # keep CPU time reasonable: the model works on at most 400 px
            s = 400 / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        x = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        y = _run_model(paths.ESRGAN, {"input": x})[0]
        return cv2.cvtColor((np.clip(y.transpose(1, 2, 0), 0, 1) * 255).round().astype(np.uint8), cv2.COLOR_RGB2BGR)
    s = SIZE / max(h, w)
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_LANCZOS4)


def find_face(img):
    """Biggest face: (box x,y,w,h, five landmarks) or None."""
    h, w = img.shape[:2]
    det = cv2.FaceDetectorYN.create(paths.YUNET, "", (w, h), 0.6)
    _, faces = det.detect(img)
    if faces is None or not len(faces):
        return None
    f = max(faces, key=lambda r: r[2] * r[3])
    return f[:4], f[4:14].reshape(5, 2).astype(np.float32)


def restore_face(img, landmarks):
    """GFPGAN on the aligned face, pasted back with a soft edge."""
    if not os.path.isfile(paths.GFPGAN):
        return img
    m, _ = cv2.estimateAffinePartial2D(landmarks, FFHQ_512, method=cv2.LMEDS)
    if m is None:
        return img
    face = cv2.warpAffine(img, m, (512, 512), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    x = (cv2.cvtColor(face, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0 - 0.5) / 0.5
    y = _run_model(paths.GFPGAN, {"input": x.transpose(2, 0, 1)[None]})[0]
    fixed = cv2.cvtColor(((np.clip(y.transpose(1, 2, 0), -1, 1) + 1) / 2 * 255).round().astype(np.uint8),
                         cv2.COLOR_RGB2BGR)
    # blend 80% restored / 20% original so it still looks like their photo, not a new person
    fixed = cv2.addWeighted(fixed, 0.8, face, 0.2, 0)
    inv = cv2.invertAffineTransform(m)
    h, w = img.shape[:2]
    back = cv2.warpAffine(fixed, inv, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    mask = np.zeros((512, 512), np.float32)
    cv2.rectangle(mask, (40, 40), (472, 472), 1.0, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), 18)
    mask = cv2.warpAffine(mask, inv, (w, h))[..., None]
    return (back * mask + img * (1 - mask)).round().astype(np.uint8)


def enhance(img):
    img = upscale(img)
    found = find_face(img)
    if found is not None:
        box, lm = found
        img = restore_face(img, lm)
        fx, fy, fw, fh = box
        side = fw * 2.6                                  # head and shoulders, like a profile picture
        cx, cy = fx + fw / 2, fy + fh * 0.62
    else:
        h, w = img.shape[:2]
        side, cx, cy = min(h, w), w / 2, h / 2
    h, w = img.shape[:2]
    side = int(min(max(side, 64), h, w))                 # never reach past the photo's edges
    x0 = int(min(max(cx - side / 2, 0), w - side))
    y0 = int(min(max(cy - side / 2, 0), h - side))
    crop = img[y0:y0 + side, x0:x0 + side]
    out = cv2.resize(crop, (SIZE, SIZE), interpolation=cv2.INTER_LANCZOS4 if side < SIZE else cv2.INTER_AREA)
    soft = cv2.GaussianBlur(out, (0, 0), 1.2)
    return cv2.addWeighted(out, 1.25, soft, -0.25, 0)    # light sharpen


def placeholder(name, out_path):
    """A photo stand-in for a guest without one: their initials on the channel's colours."""
    img = np.zeros((SIZE, SIZE, 3), np.uint8)
    for y in range(SIZE):                                  # night-blue gradient
        t = y / SIZE
        img[y] = (int(0x20 + 0x30 * t), int(0x14 + 0x18 * t), int(0x0D + 0x08 * t))
    from PIL import Image, ImageDraw, ImageFont
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name or "?")[:2]).upper() or "?"
    font = ImageFont.truetype(os.path.join(paths.FONTS, "Anton-Regular.ttf"), 420)
    dr = ImageDraw.Draw(pil)
    box = dr.textbbox((0, 0), initials, font=font)
    dr.text(((SIZE - (box[2] - box[0])) / 2 - box[0], (SIZE - (box[3] - box[1])) / 2 - box[1]), initials,
            font=font, fill=(255, 200, 58))
    pil.save(out_path)
    return out_path
