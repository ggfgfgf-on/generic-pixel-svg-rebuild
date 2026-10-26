"""Fill only the thin gaps between adjacent SVG colour paths.

Input: an SVG (or the SVG inside a generated HTML) plus a screenshot rendered from
it at native size. Output: the same SVG with tiny run-length fills appended.

Guards, learned the hard way:
  * requires an opaque background `<rect>` - a transparent SVG has no "gap" colour
    to measure, so it raises instead of silently no-op'ing;
  * only regions thinner than `max_thickness` (distance transform) or smaller than
    `max_comp` pixels are filled, so genuinely background-coloured artwork
    (white clothing, eye whites, background showing through an arm) is left alone;
  * geometry of existing paths is never touched and no path gets a stroke.
"""
import re
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

__all__ = ["seal", "main"]


def seal(svg_text, png_path, max_comp=24, max_thickness=1.5):
    """Return (new_svg, stats). Raises ValueError when the SVG has no background rect."""
    m = re.search(r'<rect[^>]*fill="#([0-9a-fA-F]{6})"', svg_text)
    if not m:
        raise ValueError("no background <rect>: transparent SVGs have no gap colour to measure")
    base = np.array([int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)], np.uint8)
    img = np.asarray(Image.open(png_path).convert("RGB"), dtype=np.uint8)
    if img.shape[:2] != Image.open(png_path).size[::-1]:
        pass  # PIL already gives (h, w, 3)
    non = np.any(img != base, axis=2)

    # enclosed holes (cracks between layers) + tiny isolated background specks
    holes = ndimage.binary_fill_holes(non) & ~non
    if holes.any():
        depth = ndimage.distance_transform_edt(holes)
        holes &= depth <= max_thickness
    small = np.zeros_like(non)
    lab, n = ndimage.label(~non, structure=np.ones((3, 3)))
    if n:
        sizes = ndimage.sum(~non, lab, range(1, n + 1))
        keep = np.nonzero(sizes <= max_comp)[0] + 1
        if len(keep):
            small = np.isin(lab, keep)
    gaps = (holes | small) & ~non
    stats = {"gaps": int(gaps.sum()), "runs": 0, "colours": 0}
    if not gaps.any():
        return svg_text, stats

    # nearest visible colour for every gap pixel
    _, (iy, ix) = ndimage.distance_transform_edt(~non, return_indices=True)
    ys, xs = np.nonzero(gaps)
    cols = img[iy[gaps], ix[gaps]]
    order = np.lexsort((xs, ys))
    ys, xs, cols = ys[order], xs[order], cols[order]

    runs = []
    i = 0
    while i < len(xs):
        j = i + 1
        while (j < len(xs) and ys[j] == ys[i] and xs[j] == xs[j - 1] + 1
               and np.array_equal(cols[j], cols[i])):
            j += 1
        runs.append((int(xs[i]), int(ys[i]), int(xs[j - 1] - xs[i] + 1), cols[i]))
        i = j

    by_col = {}
    for x, y, w, c in runs:
        by_col.setdefault(tuple(int(v) for v in c), []).append((x, y, w))
    pieces = []
    for c, rr in by_col.items():
        d = " ".join("M%d %dh%dv1h-%dZ" % (x, y, w, w) for x, y, w in rr)
        pieces.append('<path fill="#%02x%02x%02x" d="%s"/>' % (c[0], c[1], c[2], d))
    block = '<g id="gap-seal" shape-rendering="crispEdges">%s</g>' % "".join(pieces)
    stats.update(runs=len(runs), colours=len(by_col))
    return svg_text.replace("</svg>", block + "</svg>"), stats


def main():
    if len(sys.argv) < 4:
        raise SystemExit("usage: seal_gaps.py in.svg|in.html in.png out [max_component]")
    src, png_path, out_path = sys.argv[1:4]
    max_comp = int(sys.argv[4]) if len(sys.argv) > 4 else 24
    doc = open(src, encoding="utf-8").read()
    m = re.search(r'<svg\b.*?</svg>', doc, re.S)
    if not m:
        raise SystemExit("no <svg> element in %s" % src)
    try:
        new_svg, stats = seal(m.group(0), png_path, max_comp)
    except ValueError as exc:
        raise SystemExit("seal_gaps: %s" % exc)
    open(out_path, "w", encoding="utf-8").write(doc[:m.start()] + new_svg + doc[m.end():])
    print(" ".join("%s=%s" % kv for kv in stats.items()))


if __name__ == "__main__":
    main()
