# Reference implementation (agent skill)

This directory is a working implementation of `pixel-svg-reconstruction-generic.md`,
packaged as a skill so an agent can run the whole pipeline end to end:

    image -> flat-color SVG paths -> single-file HTML player -> screenshot diff -> metrics

It exists because the manual specifies *what* to produce, while everything below
*how* is what actually decides whether the output passes: geometry choice, palette
construction, layer order, step semantics, and a renderer-consistent measurement loop.

Everything here was measured on real inputs; no number below is estimated.

## Two contracts, not one

The README currently asks for both "pixel runs" and "curves", plus `mean < 2` and
`100% <= 10`. Those two goals are mutually exclusive: a pixel-exact reproduction
*requires* axis-aligned integer runs (staircase edges are the encoding, not a defect),
while any curve fit necessarily deviates by more than 2/255 on antialiased sources.
The implementation therefore exposes two branches with separate acceptance budgets:

| branch | geometry | measured | budget |
|---|---|---|---|
| `build_svg.py` | pixel run-length rects, `shape-rendering="crispEdges"` | mean 0.43 (flat), 2.16 (AA illustration) | mean < 2, uncovered = 0 |
| `vectorize.py` | potrace curves per color mask, `fill-rule="evenodd"` | mean 3.74 … 8.35 | mean < 10, uncovered = 0, no staircase |

Both are pure `<path>`/`<rect>`: no `<image>`, no base64, no gradients, single HTML file.

## Measured results

| source | branch | K | output | mean | <= 5 | <= 10 |
|---|---|---|---|---|---|---|
| 1280x800 flat illustration | pixel | 1024 | 1134 paths / 4.05 MB | 0.43 | 99.72% | 99.75% |
| 750x434 game art (AA) | pixel | 4096 | 4085 paths / 4.06 MB | 2.16 | 96.67% | 99.89% |
| 750x434 game art | curve, 2x SS | 192 | 385 paths / 19.7 MB | 6.36 | 61.0% | 88.1% |
| 960x2082 thick-paint | curve, 2x SS | 192 | 193 paths / 21.1 MB | 8.10 | 36.4% | 77.9% |
| 1200x675 RGBA cutout | curve, 2x SS | 192 | 193 paths / 7.55 MB | 3.74 | 83.0% | 91.5% |

## What the implementation adds beyond the manual

1. **Median-cut palette, never k-means.** Same-cost clustering is unstable on gradients
   and drift shows up as banding. Lab-space median cut was also tested and *rejected*:
   K=192 on the same source gives mean 6.87 (RGB) vs 8.43 (Lab).
2. **Per-color masks -> `Bitmap(~mask)`.** potracer treats `True` as white, i.e. as the
   region to *keep*; forgetting the inversion collapses the whole image to one background color.
3. **1 px dilation + border-dominant base rect.** Adjacent layers fit independently, so their
   outlines never coincide: without dilation you get white seams; with a largest-area base
   you get dark blobs at bright/dark borders (fixed 216/325 offending pixels on one source).
4. **Supersampling instead of more colors.** 2x-3x upscale, trace, divide coordinates back.
   Raising K alone stops paying: 96 -> 192 -> 384 moves mean 10.89 -> 8.10 -> 6.36.
5. **Step order by color family and luminance, not by area.** Area ordering produces
   noise-like intermediate frames; family-first (background, then dark -> light) reads as
   "base, midtone, highlight". Verified per frame with `steps.py` (renders every step,
   checks monotonic progress and flat tails).
6. **`step_delta` as the progress metric for curves.** Non-white ratio saturates as soon as
   the base rect covers the canvas, so it cannot detect a stuck step.
7. **Alpha-aware output.** Transparent PNGs get no background `<rect>` (true transparency,
   verified by screenshotting over magenta and/or a checkerboard) and transparent pixels are
   excluded from both the palette and the tracing masks.
8. **Zero-dependency screenshots.** `render.py` drives system Edge/Chrome
   (`--headless=new --screenshot`); no Playwright required.
9. **Regression suite.** `tests/run_tests.py` rebuilds three synthetic fixtures
   (flat / photo-like / transparent cutout) and asserts structure plus a recorded error
   budget, so a change that silently degrades quality fails the run.

## Measurement discipline

Same HTML rendered by Playwright and by the Edge CLI differs by `mean ~= 1.6`; a project
must pin one renderer before comparing runs. Metrics are always
`mean / pct(<=3,5,10,20) / uncovered`, where `uncovered` = rendered pure white while the
source is not pure white there. For alpha sources, only pixels with `alpha >= 250` count as
"must be covered" — antialiased edges are supposed to soften.

## Run it

    python scripts/build_svg.py  ref.png out.html 4096 8
    python scripts/vectorize.py  ref.png out.html 192 8 1.0 1 0.4 2 26
    python scripts/render.py     out.preview.html shot.png 1200 675 "?step=11"
    python scripts/metric.py     ref.png shot.png
    python scripts/steps.py      out.html outdir
    python scripts/autotune.py   ref.png outdir
    python tests/run_tests.py --record   # once, then:
    python tests/run_tests.py

Requires Python 3 + numpy + Pillow + scipy (+ `potracer` for the curve branch).
