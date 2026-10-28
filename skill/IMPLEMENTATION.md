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

`paint_process.py` is a **process replay** branch, not a pixel-fidelity competitor. It traces coherent colour regions and line masks instead of scattering random dabs, then replays eleven illustration-workflow phases: sketch/ink, flat fill, mid block, two-tone shadow, detail, mask refinement, occlusion, undertone colour, glaze, lift/colour-accent and final inking. The page is still one self-contained HTML with pure SVG paths, no image/base64/gradient/CSS transition. The only `url(#)` is the optional edge-soften filter described below.

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

#### Final-only bridge and optional edge-soften

The process algorithm is also callable as a **final-only** renderer: `build_painterly_batches()` returns the same batch sequence, and `build_final_static_svg()` flattens it to one static SVG (base, mid, shadow, detail, occlusion, undertone, glaze, colour grade, final line accent). It drops `data-step`/`data-total`, the mask overlay and the player, and keeps the line accent at its final opacity. On the 876x1904 test source the rendered PNG is byte-identical to the extracted process final; only title/provenance differs.

`--process-final` (default off) uses this path from `vectorize.py`; `--process-palette=N` defaults to 256 (or the positional K when supplied), and `--process-line-direction=FILE` is the same per-image JSON. Output `.svg` for a static SVG or `.html` for a minimal static page. The previously hard-coded trace controls are now explicit, with the old values as defaults: `--process-base-scale=0.34`, `--process-mid-scale=0.52`, `--process-detail-scale=0.85`, `--process-min-areas=24,8,2`, `--process-blurs=1.0,0.5,0.18`, `--process-focus-box=0.10,0.08,0.90,0.92`, `--process-focus=1.0,4,0,1`, `--process-dil=1`, `--process-turd=1`, `--process-alphamax=1.0`, `--process-ss=1.0`. `--process-from-front` is on by default: it copies the positional `turd/alphamax/dil` into the process options (`dil` into both region and focus), copies `ss` only when no explicit `--process-*-scale` is present, and inherits `--full-palette` plus an explicit `--palette-sample`. The process palette has its own full-pixel switch: `--process-full-palette` (or `--process-palette-sample=0`) fits the >256-colour k-means on every pixel, `--process-palette-sample=N` sets the sample budget (default 80,000), and `--no-process-full-palette` opts out of the inherited flag. The colour ceiling is `--process-palette-max=N` (default 1024; direct `paint_process.py` CLI: `--palette-max=N`), threaded into `make_global_palette(..., max_colors=N)`; values above it fail before tracing. Explicit `--process-*` wins; `--no-process-from-front` restores the fixed old behaviour (`blur` remains per-layer; `sk_thr/dark_k/med` have no process analogue). With `--no-process-from-front`, the old fixed defaults produce 54,887 paths and mean 11.8843; 0.50/0.75/1.25 with `--process-turd=6` produces 103,991 paths, 29.72 MB after compaction, mean 10.8098, raises the face edge-density from 34.31% to 34.70%, and raises headless render time from about 3.0 s to 7.0 s. The normal 192/2x trace remains sharper at mean 8.7141, so this is a knob for the process look, not a fidelity replacement. At the same 384-colour/0.50/0.75/1.25 setting, full-pixel k-means gives 104,841 paths, 29.57 MB, mean 10.7668, face edge density 35.03%, and 6.9 s headless; the 80k sample gives 103,991 paths, 29.72 MB, mean 10.8098, 34.69%, and 7.0 s. The full path is therefore a small quality gain, not a different league.

`--edge-soften=SIGMA` (default 0/off, **legacy** — kept as the quick path; prefer `--alpha-soften` for deliverables) is a separate post-process, not a tracer change: it injects one `feGaussianBlur` filter and wraps the artwork, leaving path coordinates untouched. It is applied last, so `--seal` is skipped with a warning. `svg_postprocess.py --soften N` can soften an existing file. This reduces the visual staircase at high zoom but cannot restore geometric resolution; use `ss=3` when true high-zoom geometry matters.

