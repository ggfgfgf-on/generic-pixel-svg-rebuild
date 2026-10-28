#!/usr/bin/env python3
"""Attribute rendered error to individual paths, so nobody has to guess ids.

  python scripts/attribute.py ref.png out.svg --top 8

How it works: the artwork is re-rendered once with every <path> filled by a
unique index colour, which yields a per-pixel "which path is visible here" map.
The real render is compared with the reference, and each path is charged only
for the pixels where it is actually on top. Paths are ranked by total error,
and each row says whether the error looks like a colour mistake (uniform inside
the shape) or a shape mistake (concentrated on the silhouette), plus a suggested
fill taken from the reference median.

Output is JSON so a model can turn the top rows straight into iterate.py patch
ops: {"op":"recolor","ids":[..],"to":"#rrggbb"} or {"op":"redraw","ids":[..],
"d":"..."}.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PATH_RE = __import__("re").compile(r"<path\b[^>]*/>", __import__("re").S)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def best_colour(rgb, start, rounds=5):
    """在 max-channel 误差下找最优填充色：坐标下降，比 median 更贴合这套度量。"""
    best = np.array(start, dtype=np.int16)
    cur = float(np.abs(rgb - best).max(axis=1).mean())
    step = 32
    for _ in range(rounds):
        moved = False
        for ch in range(3):
            for sign in (1, -1):
                cand = best.copy()
                cand[ch] = int(np.clip(cand[ch] + sign * step, 0, 255))
                c = float(np.abs(rgb - cand).max(axis=1).mean())
                if c < cur - 1e-6:
                    best, cur, moved = cand, c, True
        step = max(1, step // 2)
        if not moved and step == 1:
            break
    return best.astype(int), cur


def index_colour(i):
    return "#%02x%02x%02x" % (i & 255, (i >> 8) & 255, (i >> 16) & 255)


def build_label_svg(svg_text, n_paths):
    """同几何、同层级，只把每条 path 的 fill 换成一个唯一的索引色。"""
    counter = [0]

    def repl(m):
        tag = m.group(0)
        counter[0] += 1
        i = counter[0]
        return __import__("re").sub(r'fill="[^"]*"', 'fill="%s"' % index_colour(i), tag, count=1)

    return PATH_RE.sub(repl, svg_text), counter[0]


def render(svg_path, png_path, W, H):
    run([PY, os.path.join(HERE, "render.py"), svg_path, png_path, str(W), str(H)])
    return os.path.exists(png_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("svg")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--min-px", type=int, default=40, help="ignore slivers below this pixel count")
    ap.add_argument("--json", default="", help="write the full report here")
    a = ap.parse_args()

    W, H = Image.open(a.ref).size
    with open(a.svg, "r", encoding="utf-8") as fh:
        svg_text = fh.read()

    tmp = tempfile.mkdtemp(prefix="attribute-")
    labels_svg, n_paths = build_label_svg(svg_text, 0)
    lab_fill = {}
    for n_i, m_i in enumerate(PATH_RE.finditer(svg_text), start=1):
        mm = __import__("re").search(r'fill="(#[0-9a-fA-F]{6})"', m_i.group(0))
        if mm:
            lab_fill[n_i] = mm.group(1)
    labels_path = os.path.join(tmp, "labels.svg")
    with open(labels_path, "w", encoding="utf-8") as fh:
        fh.write(labels_svg)
    labels_png = os.path.join(tmp, "labels.png")
    if not render(labels_path, labels_png, W, H):
        print(json.dumps({"error": "label render failed"}, ensure_ascii=False))
        raise SystemExit(2)

    real_png = os.path.join(tmp, "real.png")
    if not render(a.svg, real_png, W, H):
        print(json.dumps({"error": "render failed"}, ensure_ascii=False))
        raise SystemExit(2)

    ref = np.asarray(Image.open(a.ref).convert("RGB"), dtype=np.int16)
    cur = np.asarray(Image.open(real_png).convert("RGB"), dtype=np.int16)
    lab = np.asarray(Image.open(labels_png).convert("RGB"), dtype=np.int32)
    if ref.shape != cur.shape:
        cur = np.asarray(Image.fromarray(cur.astype(np.uint8)).resize((W, H), Image.LANCZOS), dtype=np.int16)
    if lab.shape != ref.shape:
        lab = np.asarray(Image.fromarray(lab.astype(np.uint8)).resize((W, H), Image.NEAREST), dtype=np.int32)

    err = np.abs(ref - cur).max(axis=2)

    # 索引色 -> 编号（每像素），非纯色像素（抗锯齿边）丢弃
    n = max(n_paths, 1) + 1
    pal = np.array([[i & 255, (i >> 8) & 255, (i >> 16) & 255] for i in range(n)], dtype=np.int32)
    flat = lab.reshape(-1, 3)
    d = ((flat[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2) if n <= 512 else None
    if d is None:
        # 大图分块算，避免 n*像素 的内存爆炸
        idx = np.zeros(flat.shape[0], dtype=np.int32)
        best = np.full(flat.shape[0], 1 << 30, dtype=np.int64)
        for s in range(0, n, 64):
            chunk = pal[s:s + 64]
            dd = ((flat[:, None, :] - chunk[None, :, :]) ** 2).sum(axis=2)
            j = dd.argmin(axis=1)
            v = dd[np.arange(flat.shape[0]), j]
            take = v < best
            best[take] = v[take]
            idx[take] = s + j[take]
        dist = best
    else:
        idx = d.argmin(axis=1).astype(np.int32)
        dist = d[np.arange(flat.shape[0]), idx]
    idx = idx.reshape(err.shape)
    solid = dist.reshape(err.shape) <= 9          # 纯色像素（索引色几乎无误差）

    rows = []
    for i in range(1, n_paths + 1):
        mask = (idx == i) & solid
        px = int(mask.sum())
        if px < a.min_px:
            continue
        inner = ndimage.binary_erosion(mask, iterations=1, border_value=0)
        ring = mask & ~inner
        if ring.sum() < 10:
            ring = mask
            inner = mask
        e_all = float(err[mask].mean())
        e_in = float(err[inner].mean()) if inner.sum() else e_all
        e_ring = float(err[ring].mean()) if ring.sum() else e_all
        rgb = ref[mask].astype(np.int16)
        cur_fill = np.array([int(lab_fill[i][1:3], 16), int(lab_fill[i][3:5], 16), int(lab_fill[i][5:7], 16)]) if lab_fill.get(i) else np.median(rgb, axis=0).astype(int)
        best, best_err = best_colour(rgb, cur_fill) if px else (cur_fill, e_all)
        # 渲染实测与"纯色假设"有零点几的噪声，负收益一律按 0 处理
        gain = max(0.0, (e_all - best_err) * px)
        # 最优填充色与现有色差很远 => 这块底下根本不是它；轮廓比内部错得多 => 边描歪了
        delta = float(np.abs(np.array(cur_fill, dtype=float) - np.array(best, dtype=float)).max())
        guess = "shape" if (delta > 24 or e_ring > e_in * 1.25) else "colour"
        ys, xs = np.nonzero(mask)
        rows.append({
            "id": i,
            "fill": "#%02x%02x%02x" % tuple(int(v) for v in cur_fill),
            "gain": round(float(gain), 1),
            "px": px,
            "mean": round(e_all, 2),
            "sum": round(float(err[mask].sum()), 1),
            "inner": round(e_in, 2),
            "ring": round(e_ring, 2),
            "guess": guess,
            "suggest_fill": "#%02x%02x%02x" % tuple(int(v) for v in best),
            "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
        })

    rows.sort(key=lambda r: -r["gain"])
    total = float(err.sum())
    for r in rows:
        r["share"] = round(100.0 * r["sum"] / total, 2) if total else 0.0

    report = {"paths": n_paths, "total_err_sum": round(total, 1),
              "worst": rows[:a.top]}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"paths": n_paths, "rows": rows[:max(a.top, 50)]}, fh, ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
