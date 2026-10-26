#!/usr/bin/env python3
"""Optional SVG edge-softening post-process.

This is deliberately a *post-process*: it never changes path geometry, so a
vectorizer's measured fidelity is preserved. It injects one Gaussian-blur
filter and wraps the artwork in it, keeping the opaque background rect
outside the filter so canvas borders stay solid.

Usage:
  python svg_postprocess.py in.svg out.svg --soften 0.35
  python svg_postprocess.py in.html out.html --soften 0.35   # first <svg> only

Range: 0.15–0.6 is useful at native 876px width. 0 disables (copies input).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_SVG_OPEN = re.compile(r"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
_SVG_CLOSE = re.compile(r"</svg\s*>", re.IGNORECASE)
_BG_RECT = re.compile(
    r'(<rect\b[^>]*\bwidth="[^"]+"[^>]*\bheight="[^"]+"[^>]*/>)',
    re.IGNORECASE,
)
_FILTER_ID = "gpsr-edge-soften"


def _add_root_attr(open_tag: str, name: str, value: str, force: bool = False) -> str:
    pat = r'\b%s\s*=\s*"[^"]*"' % re.escape(name)
    if force and re.search(pat, open_tag, re.IGNORECASE):
        return re.sub(pat, '%s="%s"' % (name, value), open_tag, count=1, flags=re.IGNORECASE)
    if re.search(pat, open_tag, re.IGNORECASE):
        return open_tag
    return open_tag[:-1] + ' %s="%s">' % (name, value)


def soften_svg(svg_text: str, sigma: float = 0.35, filter_id: str = _FILTER_ID) -> str:
    """Return a copy of *svg_text* with an optional edge-softening filter.

    The first full-canvas <rect> is left outside the filter, which prevents a
    translucent halo along the document border. All remaining artwork is
    wrapped once; path geometry and layer order are untouched.

    Idempotent: if the same filter id is already present, returns input
    unchanged. Raises ValueError when there is no SVG root.
    """
    sigma = float(sigma)
    if sigma <= 0:
        return svg_text
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", filter_id or ""):
        raise ValueError("invalid filter id: %r" % (filter_id,))

    m = _SVG_OPEN.search(svg_text)
    if not m:
        raise ValueError("no <svg> element found")
    close = _SVG_CLOSE.search(svg_text, m.end())
    if not close:
        raise ValueError("no closing </svg> found")
    open_tag = m.group(0)
    if ('id="%s"' % filter_id) in svg_text or ("url(#%s)" % filter_id) in svg_text:
        return svg_text

    body = svg_text[m.end():close.start()]
    prefix = ""
    bg = _BG_RECT.search(body)
    if bg:
        # Keep everything up to and including the first opaque background rect
        # outside the filter, including a leading line-sketch path. The base
        # rect still paints over that sketch, so paint order is unchanged.
        prefix = body[:bg.start()] + bg.group(1)
        body = body[bg.end():]

    defs = (
        '<defs><filter id="%s" x="-2%%" y="-2%%" width="104%%" height="104%%" '
        'color-interpolation-filters="sRGB">'
        '<feGaussianBlur stdDeviation="%.3f"/></filter></defs>'
        % (filter_id, sigma)
    )
    open_tag = _add_root_attr(open_tag, "shape-rendering", "geometricPrecision", force=True)
    wrapped = defs + prefix + '<g id="edge-soften-wrap" filter="url(#%s)">%s</g>' % (
        filter_id, body
    )
    return svg_text[:m.start()] + open_tag + wrapped + svg_text[close.start():]


def soften_document(doc: str, sigma: float = 0.35) -> str:
    """Apply soften_svg to the first SVG inside an HTML/SVG document."""
    m = _SVG_OPEN.search(doc)
    if not m:
        raise ValueError("no <svg> element found")
    close = _SVG_CLOSE.search(doc, m.end())
    if not close:
        raise ValueError("no closing </svg> found")
    svg = doc[m.start():close.end()]
    return doc[:m.start()] + soften_svg(svg, sigma) + doc[close.end():]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Optional edge-softening post-process for SVG/HTML")
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--soften", type=float, default=0.35,
                    help="Gaussian soft-edge sigma in user units; 0 disables. Default 0.35")
    ap.add_argument("--filter-id", default=_FILTER_ID)
    args = ap.parse_args(argv)

    src = Path(args.src)
    dst = Path(args.out)
    doc = src.read_text(encoding="utf-8")
    if args.soften <= 0:
        out = doc
    elif src.suffix.lower() in (".html", ".htm"):
        out = soften_document(doc, args.soften)
    else:
        out = soften_svg(doc, args.soften)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8")
    print(json.dumps({
        "out": str(dst),
        "soften": args.soften,
        "bytes": len(out.encode("utf-8")),
        "filter": args.filter_id if args.soften > 0 else None,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
