#!/usr/bin/env python3
"""form.py - measure a drawn (or traced) SVG against proportion rules.

No reference image needed: every fill colour is a 'part', and the ratios
between parts (head w/h, shoulder span in head units, eye line position,
neck width, head height on canvas) are compared with a spec.

  python scripts/form.py yy3.svg --out yy3.form.json
  python scripts/form.py yy3.svg --guides yy3.guides.png
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_PARTS = {
    "face": "#f7d8bc", "hair": "#2f1d19", "outfit": "#b8382c",
    "inner": "#f4efe6", "gold": "#d8a94a", "skin_dark": "#ecc0a0",
    "iris": "#a8442a",
}
# 允许用 --parts 覆盖（别的画风换配色即可）
# 规则按"可见区域"度量：被头发/衣服盖住的像素不算，所以用 head=脸∪发的包围盒
# (name, target, tol, note)
RULES = [
    ("head_w_over_h", 0.82, 0.13, "头宽/头高（含发型，圆脸约 0.8）"),
    ("eye_line", 0.58, 0.10, "眼线在头高（含发）58% 处，约等于脸高 45%"),
    ("neck_over_head", 0.42, 0.12, "颈宽/头宽"),
    ("shoulder_over_head", 1.80, 0.40, "肩宽/头宽（约 1.8 个头宽）"),
    ("head_on_canvas", 0.26, 0.12, "头中心在画布纵向位置（半身像偏上）"),
    ("head_over_canvas_w", 0.34, 0.08, "头宽/画布宽"),
    ("hair_over_face", 1.80, 1.20, "发量/脸（面积比；长发盘发差异大，故宽放）"),
]


def render(svg, png, w, h):
    subprocess.run([sys.executable, os.path.join(HERE, "render.py"), svg, png, str(w), str(h)],
                   check=True, capture_output=True)
    return Image.open(png).convert("RGB")


def box(mask):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def hex_rgb(s):
    s = s.lstrip("#")
    return np.array([int(s[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.int16)


def measure(svg, png, parts, tol, w, h, guides=None):
    img = render(svg, png, w, h)
    arr = np.asarray(img, dtype=np.int16)
    out, masks = {}, {}
    for name, col in parts.items():
        m = (np.abs(arr - hex_rgb(col)).max(axis=2) <= tol)
        masks[name] = m
        b = box(m)
        out[name] = {"fill": col, "px": int(m.sum()), "bbox": b}
    H, W = arr.shape[:2]

    def dim(name, axis):
        b = out[name]["bbox"]
        return None if not b else (b[3] - b[1] if axis == "h" else b[2] - b[0])

    def cy(name):
        b = out[name]["bbox"]
        return None if not b else (b[1] + b[3]) / 2.0

    def top(name):
        b = out[name]["bbox"]
        return None if not b else b[1]

    fb, hb = out["face"]["bbox"], out["hair"]["bbox"]
    head = ([min(fb[0], hb[0]), min(fb[1], hb[1]), max(fb[2], hb[2]), max(fb[3], hb[3])]
            if (fb and hb) else (fb or hb))
    out["head"] = {"bbox": head, "px": out["face"]["px"] + out["hair"]["px"]}
    head_h = (head[3] - head[1]) if head else None
    head_w = (head[2] - head[0]) if head else None
    hh, hwd = head_h or 1, head_w or 1
    rules = []
    for rname, target, tor, note in RULES:
        val = None
        if rname == "head_w_over_h":
            val = (head_w / head_h) if (head_h and head_w) else None
        elif rname == "eye_line":
            if out["iris"]["px"] and head:
                val = (cy("iris") - head[1]) / hh
        elif rname == "neck_over_head":
            nw = dim("skin_dark", "w")
            val = (nw / hwd) if nw else None
        elif rname == "shoulder_over_head":
            sw = dim("outfit", "w")
            val = (sw / hwd) if sw else None
        elif rname == "head_on_canvas":
            val = (((head[1] + head[3]) / 2.0) / H) if head else None
        elif rname == "head_over_canvas_w":
            val = (head_w / W) if head_w else None
        elif rname == "hair_over_face":
            val = (out["hair"]["px"] / out["face"]["px"]) if out["face"]["px"] else None
        ok = None if val is None else (abs(val - target) <= tor)
        rules.append({"rule": rname, "value": None if val is None else round(val, 3),
                      "target": target, "tol": tor, "ok": ok, "note": note})
    if guides:
        draw_guides(img, out, W, H).save(guides)
    return {"svg": svg, "size": [w, h], "parts": out,
            "ratios": {r["rule"]: r["value"] for r in rules},
            "verdicts": rules,
            "failed": [r["rule"] for r in rules if r["ok"] is False],
            "guides_png": guides}


def draw_guides(img, parts, W, H):
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    import PIL.ImageDraw as D
    d = D.Draw(base)
    f = parts.get("face", {}).get("bbox")
    fh = (f[3] - f[1]) if f else H // 5
    # 头高刻度尺 + 中轴
    y = 0
    i = 1
    while y < H:
        d.line([(0, y), (W, y)], fill=(0, 200, 255, 90), width=1)
        if f:
            d.text((4, y + 1), "H%d" % i, fill=(0, 120, 200, 220))
        y += fh
        i += 1
    d.line([(W // 2, 0), (W // 2, H)], fill=(255, 0, 180, 110), width=1)
    # 脸框 + 眼线
    if f:
        d.rectangle(f, outline=(255, 60, 60, 200))
        d.line([(f[0], f[1] + 0.45 * fh), (f[2], f[1] + 0.45 * fh)], fill=(255, 200, 0, 220))
    return Image.alpha_composite(img.convert("RGBA"), base).convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("svg")
    ap.add_argument("--out", default="")
    ap.add_argument("--guides", default="", help="写一张带定形网格的预览图")
    ap.add_argument("--size", default="", help="WxH，缺省从 svg 读")
    ap.add_argument("--tol", type=int, default=2, help="颜色容差；默认 2（平涂取纯色，避免抗锯齿噪声把包围盒放大）")
    ap.add_argument("--parts", default="", help="JSON 覆盖 {name: '#rrggbb'}")
    a = ap.parse_args()
    if a.size:
        w, h = [int(v) for v in a.size.lower().split("x")]
    else:
        head = open(a.svg, encoding="utf-8").read(600)
        import re
        m = re.search(r'width="(\d+)"\s+height="(\d+)"', head) or re.search(r'viewBox="0 0 (\d+) (\d+)"', head)
        w, h = (int(m.group(1)), int(m.group(2))) if m else (400, 600)
    parts = dict(DEFAULT_PARTS)
    if a.parts:
        parts.update(json.load(open(a.parts, encoding="utf-8")))
    png = os.path.splitext(a.svg)[0] + ".form-render.png"
    res = measure(a.svg, png, parts, a.tol, w, h, a.guides or None)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"ratios": res["ratios"], "failed": res["failed"],
                      "guides_png": res["guides_png"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
