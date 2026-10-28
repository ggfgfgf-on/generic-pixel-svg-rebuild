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


def curv_d_offset(trace, ox=0.0, oy=0.0):
    d = []
    for cv_ in trace:
        if not cv_.segments:
            continue
        s = "M%.1f %.1f" % (cv_.start_point.x + ox, cv_.start_point.y + oy)
        for sg in cv_.segments:
            if sg.is_corner:
                s += " L%.1f %.1f L%.1f %.1f" % (sg.c.x + ox, sg.c.y + oy,
                                                 sg.end_point.x + ox, sg.end_point.y + oy)
            else:
                s += " C%.1f %.1f %.1f %.1f %.1f %.1f" % (sg.c1.x + ox, sg.c1.y + oy,
                                                          sg.c2.x + ox, sg.c2.y + oy,
                                                          sg.end_point.x + ox, sg.end_point.y + oy)
        d.append(s + " Z")
    return d


def trace_mask(mask, turd):
    bmp = potrace.Bitmap(~mask)
    tr = bmp.trace(turdsize=turd, alphamax=1.0, opticurve=True, opttolerance=0.2)
    return curv_d_offset(tr, 0.0, 0.0)


def trace_parts(mask, turd, ox=0, oy=0):
    """单块 mask -> d 列表（可带偏移），per-stroke 上色时逐个连通块调用。"""
    bmp = potrace.Bitmap(~mask)
    tr = bmp.trace(turdsize=turd, alphamax=1.0, opticurve=True, opttolerance=0.2)
    return curv_d_offset(tr, ox, oy)


def per_stroke(mask, rgb, gray, turd, darken, min_px, width_mode="flat", wmax=1):
    """每条线自己取色；width_mode=auto 时按该线处的梯度强度分配粗细。

    依据：对一条边，Sobel 梯度大 = 强轮廓（该画粗），梯度小 = 纹理/弱结构（该画细）。
    强度先按全图线条像素的 90 分位归一，再映射成 1,3,5... px。
    """
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    n_px = max(1, int(mask.sum()))
    ref = float(np.percentile(mag[mask], 60)) if n_px else 1.0
    ref = max(ref, 1e-3)
    out, widths = [], []
    H, W = mask.shape
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_px:
            continue
        pad = wmax if width_mode == "auto" else 0
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
        sub = (lab[y0:y1, x0:x1] == i)
        ys, xs = np.nonzero(sub)
        col = np.median(rgb[y0:y1, x0:x1][ys, xs], axis=0)
        col = np.clip(col * darken, 0, 255).astype(int)
        fill = "#%02x%02x%02x" % tuple(int(v) for v in col)
        width = 1
        # 只有"长"的笔画才配加粗：眼睛/鼻孔这类小闭环加粗会糊成块
        if width_mode == "auto" and area >= 150 and max(w, h) >= 40:
            strength = float(mag[y0:y1, x0:x1][ys, xs].mean()) / ref
            s = float(np.clip((strength - 0.45) / 0.55, 0.0, 1.0)) ** 1.5
            width = 1 + 2 * int(round(s * wmax))

        # 细线永远先描：加粗会把小闭环（眼睛、鼻孔）的内孔填死，
        # 遇到这种情况就保留细线，宁可这一段不加粗。
        lab_crop = lab[y:y + h, x:x + w]
        thin_crop = (lab_crop == i)
        ds = trace_parts(thin_crop, turd, x, y)
        if width > 1:
            k = np.ones((width, width), np.uint8)
            wide_crop = cv2.dilate((thin_crop.astype(np.uint8) * 255), k) > 0
            others = (lab_crop > 0) & (lab_crop != i)
            # 加粗后如果吃到了别的线（密集区），就保持细线：否则相邻线会糊成一片
            collides = bool((wide_crop & others).any())
            ds_wide = trace_parts(wide_crop, turd, x, y)
            if not collides and len(ds_wide) >= len(ds):
                ds = ds_wide
            else:
                width = 1
        widths.append(width)
        for d in ds:
            out.append((fill, d))
    return out, widths


def write_svg(path, W, H, ds, fill, bg=True):
    body = "".join('<path fill="%s" fill-rule="evenodd" d="%s"/>' % (fill, d) for d in ds)
    rect = ('<rect width="%d" height="%d" fill="#ffffff"/>' % (W, H)) if bg else ""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
                 '%s%s</svg>' % (W, H, W, H, rect, body))


