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
| `build_svg.py` (**deprecated**) | pixel run-length rects, `shape-rendering="crispEdges"` | mean 0.43 (flat), 2.16 (AA illustration) | historical only; not a delivery mode |
| `vectorize.py` | potrace curves per color mask, `fill-rule="evenodd"` | mean 3.74 … 8.35 | mean < 10, uncovered = 0, no staircase |

`vectorize.py` is the default SVG redraw. The pixel branch is deprecated: it is kept only to reproduce the old mean<2 numbers, not as a default or deliverable. Both are pure `<path>`/`<rect>`: no `<image>`, no base64, no gradients, single HTML file.

## Measured results

| source | branch | K | output | mean | <= 5 | <= 10 |
|---|---|---|---|---|---|---|
| 1280x800 flat illustration | pixel | 1024 | 1134 paths / 4.05 MB | 0.43 | 99.72% | 99.75% |
| 750x434 game art (AA) | pixel | 4096 | 4085 paths / 4.06 MB | 2.16 | 96.67% | 99.89% |
| 750x434 game art | curve, 2x SS | 192 | 385 paths / 19.7 MB | 6.36 | 61.0% | 88.1% |
| 960x2082 thick-paint | curve, 2x SS | 192 | 193 paths / 21.1 MB | 8.10 | 36.4% | 77.9% |
| 1200x675 RGBA cutout | curve, 2x SS | 192 | 193 paths / 7.55 MB | 3.74 | 83.0% | 91.5% |

### Painterly process branch (new)

`paint_process.py` is a **process replay** branch, not a pixel-fidelity competitor. It traces coherent colour regions and line masks instead of scattering random dabs, then replays eleven illustration-workflow phases: sketch/ink, flat fill, mid block, two-tone shadow, detail, mask refinement, occlusion, undertone colour, glaze, lift/colour-accent and final inking. The page is still one self-contained HTML with pure SVG paths, no image/base64/gradient/`url(#`/CSS transition.

Measured on the 876x1904 test source:

| metric | value |
|---|---|
| output | 63,719 paths / 28.15 MB / 855 batches |
| phases | 11 |
| duration | 60.4 s |
| mean channel error | 11.83 / 255 |
| <= 5 / 255 | 25.56% |
| uncovered | 0 |

The line sketch now sits below the colour layers (initial underdrawing gets covered), while the final accent line stays on top. Detail components are ordered dark-to-light so highlights are not buried. The old face/hands/feet focus pass was replaced by a generic central-subject prior, so the branch no longer assumes a portrait.

#### Line direction is per image, not a skill constant

The line stage is infrastructure, not a fixed portrait pipeline. The agent inspects the reference and writes a `line-direction.json`; Python only executes it. `regions` are arbitrary semantic boxes with **normalized 0..1 bboxes** (first match wins), a per-region `min_diag` (at 876px reference width), and optional `density_exempt`. Small or dense parts become `line-accent` and are hidden from the initial `line-sketch`. Skeletonization and manual strokes were rejected: they fragmented contours or looked cartoonish. The shipped line stage traces the full step-0 mask once and renders it as a **stroke, never a fill**, so large closed loops cannot become solid colour blocks.

The JSON also exposes the native trace parameters (`turdsize / alphamax / precision`) and line style (`color / stroke_width / sketch_opacity / accent_opacity`), so those are per-image decisions too. `--line-only` builds only this layer (about 1.5 MB, 2-3 batches) for fast visual review; the full process file remains 27-30 MB.

The painter and vector branches use an optional Windows x64 native tracing DLL (`scripts/native/trace_core.dll`, potrace 1.16) and fall back to pure Python. At 438x952/256 the render drops from 40.4s to 2.61s; the full 876x1904/256 render is about 10.5s.

A global palette is sampled from the reference and reused by every layer. `--palette 256` (the default) uses median cut; `--palette 512`/`1024` uses MiniBatchKMeans (scikit-learn) for a larger source palette; `--palette 0` restores per-layer median cut. A 512-colour test lowered mean error to 10.81 but raised the file to 74,674 paths / 29.0 MB, so the shipped playback file stays at 256 for weight. A mild saturation/contrast lift, plus explicit undertone (cool shadows) and lift/colour-accent (warm lights, red/green accents) phases reduce the washed-out look without changing the default branches. This branch deliberately trades pixel error for a readable making-of sequence. Use `build_svg.py` for pixel fidelity and `vectorize.py` for a static curve reproduction.

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

    # default SVG redraw (K=384, 3x supersampling)
    python scripts/vectorize.py  ref.png out.html 384 6 1.0 1 0.4 3 0
    # static SVG export: .svg output strips data-step/data-total automatically
    python scripts/vectorize.py  ref.png out.svg 128 6 1.0 1 0.4 1 0
    # extreme palette: full 15M-pixel median cut via native/palette_core.dll
    python scripts/vectorize.py  ref.png out.html 384 6 1.0 1 0.4 3 0 --full-palette
    # deprecated pixel branch, kept only for the old mean<2 reference:
    # python scripts/build_svg.py ref.png out.html 4096 8
    python scripts/paint_process.py ref.png process.html --size 876x1904 --duration 58 --line-direction line-direction.json
    python scripts/paint_process.py ref.png line.html --size 876x1904 --palette 0 --line-direction line-direction.json --line-only
    python scripts/render.py     out.preview.html shot.png 1200 675 "?step=11"
    python scripts/metric.py     ref.png shot.png
    python scripts/steps.py      out.html outdir
    python scripts/autotune.py   ref.png outdir
    python tests/run_tests.py --record   # once, then:
    python tests/run_tests.py

### Full-pixel palette option

The default palette is sampled (240,000 pixels) and then every pixel is assigned with
`cKDTree`. `--full-palette` switches to the original full-pixel median cut over all
15,011,136 pixels at 3x supersampling. On Windows x64 that path calls
`native/palette_core.dll` (plain C++17, no potrace dependency); if the DLL is missing
the script falls back to Python full-pixel median cut, which is correct but very slow.

Measured on the 876x1904 source, K=384 / 3x:

| palette | wall time | mean | <=5 |
|---|---:|---:|---:|
| sampled 240k (default) | 51.7 s | 5.4917 | 68.14% |
| full 15.0M (native DLL) | 49.6 s | 5.4885 | 68.44% |

The full path is kept as an explicit extreme option, not the default: on this source it
does not materially improve the metric, and it needs a native DLL. `--palette-sample=N`
tunes the sampled path instead.

Requires Python 3 + numpy + Pillow + scipy (+ `potracer` for the curve branch). `vectorize.py` samples the palette (240k pixels) and assigns full-resolution pixels with `scipy.spatial.cKDTree`; this replaced the old O(N*K) Python chunk loop that made K=384/3x appear hung.
