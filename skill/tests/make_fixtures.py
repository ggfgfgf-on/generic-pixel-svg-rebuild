"""Generate the three regression fixtures locally (no external assets, no copyrighted input).

  flat-320x200.png       -> 8 flat colors, crisp edges: exercises the pixel-raster branch
  photo-320x180.png      -> smooth gradients + noise + soft edges: exercises the curve branch
  alpha-cutout-320x180.png -> shape on a transparent background: exercises alpha handling

Run:  python make_fixtures.py
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures")
os.makedirs(OUT, exist_ok=True)

# 1) flat: few colors, hard edges
flat = Image.new("RGB", (320, 200), (250, 250, 250))
d = ImageDraw.Draw(flat)
d.rectangle([10, 10, 120, 80], fill=(214, 48, 49))
d.ellipse([140, 20, 240, 100], fill=(38, 120, 190))
d.polygon([(20, 120), (110, 190), (20, 190)], fill=(240, 178, 20))
d.rectangle([130, 120, 300, 190], fill=(30, 30, 40))
d.rectangle([150, 140, 280, 170], fill=(120, 200, 120))
flat.save(os.path.join(OUT, "flat-320x200.png"))

# 2) photo-like: smooth gradients, noise, soft edges
w, h = 320, 180
yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
base = np.stack([
    120 + 90 * np.sin(xx / 55.0) * np.cos(yy / 41.0),
    110 + 70 * np.cos((xx + yy) / 63.0),
    140 + 60 * np.sin(yy / 33.0),
], axis=2)
rng = np.random.default_rng(7)
base += rng.normal(0, 9, base.shape)
img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
dr = ImageDraw.Draw(img)
dr.ellipse([90, 40, 210, 150], fill=(210, 120, 90))
img = img.filter(ImageFilter.GaussianBlur(1.2))
img.save(os.path.join(OUT, "photo-320x180.png"))

# 3) transparent cutout: subject on alpha=0 background
cut = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
dc = ImageDraw.Draw(cut)
dc.ellipse([40, 30, 170, 160], fill=(200, 40, 60, 255))
dc.rectangle([180, 50, 280, 150], fill=(30, 90, 170, 255))
dc.polygon([(150, 140), (200, 40), (250, 140)], fill=(240, 190, 40, 255))
cut.save(os.path.join(OUT, "alpha-cutout-320x180.png"))

for n in sorted(os.listdir(OUT)):
    p = os.path.join(OUT, n)
    print(n, os.path.getsize(p), Image.open(p).size)