**Geometric edge softening (`--alpha-soften=N`).** Where `--edge-soften` is a blur filter over the finished picture, this one stays pure geometry: for every colour class it emits N extra `<path>` rings just *outside* that class boundary, with `fill-opacity` decaying by half per ring (`--alpha-strength`, default 0.5) at `--alpha-width` mask pixels per ring (default 1.0). Rings are appended after the opaque layers so they blend on top, and they are clipped to the subject mask so transparent sources do not gain a halo. The two soften modes are mutually exclusive and `vectorize.py` refuses both at once.

Measured on 400x711, K=96/ss=1: base mean 6.06, 48,114 step edges above 10/255, mean step 5.06; `--alpha-soften 2 --alpha-strength 0.25` gives 7.06, 35,892 (-25%), 4.16; strength 0.5 gives 7.99, 32,112 (-33%), 4.09; `--alpha-width 2` gives 10.26, 25,271, 3.60. At K=192/ss=2: 4.82 / 8 s / 9.6 MB base versus 7.22 / 21 s / 40.9 MB softened (step edges 46,813 -> 31,152). So the flag trades per-pixel mean error and roughly 4x file size for fewer visible band steps; it is off by default and should be tuned per image. `--alpha-ramp` / `--no-alpha-ramp` (auto: on for blending, off for reference-colour) restricts rings to reference pixels that are *slowly* varying (5x5 luminance span 6-72 and fine gradient <= 14). That is the "only soften where a gradient exists" gate: on blending it cuts the cost per removed step from 0.082 to 0.046 mean per 1k edges. `--alpha-true-color=N` (default 0) instead splits each ring by reference luminance into N sub-levels and paints each with its own true mean colour, which is how mean can actually drop (6.06 -> 5.65 at N=6, alpha 0.7); `--alpha-strength` defaults to 0.25 for blending and 0.7 for reference-colour mode.

The mean increase is intrinsic, not a bug: mean is per-pixel RGB deviation, and rings push boundary pixels toward the neighbour colour. Where the reference is genuinely flat there, that is pure loss; the gain only exists where the reference really is a gradient. Smoothness and per-pixel fidelity are different objectives — to lower mean, raise K instead.

**Refine loop (`--alpha-refine=N`).** Generation is followed by measurement: the flat layers and the ring-augmented layers are both rendered headlessly, each compared against the reference, and any ring pixel whose 3x3 local error is worse than the flat baseline (by more than `--alpha-tol`) is removed before the rings are rebuilt. Per-pixel comparison is useless here — the first attempt dropped 501,111 of the ring pixels and with them the entire effect — because softening deliberately spreads error; the 3x3 window asks "did this neighbourhood get worse" instead of "did this pixel move". On the K=32 skin crop one pass at the default tolerance moves mean 5.299 -> 5.148 and trims 43.7MB -> 38.6MB; tighter tolerance 0.5 reaches 5.116 / 35.7MB, looser 3.0 gives 5.245 / 43.2MB. Extra passes converge fast: at tol 3.0 the drops are 61,506 / 8,290 / 2,070 / 846 / 306 px for passes 1-5 and mean goes 5.245 / 5.236 / ... / 5.234, so one pass is enough in practice. Each pass costs one extra headless render (about 3 s at 760x900).

