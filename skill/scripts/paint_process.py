#!/usr/bin/env python3
"""Layered painterly process replay: sketch -> flat fill -> form -> detail -> inking.

Optional special mode. The default skill path is still build_svg.py (pixel) or vectorize.py
(static vector); do not use this branch unless the user explicitly asks for a process replay.

Pure SVG paths in one self-contained HTML. The sequence mirrors a human digital-painting
workflow instead of scattering random dabs: connected colour regions are filled one by one,
then a final line-art pass tightens the drawing.

Usage:
  python paint_process.py ref.png out.html --size 876x1904 --duration 58 --title "..." --palette 256
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import random
import re
import sys
from pathlib import Path

import numpy as np
import potrace
from PIL import Image, ImageEnhance, ImageFilter
from scipy import ndimage as ndi

sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_loader  # noqa: E402  shared ctypes bindings (see native_loader.py)


def parse_size(text):
    if not text:
        return None
    a, b = text.lower().split("x", 1)
    return int(a), int(b)


def hex_color(rgb):
    r, g, b = (max(0, min(255, int(round(v)))) for v in rgb)
    return "#%02x%02x%02x" % (r, g, b)


def load_reference(path, size, max_pixels):
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "PA"):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (244, 238, 226, 255))
        im = Image.alpha_composite(bg, rgba)
    im = im.convert("RGB")
    if size is not None:
        im = im.resize(size, Image.LANCZOS)
    elif im.width * im.height > max_pixels:
        s = math.sqrt(max_pixels / float(im.width * im.height))
        im = im.resize((max(1, int(round(im.width * s))), max(1, int(round(im.height * s)))), Image.LANCZOS)
    # Keep the palette from drifting toward the muted, averaged look of flat quantisation.
    im = ImageEnhance.Color(im).enhance(1.04)
    im = ImageEnhance.Contrast(im).enhance(1.015)
    return im


def path_from_curve(curve, sx, sy, ox, oy, prec=1):
    fmt = "%." + str(prec) + "f"
    def f(v, scale, off):
        return fmt % ((v + off) * scale)
    p = curve.start_point
    d = ["M" + f(p.x, sx, ox) + " " + f(p.y, sy, oy)]
    for seg in curve.segments if hasattr(curve, "segments") else curve:
        if getattr(seg, "is_corner", False):
            c, e = seg.c, seg.end_point
            d.append("L" + f(c.x, sx, ox) + " " + f(c.y, sy, oy))
            d.append("L" + f(e.x, sx, ox) + " " + f(e.y, sy, oy))
        else:
            c1, c2, e = seg.c1, seg.c2, seg.end_point
            d.append("C" + " ".join(f(v, sx if i % 2 == 0 else sy, ox if i % 2 == 0 else oy)
                                    for i, v in enumerate((c1.x, c1.y, c2.x, c2.y, e.x, e.y))))
    d.append("Z")
    return "".join(d)


def trace_mask(mask, sx, sy, ox, oy):
    """Native DLL when available, otherwise the pure Python tracer (same curves)."""
    d = native_loader.trace_mask(mask, sx, sy, ox, oy, turdsize=1, alphamax=1.0, prec=1)
    if d is not None:
        return d
    bmp = potrace.Bitmap(~mask)
    tr = bmp.trace(turdsize=1, alphamax=1.0, opticurve=True, opttolerance=0.2)
    return "".join(path_from_curve(cv, sx, sy, ox, oy) for cv in tr)



def trace_mask_turd(mask, sx, sy, ox, oy, turdsize=6, alphamax=1.0, prec=1):
    d = native_loader.trace_mask(mask, sx, sy, ox, oy, turdsize, alphamax, prec)
    if d is not None:
        return d
    bmp = potrace.Bitmap(~mask)
    tr = bmp.trace(turdsize=turdsize, alphamax=alphamax, opticurve=True, opttolerance=0.2)
    return "".join(path_from_curve(cv, sx, sy, ox, oy, prec=prec) for cv in tr)
def focus_value(cx, cy, W, H):
    # Generic central-subject prior; no face/hand/foot assumption. Per-image
    # focus regions can replace this when the skill grows a focus config.
    nx, ny = cx / W, cy / H
    return math.exp(-((nx - 0.50) / 0.42) ** 2 - ((ny - 0.45) / 0.48) ** 2)


def map_to_palette(im, palette):
    from scipy.spatial import cKDTree
    arr = np.asarray(im, dtype=np.float32)
    tree = cKDTree(palette.astype(np.float32))
    idx = tree.query(arr.reshape(-1, 3), workers=-1)[1]
    return idx.reshape(arr.shape[:2]).astype(np.int16)


def component_batches_for_regions(img, W, H, kind, scale, colors, min_area, blur, target, palette=None):
    sw, sh = max(1, round(W * scale)), max(1, round(H * scale))
    small = img.resize((sw, sh), Image.LANCZOS)
    if blur > 0:
        small = small.filter(ImageFilter.GaussianBlur(blur))
    if palette is not None:
        if palette.get("pil") is not None:
            q = small.quantize(palette=palette["pil"], dither=Image.Dither.NONE)
            idx = np.asarray(q, dtype=np.int16)
        else:
            idx = map_to_palette(small, palette["pal"])
        pal = palette["pal"]
    else:
        q = small.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        idx = np.asarray(q, dtype=np.int16)
        pal = np.asarray(q.getpalette()[:256 * 3], dtype=np.uint8).reshape(-1, 3)
    sx, sy = W / float(sw), H / float(sh)
    comps = []
    for c in np.unique(idx).tolist():
        mask = idx == int(c)
        labels, n = ndi.label(mask, structure=np.ones((3, 3), dtype=np.uint8))
        for cid, sl in enumerate(ndi.find_objects(labels), start=1):
            if sl is None:
                continue
            area = int((labels[sl] == cid).sum())
            if area < min_area:
                continue
            y0 = max(0, sl[0].start - 2); y1 = min(sh, sl[0].stop + 2)
            x0 = max(0, sl[1].start - 2); x1 = min(sw, sl[1].stop + 2)
            crop = labels[y0:y1, x0:x1] == cid
            crop = ndi.binary_dilation(crop, iterations=1)
            d = trace_mask(crop, sx, sy, x0, y0)
            if not d:
                continue
            bbox = (x0 * sx, y0 * sy, x1 * sx, y1 * sy)
            col = pal[int(c)]
            cx, cy = (bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5
            comps.append({
                "kind": kind, "d": d, "color": hex_color(col), "area": area * sx * sy,
                "bbox": bbox, "cx": cx, "cy": cy, "lum": float(col.mean()),
                "focus": focus_value(cx, cy, W, H),
                "size": 14 if kind == "detail" else (26 if kind == "mid" else 46),
            })
    if kind == "base":
        comps.sort(key=lambda c: (int(c["lum"] // 42), -c["area"]))
    elif kind == "mid":
        comps.sort(key=lambda c: (int(c["cy"] // 150), c["cx"] if int(c["cy"] // 150) % 2 == 0 else -c["cx"], -c["area"]))
    else:
        # Detail work moves toward the masked face/hands/feet near the end.
        comps.sort(key=lambda c: (c["focus"], int(c["cy"] // 80), c["cx"] if int(c["cy"] // 80) % 2 == 0 else -c["cx"]))
    batches = []
    for i in range(0, len(comps), target):
        chunk = comps[i:i + target]
        batch_kind = kind
        groups = {}
        for c in sorted(chunk, key=lambda c: c["lum"]):
            groups.setdefault(c["color"], []).append(c["d"])
        parts = []
        for color, ds in groups.items():
            # One compound path per colour per batch: same geometry, far fewer DOM nodes.
            parts.append('<path fill="%s" fill-rule="evenodd" shape-rendering="geometricPrecision" d="%s"/>' %
                         (color, "".join(ds)))
        batches.append({
            "kind": batch_kind,
            "svg": "".join(parts),
            "pos": (float(np.mean([c["cx"] for c in chunk])), float(np.mean([c["cy"] for c in chunk]))),
            "angle": 0.0,
            "size": float(np.mean([c["size"] for c in chunk])),
        })
    return batches

def nms_edges(gray, pct=84.0, floor=0.028):
    gx = ndi.sobel(gray, axis=1)
    gy = ndi.sobel(gray, axis=0)
    mag = np.hypot(gx, gy)
    ang = (np.degrees(np.arctan2(gy, gx)) + 180.0) % 180.0
    p = np.pad(mag, 1, mode="constant")
    keep = np.zeros_like(mag, dtype=bool)
    m0 = (ang < 22.5) | (ang >= 157.5)
    m45 = (ang >= 22.5) & (ang < 67.5)
    m90 = (ang >= 67.5) & (ang < 112.5)
    m135 = (ang >= 112.5) & (ang < 157.5)
    keep[m0] = mag[m0] >= p[1:-1, :-2][m0]
    keep[m0] &= mag[m0] >= p[1:-1, 2:][m0]
    keep[m45] = mag[m45] >= p[2:, :-2][m45]
    keep[m45] &= mag[m45] >= p[:-2, 2:][m45]
    keep[m90] = mag[m90] >= p[:-2, 1:-1][m90]
    keep[m90] &= mag[m90] >= p[2:, 1:-1][m90]
    keep[m135] = mag[m135] >= p[2:, 2:][m135]
    keep[m135] &= mag[m135] >= p[:-2, :-2][m135]
    threshold = max(floor, float(np.percentile(mag, pct)))
    return keep & (mag >= threshold)


def _line_mask(img, W, H):
    gray = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    gx = np.zeros_like(gray); gy = np.zeros_like(gray)
    gx[:, 1:-1] = np.abs(gray[:, 2:] - gray[:, :-2])
    gy[1:-1, :] = np.abs(gray[2:, :] - gray[:-2, :])
    return (gx + gy) > (24.0 / 255.0)


def _path_batch(d, kind, opacity, W, H, color="#33445c", width=0.50):
    if not d:
        return []
    return [{
        "kind": kind,
        "svg": '<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" stroke-linecap="round" stroke-linejoin="round" opacity="%.2f"/>' % (d, color, width, opacity),
        "pos": (W * 0.5, H * 0.5), "angle": 0.0, "size": 5.0,
    }]


def load_line_direction(path):
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.setdefault("regions", [])
    return data


def component_batches_for_lines(img, W, H, direction=None):
    direction = direction or {}
    trace_cfg = direction.get("trace", {})
    style_cfg = direction.get("style", {})
    turdsize = int(trace_cfg.get("turdsize", 6))
    alphamax = float(trace_cfg.get("alphamax", 1.0))
    prec = int(trace_cfg.get("precision", 2))
    color = str(style_cfg.get("color", "#33445c"))
    width = float(style_cfg.get("stroke_width", 0.50)) * (W / 876.0)
    sketch_op = float(style_cfg.get("sketch_opacity", 0.95))
    accent_op = float(style_cfg.get("accent_opacity", 0.80))

    mask = _line_mask(img, W, H)
    d = trace_mask_turd(mask, 1.0, 1.0, 0, 0, turdsize=turdsize, alphamax=alphamax, prec=prec)
    if not d:
        return []
    parts = [p.strip() for p in d.split("Z") if p.strip()]

    default_min = float(direction.get("default_min_diag", 34.0)) * (W / 876.0)
    pct = float(direction.get("density_percentile", 58.0))
    regions = direction.get("regions", [])
    centers, diags = [], []
    for part in parts:
        nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", part)]
        xs, ys = nums[0::2], nums[1::2]
        centers.append(((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5))
        diags.append(math.hypot(max(xs) - min(xs), max(ys) - min(ys)))

    cell = 24
    gw, gh = W // cell + 2, H // cell + 2
    grid = np.zeros((gh, gw), dtype=np.float32)
    for cx, cy in centers:
        grid[int(cy) // cell, int(cx) // cell] += 1
    dens = ndi.gaussian_filter(grid, 1.2)
    vals = np.array([dens[int(cy) // cell, int(cx) // cell] for cx, cy in centers])
    thr = float(np.percentile(vals, pct))

    def rule_for(cx, cy):
        for r in regions:
            x0, y0, x1, y1 = r["bbox"]  # normalized 0..1, image-size independent
            if x0 <= cx / W <= x1 and y0 <= cy / H <= y1:
                return r
        return None

    sketch, accent = [], []
    for part, (cx, cy), v, diag in zip(parts, centers, vals, diags):
        rule = rule_for(cx, cy) or {}
        min_diag = float(rule.get("min_diag", default_min / (W / 876.0))) * (W / 876.0)
        small = diag < min_diag
        exempt = bool(rule.get("density_exempt", False))
        keep = (not small) and (exempt or v <= thr)
        (sketch if keep else accent).append(part + "Z")

    out = _path_batch("".join(sketch), "line-sketch", sketch_op, W, H, color, width)
    out += _path_batch("".join(accent), "line-accent", accent_op, W, H, color, width)
    return out


def mask_components(mask, sx, sy, W, H, min_area, size=30):
    labels, n = ndi.label(mask, structure=np.ones((3, 3), dtype=np.uint8))
    comps = []
    for cid, sl in enumerate(ndi.find_objects(labels), start=1):
        if sl is None:
            continue
        area = int((labels[sl] == cid).sum())
        if area < min_area:
            continue
        y0 = max(0, sl[0].start - 2); y1 = min(mask.shape[0], sl[0].stop + 2)
        x0 = max(0, sl[1].start - 2); x1 = min(mask.shape[1], sl[1].stop + 2)
        crop = labels[y0:y1, x0:x1] == cid
        crop = ndi.binary_dilation(crop, iterations=1)
        d = trace_mask(crop, sx, sy, x0, y0)
        if not d:
            continue
        bbox = (x0 * sx, y0 * sy, x1 * sx, y1 * sy)
        comps.append({"d": d, "area": area * sx * sy, "bbox": bbox,
                      "cx": (bbox[0] + bbox[2]) * .5, "cy": (bbox[1] + bbox[3]) * .5, "size": size})
    comps.sort(key=lambda c: (int(c["cy"] // 130), c["cx"] if int(c["cy"] // 130) % 2 == 0 else -c["cx"], -c["area"]))
    return comps


def component_batches_for_overlay(img, W, H, kind, scale, make_mask, fill, opacity, blend, min_area, target, size):
    sw, sh = max(1, round(W * scale)), max(1, round(H * scale))
    small = img.resize((sw, sh), Image.LANCZOS)
    mask = make_mask(np.asarray(small.convert("L"), dtype=np.float32) / 255.0)
    mask = ndi.binary_closing(mask, structure=np.ones((3, 3), dtype=bool), iterations=1)
    mask = ndi.binary_dilation(mask, iterations=1)
    sx, sy = W / float(sw), H / float(sh)
    comps = mask_components(mask, sx, sy, W, H, min_area, size=size)
    style = ' style="mix-blend-mode:%s"' % blend if blend else ""
    out = []
    for i in range(0, len(comps), target):
        chunk = comps[i:i + target]
        d = "".join(c["d"] for c in chunk)
        svg = '<path fill="%s" fill-opacity="%.3f" fill-rule="evenodd" shape-rendering="geometricPrecision"%s d="%s"/>' % (
            fill, opacity, style, d)
        out.append({"kind": kind, "svg": svg,
                    "pos": (float(np.mean([c["cx"] for c in chunk])), float(np.mean([c["cy"] for c in chunk]))),
                    "angle": 0.0, "size": size})
    return out


def build_two_tone(img, W, H):
    def make_mask(gray):
        smooth = ndi.gaussian_filter(gray, 0.8)
        thr = float(np.percentile(smooth, 42.0))
        return (smooth < thr) & (smooth > 0.016)
    return component_batches_for_overlay(img, W, H, "two-tone", 0.40, make_mask,
                                         "#263548", 0.08, "multiply", min_area=12, target=30, size=42)


def build_occlusion(img, W, H):
    def make_mask(gray):
        fine = ndi.gaussian_filter(gray, 1.0)
        broad = ndi.gaussian_filter(gray, 8.0)
        local = fine - broad
        gx = ndi.sobel(fine, axis=1)
        gy = ndi.sobel(fine, axis=0)
        edge = np.hypot(gx, gy)
        edge_thr = max(0.045, float(np.percentile(edge, 56)))
        return (local < -0.040) & (edge > edge_thr)
    return component_batches_for_overlay(img, W, H, "occlusion", 0.50, make_mask,
                                         "#130e0b", 0.10, "multiply", min_area=4, target=34, size=12)


def build_glaze(img, W, H):
    sw, sh = max(1, round(W * 0.32)), max(1, round(H * 0.32))
    small = img.resize((sw, sh), Image.LANCZOS)
    gray = np.asarray(small.convert("L"), dtype=np.float32) / 255.0
    sx, sy = W / float(sw), H / float(sh)
    specs = [
        ("shadow glaze", gray < float(np.percentile(gray, 40.0)), "#20364a", 0.05, "multiply"),
        ("light glaze", gray > float(np.percentile(gray, 62.0)), "#efb874", 0.03, "soft-light"),
    ]
    out = []
    for name, mask, fill, opacity, blend in specs:
        for bi, y0 in enumerate(range(0, sh, max(1, sh // 4))):
            y1 = min(sh, y0 + max(1, sh // 4))
            crop = mask[y0:y1]
            if not crop.any():
                continue
            d = trace_mask(crop, sx, sy, 0, y0)
            if not d:
                continue
            svg = ('<path fill="%s" fill-opacity="%.3f" fill-rule="evenodd" '
                   'shape-rendering="geometricPrecision" style="mix-blend-mode:%s" d="%s"/>') % (
                       fill, opacity, blend, d)
            out.append({"kind": "glaze", "svg": svg,
                        "pos": (W * 0.5, (y0 + (y1 - y0) * .5) * sy), "angle": 0.0, "size": 82.0})
    return out


def build_undertone(img, W, H):
    def make_mask(gray):
        smooth = ndi.gaussian_filter(gray, 1.3)
        thr = float(np.percentile(smooth, 38.0))
        return (smooth < thr) & (smooth > 0.012)
    return component_batches_for_overlay(img, W, H, "undertone", 0.40, make_mask,
                                         "#4a3f72", 0.09, "soft-light", min_area=16, target=28, size=48)


def build_color_grade(img, W, H):
    sw, sh = max(1, round(W * 0.36)), max(1, round(H * 0.36))
    small = img.resize((sw, sh), Image.LANCZOS)
    a = np.asarray(small, dtype=np.float32) / 255.0
    gray = a.mean(axis=2)
    mx = a.max(axis=2); mn = a.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    light = (gray > float(np.percentile(gray, 64.0))) & (gray < 0.985)
    red = (sat > 0.50) & (a[:, :, 0] > a[:, :, 1] * 1.12) & (a[:, :, 0] > a[:, :, 2] * 1.12)
    green = (sat > 0.32) & (a[:, :, 1] > a[:, :, 0] * 1.04) & (a[:, :, 1] > a[:, :, 2] * 1.03)
    specs = [
        ("提亮", light, "#ffc27a", 0.07, "soft-light"),
        ("补红", red, "#d94b2b", 0.05, "overlay"),
        ("补绿", green, "#4f8a4b", 0.04, "overlay"),
    ]
    sx, sy = W / float(sw), H / float(sh)
    out = []
    for label, mask, fill, opacity, blend in specs:
        mask = ndi.binary_closing(mask, structure=np.ones((3, 3), dtype=bool), iterations=1)
        mask = ndi.binary_dilation(mask, iterations=1)
        for y0 in range(0, sh, max(1, sh // 4)):
            y1 = min(sh, y0 + max(1, sh // 4))
            band = mask[y0:y1]
            if not band.any():
                continue
            d = trace_mask(band, sx, sy, 0, y0)
            if not d:
                continue
            svg = ('<path fill="%s" fill-opacity="%.3f" fill-rule="evenodd" '
                   'shape-rendering="geometricPrecision" style="mix-blend-mode:%s" d="%s"/>') % (
                       fill, opacity, blend, d)
            out.append({"kind": "color-grade", "svg": svg,
                        "pos": (W * 0.5, (y0 + (y1 - y0) * .5) * sy), "angle": 0.0, "size": 76.0})
    return out


def mask_ui_svg(W, H):
    boxes = [
        (0.10 * W, 0.08 * H, 0.80 * W, 0.84 * H, "FOCUS · 主体"),
    ]
    parts = []
    for x, y, w, h, label in boxes:
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="10"/>' % (x, y, w, h))
        parts.append('<text x="%.1f" y="%.1f" fill="#f0c48e" stroke="none" font-size="15" '
                     'font-family="system-ui,sans-serif" letter-spacing="1">%s</text>' % (x + 12, y + 24, label))
    return ('<g id="maskUI" opacity="0" pointer-events="none" fill="#f0b56f" fill-opacity="0.045" '
            'stroke="#f0b56f" stroke-opacity="0.72" stroke-width="2.4" stroke-dasharray="10 8">%s</g>' % "".join(parts))



def component_batches_for_focus(img, W, H, palette=None):
    # Generic subject box; replace per image when a real focus config is added.
    specs = [
        ("subject", 0.10, 0.08, 0.90, 0.92, 96, 4),
    ]
    out = []
    for tag, nx0, ny0, nx1, ny1, colors, min_area in specs:
        x0, y0 = int(round(nx0 * W)), int(round(ny0 * H))
        x1, y1 = int(round(nx1 * W)), int(round(ny1 * H))
        crop = img.crop((x0, y0, x1, y1))
        cw, ch = crop.size
        if palette is not None:
            if palette.get("pil") is not None:
                q = crop.quantize(palette=palette["pil"], dither=Image.Dither.NONE)
                idx = np.asarray(q, dtype=np.int16)
            else:
                idx = map_to_palette(crop, palette["pal"])
            pal = palette["pal"]
        else:
            q = crop.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
            idx = np.asarray(q, dtype=np.int16)
            pal = np.asarray(q.getpalette()[:256 * 3], dtype=np.uint8).reshape(-1, 3)
        comps = []
        for c in np.unique(idx).tolist():
            mask = idx == int(c)
            labels, n = ndi.label(mask, structure=np.ones((3, 3), dtype=np.uint8))
            for cid, sl in enumerate(ndi.find_objects(labels), start=1):
                if sl is None:
                    continue
                area = int((labels[sl] == cid).sum())
                if area < min_area:
                    continue
                cy0 = max(0, sl[0].start - 2); cy1 = min(ch, sl[0].stop + 2)
                cx0 = max(0, sl[1].start - 2); cx1 = min(cw, sl[1].stop + 2)
                mask_crop = labels[cy0:cy1, cx0:cx1] == cid
                mask_crop = ndi.binary_dilation(mask_crop, iterations=1)
                d = trace_mask(mask_crop, 1.0, 1.0, x0 + cx0, y0 + cy0)
                if not d:
                    continue
                col = pal[int(c)]
                comps.append({"d": d, "color": hex_color(col), "area": area, "lum": float(col.mean()),
                              "cx": x0 + (cx0 + cx1) * .5, "cy": y0 + (cy0 + cy1) * .5})
        comps.sort(key=lambda c: (c["lum"], int(c["cy"] // 90), c["cx"] if int(c["cy"] // 90) % 2 == 0 else -c["cx"]))
        for i in range(0, len(comps), 90):
            chunk = comps[i:i + 90]
            groups = {}
            for c in sorted(chunk, key=lambda c: c["lum"]):
                groups.setdefault(c["color"], []).append(c["d"])
            parts = []
            for color, ds in groups.items():
                parts.append('<path fill="%s" fill-rule="evenodd" shape-rendering="geometricPrecision" d="%s"/>' %
                             (color, "".join(ds)))
            out.append({"kind": "focus-detail", "svg": "".join(parts),
                        "pos": (float(np.mean([c["cx"] for c in chunk])), float(np.mean([c["cy"] for c in chunk]))),
                        "angle": 0.0, "size": 9.0})
    return out


def assign_timeline(all_batches, duration, rng):
    order = ["line-sketch", "base", "mid", "two-tone", "detail", "focus-detail", "occlusion", "undertone", "glaze", "color-grade", "line-accent"]
    labels = {"line-sketch": "起稿勾线", "base": "铺大色", "mid": "分区平涂", "two-tone": "二分光影",
              "detail": "深入细化", "focus-detail": "遮罩细化", "occlusion": "闭塞塑形",
              "undertone": "藏色", "glaze": "罩染调色", "color-grade": "提亮补色",
              "line-accent": "收线高光"}
    weights = {"line-sketch": 0.07, "base": 0.10, "mid": 0.13, "two-tone": 0.07, "detail": 0.17,
               "focus-detail": 0.10, "occlusion": 0.07, "undertone": 0.06, "glaze": 0.07,
               "color-grade": 0.08, "line-accent": 0.08}
    phases, cursor = [], 0.0
    for kind in order:
        group = [b for b in all_batches if b["kind"] == kind]
        if not group:
            continue
        dur = weights[kind] * duration
        start = cursor + (0.22 if phases else 0.0)
        end = start + dur
        n = len(group)
        gaps = []
        for _ in range(n):
            g = rng.uniform(0.72, 1.38)
            if n > 5 and rng.random() < 0.045:
                g *= rng.uniform(2.0, 3.0)
            gaps.append(g)
        total = sum(gaps)
        acc = 0.0
        for b, g in zip(group, gaps):
            b["t"] = start + acc / total * dur
            acc += g
        phases.append({"id": kind, "name": labels[kind], "start": round(start, 3), "end": round(end, 3)})
        cursor = end
    all_batches.sort(key=lambda b: b["t"])
    for i, b in enumerate(all_batches):
        b["seq"] = i
    if phases:
        phases[-1]["end"] = round(max(phases[-1]["end"], max(b["t"] for b in all_batches) + 0.6), 3)
    return phases, max([p["end"] for p in phases] + [duration])


def svg_from_batches(all_batches, W, H, underlay):
    specs = [
        ("layer-sketch", ["line-sketch"]),
        ("layer-base", ["base"]),
        ("layer-mid", ["mid"]),
        ("layer-shadow", ["two-tone"]),
        ("layer-detail", ["detail", "focus-detail"]),
        ("layer-occlusion", ["occlusion"]),
        ("layer-undertone", ["undertone"]),
        ("layer-glaze", ["glaze"]),
        ("layer-grade", ["color-grade"]),
        ("layer-line", ["line-accent"]),
    ]
    layers = []
    for gid, kinds in specs:
        group = []
        for kind in kinds:
            group.extend([b for b in all_batches if b["kind"] == kind])
        if not group:
            continue
        inner = []
        for b in group:
            inner.append('<g class="batch" data-seq="%d" data-t="%.4f" data-kind="%s" data-pos="%.1f,%.1f" data-angle="%.3f" data-size="%.2f">%s</g>' %
                         (b["seq"], b["t"], b["kind"], b["pos"][0], b["pos"][1], b["angle"], b["size"], b["svg"]))
        if gid == "layer-base" and underlay:
            first_t = min([b["t"] for b in group] or [0.0])
            inner.insert(0, '<g class="batch" data-seq="-1" data-t="%.4f" data-kind="base" data-pos="%.1f,%.1f" data-angle="0" data-size="70">%s</g>' %
                         (first_t - 0.01, W * 0.5, H * 0.5, underlay))
        style = ' style="opacity:.32"' if gid == "layer-line" else ""
        layers.append('<g id="%s"%s>%s</g>' % (gid, style, "".join(inner)))
    if underlay and not any(gid == "layer-base" for gid in [l[0] for l in specs]):
        layers.insert(0, '<g id="layer-base">%s</g>' % underlay)
    return "".join(layers)

def build_html(img, batches, phases, total_time, title, out_path, preview_path, underlay):
    W, H = img.size
    body_svg = svg_from_batches(batches, W, H, underlay)
    total = len(batches) + (1 if underlay else 0)
    phases_json = json.dumps([{"id": p["id"], "name": p["name"], "start": p["start"], "end": p["end"]} for p in phases], ensure_ascii=False)
    brush = '''<g id="brush" pointer-events="none" opacity="0">
  <g>
    <rect x="-94" y="-4" width="64" height="8" rx="4" fill="#6e4b2d"/>
    <rect x="-30" y="-8" width="10" height="16" rx="3" fill="#b8a382"/>
    <path d="M-22 -9 C-8 -8 -2 -4 0 0 C-2 4 -8 8 -22 9 Z" fill="#3a2b1f"/>
    <path d="M-22 -5 C-10 -4 -4 -2 0 0" fill="none" stroke="#e8d6b5" stroke-opacity="0.28" stroke-width="1"/>
  </g>
</g>'''
    mask_ui = mask_ui_svg(W, H)
    svg = ('<svg id="art" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
           'data-total="%d" data-duration="%.3f">%s%s%s</svg>') % (W, H, W, H, total, total_time, body_svg, mask_ui, brush)
    preview = ('<!doctype html><html><head><meta charset="utf-8"><title>preview</title>'
               '<style>html,body{margin:0;background:#f4eee3}#wrap{display:flex;align-items:center;justify-content:center;min-height:100vh}'
               '#art{display:block;height:100vh;width:auto;max-width:100vw}#art .batch{display:inline}#layer-line{opacity:.32}#maskUI{opacity:0!important}</style></head>'
               '<body><div id="wrap">' + svg + '</div></body></html>')
    Path(preview_path).write_text(preview, encoding="utf-8")
    html = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="ai-generated" content="true">
<meta name="process-type" content="algorithmic-path-simulation">
<title>__TITLE__</title>
<style>
:root{--ink:#eadfce;--dim:#a89580;--gold:#c8a66b;--panel:#17120e;--line:#3b2e22}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#0d0a08;color:var(--ink)}
body{font:14px/1.55 "Segoe UI",system-ui,sans-serif;overflow:hidden}
.app{display:flex;height:100vh;min-height:0}
.panel{width:348px;flex:none;display:flex;flex-direction:column;gap:18px;padding:34px 28px 26px;background:var(--panel);border-right:1px solid var(--line);z-index:3}
.kicker{color:var(--gold);letter-spacing:.16em;font-size:10px;font-weight:700}
h1{font:600 29px/1.18 "Songti SC","STSong","Noto Serif CJK SC",serif;margin:7px 0 4px;letter-spacing:.02em}
.sub{color:var(--dim);font-size:12px;margin:0}
.badge{display:inline-flex;align-items:center;gap:7px;align-self:flex-start;border:1px solid #675337;color:#e1c18b;border-radius:99px;padding:5px 10px;font-size:10px;letter-spacing:.06em;background:#1f1811}
.badge i{width:5px;height:5px;border-radius:50%;background:var(--gold);display:block}
.phase-list{display:flex;flex-direction:column;gap:4px;margin-top:2px}
.phase{display:flex;align-items:center;gap:9px;background:none;border:0;color:var(--dim);padding:5px 0;text-align:left;font:inherit;cursor:pointer}
.phase span.n{width:19px;height:19px;border:1px solid #574733;border-radius:50%;display:grid;place-items:center;font-size:9px;flex:none;color:#a99377}
.phase span.t{font-size:10px;color:#756653;margin-left:auto;font-variant-numeric:tabular-nums}
.phase.active{color:#f0dfc6}.phase.active span.n{border-color:var(--gold);color:#f0d4a5;background:#2b2116}
.spacer{flex:1;min-height:12px}
.note{border-top:1px solid var(--line);padding-top:14px;color:#907f6b;font-size:11px;line-height:1.55}
.note strong{color:#d5b177;font-weight:600}
.controls{display:flex;flex-direction:column;gap:10px}
.track{height:5px;background:#352a20;border-radius:99px;overflow:hidden;cursor:pointer;position:relative}
.fill{height:100%;width:0;background:var(--gold);border-radius:99px;pointer-events:none}
.row{display:flex;align-items:center;gap:8px}
.btn{border:1px solid #4a3b2b;background:#211910;color:#e8d9c3;border-radius:8px;padding:7px 11px;font:inherit;font-size:12px;cursor:pointer}
.btn:hover{border-color:#8a6f47;color:#fff0d3}.btn.icon{width:36px;padding:7px 0;text-align:center;font-size:13px}
.time{margin-left:auto;color:#907f6b;font-size:11px;font-variant-numeric:tabular-nums}
.stage{position:relative;flex:1;min-width:0;display:flex;align-items:center;justify-content:center;padding:22px;background:#0d0a08}
#art{display:block;height:calc(100vh - 44px);width:auto;max-width:100%;filter:drop-shadow(0 22px 46px #000c);background:#e9dfce}
.wm{position:absolute;left:38px;bottom:30px;z-index:5;color:#d8c7aecc;background:#17120ec9;border:1px solid #5d4a32;border-radius:99px;padding:5px 10px;font-size:10px;letter-spacing:.04em;pointer-events:none}
#art .batch{display:none}#art .batch.on{display:inline}
body.ui0 .panel{display:none}body.ui0 .stage{padding:0}body.ui0 #art{height:100vh;max-width:100vw}
@media (max-width:900px){.app{display:block}.panel{position:fixed;left:12px;right:12px;bottom:12px;width:auto;max-height:37vh;overflow:auto;border:1px solid #4b3a29;border-radius:14px;padding:17px 18px;background:#17120ef2}.panel h1{font-size:23px;margin:2px 0}.phase-list{display:none}.spacer{display:none}.note{padding-top:10px}.stage{height:100vh;padding:12px 12px 280px}#art{height:calc(100vh - 300px);max-height:calc(100vh - 300px);max-width:100%}.wm{left:24px;top:19px;bottom:auto}body.ui0 .stage{padding:0}body.ui0 #art{height:100vh;max-height:100vh;max-width:100vw}}
</style></head>
<body><div class="app">
<aside class="panel">
  <div class="badge"><i></i>算法路径模拟 · AI 生成</div>
  <div><div class="kicker">PROCESS REPLAY / LAYERED SVG</div><h1>__TITLE__</h1><p class="sub">勾线、平涂、藏色、提亮补色，最后收线</p></div>
  <div class="phase-list" id="phaseList"></div>
  <div class="spacer"></div>
  <div class="controls">
    <div class="track" id="track" role="slider" aria-label="播放进度" tabindex="0"><div class="fill" id="fill"></div></div>
    <div class="row">
      <button class="btn icon" id="play" aria-label="播放或暂停">❚❚</button>
      <button class="btn icon" id="restart" aria-label="重新播放">↺</button>
      <button class="btn" id="speed" aria-label="切换速度">1×</button>
      <span class="time" id="time">0.0 / __DUR__ s</span>
    </div>
  </div>
  <div class="note"><strong>来源说明</strong>：画面由 AI 生成，本页是算法按图层与路径重演的过程模拟，不是真人作画录像。</div>
</aside>
<main class="stage">
  __SVG__
  <div class="wm">AI 生成 · 算法绘画过程模拟</div>
</main>
</div>
<script>
const art=document.getElementById('art'), lineEl=document.getElementById('layer-line'), maskEl=document.getElementById('maskUI');
const batches=Array.from(art.querySelectorAll('.batch')).sort((a,b)=>(+a.dataset.t)-(+b.dataset.t));
const DURATION=+art.dataset.duration, PHASES=__PHASES__;
const brush=document.getElementById('brush'), fill=document.getElementById('fill'), timeEl=document.getElementById('time');
const playBtn=document.getElementById('play'), speedBtn=document.getElementById('speed'), track=document.getElementById('track');
const phaseList=document.getElementById('phaseList'), query=new URLSearchParams(location.search);
let t=0, playing=true, speed=1, shown=-1, lineAlpha=.32, maskAlpha=0;
let cursorX=438,cursorY=950,cursorAngle=-.5,cursorSize=8,targetX=cursorX,targetY=cursorY,targetAngle=cursorAngle,targetSize=cursorSize;
let last=performance.now();
function fmt(n){return n.toFixed(1)}
function phaseIndexAt(time){for(let i=PHASES.length-1;i>=0;i--){if(time>=PHASES[i].start)return i}return 0}
function lineTarget(i){const id=PHASES[i]&&PHASES[i].id;if(id==='line-sketch')return .68;if(id==='line-accent')return .32;if(id==='base')return .10;if(id==='mid')return .12;if(id==='two-tone')return .11;if(id==='detail')return .15;if(id==='focus-detail')return .17;if(id==='occlusion')return .18;if(id==='glaze')return .20;return .18}
function makeChips(){PHASES.forEach((p,i)=>{const b=document.createElement('button');b.className='phase';b.dataset.i=i;b.innerHTML='<span class="n">'+(i+1)+'</span><span>'+p.name+'</span><span class="t">'+p.start.toFixed(0)+'s</span>';b.onclick=()=>{setTime(p.start+.01);playing=true;syncPlay()};phaseList.appendChild(b)})}
function activePhase(i){phaseList.querySelectorAll('.phase').forEach((el,j)=>el.classList.toggle('active',j===i))}
function syncPlay(){playBtn.textContent=playing?'❚❚':'▶';brush.setAttribute('opacity',(playing&&t<DURATION-.05)?'1':'0')}
function setTime(v){t=Math.max(0,Math.min(DURATION,v));syncPlay()}
function resetBatches(){batches.forEach(b=>b.classList.remove('on'));shown=-1}
function syncBatches(){let n=0;while(n<batches.length&&(+batches[n].dataset.t)<=t)n++;if(n<shown)resetBatches();if(n>shown){for(let i=Math.max(0,shown);i<n;i++)batches[i].classList.add('on')}shown=n;if(n>0){const b=batches[n-1],p=(b.dataset.pos||'0,0').split(',');targetX=+p[0];targetY=+p[1];targetAngle=+(b.dataset.angle||0);targetSize=Math.min(36,10+Math.sqrt(Math.max(1,+(b.dataset.size||8)))*3.3)}}
function updateUI(){const pct=Math.max(0,Math.min(100,t/DURATION*100));fill.style.width=pct+'%';timeEl.textContent=fmt(t)+' / '+DURATION.toFixed(1)+' s';track.setAttribute('aria-valuenow',pct.toFixed(0));activePhase(phaseIndexAt(t))}
function draw(now){const dt=Math.min(.06,(now-last)/1000);last=now;if(playing&&query.get('step')===null){t=Math.min(DURATION,t+dt*speed);if(t>=DURATION){playing=false;syncPlay()}}syncBatches();updateUI();const ease=1-Math.pow(.8,dt*60);cursorX+=(targetX-cursorX)*ease;cursorY+=(targetY-cursorY)*ease;let da=targetAngle-cursorAngle;while(da>Math.PI)da-=Math.PI*2;while(da<-Math.PI)da+=Math.PI*2;cursorAngle+=da*ease;cursorSize+=(targetSize-cursorSize)*ease;brush.setAttribute('transform','translate('+(cursorX+Math.sin(now/120)*1.2)+' '+(cursorY+Math.cos(now/170)*.8)+') rotate('+(cursorAngle*180/Math.PI)+') scale('+(cursorSize/22)+')');const p=phaseIndexAt(t);lineAlpha+=(lineTarget(p)-lineAlpha)*ease;if(lineEl)lineEl.style.opacity=lineAlpha.toFixed(3);const mt=(PHASES[p]&&PHASES[p].id==='focus-detail')?1:0;maskAlpha+=(mt-maskAlpha)*ease;if(maskEl)maskEl.setAttribute('opacity',maskAlpha.toFixed(3));if(t>=DURATION-.05)brush.setAttribute('opacity','0');else if(!playing)brush.setAttribute('opacity','.35');requestAnimationFrame(draw)}
function init(){makeChips();const qstep=query.get('step');if(query.get('static')==='1'||qstep!==null){const n=qstep===null?batches.length:Math.max(0,Math.min(batches.length,parseInt(qstep,10)||0));batches.forEach((b,i)=>b.classList.toggle('on',i<n));shown=n;playing=false;t=qstep===null?DURATION:(n>0?+batches[n-1].dataset.t:0);if(lineEl)lineEl.style.opacity=qstep===null?'.32':lineTarget(phaseIndexAt(t)).toFixed(2);if(maskEl)maskEl.setAttribute('opacity','0');brush.setAttribute('opacity','0');syncPlay();updateUI();if(query.get('ui')==='0')document.body.classList.add('ui0');requestAnimationFrame(draw);return}if(query.get('ui')==='0')document.body.classList.add('ui0');t=0;playing=true;resetBatches();syncPlay();updateUI();requestAnimationFrame(draw)}
playBtn.onclick=()=>{if(t>=DURATION-.02){setTime(0);resetBatches()}playing=!playing;syncPlay();requestAnimationFrame(draw)};
document.getElementById('restart').onclick=()=>{setTime(0);resetBatches();playing=true;syncPlay();requestAnimationFrame(draw)};
speedBtn.onclick=()=>{const o=[1,1.5,2,3];speed=o[(o.indexOf(speed)+1)%o.length];speedBtn.textContent=speed+'×'};
track.onclick=e=>{const r=track.getBoundingClientRect();setTime((e.clientX-r.left)/r.width*DURATION)};
track.onkeydown=e=>{if(e.key==='ArrowRight'){setTime(t+2);e.preventDefault()}if(e.key==='ArrowLeft'){setTime(t-2);e.preventDefault()}};
addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();playBtn.click()}if(e.key==='r'||e.key==='R'){document.getElementById('restart').click()}});
init();
</script></body></html>'''
    html = html.replace("__TITLE__", title).replace("__DUR__", "%.1f" % total_time).replace("__PHASES__", phases_json).replace("__SVG__", svg)
    Path(out_path).write_text(html, encoding="utf-8")
    return {"W": W, "H": H, "paths": html.count("<path"), "batches": total, "phases": phases,
            "bytes": len(html.encode("utf-8")), "mb": round(len(html.encode("utf-8")) / 1048576, 2),
            "duration": round(total_time, 1), "preview": str(preview_path)}


def make_global_palette(img, colors):
    if colors <= 0:
        return None
    if colors > 1024:
        raise SystemExit("--palette max is 1024; use 256-512 for practical playback")
    n = max(8, int(colors))
    if n <= 256:
        q = img.quantize(colors=n, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        pal = np.asarray(q.getpalette(), dtype=np.uint8).reshape(-1, 3)[:n]
        if len(pal) < n:
            pal = np.vstack([pal, np.repeat(pal[-1:], n - len(pal), axis=0)])
        return {"pil": q, "pal": pal}
    try:
        from sklearn.cluster import MiniBatchKMeans
    except ImportError as exc:
        raise SystemExit("--palette > 256 requires scikit-learn") from exc
    W, H = img.size
    scale = min(1.0, math.sqrt(80000.0 / max(1, W * H)))
    sample = img.resize((max(1, round(W * scale)), max(1, round(H * scale))), Image.LANCZOS)
    x = np.asarray(sample, dtype=np.float32).reshape(-1, 3)
    km = MiniBatchKMeans(n_clusters=n, random_state=42, batch_size=4096, n_init=3, max_iter=100)
    km.fit(x)
    pal = np.clip(np.rint(km.cluster_centers_), 0, 255).astype(np.uint8)
    return {"pil": None, "pal": pal}


def main():
    ap = argparse.ArgumentParser(description="Layered painterly SVG process replay")
    ap.add_argument("ref"); ap.add_argument("out")
    ap.add_argument("--size", default="")
    ap.add_argument("--max-pixels", type=int, default=2000000)
    ap.add_argument("--duration", type=float, default=52.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--title", default="她与金线")
    ap.add_argument("--palette", type=int, default=256, help="global palette colours sampled from the reference; 0 = per-layer cut, 1-256 median cut, 257-1024 k-means")
    ap.add_argument("--line-direction", default="", help="JSON written by the agent: per-region line hierarchy")
    ap.add_argument("--line-only", action="store_true", help="fast preview: only the line layer, no paint batches")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    img = load_reference(args.ref, parse_size(args.size), args.max_pixels)
    W, H = img.size
    palette = make_global_palette(img, args.palette)
    line_direction = load_line_direction(args.line_direction)
    arr = np.asarray(img, dtype=np.uint8)
    border = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]], axis=0)
    bg = np.median(border, axis=0).astype(np.uint8)
    underlay = '<rect width="%d" height="%d" fill="%s"/>' % (W, H, hex_color(bg))
    if args.line_only:
        all_batches = component_batches_for_lines(img, W, H, line_direction)
    else:
        all_batches = []
        all_batches.extend(component_batches_for_regions(img, W, H, "base", 0.34, 14, 24, 1.0, 40, palette))
        all_batches.extend(component_batches_for_regions(img, W, H, "mid", 0.52, 48, 8, 0.50, 80, palette))
        all_batches.extend(component_batches_for_regions(img, W, H, "detail", 0.85, 128, 2, 0.18, 240, palette))
        all_batches.extend(component_batches_for_focus(img, W, H, palette))
        all_batches.extend(build_two_tone(img, W, H))
        all_batches.extend(build_occlusion(img, W, H))
        all_batches.extend(build_undertone(img, W, H))
        all_batches.extend(build_glaze(img, W, H))
        all_batches.extend(build_color_grade(img, W, H))
        all_batches.extend(component_batches_for_lines(img, W, H, line_direction))
    phases, total_time = assign_timeline(all_batches, args.duration, rng)
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    preview_path = out.with_suffix(".preview.html")
    info = build_html(img, all_batches, phases, total_time, args.title, out, preview_path, underlay)
    info["out"] = str(out)
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
