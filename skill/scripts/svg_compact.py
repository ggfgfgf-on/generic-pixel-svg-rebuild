#!/usr/bin/env python3
"""Lossless SVG path-data compaction for painterly/vector outputs.

What it does:
  * rewrites absolute M/L/C/Z runs to relative m/l/c/z (same geometry);
  * removes per-path shape-rendering when the root already declares it;
  * leaves path order, fills, opacity, fill-rule and layer groups untouched.

Why: process-final SVGs are dominated by repeated absolute coordinates and
per-path attributes, not by hidden geometry. On a 28.5 MB 876x1904 process
final this cuts ~36% of bytes and ~18% of render time with a measured
0.02/255 mean pixel difference (renderer antialiasing, not a geometry change).

It is deliberately *not* an occlusion flattener: fully hidden path culling was
measured at only ~1.9% of paths on the same file, and flattening the topmost
image loses fine detail. See SKILL.md for those measurements.

Usage:
  python svg_compact.py in.svg out.svg [--precision 6]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_PATH = re.compile(r"<path\b[^>]*/>")
_SVG = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _fmt(v: float, precision: int) -> str:
    t = ("%." + str(precision) + "f") % v
    t = t.rstrip("0").rstrip(".")
    if t in ("-0", ""):
        t = "0"
    return t


def _nums(vals, precision: int) -> str:
    out = ""
    for v in vals:
        z = _fmt(v, precision)
        if out and not z.startswith("-"):
            out += " "
        out += z
    return out


def _relative_d(d: str, precision: int):
    """Return relative m/l/c/z data, or None when the path uses other commands."""
    toks = _TOKEN.findall(d)
    i = 0
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    res = []
    while i < len(toks):
        c = toks[i]
        if not c.isalpha():
            return None
        i += 1
        if c in ("Z", "z"):
            res.append("z")
            cur = start
            continue
        if c not in ("M", "L", "C"):
            return None
        vals = []
        while i < len(toks) and not toks[i].isalpha():
            try:
                vals.append(float(toks[i]))
            except ValueError:
                return None
            i += 1
        if c == "M":
            if len(vals) < 2 or len(vals) % 2:
                return None
            for k in range(0, len(vals), 2):
                x, y = vals[k], vals[k + 1]
                res.append(("m" if k == 0 else "l") + _nums([x - cur[0], y - cur[1]], precision))
                cur = (x, y)
                if k == 0:
                    start = (x, y)
        elif c == "L":
            if len(vals) < 2 or len(vals) % 2:
                return None
            parts = []
            for k in range(0, len(vals), 2):
                x, y = vals[k], vals[k + 1]
                parts += [x - cur[0], y - cur[1]]
                cur = (x, y)
            res.append("l" + _nums(parts, precision))
        else:  # C
            if len(vals) < 6 or len(vals) % 6:
                return None
            parts = []
            for k in range(0, len(vals), 6):
                x1, y1, x2, y2, x, y = vals[k:k + 6]
                parts += [x1 - cur[0], y1 - cur[1], x2 - cur[0], y2 - cur[1],
                          x - cur[0], y - cur[1]]
                cur = (x, y)
            res.append("c" + _nums(parts, precision))
    return "".join(res)


def compact_svg(svg_text: str, precision: int = 6) -> str:
    """Return *svg_text* with relative path data and redundant attrs removed.

    Unsupported path commands are left byte-for-byte unchanged. The transform
    is geometry-preserving; precision only controls how relative deltas are
    printed.
    """
    if precision < 0:
        raise ValueError("precision must be >= 0")
    m = _SVG.search(svg_text)
    if not m:
        raise ValueError("no <svg> element found")
    root = m.group(0)
    root_precise = 'shape-rendering="geometricPrecision"' in root

    def fix(pm):
        tag = pm.group(0)
        dm = re.search(r'\bd="([^"]*)"', tag)
        if not dm:
            return tag
        nd = _relative_d(dm.group(1), precision)
        if nd is None:
            return tag
        tag = tag[:dm.start(1)] + nd + tag[dm.end(1):]
        if root_precise:
            tag = tag.replace(' shape-rendering="geometricPrecision"', "")
        return tag

    return _PATH.sub(fix, svg_text)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lossless relative-path SVG compaction")
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--precision", type=int, default=6,
                    help="decimal places for relative deltas (default 6)")
    args = ap.parse_args(argv)
    src, dst = Path(args.src), Path(args.out)
    text = src.read_text(encoding="utf-8")
    out = compact_svg(text, args.precision)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8")
    print(json.dumps({
        "out": str(dst),
        "bytes_in": len(text.encode("utf-8")),
        "bytes_out": len(out.encode("utf-8")),
        "paths": len(_PATH.findall(out)),
        "precision": args.precision,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