**Region budget (`--region x0,y0,x1,y1:K`).** The flat layers are traced first at the global palette, then each region repeats the same trace on its own crop with its own palette, and the resulting paths are appended last so they cover the global layers inside the box. Coordinates are source pixels; the crop is taken at the supersampled resolution, so region geometry keeps the same `ss` accuracy. Regions do not segment anything — the caller supplies the box or a mask image (white = retrace). The region colour budget is either absolute (`:384`) or relative to the global K (`:3x`, resolved inside `trace_region` so every preset resolves against its own K; verified that K=48 with `:8x` reproduces the `:384` result exactly, face mean 5.92). A region may also carry its own supersampling factor (`--region x0,y0,x1,y1:K,SS`), because the region is traced on its own crop and positioned with a translate group rather than being painted into the global canvas; that also makes mask-shaped regions possible (`--region-mask file.png:K,SS`). The process branch accepts the same flags and appends the region group before `</svg>`. Measured on the 400x711 portrait at global K=48/ss=2: whole-image mean 8.23, face-region mean 11.22, 2.5MB; adding `--region 150,200,330,430:384` gives 7.46 / 5.93 / 3.55MB with the background untouched (7.72); raising the global K to 96 instead gives 6.35 / 8.94 / 4.02MB. The region buys a 47% face-error drop for +1MB where the global bump buys 20% for +1.5MB.

**Iterate loop (`iterate.py`).** A peer entry point to vectorize/process/auto. It renders the current SVG, computes per-tile max-channel error on a `--grid` tiling, and patches the worst tiles by adding `--region` entries at `global_K * k_mult^round`, skipping tiles already covered by an accepted region and stopping when a round gains less than `--min-gain`. Model-authored patches use the same mechanism through `--patch` (JSON ops), so the model decides and the script only executes. Measured on the 400x711 portrait, base K=48/ss=2: 8.23 (2.5MB) -> 7.48 (3.1MB, 4 tiles at K=96) -> 6.80 (3.9MB, 4 tiles at K=192); a two-box model patch (face 384/ss2, torso 192) reaches 6.91 in one build.

**Patch ops.** `iterate.py` accepts four op kinds: `region` (extra budget, re-traces), `style` (overrides base trace params and rebuilds the whole picture), `remap` (text-level fill rewriting: exact from/to within tolerance, or snapping every fill to a palette), and `draw` (injects model-authored path data above the artwork). Path surgery ops (`drop` / `recolor` / `redraw` / `reorder`) operate on the emitted `<path>` tags, addressed either by `ids` or by a `where` matcher on `fill`/`id`. Ids (`p1..pN`) are injected only when a path op is present, so the default artefact stays byte-identical; they follow emission order, which means changing K/ss renumbers them. `redraw` replaces the `d` in place and therefore keeps the original z-order (verified: a redrawn full-canvas path stayed beneath the later layers), while `draw` and `reorder` are the ops for changing stacking. `--blank WxH` starts from an empty canvas so content comes only from `draw` ops, which is genuine from-scratch drawing for symbolic art: a 7-path / 0.8KB scene renders correctly, while photographic subjects remain out of reach because the model would have to emit millions of tokens of geometry.

**Attribution (`attribute.py`).** One extra render labels every pixel with the path that is visible there: all `<path>` fills are replaced by unique index colours, the label render is decoded back to indices (rejecting antialiased pixels whose colour is not within 9 units of a palette entry), and the real render's per-pixel error is charged only to the owning path. Rows carry `inner`/`ring` error means and a `guess`: `shape` when the error is boundary-weighted or when the optimal fill differs from the current one by more than 24 levels (the path is sitting on pixels that are not its colour), `colour` otherwise. Ranking is by `gain` = recoverable error if the suggested fill is applied, computed by coordinate descent on mean max-channel error. Controlled check: displacing the drawn scene's sun path by ~110px puts that path first (mean 126.26, `shape`). On a quantized portrait the worst paths are colour classes spanning the frame with boundary-dominated error, so recolouring recovers almost nothing (8.23 -> 8.13) and the right fix is regional budget instead.

