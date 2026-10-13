"""Thumbnails in the channel's style (1280x720).

Patterns taken from the channel's own top thumbnails:
  * two people split left/right: the guest (or a stand-in image of the caller type) and Elijah,
    both cut out, faces big, often with a white outline glow
  * 1-3 words, ALL CAPS, ultra-heavy type: white with a yellow or red accent word, thick dark stroke
  * one accent device: red curved arrow at the guest, a gold lightning split, a yellow banner,
    or a speech bubble
  * a themed background (church, flag, Quran pages, Vatican) darkened so the people pop

Presets: "lightning", "arrow", "banner", "bubble", "solo" (caller with no camera). Each run makes 3 variants for YouTube's
built-in thumbnail test.
"""
import math, os
import numpy as np
import cv2
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(ROOT, "assets", "fonts")
SEG_MODEL = os.path.join(ROOT, "models", "u2net_human_seg.onnx")
W, H = 1280, 720
YELLOW, RED, WHITE, BLACK = (255, 214, 0), (226, 32, 38), (255, 255, 255), (12, 12, 14)
_sess = None


# ---------- cutouts ----------
def cutout(img: Image.Image) -> Image.Image:
    """Person cutout with clean edges (U2-Net human segmentation, refined with a guided filter)."""
    global _sess
    _sess = _sess or ort.InferenceSession(SEG_MODEL, providers=["CPUExecutionProvider"])
    rgb = np.asarray(img.convert("RGB"))
    x = cv2.resize(rgb, (320, 320), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    m = _sess.run(None, {"input.1": x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0, 0]
    m = (m - m.min()) / (m.max() - m.min() + 1e-6)
    m = cv2.resize(m, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_CUBIC)
    try:   # edge-aware refinement keeps braids and fingers instead of a blobby outline
        m = cv2.ximgproc.guidedFilter(rgb.astype(np.float32) / 255, m.astype(np.float32), 8, 1e-3)
    except AttributeError:
        pass
    m = np.clip((m - 0.35) / 0.3, 0, 1)
    out = img.convert("RGBA")
    out.putalpha(Image.fromarray((m * 255).astype(np.uint8)))
    return out.crop(out.getbbox())


def outline(person: Image.Image, width=7, color=WHITE, glow=0) -> Image.Image:
    """White sticker outline (and optional soft glow) around a cutout, like the channel's thumbnails."""
    pad = width * 2 + glow * 2
    a = person.split()[3]
    big = Image.new("L", (person.width + 2 * pad, person.height + 2 * pad), 0)
    big.paste(a, (pad, pad))
    stroke = big.filter(ImageFilter.MaxFilter(width * 2 + 1)).point(lambda v: 255 if v > 60 else 0)
    # a side where the photo was cropped through the body gets no outline (it would draw a box edge)
    al = np.asarray(a)
    st = np.asarray(stroke).copy()
    if al[:, :3].mean() > 60: st[:, :pad + 2] = 0
    if al[:, -3:].mean() > 60: st[:, -pad - 2:] = 0
    if al[-3:, :].mean() > 60: st[-pad - 2:, :] = 0
    if al[:3, :].mean() > 60: st[:pad + 2, :] = 0
    stroke = Image.fromarray(st)
    canvas = Image.new("RGBA", big.size, (0, 0, 0, 0))
    if glow:
        g = stroke.filter(ImageFilter.GaussianBlur(glow))
        canvas.paste(Image.new("RGBA", big.size, color + (255,)), (0, 0), g.point(lambda v: int(v * 0.8)))
    canvas.paste(Image.new("RGBA", big.size, color + (255,)), (0, 0), stroke.filter(ImageFilter.GaussianBlur(1)))
    canvas.alpha_composite(person, (pad, pad))
    return canvas


def place(canvas, person, side, height_frac=1.0, nudge=0, bottom_bleed=0.04):
    """Scale a cutout to a fraction of the frame height and anchor it to the bottom on one side."""
    s = H * height_frac / person.height
    p = person.resize((max(1, int(person.width * s)), int(person.height * s)), Image.LANCZOS)
    x = (W * 0.75 - p.width / 2 if side == "right" else W * 0.25 - p.width / 2) + nudge
    x = int(min(max(x, -p.width * 0.15), W - p.width * 0.85))
    canvas.alpha_composite(p, (x, int(H - p.height + H * bottom_bleed)))
    return canvas


# ---------- backgrounds ----------
def background(img=None, tint=(20, 40, 90), blur=6, dark=0.45):
    if img is None:   # dark studio gradient with grain
        y, x = np.mgrid[0:H, 0:W]
        r = np.sqrt(((x - W / 2) / W) ** 2 + ((y - H * 0.4) / H) ** 2)
        base = np.clip(1 - r * 1.4, 0, 1)[..., None] * np.array(tint, np.float32) * 1.6
        base += np.random.default_rng(7).normal(0, 6, (H, W, 1))
        return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).convert("RGBA")
    im = img.convert("RGB")
    s = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    im = im.crop(((im.width - W) // 2, (im.height - H) // 2, (im.width - W) // 2 + W, (im.height - H) // 2 + H))
    im = ImageEnhance.Brightness(im.filter(ImageFilter.GaussianBlur(blur))).enhance(dark)
    tint_layer = Image.new("RGB", (W, H), tint)
    im = Image.blend(im, tint_layer, 0.18)
    y, x = np.mgrid[0:H, 0:W]
    vig = np.clip(1.25 - np.sqrt(((x - W / 2) / (W * 0.6)) ** 2 + ((y - H / 2) / (H * 0.6)) ** 2) * 0.55, 0.35, 1)
    return Image.fromarray((np.asarray(im) * vig[..., None]).astype(np.uint8)).convert("RGBA")


# ---------- text ----------
def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), size)


def fit_size(words, fname, max_w, max_size, min_size=60):
    size = max_size
    while size > min_size:
        if font(fname, size).getlength(" ".join(w for w, _ in words)) <= max_w:
            break
        size -= 4
    return size


def headline(canvas, lines, cx, cy, max_w, fname="Anton-Regular.ttf", max_size=200, stroke=12,
             italic=False, shadow=True):
    """lines: [[(word, color), ...], ...] drawn centred at (cx, cy) with thick dark stroke + drop shadow."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    sizes = [fit_size(l, fname, max_w, max_size) for l in lines]
    size = min(sizes)
    f = font(fname, size)
    asc, desc = f.getmetrics()
    lh = int((asc + desc) * 0.88)
    y = cy - lh * len(lines) / 2
    for l in lines:
        total = f.getlength(" ".join(w for w, _ in l))
        x = cx - total / 2
        for w, col in l:
            if shadow:
                d.text((x + 8, y + 10), w, font=f, fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
            d.text((x, y), w, font=f, fill=col, stroke_width=stroke, stroke_fill=BLACK)
            x += f.getlength(w + " ")
        y += lh
    if italic:
        layer = layer.transform(layer.size, Image.AFFINE, (1, 0.18, -0.18 * cy, 0, 1, 0), Image.BICUBIC)
    canvas.alpha_composite(layer)
    return canvas


# ---------- accents ----------
def arrow(canvas, start, end, color=RED, width=34, bend=0.35):
    """Red curved arrow with white edge, pointing from start to end."""
    (x0, y0), (x1, y1) = start, end
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    nx, ny = -(y1 - y0), (x1 - x0)
    cx, cy = mx + nx * bend, my + ny * bend
    pts = [((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1, (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1)
           for t in np.linspace(0, 0.82, 40)]
    ex, ey = pts[-1]
    ang = math.atan2(y1 - ey, x1 - ex)
    head = [(x1, y1), (ex + width * 1.5 * math.cos(ang + 2.2), ey + width * 1.5 * math.sin(ang + 2.2)),
            (ex + width * 1.5 * math.cos(ang - 2.2), ey + width * 1.5 * math.sin(ang - 2.2))]
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for wdt, col in ((width + 12, WHITE), (width, color)):
        d.line(pts, fill=col, width=wdt, joint="curve")
        grow = (wdt - width) * 0.9
        hd = [(x + grow * math.cos(math.atan2(y - ey, x - ex)), y + grow * math.sin(math.atan2(y - ey, x - ex))) for x, y in head]
        d.polygon(hd, fill=col)
    sh = layer.filter(ImageFilter.GaussianBlur(6))
    canvas.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", canvas.size, 0).split(), sh.split()[3].point(lambda v: v // 2))), (6, 8))
    canvas.alpha_composite(layer)
    return canvas


def lightning(canvas, x=W // 2, seed=3):
    rng = np.random.default_rng(seed)
    pts, y = [], -10
    while y < H + 10:
        pts.append((x + rng.uniform(-22, 22), y)); y += rng.uniform(30, 70)
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    d.line(pts, fill=(255, 190, 40, 255), width=26)
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)))
    core = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(core)
    d.line(pts, fill=(255, 230, 120, 255), width=9)
    d.line(pts, fill=(255, 255, 240, 255), width=4)
    canvas.alpha_composite(core)
    return canvas


def banner(canvas, text, color=YELLOW, fname="Anton-Regular.ttf"):
    h = 150
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, H - h, W, H], fill=color + (255,))
    d.rectangle([0, H - h, W, H - h + 8], fill=BLACK + (255,))
    size = fit_size([(text, None)], fname, W - 120, 140)
    f = font(fname, size)
    tw = f.getlength(text)
    d.text(((W - tw) / 2, H - h + (h - size * 1.2) / 2), text, font=f, fill=WHITE, stroke_width=9, stroke_fill=(20, 30, 60))
    return canvas


def bubble(canvas, lines, xy, w=420):
    x, y = xy
    h = 70 + 92 * len(lines)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([x, y, x + w, y + h], radius=40, fill=WHITE, outline=BLACK, width=6)
    d.polygon([(x + 60, y + h - 4), (x + 30, y + h + 70), (x + 140, y + h - 4)], fill=WHITE, outline=BLACK)
    d.line([(x + 62, y + h - 3), (x + 138, y + h - 3)], fill=WHITE, width=8)
    f = font("Anton-Regular.ttf", 92)
    for i, (t, col) in enumerate(lines):
        tw = f.getlength(t)
        d.text((x + (w - tw) / 2, y + 22 + i * 92), t, font=f, fill=col)
    return canvas


# ---------- presets ----------
def make(preset, host, words, bg=None, guest=None, host_side="right", out=None, tint=(20, 40, 90), seed=0):
    """host/guest: cutout RGBA images. words: [[(word, color)...], ...] (1-2 lines)."""
    c = background(bg, tint=tint)
    gside = "left" if host_side == "right" else "right"
    if preset == "lightning":
        if guest is not None:
            place(c, outline(guest, 0 or 1, glow=0), gside, 1.02)
        place(c, host, host_side, 1.05)
        lightning(c, seed=seed)
        headline(c, words, W / 2, H * 0.80, W * 0.86, "Anton-Regular.ttf", 230, italic=True)
    elif preset == "arrow":
        if guest is not None:
            place(c, outline(guest, 7), gside, 0.98)
        place(c, outline(host, 7), host_side, 1.02)
        tx = W * 0.25 if host_side == "right" else W * 0.75
        headline(c, words, tx if guest is None else W / 2, H * 0.80 if guest is not None else H * 0.42,
                 W * 0.48 if guest is None else W * 0.9, "Anton-Regular.ttf", 210)
        if guest is not None:
            gx = W * 0.25 if gside == "left" else W * 0.75
            arrow(c, (W / 2 + (80 if gside == "left" else -80), H * 0.58), (gx + (90 if gside == "left" else -90), H * 0.42))
        else:
            hx = W * 0.75 if host_side == "right" else W * 0.25
            arrow(c, (tx, H * 0.70), (hx - (190 if host_side == "right" else -190), H * 0.55), bend=-0.35)
    elif preset == "solo":          # caller had no camera: Elijah + headline + themed background
        place(c, outline(host, 7, glow=10), host_side, 1.04)
        tx = W * 0.28 if host_side == "right" else W * 0.72
        headline(c, words, tx, H * 0.47, W * 0.52, "Anton-Regular.ttf", 230)
    elif preset == "banner":
        if guest is not None:
            place(c, guest, gside, 0.95, bottom_bleed=0.0)
        place(c, host, host_side, 1.0, bottom_bleed=0.0)
        banner(c, " ".join(w for line in words for w, _ in line))
    elif preset == "bubble":
        if guest is not None:
            place(c, outline(guest, 6), gside, 0.98)
        place(c, outline(host, 7), host_side, 1.02)
        bubble(c, words[0] if isinstance(words[0], list) else words, (W * 0.08 if host_side == "right" else W * 0.55, H * 0.08))
    if out:
        c.convert("RGB").save(out, quality=95)
    return c
