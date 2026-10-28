#!/usr/bin/env python3
"""Ink helpers: pull strokes / poster masses out of a bitmap as their own layers.

  python scripts/ink.py ref.png --style poster --method sauvola --out poster.svg
  python scripts/ink.py ref.png --style line   --method canny  --out line.svg --dilate 1
  python scripts/ink.py ref.png --style both   --outdir layers

Why a separate helper instead of a vectorize flag: the model needs to compare
styles before committing. Every run prints one JSON line with front pixels,
path count and file size, so the choice can be made on numbers, and each layer
is a normal SVG (paths only) that iterate.py / attribute.py already understand.

  style=poster  solid masses (Sauvola / adaptive) -> potrace straight through
  style=line    edges (Canny) -> thinning -> small dilation -> potrace bands
  style=both    both layers, line drawn on top of poster
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np
import potrace


def quantize_colours(rgb_pixels, k):
    """把线条像素自己的颜色聚成 k 类：线不必是黑的，头发线、肤色线本来就不同。"""
    z = rgb_pixels.astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5)
    _, labels, centers = cv2.kmeans(z, min(k, max(1, len(z))), None, crit, 3, cv2.KMEANS_PP_CENTERS)
    return labels.ravel(), np.clip(centers.astype(int), 0, 255)

PATH_DPI = 1.0


def mask_for(gray, method, block, k, lo, hi):
    if method == "canny":
        g = cv2.GaussianBlur(gray, (3, 3), 0)
        return cv2.Canny(g, lo, hi) > 0
    if method == "sauvola":
        inv = cv2.ximgproc.niBlackThreshold(gray, 255, cv2.THRESH_BINARY_INV, block, k,
                                            None, cv2.ximgproc.BINARIZATION_SAUVOLA)
        return inv > 0
    if method == "adaptive":
        inv = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                    cv2.THRESH_BINARY_INV, block, max(2, int(k * 50)))
        return inv > 0
    raise SystemExit("unknown method: %s" % method)


def curv_d(trace):
    d = []
    for cv_ in trace:
        if not cv_.segments:
            continue
        s = "M%.1f %.1f" % (cv_.start_point.x, cv_.start_point.y)
        for sg in cv_.segments:
            if sg.is_corner:
                s += " L%.1f %.1f L%.1f %.1f" % (sg.c.x, sg.c.y, sg.end_point.x, sg.end_point.y)
            else:
                s += " C%.1f %.1f %.1f %.1f %.1f %.1f" % (sg.c1.x, sg.c1.y, sg.c2.x,
                                                          sg.c2.y, sg.end_point.x, sg.end_point.y)
        d.append(s + " Z")
    return d


def trace_mask(mask, turd):
    bmp = potrace.Bitmap(~mask)
    tr = bmp.trace(turdsize=turd, alphamax=1.0, opticurve=True, opttolerance=0.2)
    return curv_d(tr)


def write_svg(path, W, H, ds, fill):
    body = "".join('<path fill="%s" fill-rule="evenodd" d="%s"/>' % (fill, d) for d in ds)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
                 '<rect width="%d" height="%d" fill="#ffffff"/>%s</svg>'
                 % (W, H, W, H, W, H, body))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("--style", choices=["poster", "line", "both"], default="poster")
    ap.add_argument("--method", default="", help="canny|sauvola|adaptive (default: sauvola for poster, canny for line)")
    ap.add_argument("--out", default="")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--dilate", type=int, default=1, help="line width in px (0 = hairline skeleton)")
    ap.add_argument("--turd", type=int, default=4, help="drop specks smaller than this")
    ap.add_argument("--block", type=int, default=31)
    ap.add_argument("--k", type=float, default=0.2)
    ap.add_argument("--lo", type=float, default=60)
    ap.add_argument("--hi", type=float, default=160)
    ap.add_argument("--line-color", default="", help="统一线色，例如 #111111")
    ap.add_argument("--line-colors", type=int, default=0,
                    help=">1 时按参考图在线条上的颜色聚类，出彩色线稿")
    ap.add_argument("--darken", type=float, default=0.85, help="彩色线稿压暗系数，保住'线'的感觉")
    ap.add_argument("--poster-color", default="#111111")
    a = ap.parse_args()

    img = cv2.imread(a.ref, cv2.IMREAD_GRAYSCALE)
    bgr = cv2.imread(a.ref)
    if img is None:
        raise SystemExit("cannot read %s" % a.ref)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    H, W = img.shape

    jobs = []
    if a.style in ("poster", "both"):
        jobs.append(("poster", a.method or "sauvola"))
    if a.style in ("line", "both"):
        jobs.append(("line", a.method or "canny"))
    if a.style == "both" and a.method:
        jobs = [("poster", a.method), ("line", a.method)]

    outdir = a.outdir or (os.path.dirname(os.path.abspath(a.out)) if a.out else ".")
    os.makedirs(outdir, exist_ok=True)
    report = {"ref": a.ref, "size": [W, H], "layers": []}
    for style, method in jobs:
        m = mask_for(img, method, a.block, a.k, a.lo, a.hi)
        if a.style == "both":
            out = os.path.join(outdir, "%s.svg" % style)
        elif a.out:
            out = a.out
        else:
            out = os.path.join(outdir, "%s.svg" % style)

        if style == "line":
            skel = cv2.ximgproc.thinning((m.astype(np.uint8) * 255)) > 0
            groups = []          # [(mask, fill), ...]
            if a.line_colors and a.line_colors > 1:
                ys, xs = np.nonzero(skel)
                labels, centers = quantize_colours(rgb[ys, xs], a.line_colors)
                for ci in range(len(centers)):
                    g = np.zeros_like(skel)
                    g[ys[labels == ci], xs[labels == ci]] = True
                    if g.sum() < 30:
                        continue
                    col = np.clip(centers[ci] * a.darken, 0, 255).astype(int)
                    groups.append((g, "#%02x%02x%02x" % tuple(col)))
            else:
                groups.append((skel, a.line_color or "#111111"))
            fronts, ds_all, colours = 0, [], []
            for g, fill in groups:
                if a.dilate > 0:
                    k = np.ones((a.dilate * 2 + 1, a.dilate * 2 + 1), np.uint8)
                    g = cv2.dilate((g.astype(np.uint8) * 255), k) > 0
                fronts += int(g.sum())
                colours.append(fill)
                ds_all.extend((fill, d) for d in trace_mask(g, a.turd))
            body = "".join('<path fill="%s" fill-rule="evenodd" d="%s"/>' % (f, d) for f, d in ds_all)
            with open(out, "w", encoding="utf-8") as fh:
                fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                         'viewBox="0 0 %d %d"><rect width="%d" height="%d" fill="#ffffff"/>%s</svg>'
                         % (W, H, W, H, W, H, body))
            report["layers"].append({"style": style, "method": method, "front_px": fronts,
                                     "paths": len(ds_all), "colours": colours,
                                     "kb": round(os.path.getsize(out) / 1024, 1), "svg": out})
            continue

        ds = trace_mask(m, a.turd)
        write_svg(out, W, H, ds, a.poster_color)
        report["layers"].append({"style": style, "method": method,
                                 "front_px": int(m.sum()), "paths": len(ds),
                                 "kb": round(os.path.getsize(out) / 1024, 1),
                                 "svg": out})

    if a.style == "both" and a.out:
        # 合成：poster 打底，line 压在上面
        post = report["layers"][0]["svg"]
        line = report["layers"][1]["svg"]
        with open(post, "r", encoding="utf-8") as fh:
            base = fh.read()
        with open(line, "r", encoding="utf-8") as fh:
            top = fh.read()
        inner = top.split("</svg>")[0].split(">", 1)[1]
        close = base.rfind("</svg>")
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(base[:close] + inner + base[close:])
        report["composed"] = {"svg": a.out, "kb": round(os.path.getsize(a.out) / 1024, 1)}

    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