**Ink helpers (`ink.py`).** Separate layers, not a vectorize mode, so the model can compare candidates by numbers before composing. `poster` thresholds with Sauvola (`xiBlackThreshold`) and traces the masses straight through potrace; `line` runs Canny, thins with `ximgproc.thinning`, then optionally dilates to control stroke weight before tracing bands. Every run prints JSON with front pixels, path count and size; `--style both` writes `poster.svg` + `line.svg` and composes them into one file with the line layer on top. Measured on the 400x711 portrait: poster 147 paths / 82.2KB, line at Canny 60/160 with dilate 1 is 121 paths / 70.8KB but lumps shading texture into black masses, while Canny 110/240 with dilate 0 gives 168 paths / 73.2KB and a clean hairline drawing. Soft-lit photos need the higher thresholds and a hairline first pass.

**How to judge banding.** Not with a single "strong edge" threshold: step magnitudes have to be bucketed. Reference / K=32 flat / true-colour 6 on the skin crop: >20 steps 9,506 / 13,929 / 11,596; 2-4 steps 120,561 / 43,716 / 128,900. Flat fills collapse the reference's fine gradation into fewer, larger steps, and true-colour rings restore the 2-4 band to reference level while cutting the >20 band. A ">10 edges" count shows the opposite because the 8-12 bucket grows.

**Lossless compaction (`--compact`).** `svg_compact.py` rewrites absolute `M/L/C/Z` runs to relative `m/l/c/z` and drops per-path `shape-rendering` when the root already declares it. It does not delete paths, reorder layers, or touch fills/opacity/fill-rule. On the 876x1904 process final: 28,550,352 -> 18,121,227 bytes (-36.5%), headless render 2.92 s -> 2.39 s, and a 0.0213/255 mean pixel difference (relative-coordinate float/antialias noise, not a geometry change). This is the compression that pays off; full occlusion culling was measured at only 954 / 50,927 paths (1.9%, <0.4 MB saved) because most detail paths are partially visible, and topmost flattening to 192 paths costs mean 8.43/255, so it is not a lossless default.

A global palette is sampled from the reference and reused by every layer. `--palette 256` (the default) uses median cut; `--palette 512`/`1024` uses MiniBatchKMeans (scikit-learn) for a larger source palette; `--palette 0` restores per-layer median cut. Above 256 colours the k-means fit defaults to an 80,000-pixel sample; `--palette-full` (or `--palette-sample=0`) uses every pixel and `--palette-sample=N` sets the budget. A 512-colour test lowered mean error to 10.81 but raised the file to 74,674 paths / 29.0 MB, so the shipped playback file stays at 256 for weight. A mild saturation/contrast lift, plus explicit undertone (cool shadows) and lift/colour-accent (warm lights, red/green accents) phases reduce the washed-out look without changing the default branches. This branch deliberately trades pixel error for a readable making-of sequence. Use `build_svg.py` for pixel fidelity and `vectorize.py` for a static curve reproduction.

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
   (flat / photo-like / transparent cutout) into ten cases: two pixel, four curve
   (including `--edge-soften` and `--compact`), and four process-final (default,
   configurable trace scales/focus, from-front inheritance, and full-pixel palette). It asserts
   structure plus a recorded error budget, so a change that silently degrades quality
   fails the run.

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
    # process algorithm, final-only static SVG (default off)
    python scripts/vectorize.py ref.png process-final.svg --process-final --process-palette=256 --process-line-direction line-direction.json
    # old fixed process behavior (pre-from-front): add --no-process-from-front
    # process final with higher trace scales (bigger/slower, more detail)
    python scripts/vectorize.py ref.png process-hd.svg 384 6 1.0 1 0.4 1 0 \
      --process-final --process-base-scale=0.5 --process-mid-scale=0.75 \
      --process-detail-scale=1.25 --process-turd=6 --edge-soften=0.35 --compact
    # optional edge soften (default off; true geometry still needs ss=3)
    python scripts/vectorize.py ref.png out-soft.svg 192 6 1.0 1 0.4 2 0 --edge-soften=0.35
    # optional lossless compaction (default off; can combine with both above)
    python scripts/vectorize.py ref.png out-compact.svg 192 6 1.0 1 0.4 2 0 --compact
    python scripts/svg_compact.py out.svg out-compact.svg
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