def compose(base_svg, top_svg, out_svg):
    """把上层（必须无背景）的 path 插到 base 的 </svg> 之前。"""
    with open(base_svg, "r", encoding="utf-8") as fh:
        base = fh.read()
    with open(top_svg, "r", encoding="utf-8") as fh:
        top = fh.read()
    inner = top.split(">", 1)[1].rsplit("</svg>", 1)[0]
    close = base.rfind("</svg>")
    if close < 0:
        raise SystemExit("base svg has no closing tag")
    with open(out_svg, "w", encoding="utf-8") as fh:
        fh.write(base[:close] + inner + base[close:])
    return os.path.getsize(out_svg) / 1024.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("--style", choices=["poster", "line", "both"], default="poster")
    ap.add_argument("--method", default="", help="canny|sauvola|adaptive (default: sauvola for poster, canny for line)")
    ap.add_argument("--out", default="")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--dilate", type=int, default=0,
                    help="line width in px; 0 = hairline (complete, matches a raw edge trace)")
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
    ap.add_argument("--line-source", choices=["edges", "skeleton"], default="edges",
                    help="edges=直接描边（线更完整）；skeleton=细化后再描（可调粗）")
    ap.add_argument("--line-per-stroke", action="store_true",
                    help="每条连通线单独取色（线数=颜色数）")
    ap.add_argument("--min-stroke-px", type=int, default=1)
    ap.add_argument("--no-bg", action="store_true", help="不写白底矩形，方便叠加")
    ap.add_argument("--compose", default="", help="把线层叠到这张已有 SVG 上（彩色底 + 线）")
    ap.add_argument("--method-poster", default="", help="海报层单独指定方法")
    ap.add_argument("--method-line", default="", help="线稿层单独指定方法")
    ap.add_argument("--line-width", choices=["flat", "auto"], default="flat",
                    help="auto=按梯度强度给每段线分粗细（强轮廓粗、纹理细）")
    ap.add_argument("--line-width-max", type=int, default=1,
                    help="auto 时最大加粗级数：1 => 1/3px，2 => 1/3/5px")
    a = ap.parse_args()

    img = cv2.imread(a.ref, cv2.IMREAD_GRAYSCALE)
    bgr = cv2.imread(a.ref)
    if img is None:
        raise SystemExit("cannot read %s" % a.ref)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    H, W = img.shape

    jobs = []
    if a.style in ("poster", "both"):
        jobs.append(("poster", a.method_poster or a.method or "sauvola"))
    if a.style in ("line", "both"):
        jobs.append(("line", a.method_line or a.method or "canny"))

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
            if a.line_source == "skeleton":
                m = cv2.ximgproc.thinning((m.astype(np.uint8) * 255)) > 0
            if a.line_per_stroke:
                parts, widths = per_stroke(m, rgb, img, a.turd, a.darken, a.min_stroke_px,
                                          a.line_width, a.line_width_max)
                fills = sorted(set(f for f, _ in parts))
                body = "".join('<path fill="%s" fill-rule="evenodd" d="%s"/>' % (f, d) for f, d in parts)
                rect = "" if (a.no_bg or a.style == "both" or a.compose) else (
                    '<rect width="%d" height="%d" fill="#ffffff"/>' % (W, H))
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                             'viewBox="0 0 %d %d">%s%s</svg>' % (W, H, W, H, rect, body))
                report["layers"].append({"style": style, "method": method, "source": a.line_source,
                                         "front_px": int(m.sum()), "paths": len(parts),
                                         "colours": len(fills), "bg": bool(rect), "sample": fills[:6],
                                         "widths": {w: widths.count(w) for w in sorted(set(widths))},
                                         "kb": round(os.path.getsize(out) / 1024, 1), "svg": out})
                continue
            skel = m
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
            rect = "" if (a.no_bg or a.style == "both" or a.compose) else (
                '<rect width="%d" height="%d" fill="#ffffff"/>' % (W, H))
            with open(out, "w", encoding="utf-8") as fh:
                fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                         'viewBox="0 0 %d %d">%s%s</svg>' % (W, H, W, H, rect, body))
            report["layers"].append({"style": style, "method": method, "front_px": fronts,
                                     "paths": len(ds_all), "colours": colours, "bg": bool(rect),
                                     "kb": round(os.path.getsize(out) / 1024, 1), "svg": out})
            continue

        ds = trace_mask(m, a.turd)
        write_svg(out, W, H, ds, a.poster_color, bg=not a.no_bg)
        report["layers"].append({"style": style, "method": method,
                                 "front_px": int(m.sum()), "paths": len(ds), "bg": not a.no_bg,
                                 "kb": round(os.path.getsize(out) / 1024, 1),
                                 "svg": out})

    line_layer = next((l for l in report["layers"] if l["style"] == "line"), None)
    if a.compose:
        if line_layer is None:
            raise SystemExit("--compose needs the line layer (use --style line/both)")
        kb = compose(a.compose, line_layer["svg"], a.out or line_layer["svg"])
        report["composed"] = {"base": a.compose, "line": line_layer["svg"],
                              "svg": a.out or line_layer["svg"], "kb": round(kb, 1)}
    elif a.style == "both":
        post = next(l for l in report["layers"] if l["style"] == "poster")
        # 没给 --out 时不要覆盖 poster 层，另写 composed.svg
        target = a.out or os.path.join(outdir, "composed.svg")
        kb = compose(post["svg"], line_layer["svg"], target)
        report["composed"] = {"base": post["svg"], "line": line_layer["svg"],
                              "svg": target, "kb": round(kb, 1)}

    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
