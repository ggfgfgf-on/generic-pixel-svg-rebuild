import sys, json
import numpy as np
from PIL import Image

def load_flat(p):
    """透明图按白底合成后再比：浏览器截图本身不带 alpha，直接 convert('RGB') 会把透明当成黑底。"""
    im = Image.open(p)
    if im.mode in ("RGBA", "LA", "PA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.convert("RGBA").split()[3])
        return np.asarray(bg, dtype=np.int16), np.asarray(im.convert("RGBA").split()[3]) >= 250
    return np.asarray(im.convert("RGB"), dtype=np.int16), None

ref_p, ren_p = sys.argv[1], sys.argv[2]
a, opaque = load_flat(ref_p)
b, _ = load_flat(ren_p)
if a.shape != b.shape:
    print(json.dumps({"error": "size mismatch", "ref": list(a.shape), "render": list(b.shape)})); sys.exit(2)
d = np.abs(a - b).max(axis=2)
white = (a >= 255).all(axis=2)
# 透明图只在不透明像素(alpha>=250)上判"漏画"：alpha 128~249 是抗锯齿描边，本来就该柔化
if opaque is not None:
    white = white | ~opaque
unc = int(((b >= 255).all(axis=2) & ~white).sum())
tot = d.size
rep = {"mean": round(float(d.mean()), 4),
       "pct_le3": round(float((d <= 3).mean() * 100), 3),
       "pct_le5": round(float((d <= 5).mean() * 100), 3),
       "pct_le10": round(float((d <= 10).mean() * 100), 3),
       "pct_le20": round(float((d <= 20).mean() * 100), 3),
       "uncovered": unc, "pixels": int(tot)}
print(json.dumps(rep))
