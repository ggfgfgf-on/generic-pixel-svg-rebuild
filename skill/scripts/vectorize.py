import sys, json, os, ctypes, time, re
from pathlib import Path
import numpy as np
import potrace
from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_loader  # noqa: E402  shared ctypes bindings (see native_loader.py)
import svg_postprocess  # noqa: E402  optional edge-soften post-process
import svg_compact  # noqa: E402  optional lossless path-data compaction


def _csv_floats(text, count, name):
    parts = [p for p in text.replace(" ", "").split(",") if p]
    if len(parts) != count:
        raise SystemExit("%s needs %d comma-separated values" % (name, count))
    try:
        return tuple(float(p) for p in parts)
    except ValueError:
        raise SystemExit("%s must contain numbers" % name)


def _flag_value(a, nxt, name):
    if a.startswith(name + "="):
        return a.split("=", 1)[1], False
    if a == name:
        if nxt is None:
            raise SystemExit("%s needs a value" % name)
        return nxt, True
    return None, False


def load_native():
    """Optional C++/potrace DLL; same curves as potrace.py, just faster."""
    return native_loader.trace_dll()


def native_path(mask, scale, turdsize, alphamax, prec=2):
    # scale > 1 means "mask was supersampled": the DLL divides the coordinates back
    return native_loader.trace_mask(mask, 1.0 / scale, 1.0 / scale, 0.0, 0.0,
                                    turdsize, alphamax, prec)

def median_cut(px, k):
    boxes = [px]
    while len(boxes) < k:
        best = None
        for i, bx in enumerate(boxes):
            if len(bx) < 2: continue
            rng = bx.max(0) - bx.min(0); ax = int(rng.argmax())
            score = float(rng[ax]) * len(bx)
            if best is None or score > best[0]: best = (score, i, ax)
        if best is None: break
        _, i, ax = best
        bx = boxes.pop(i)
        order = np.argpartition(bx[:, ax], len(bx) // 2)
        s = bx[order]; h = len(s) // 2
        boxes.append(s[:h]); boxes.append(s[h:])
    return boxes

def curv_d(curve, scale=1.0, prec=2):
    fmt = "%." + str(prec) + "f"
    k = 1.0 / scale
    def f(v):
        return fmt % (v * k)
    p = curve.start_point
    d = ["M" + f(p.x) + " " + f(p.y)]
    for seg in curve.segments if hasattr(curve, "segments") else curve:
        if getattr(seg, "is_corner", False):
            c = seg.c; e = seg.end_point
            d.append("L" + f(c.x) + " " + f(c.y))
            d.append("L" + f(e.x) + " " + f(e.y))
        else:
            c1, c2, e = seg.c1, seg.c2, seg.end_point
            d.append("C" + " ".join(f(v) for v in (c1.x, c1.y, c2.x, c2.y, e.x, e.y)))
    d.append("Z")
    return "".join(d)

def kmeans(cent, f, iters=15):
    f = max(1, min(f, len(cent)))
    idx = np.linspace(0, len(cent) - 1, f).astype(int)
    cen = cent[np.argsort(cent.mean(1))][idx]
    lab = np.zeros(len(cent), np.int32)
    for _ in range(iters):
        d = ((cent[:, None, :] - cen[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        for j in range(f):
            m = lab == j
            if m.any(): cen[j] = cent[m].mean(0)
    return lab

def _palette_sample(px, limit=240000):
    if len(px) <= limit:
        return px
    idx = np.linspace(0, len(px) - 1, limit).astype(np.int64)
    return px[idx]


def load_palette_native():
    """Optional full-pixel median-cut DLL; sampling stays the default path."""
    return native_loader.palette_dll()


def palette_native(px, k):
    return native_loader.palette_centers(px, k)


def _k_label(spec):
    """日志用：绝对值直接印数字，倍数印 3x。"""
    if spec.get("k") is not None:
        return str(spec["k"])
    return "%gx" % float(spec["kmul"])


def _parse_k(k_s, flag):
    """K 支持三种写法：384（绝对值）、3x（全局 K 的倍数）、384,2 / 3x,2（带 ss）。"""
    k_part, _, ss_part = k_s.partition(",")
    kmul = None
    k = None
    if k_part.lower().endswith("x"):
        try:
            kmul = float(k_part[:-1])
        except ValueError:
            raise SystemExit("%s multiplier must look like 3x" % flag)
        if not (0.1 <= kmul <= 32):
            raise SystemExit("%s multiplier must be 0.1..32" % flag)
    else:
        try:
            k = int(k_part)
        except ValueError:
            raise SystemExit("%s colour count must be an integer or Nx" % flag)
        if not (2 <= k <= 4096):
            raise SystemExit("%s colour count must be 2..4096" % flag)
    sr = None
    if ss_part:
        try:
            sr = int(ss_part)
        except ValueError:
            raise SystemExit("%s ss must be an integer" % flag)
        if not (1 <= sr <= 4):
            raise SystemExit("%s ss must be 1..4" % flag)
    return k, kmul, sr


def _parse_region(box_s, k_s):
    try:
        box = [int(round(float(v))) for v in box_s.split(",")]
    except ValueError:
        raise SystemExit("--region needs x0,y0,x1,y1:K[,SS] (source pixels, colour count, optional ss)")
    if len(box) != 4 or not k_s:
        raise SystemExit("--region needs x0,y0,x1,y1:K[,SS]")
    k_spec = _parse_k(k_s, "--region")
    return {"box": (box[0], box[1], box[2], box[3]), "k": k_spec[0], "kmul": k_spec[1],
            "ss": k_spec[2], "mask": None}


def _parse_region_mask(path_s, k_s):
    from PIL import Image as _Image
    if not path_s or not k_s:
        raise SystemExit("--region-mask needs FILE:K[,SS]")
    mask = _Image.open(path_s).convert("L")
    k_spec = _parse_k(k_s, "--region-mask")
    return {"box": None, "k": k_spec[0], "kmul": k_spec[1], "ss": k_spec[2], "mask": mask}


def refine_elements(out, base_img, specs, rounds, turd, alphamax, sample_n, ss, K,
                    shared_centers, feather, base_centers, snap, snap_tol, W, H):
    """元素级迭代：渲染 -> 量每个元素的残差 -> 只给最差的那批加预算重描。

    只重描真正没画好的元素，所以每轮成本远低于整体重跑。
    """
    import subprocess
    ref_arr = np.asarray(base_img.convert("RGB"), dtype=np.float32)
    tmp_png = out + ".refine.png"
    for r in range(rounds):
        subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "render.py"),
                        out, tmp_png, str(W), str(H)], check=True, capture_output=True)
        cur = np.asarray(Image.open(tmp_png).convert("RGB"), dtype=np.float32)
        errs = []
        for spec in specs:
            m = np.asarray(spec["mask"], dtype=np.uint8) > 127
            errs.append(float(np.abs(cur[m] - ref_arr[m]).mean()) if m.sum() >= 64 else 0.0)
        order = list(np.argsort(np.asarray(errs))[::-1])
        worst = order[:max(1, len(order) // 4)]
        doc = Path(out).read_text(encoding="utf-8")
        add, changed = [], 0
        for idx in worst:
            if errs[idx] < 6.0:
                continue
            spec2 = dict(specs[idx])
            spec2["k"] = int(min(4096, max(16, spec["k"] * 2)))
            spec2["ss"] = int(min(3, int(specs[idx].get("ss") or ss) + 1))
            grp, used, kres = trace_region(base_img, spec2, turd, alphamax, sample_n=sample_n,
                                           step=11, ss_default=ss, global_k=K,
                                           centers=shared_centers, feather=feather,
                                           snap_to=(base_centers if snap else None), snap_tol=snap_tol)
            if grp:
                add.append(grp)
                specs[idx] = spec2
                changed += 1
        if not changed:
            break
        close = doc.rfind("</svg>")
        Path(out).write_text(doc[:close] + "".join(add) + doc[close:], encoding="utf-8")
        print("[vectorize] element refine round %d: %d/%d elements re-traced (worst err %.1f)"
              % (r + 1, changed, len(specs), errs[order[0]]), file=sys.stderr)


def auto_elements(image, colours=16, min_area=600, kmax=384, ss=1):
    """元素级预算：先把画面切成连通色块（元素），再按面积与内部复杂度给每块定色数。

    边界落在真实色块轮廓上，所以不会出现矩形接缝。
    """
    from scipy import ndimage
    from scipy.spatial import cKDTree
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    H, W = arr.shape[:2]
    px = arr.reshape(-1, 3)
    boxes = median_cut(_palette_sample(px, 200000), int(colours))
    cent = np.array([b.mean(0) for b in boxes], dtype=np.float32)
    lab = np.empty(len(px), np.int32)
    for i in range(0, len(px), 200000):
        chunk = px[i:i + 200000]
        lab[i:i + 200000] = cKDTree(cent).query(chunk, workers=-1)[1]
    lab = lab.reshape(H, W)
    specs = []
    total = float(W * H)
    for ci in range(len(cent)):
        cc, n = ndimage.label(lab == ci)
        if n == 0:
            continue
        sizes = ndimage.sum(np.ones_like(cc), cc, range(1, n + 1))
        for k in range(1, n + 1):
            if sizes[k - 1] < min_area:
                continue
            sel = cc == k
            vals = arr[sel]
            std = float(vals.std(0).max())
            share = float(sizes[k - 1]) / total
            ki = int(round(8 + kmax * min(1.0, (share ** 0.5) * (0.5 + std / 80.0))))
            ki = max(8, min(kmax, ki))
            mask = Image.fromarray((sel * 255).astype(np.uint8))
            specs.append({"box": None, "k": ki, "kmul": None, "ss": ss, "mask": mask})
    return specs


def shared_palette(image, k):
    """整幅图一份调色板：区域之间不再各算各的，边界不会出现色差方块。"""
    arr = np.asarray(image.convert("RGB"), dtype=np.float32).reshape(-1, 3)
    boxes = median_cut(_palette_sample(arr, 240000), int(k))
    return np.array([b.mean(0) for b in boxes], dtype=np.float32)


def _region_poly_mask(points, size):
    """不规则区域：多边形 -> 全分辨率 L 掩码。"""
    from PIL import Image as _Image
    from PIL import ImageDraw as _Draw
    m = _Image.new("L", size, 0)
    _Draw.Draw(m).polygon(points, fill=255)
    return m


def _parse_region_poly(points_s, k_s, size=None):
    pts = []
    for pair in points_s.replace(";", " ").split():
        x, y = pair.split(",")
        pts.append((float(x), float(y)))
    if len(pts) < 3:
        raise SystemExit("--region-poly needs at least 3 'x,y' points")
    k_spec = _parse_k(k_s, "--region-poly")
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    box = (int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1)
    return {"box": box, "k": k_spec[0], "kmul": k_spec[1], "ss": k_spec[2],
            "mask": None, "poly": pts}


def trace_region(base_img, spec, turd, alphamax, sample_n=240000, step=999, ss_default=1, global_k=0,
                 centers=None, feather=0, snap_to=None, snap_tol=10.0):
    """Trace one region at its own colour budget and (optional) own supersampling.

    spec = {"box": (x0, y0, x1, y1) or None, "k": K, "ss": ss or None,
            "mask": PIL mask (L, original resolution) or None}

    Returns (svg_group, pixels_used). Coordinates are kept in source pixels by
    wrapping the region paths in a translate group, so a region may use a
    different ss from the global pass without touching the global geometry.
    """
    from scipy.spatial import cKDTree
    W, H = base_img.size
    mask = spec.get("mask")
    box = spec.get("box")
    if mask is not None:
        arr = np.asarray(mask, dtype=np.uint8) > 127
        ys, xs = np.nonzero(arr)
        if len(xs) == 0:
            return "", 0, 0
        if box is None:
            box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    if box is None:
        return "", 0, 0
    x0 = max(0, min(W - 1, int(box[0]))); y0 = max(0, min(H - 1, int(box[1])))
    x1 = max(x0 + 1, min(W, int(box[2]))); y1 = max(y0 + 1, min(H, int(box[3])))
    sr = int(spec.get("ss") or ss_default)
    crop = base_img.crop((x0, y0, x1, y1))
    cw, ch = crop.size
    if sr != 1:
        crop = crop.resize((cw * sr, ch * sr), Image.LANCZOS)
    carr = np.asarray(crop, dtype=np.float32).reshape(-1, 3)
    if spec.get("kmul"):
        # 相对预算：区域色数 = 全局 K × 倍数，随预设/全局 K 一起缩放
        k = int(round(float(global_k) * float(spec["kmul"]))) if global_k else int(round(16 * float(spec["kmul"])))
        k = max(8, min(4096, k))
    else:
        k = int(spec["k"])
    if centers is None:
        boxes = median_cut(_palette_sample(carr, sample_n), k)
        centers = np.array([b.mean(0) for b in boxes], dtype=np.float32)
    else:
        centers = np.asarray(centers, dtype=np.float32)
    if len(centers) == 0:
        return "", 0, 0
    if snap_to is not None and len(snap_to):
        # 接缝修复：区域色若底图本来就能表达，就换成底图那个色，边界不跳色
        st = np.asarray(snap_to, dtype=np.float32)
        d2 = ((centers[:, None, :] - st[None, :, :]) ** 2).sum(2)
        near = d2.argmin(1)
        dist = np.sqrt(d2[np.arange(len(centers)), near])
        centers = centers.copy()
        m = dist <= float(snap_tol)
        centers[m] = st[near[m]]
    if snap_to is not None and len(snap_to):
        # 接缝修复：区域色若底图本来就能表达，就换成底图那个色，边界不跳色
        st = np.asarray(snap_to, dtype=np.float32)
        d2 = ((centers[:, None, :] - st[None, :, :]) ** 2).sum(2)
        near = d2.argmin(1)
        dist = np.sqrt(d2[np.arange(len(centers)), near])
        centers = centers.copy()
        m = dist <= float(snap_tol)
        centers[m] = st[near[m]]
    lab = cKDTree(centers).query(carr, workers=-1)[1].astype(np.int32).reshape(ch * sr, cw * sr)
    if mask is not None:
        m = mask.crop((x0, y0, x1, y1))
        if sr != 1:
            m = m.resize((cw * sr, ch * sr), Image.NEAREST)
        keep = np.asarray(m, dtype=np.uint8) > 127
    elif spec.get("poly"):
        m = _region_poly_mask(spec["poly"], base_img.size).crop((x0, y0, x1, y1))
        if sr != 1:
            m = m.resize((cw * sr, ch * sr), Image.NEAREST)
        keep = np.asarray(m, dtype=np.uint8) > 127
    else:
        keep = np.ones_like(lab, bool)
    if feather:
        try:
            from scipy.ndimage import binary_dilation
            keep = binary_dilation(keep, iterations=int(feather))
        except Exception:
            pass
    out, used = [], 0
    for ci in range(len(centers)):
        sub = (lab == ci) & keep
        n = int(sub.sum())
        if n < 8:
            continue
        d = native_path(sub, sr, turd, alphamax, 2)
        if d is None:
            bmp = potrace.Bitmap(~sub)
            tr = bmp.trace(turdsize=turd, alphamax=alphamax, opticurve=True, opttolerance=0.2)
            d = "".join(curv_d(cv, scale=sr) for cv in tr)
        if not d:
            continue
        col = "#%02x%02x%02x" % tuple(int(round(v)) for v in centers[ci])
        out.append('<path fill="%s" fill-rule="evenodd" data-step="%d" d="%s"/>' % (col, step, d))
        used += n
    if not out:
        return "", 0, k
    return ('<g transform="translate(%d %d)">%s</g>' % (x0, y0, "".join(out))), used, k


def _render_file(target, png, W, H, step=None):
    import subprocess
    cmd = [sys.executable, str(Path(__file__).resolve().parent / "render.py"),
           str(target), str(png), str(W), str(H)]
    if step is not None:
        cmd.append("?step=%d&v=%d" % (step, int(time.time())))
    subprocess.run(cmd, check=True, capture_output=True)


def _flat(path):
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "PA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.convert("RGBA").split()[3])
        return np.asarray(bg, dtype=np.int16)
    return np.asarray(im.convert("RGB"), dtype=np.int16)


def seal_output(out_path, ref_path, W, H):
    """--seal: fill thin inter-layer cracks, then report the measured effect."""
    import tempfile
    from seal_gaps import seal
    out_path = Path(out_path)
    doc = out_path.read_text(encoding="utf-8")
    m = re.search(r'<svg\b.*?</svg>', doc, re.S)
    if not m:
        print("[seal] no <svg> found, skipped", file=sys.stderr)
        return
    step = None
    if out_path.suffix.lower() in (".html", ".htm"):
        t = re.search(r'data-total="(\d+)"', doc)
        step = int(t.group(1)) if t else None
    tmp = Path(tempfile.mkdtemp(prefix="seal-"))
    before, after = tmp / "before.png", tmp / "after.png"
    _render_file(out_path, before, W, H, step)
    try:
        new_svg, stats = seal(m.group(0), str(before))
    except ValueError as exc:
        print("[seal] skipped: %s" % exc, file=sys.stderr)
        return
    out_path.write_text(doc[:m.start()] + new_svg + doc[m.end():], encoding="utf-8")
    _render_file(out_path, after, W, H, step)
    ref = _flat(ref_path)
    print(json.dumps({"seal": stats,
                      "mean_before": round(float(np.abs(_flat(before) - ref).mean()), 4),
                      "mean_after": round(float(np.abs(_flat(after) - ref).mean()), 4)}))


def main():
    raw_args = sys.argv[1:]
    full_palette = False
    seal_flag = False
    sample_n = 240000
    palette_sample_explicit = False
    edge_soften = 0.0
    alpha_soften = 0
    alpha_width = 1.0
    alpha_strength = None          # None = auto (0.25 blend, 0.7 reference-colour)
    alpha_ramp = None              # None = auto (on for blending, off for reference-colour)
    alpha_true_color = 0
    alpha_min_err = 0.0
    regions = []          # 局部重绘：给指定区域独立色数
    tile_spec = None      # --tile CxR:K[,SS]：整幅图平铺，不留低预算底图
    elements_spec = None  # --elements C:MIN:KMAX[,SS]：自动切元素，按元素给预算
    elements_refine = 0   # --elements-refine R：按元素残差再迭代 R 轮
    element_specs = None
    palette_shared = None # --palette-shared[=K]：所有区域共用一份调色板，消除接缝色差
    region_feather = -1   # --region-feather N：区域掩码外扩 N 像素（有区域时默认 2）
    region_snap = True    # 区域颜色向底图调色板靠拢，消除矩形接缝
    region_snap_tol = 10.0
    alpha_refine = 0
    alpha_tol = 1.0
    compact_flag = False
    compact_precision = 6
    process_final = False
    process_palette = None
    process_line_direction = ""
    process_from_front = True
    process_from_front_explicit = False
    process_full_palette = False
    process_full_palette_explicit = False
    process_palette_sample = None
    process_palette_max = 1024
    process_options = {}
    args = []
    i = 0
    while i < len(raw_args):
        a = raw_args[i]
        nxt = raw_args[i + 1] if i + 1 < len(raw_args) else None
        if a in ("--full-palette", "--seal"):
            if a == "--full-palette":
                full_palette = True
            else:
                seal_flag = True
        elif a.startswith("--palette-sample="):
            sample_n = int(a.split("=", 1)[1])
            palette_sample_explicit = True
        elif a == "--process-final":
            process_final = True
        elif a == "--process-from-front":
            process_from_front = True
            process_from_front_explicit = True
        elif a == "--no-process-from-front":
            process_from_front = False
            process_from_front_explicit = True
        elif a.startswith("--process-palette="):
            process_palette = int(a.split("=", 1)[1])
        elif a == "--process-palette":
            if nxt is None:
                raise SystemExit("--process-palette needs a value")
            process_palette = int(nxt); i += 1
        elif a == "--process-full-palette":
            process_full_palette = True
            process_full_palette_explicit = True
        elif a == "--no-process-full-palette":
            process_full_palette = False
            process_full_palette_explicit = True
        elif a.startswith("--process-palette-sample="):
            process_palette_sample = int(a.split("=", 1)[1])
        elif a == "--process-palette-sample":
            if nxt is None:
                raise SystemExit("--process-palette-sample needs a value")
            process_palette_sample = int(nxt); i += 1
        elif a.startswith("--process-palette-max="):
            process_palette_max = int(a.split("=", 1)[1])
        elif a == "--process-palette-max":
            if nxt is None:
                raise SystemExit("--process-palette-max needs a value")
            process_palette_max = int(nxt); i += 1
        elif a.startswith("--process-line-direction="):
            process_line_direction = a.split("=", 1)[1]
        elif a == "--process-line-direction":
            if nxt is None:
                raise SystemExit("--process-line-direction needs a value")
            process_line_direction = nxt; i += 1
        elif a == "--process-base-scale" or a.startswith("--process-base-scale="):
            text, used_next = _flag_value(a, nxt, "--process-base-scale")
            process_options["base_scale"] = float(text)
            if used_next:
                i += 1
        elif a == "--process-mid-scale" or a.startswith("--process-mid-scale="):
            text, used_next = _flag_value(a, nxt, "--process-mid-scale")
            process_options["mid_scale"] = float(text)
            if used_next:
                i += 1
        elif a == "--process-detail-scale" or a.startswith("--process-detail-scale="):
            text, used_next = _flag_value(a, nxt, "--process-detail-scale")
            process_options["detail_scale"] = float(text)
            if used_next:
                i += 1
        elif a == "--process-min-areas" or a.startswith("--process-min-areas="):
            text, used_next = _flag_value(a, nxt, "--process-min-areas")
            vals = _csv_floats(text, 3, "--process-min-areas")
            process_options.update(base_min_area=vals[0], mid_min_area=vals[1], detail_min_area=vals[2])
            if used_next:
                i += 1
        elif a == "--process-blurs" or a.startswith("--process-blurs="):
            text, used_next = _flag_value(a, nxt, "--process-blurs")
            vals = _csv_floats(text, 3, "--process-blurs")
            process_options.update(base_blur=vals[0], mid_blur=vals[1], detail_blur=vals[2])
            if used_next:
                i += 1
        elif a == "--process-focus-box" or a.startswith("--process-focus-box="):
            text, used_next = _flag_value(a, nxt, "--process-focus-box")
            process_options["focus_box"] = _csv_floats(text, 4, "--process-focus-box")
            if used_next:
                i += 1
        elif a == "--process-focus" or a.startswith("--process-focus="):
            text, used_next = _flag_value(a, nxt, "--process-focus")
            vals = _csv_floats(text, 4, "--process-focus")
            process_options.update(focus_scale=vals[0], focus_min_area=vals[1],
                                   focus_blur=vals[2], focus_dil=vals[3])
            if used_next:
                i += 1
        elif a == "--process-dil" or a.startswith("--process-dil="):
            text, used_next = _flag_value(a, nxt, "--process-dil")
            process_options["dil"] = int(text)
            if used_next:
                i += 1
        elif a == "--process-turd" or a.startswith("--process-turd="):
            text, used_next = _flag_value(a, nxt, "--process-turd")
            process_options["turd"] = int(text)
            if used_next:
                i += 1
        elif a == "--process-alphamax" or a.startswith("--process-alphamax="):
            text, used_next = _flag_value(a, nxt, "--process-alphamax")
            process_options["alphamax"] = float(text)
            if used_next:
                i += 1
        elif a == "--process-ss" or a.startswith("--process-ss="):
            text, used_next = _flag_value(a, nxt, "--process-ss")
            process_options["ss"] = float(text)
            if used_next:
                i += 1
        elif a.startswith("--edge-soften="):
            edge_soften = float(a.split("=", 1)[1])
        elif a == "--edge-soften":
            if nxt is None:
                raise SystemExit("--edge-soften needs a value")
            edge_soften = float(nxt); i += 1
        elif a.startswith("--alpha-soften="):
            alpha_soften = int(a.split("=", 1)[1])
        elif a == "--alpha-soften":
            if nxt is None:
                raise SystemExit("--alpha-soften needs a value")
            alpha_soften = int(nxt); i += 1
        elif a.startswith("--alpha-strength="):
            alpha_strength = float(a.split("=", 1)[1])
        elif a == "--alpha-strength":
            if nxt is None:
                raise SystemExit("--alpha-strength needs a value")
            alpha_strength = float(nxt); i += 1
        elif a.startswith("--alpha-true-color="):
            alpha_true_color = int(a.split("=", 1)[1])
            if alpha_true_color < 2:
                raise SystemExit("--alpha-true-color must be >= 2")
        elif a == "--alpha-true-color":
            alpha_true_color = 3
        elif a.startswith("--region="):
            txt = a.split("=", 1)[1]
            box_s, _, k_s = txt.partition(":")
            regions.append(_parse_region(box_s, k_s))
        elif a == "--region":
            if nxt is None:
                raise SystemExit("--region needs x0,y0,x1,y1:K[,SS]")
            box_s, _, k_s = nxt.partition(":")
            regions.append(_parse_region(box_s, k_s))
            i += 1
        elif a.startswith("--elements-refine="):
            elements_refine = int(a.split("=", 1)[1])
        elif a == "--elements-refine":
            if nxt is None:
                raise SystemExit("--elements-refine needs a round count")
            elements_refine = int(nxt)
            i += 1
        elif a.startswith("--elements="):
            elements_spec = a.split("=", 1)[1]
        elif a == "--elements":
            if nxt is None:
                raise SystemExit("--elements needs COLOURS:MINAREA:KMAX[,SS]")
            elements_spec = nxt
            i += 1
        elif a.startswith("--region-poly="):
            txt = a.split("=", 1)[1]
            pts_s, _, k_s = txt.rpartition(":")
            regions.append(_parse_region_poly(pts_s, k_s))
        elif a == "--region-poly":
            if nxt is None:
                raise SystemExit("--region-poly needs \"x,y x,y ...\":K[,SS]")
            pts_s, _, k_s = nxt.rpartition(":")
            regions.append(_parse_region_poly(pts_s, k_s))
            i += 1
        elif a.startswith("--tile="):
            tile_spec = a.split("=", 1)[1]
        elif a == "--tile":
            if nxt is None:
                raise SystemExit("--tile needs CxR:K[,SS]")
            tile_spec = nxt
            i += 1
        elif a.startswith("--palette-shared="):
            palette_shared = int(a.split("=", 1)[1])
        elif a == "--palette-shared":
            palette_shared = 0        # 0 = 取区域里最大的 K
        elif a == "--no-region-snap":
            region_snap = False
        elif a.startswith("--region-snap-tol="):
            region_snap_tol = float(a.split("=", 1)[1])
        elif a == "--region-snap-tol":
            if nxt is None:
                raise SystemExit("--region-snap-tol needs a value")
            region_snap_tol = float(nxt)
            i += 1
        elif a.startswith("--region-feather="):
            region_feather = int(a.split("=", 1)[1])
        elif a == "--region-feather":
            if nxt is None:
                raise SystemExit("--region-feather needs a pixel count")
            region_feather = int(nxt)
            i += 1
        elif a.startswith("--region-mask="):
            # Windows 盘符里也有冒号，必须从右边切
            m_s, _, k_s = a.split("=", 1)[1].rpartition(":")
            regions.append(_parse_region_mask(m_s, k_s))
        elif a == "--region-mask":
            if nxt is None:
                raise SystemExit("--region-mask needs FILE:K[,SS]")
            m_s, _, k_s = nxt.rpartition(":")
            regions.append(_parse_region_mask(m_s, k_s))
            i += 1
        elif a.startswith("--alpha-refine="):
            alpha_refine = int(a.split("=", 1)[1])
        elif a == "--alpha-refine":
            if nxt is None:
                raise SystemExit("--alpha-refine needs a value")
            alpha_refine = int(nxt); i += 1
        elif a.startswith("--alpha-tol="):
            alpha_tol = float(a.split("=", 1)[1])
        elif a == "--alpha-tol":
            if nxt is None:
                raise SystemExit("--alpha-tol needs a value")
            alpha_tol = float(nxt); i += 1
        elif a.startswith("--alpha-min-err="):
            alpha_min_err = float(a.split("=", 1)[1])
        elif a == "--alpha-min-err":
            if nxt is None:
                raise SystemExit("--alpha-min-err needs a value")
            alpha_min_err = float(nxt); i += 1
        elif a == "--alpha-ramp":
            alpha_ramp = True
        elif a == "--no-alpha-ramp":
            alpha_ramp = False
        elif a.startswith("--alpha-width="):
            alpha_width = float(a.split("=", 1)[1])
        elif a == "--alpha-width":
            if nxt is None:
                raise SystemExit("--alpha-width needs a value")
            alpha_width = float(nxt); i += 1
        elif a == "--compact":
            compact_flag = True
        elif a.startswith("--compact-precision="):
            compact_precision = int(a.split("=", 1)[1])
        elif a == "--compact-precision":
            if nxt is None:
                raise SystemExit("--compact-precision needs a value")
            compact_precision = int(nxt); i += 1
        else:
            args.append(a)
        i += 1
    if edge_soften < 0:
        raise SystemExit("--edge-soften must be >= 0")
    if alpha_soften < 0 or alpha_soften > 8:
        raise SystemExit("--alpha-soften must be 0..8")
    if alpha_width <= 0:
        raise SystemExit("--alpha-width must be > 0")
    if alpha_strength is None:
        alpha_strength = 0.7 if alpha_true_color else 0.25
    if alpha_ramp is None:
        # 混合模式只在缓变区补环更划算（每千条边代价 0.046 vs 0.082）；
        # 真实局部色模式则相反——它靠补中间色降误差，全边界覆盖才够
        alpha_ramp = (alpha_true_color == 0)
    if not (0 < alpha_strength <= 1):
        raise SystemExit("--alpha-strength must be in (0, 1]")
    if alpha_soften and edge_soften:
        raise SystemExit("--alpha-soften and --edge-soften both soften edges; pick one")
    if process_options and not process_final:
        print("[vectorize] process options are ignored without --process-final", file=sys.stderr)
    if (process_full_palette_explicit or process_palette_sample is not None
            or process_palette_max != 1024) and not process_final:
        print("[vectorize] process palette options are ignored without --process-final", file=sys.stderr)
    ref, out = args[0], args[1]
    K = int(args[2]) if len(args) > 2 else 128
    if len(args) <= 2 and not process_final:
        print("[vectorize] no K given, using default K=128; pass 256-384 for final output", flush=True)
    turd = int(args[3]) if len(args) > 3 else 6
    alphamax = float(args[4]) if len(args) > 4 else 1.0
    dil = int(args[5]) if len(args) > 5 else 1        # 掩码膨胀像素数：消除层间白缝
    blur = float(args[6]) if len(args) > 6 else 0.0      # 先轻微高斯模糊：边界更干净，曲线更顺
    ss = int(args[7]) if len(args) > 7 else 1          # 超采样：2~3 倍放大再描，曲线更顺、边界更准
    sk_thr = float(args[8]) if len(args) > 8 else 0.0   # 线稿层：边缘梯度阈值，0=不要线稿
    dark_k = int(args[9]) if len(args) > 9 else 0      # 分层色板：暗区(L<80)单独分配多少色，0=不分层
    med = int(args[10]) if len(args) > 10 else 0         # 中值滤波半径(3/5)：压掉边缘暗碎块，0=关

    if process_from_front and process_final:
        # Enabled by default. blur stays per-layer (1.0/0.5/0.18);
        # sk_thr/dark_k/med have no process equivalent.
        if "turd" not in process_options:
            process_options["turd"] = turd
        if "alphamax" not in process_options:
            process_options["alphamax"] = alphamax
        if "dil" not in process_options:
            process_options["dil"] = dil
        if "focus_dil" not in process_options:
            process_options["focus_dil"] = dil
        if "ss" not in process_options and not any(k.endswith("_scale") for k in process_options):
            # Explicit --process-*-scale already chooses the working resolution;
            # do not multiply it by the front ss as well.
            process_options["ss"] = ss
        if not process_full_palette_explicit:
            process_full_palette = full_palette
        if process_palette_sample is None and palette_sample_explicit:
            process_palette_sample = sample_n
    elif process_from_front_explicit and not process_final:
        print("[vectorize] --process-from-front is ignored without --process-final", file=sys.stderr)

    if process_final:
        import paint_process
        if seal_flag:
            print("[vectorize] --seal is ignored with --process-final", file=sys.stderr)
        if full_palette and not process_full_palette:
            print("[vectorize] --full-palette is not applied with --process-final; "
                  "use --process-full-palette", file=sys.stderr)
        if palette_sample_explicit and process_palette_sample is None:
            print("[vectorize] --palette-sample is not applied with --process-final; "
                  "use --process-palette-sample=N", file=sys.stderr)
        if process_palette is None:
            process_palette = K if len(args) > 2 else 256
        if process_palette_max < 8:
            raise SystemExit("--process-palette-max must be >= 8")
        if not (0 <= process_palette <= process_palette_max):
            raise SystemExit("--process-palette must be 0..%d (raise --process-palette-max to allow more)"
                             % process_palette_max)
        palette_sample_n = process_palette_sample if process_palette_sample is not None else 80000
        if palette_sample_n < 0:
            raise SystemExit("--process-palette-sample must be >= 0")
        img = paint_process.load_reference(ref, None, 2000000)
        W, H = img.size
        palette = paint_process.make_global_palette(img, process_palette,
                                                    full=process_full_palette,
                                                    sample_n=palette_sample_n,
                                                    max_colors=process_palette_max)
        palette_full = bool(palette.get("full")) if palette else False
        palette_sample_pixels = int(palette.get("sample_pixels", 0)) if palette else 0
        line_dir = paint_process.load_line_direction(process_line_direction)
        try:
            options_info = paint_process.normalize_process_options(process_options)
        except ValueError as exc:
            raise SystemExit(str(exc))
        svg = paint_process.build_final_static_svg(
            img, palette, line_dir,
            title="过程模式最终画面",
            edge_soften=edge_soften,
            options=process_options,
        )
        if regions:
            # 过程分支同样支持区域重绘：把区域层追加到过程画面之上（静态最终图）
            extra = []
            for spec in regions:
                grp, used_r, k_res = trace_region(img, spec, 4, 1.0, sample_n=120000, step=99, ss_default=1,
                                                  global_k=K)
                if not grp:
                    print("[vectorize] region %s produced nothing" % (spec.get("box"),), file=sys.stderr)
                    continue
                extra.append(grp)
                print("[vectorize] process region box=%s K=%s(=%d) ss=%s -> %d px"
                      % (spec.get("box"), _k_label(spec), k_res, spec.get("ss"), used_r), file=sys.stderr)
            if extra:
                close = svg.rfind("</svg>")
                if close > 0:
                    svg = svg[:close] + "".join(extra) + svg[close:]
        if compact_flag:
            svg = svg_compact.compact_svg(svg, compact_precision)
        paths = svg.count("<path")
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() in (".html", ".htm"):
            html = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                    "<title>过程模式最终画面</title><style>html,body{margin:0;background:#fff}"
                    "svg{display:block}</style></head><body>" + svg + "</body></html>")
            out_path.write_text(html, encoding="utf-8")
            info = {"out": out, "preview": out, "W": W, "H": H, "mode": "process-final",
                    "palette": process_palette, "palette_full": palette_full,
                    "palette_sample": palette_sample_pixels,
                    "palette_max": process_palette_max,
                    "paths": paths, "compact": compact_flag,
                    "from_front": process_from_front, "options": options_info,
                    "kb": round(len(html.encode("utf-8")) / 1024, 1)}
        else:
            doc = '<?xml version="1.0" encoding="UTF-8"?>\n' + svg + "\n"
            out_path.write_text(doc, encoding="utf-8")
            info = {"out": out, "W": W, "H": H, "mode": "process-final",
                    "palette": process_palette, "palette_full": palette_full,
                    "palette_sample": palette_sample_pixels,
                    "palette_max": process_palette_max,
                    "paths": paths, "compact": compact_flag,
                    "from_front": process_from_front, "options": options_info,
                    "layers": svg.count('<g id="layer-'),
                    "kb": round(len(doc.encode("utf-8")) / 1024, 1)}
        print(json.dumps(info, ensure_ascii=False))
        return

    raw = Image.open(ref)
    alpha_mask = None
    if raw.mode in ("RGBA", "LA", "PA"):
        a = raw.convert("RGBA").split()[3]
        if a.getextrema()[0] < 128:          # 含真透明像素：输出背景透明，不铺底色 rect
            alpha_mask = a
    img = raw.convert("RGB")
    if blur > 0:
        from PIL import ImageFilter
        img = img.filter(ImageFilter.GaussianBlur(blur))
    W, H = img.size
    base_img = img                      # 原分辨率副本：区域重绘用它，可按自己的 ss 采样

    if tile_spec:
        # 整幅平铺：不留"低预算底图"，接缝只出现在同样的高预算区域之间
        grid_s, k_s = tile_spec.rpartition(":")[0::2]
        cols, rows = [int(v) for v in grid_s.lower().split("x")]
        for gy in range(rows):
            for gx in range(cols):
                spec = _parse_region("%d,%d,%d,%d" % (
                    round(W * gx / cols), round(H * gy / rows),
                    round(W * (gx + 1) / cols), round(H * (gy + 1) / rows)), k_s)
                regions.append(spec)
    if elements_spec:
        _es, _, _ss_s = elements_spec.rpartition(",")
        if not _es:
            _es = elements_spec; _ss_s = ""
        _cols, _min, _kmax = [int(x) for x in _es.split(":")]
        _ess = int(_ss_s) if _ss_s.strip().isdigit() else 1
        _els = auto_elements(base_img, _cols, _min, _kmax, _ess)
        print("[vectorize] elements=%d (colours=%d min=%d kmax=%d ss=%d)"
              % (len(_els), _cols, _min, _kmax, _ess), file=sys.stderr)
        regions.extend(_els)
        element_specs = _els
    if regions and region_feather < 0:
        region_feather = 2
    if region_feather < 0:
        region_feather = 0
    shared_centers = None
    if palette_shared is not None and regions:
        _kmax = 0
        for s in regions:
            _kmax = max(_kmax, int(s["k"]) if not s.get("kmul") else int(round(K * float(s["kmul"]))))
        shared_centers = shared_palette(base_img, palette_shared or _kmax)
    if ss > 1:
        img = img.resize((W * ss, H * ss), Image.LANCZOS)
    if med >= 3:
        from PIL import ImageFilter
        img = img.filter(ImageFilter.MedianFilter(med))
    arr = np.asarray(img, dtype=np.float32).reshape(-1, 3)
    if alpha_mask is not None:
        am = alpha_mask.resize(img.size, Image.LANCZOS)
        subject = np.asarray(am, dtype=np.uint8).reshape(-1) >= 128
        if not subject.any():
            subject = None
    else:
        subject = None
    def nearest(px, cent):
        # cKDTree is exact and avoids the O(N*K) Python chunk loop. K=384 with
        # 3x supersampling used to spend all its time here, not in the DLL.
        from scipy.spatial import cKDTree
        return cKDTree(np.asarray(cent, dtype=np.float32)).query(
            np.asarray(px, dtype=np.float32), workers=-1)[1].astype(np.int32)

    usable = subject if subject is not None else np.ones(len(arr), bool)

    def make_centers(px, k):
        k = int(k)
        if k <= 0 or len(px) == 0:
            return np.zeros((0, 3), np.float32)
        if full_palette:
            native = palette_native(px, k)
            if native is not None:
                return native.astype(np.float32)
            print("[vectorize] native palette unavailable; full-pixel Python median_cut may be very slow", file=sys.stderr)
            boxes = median_cut(px, k)
        else:
            boxes = median_cut(_palette_sample(px, sample_n), k)
        return np.array([b.mean(0) for b in boxes], dtype=np.float32)

    if full_palette:
        print("[vectorize] palette mode=full pixels=%d sample=%s" % (int(usable.sum()), "native" if load_palette_native() else "python"), flush=True)
    else:
        print("[vectorize] palette mode=sample pixels=%d sample=%d" % (int(usable.sum()), min(sample_n, int(usable.sum()))), flush=True)
    if dark_k > 0:
        # 分层色板：暗区单独切，避免渐变暗部被亮区的中心"吃掉"色阶
        lum = arr.mean(1)
        dmask = usable & (lum < 80)
        cd = make_centers(arr[dmask], dark_k)
        bright = usable & ~dmask
        cb = make_centers(arr[bright], max(1, K - dark_k))
        cent = np.vstack([cd, cb]).astype(np.float32)
        lab = np.empty(len(arr), np.int32)
        lab[dmask] = nearest(arr[dmask], cd) if len(cd) else 0
        lab[bright] = nearest(arr[bright], cb) + len(cd)
    else:
        cent = make_centers(arr[usable], K)
        lab = nearest(arr, cent)
    base_centers = np.asarray(cent, dtype=np.float32)
    print("[vectorize] palette assigned K=%d ss=%d pixels=%d" % (K, ss, len(lab)), flush=True)
    if subject is not None:
        lab[~subject] = -1                   # 透明像素不属于任何色类，不参与描摹
    lab = lab.reshape(H * ss, W * ss)
    # 空类（调色板里没有被任何像素选中的中心）必须提前剔掉，否则面积加权会除零
    areas = [(int((lab == c).sum()), c) for c in range(len(cent))]
    areas = [ac for ac in areas if ac[0] > 0]
    # 底色取"画面边框最多的颜色"（通常是天空/背景），而不是面积最大的类：
    # 面积最大的类往往是深色主体，一旦有细小层被 turdsize 丢掉，就会露出黑块（边缘发黑就是这么来的）
    bpx = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    vals, cnts = np.unique(bpx, return_counts=True)
    bgc = int(vals[cnts.argmax()])
    # 语义分步：背景族最先，其余按色族平均亮度从暗到亮（暗部 → 中调 → 高光），族内按面积降序。
    # 只按面积排会得到"色块堆叠"；按亮度排才像一幅画被逐层铺出来的顺序。
    fam = kmeans(cent, 8)
    fam_area, fam_lum = {}, {}
    for ar, c in areas:
        k = int(fam[c])
        fam_area[k] = fam_area.get(k, 0) + ar
        fam_lum[k] = (fam_lum.get(k, 0.0) * max(0, fam_area[k] - ar) + float(cent[c].mean()) * ar) / max(1, fam_area[k])
    fam_bg = int(fam[bgc])
    fam_order = [fam_bg] + sorted([k for k in fam_area if k != fam_bg], key=lambda k: fam_lum[k])
    areas.sort(key=lambda ac: (fam_order.index(int(fam[ac[1]])), -ac[0]))
    MAXSTEP = 11
    n_layers = max(1, len([1 for a, _ in areas if a >= 8]))
    print("[vectorize] %d colors, %d layers, starting trace at ss=%d" % (len(cent), len(areas), ss), flush=True)
    _t0 = time.time()
    paths = []
    if subject is None:
        paths.append('<rect width="%d" height="%d" fill="#%02x%02x%02x" data-step="1"/>'
                     % (W, H, *(int(round(v)) for v in cent[bgc])))
    for rank, (area, c) in enumerate(areas):
        if rank and rank % 32 == 0:
            print("[vectorize] traced %d/%d colors, %.1fs" % (rank, len(areas), time.time() - _t0), flush=True)
        if area < 8: continue
        mask = (lab == c)
        if dil > 0:
            from scipy.ndimage import binary_dilation
            mask = binary_dilation(mask, iterations=dil)
        d = native_path(mask, ss, turd, alphamax, 2)
        if d is None:
            # potracer 约定：True = 白底，它描的是 0 值区域，所以前景要按位取反
            bmp = potrace.Bitmap(~mask)
            tr = bmp.trace(turdsize=turd, alphamax=alphamax, opticurve=True, opttolerance=0.2)
            d = "".join(curv_d(cv, scale=ss) for cv in tr)
        col = "#%02x%02x%02x" % tuple(int(round(v)) for v in cent[c])
        step = 1 + int(round(min(rank, n_layers - 1) * (MAXSTEP - 1) / max(1, n_layers - 1)))
        if d:
            paths.append('<path fill="%s" fill-rule="evenodd" data-step="%d" d="%s"/>' % (col, step, d))
    for spec in regions:
        # 局部重绘：把指定区域用独立的色数（和可选的独立 ss）重描一遍，覆盖在全局层之上。
        # 人像的脸/手值得高色数高分辨率，背景不值得——这就是"区域预算"。
        grp, used_r, k_res = trace_region(base_img, spec, turd, alphamax, sample_n=sample_n, step=MAXSTEP,
                                          ss_default=ss, global_k=K,
                                          centers=shared_centers, feather=region_feather,
                                          snap_to=(base_centers if (region_snap and regions) else None),
                                          snap_tol=region_snap_tol)
        if not grp:
            print("[vectorize] region %s produced nothing" % (spec.get("box"),), file=sys.stderr)
            continue
        paths.append(grp)
        print("[vectorize] region box=%s K=%s(=%d) ss=%s -> %d px"
              % (spec.get("box"), _k_label(spec), k_res, spec.get("ss"), used_r), file=sys.stderr)

    if alpha_soften > 0:
        # 几何式边界软化：在每个色带外侧补 alpha 递减的环，让相邻色带之间
        # 从"1 级跳变"变成"N 级缓变"。不用渐变、不用滤镜，纯 <path> + fill-opacity。
        from scipy.ndimage import binary_dilation
        ring_step = max(1, int(round(alpha_width * ss)))
        valid = (lab >= 0)
        px_all = np.asarray(img, dtype=np.float32).reshape(-1, 3)
        if alpha_min_err > 0:
            # 只修"平涂确实画错了"的地方：该像素当前误差小于阈值就别动它，
            # 平坦区块上补环是纯损失（实测 fine-grad 0-6 区间净亏 0.24-0.57）
            lab_safe = np.where(lab < 0, 0, lab)
            band_rgb = cent[lab_safe]
            cur_err = np.abs(px_all - band_rgb.reshape(-1, 3)).max(axis=1)
            valid = valid & (cur_err >= alpha_min_err).reshape(lab.shape)
        if alpha_ramp:
            # 只软"该软的地方"：参考图在这条边界上本来就是一段缓变（5x5 邻域的
            # 色差中等、细梯度很小）。硬边（1px 跳变）和平坦色块都不补环。
            from scipy.ndimage import maximum_filter, minimum_filter
            gl = np.asarray(img.convert("L"), dtype=np.float32)
            lo = minimum_filter(gl, size=5)
            hi = maximum_filter(gl, size=5)
            span = hi - lo
            gx = np.zeros_like(gl); gy = np.zeros_like(gl)
            gx[:, 1:-1] = np.abs(gl[:, 2:] - gl[:, :-2])
            gy[1:-1, :] = np.abs(gl[2:, :] - gl[:-2, :])
            fine = gx + gy
            valid = valid & (span >= 6) & (span <= 72) & (fine <= 14)
        def build_rings(valid_mask):
            ring_paths = []
            for area, c in areas:
                if area < 8:
                    continue
                base = (lab == c)
                prev = base
                col = "#%02x%02x%02x" % tuple(int(round(v)) for v in cent[c])
                for k in range(1, alpha_soften + 1):
                    cur = binary_dilation(prev, iterations=ring_step)
                    ring = cur & ~prev & valid_mask
                    prev = cur
                    if int(ring.sum()) < 8:
                        continue
                    d = native_path(ring, ss, turd, alphamax, 2)
                    if d is None:
                        bmp = potrace.Bitmap(~ring)
                        tr = bmp.trace(turdsize=turd, alphamax=alphamax, opticurve=True, opttolerance=0.2)
                        d = "".join(curv_d(cv, scale=ss) for cv in tr)
                    if not d:
                        continue
                    if alpha_true_color:
                        # 环按参考亮度再切 S 段，每段用该段真实均值色：等于在该边界
                        # 临时提高色阶密度，这才可能把 mean 压下去（不是靠 alpha 混合）
                        idx = np.flatnonzero(ring.reshape(-1))
                        ring_lum = px_all[idx].mean(axis=1)
                        edges_q = np.quantile(ring_lum, np.linspace(0, 1, alpha_true_color + 1))
                        for j in range(alpha_true_color):
                            sel = (ring_lum >= edges_q[j]) & (
                                ring_lum <= edges_q[j + 1] if j == alpha_true_color - 1
                                else ring_lum < edges_q[j + 1])
                            if int(sel.sum()) < 8:
                                continue
                            chosen = idx[sel]
                            rc = px_all[chosen].mean(axis=0)
                            flat = np.zeros(ring.size, bool)
                            flat[chosen] = True
                            sub = flat.reshape(ring.shape)
                            d2 = native_path(sub, ss, turd, alphamax, 2)
                            if d2 is None:
                                tr2 = potrace.Bitmap(~sub).trace(turdsize=turd, alphamax=alphamax,
                                                                 opticurve=True, opttolerance=0.2)
                                d2 = "".join(curv_d(cv, scale=ss) for cv in tr2)
                            if d2:
                                ring_paths.append('<path fill="#%02x%02x%02x" fill-opacity="%.3f" data-step="0" d="%s"/>'
                                                  % (int(round(rc[0])), int(round(rc[1])), int(round(rc[2])),
                                                     alpha_strength, d2))
                    else:
                        ring_paths.append('<path fill="%s" fill-opacity="%.3f" data-step="0" d="%s"/>'
                                          % (col, alpha_strength * (0.5 ** (k - 1)), d))
            return ring_paths

        if alpha_refine > 0:
            # 先按全边界生成、渲染、再看红绿图：把"加了反而更差"的像素剔掉，
            # 迭代 alpha_refine 次。这是把人工调参的那一步自动化。
            import tempfile
            ref_arr = _flat(ref)
            tmpdir = Path(tempfile.mkdtemp(prefix="alpha-refine-"))

            def _render(ps, tag):
                doc = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">%s</svg>'
                       % (W, H, W, H, "".join(ps)))
                hp = tmpdir / (tag + ".html")
                hp.write_text('<!doctype html><meta charset="utf-8">'
                              '<style>html,body{margin:0;background:#fff}</style>' + doc, encoding="utf-8")
                pp = tmpdir / (tag + ".png")
                _render_file(hp, pp, W, H)
                return _flat(str(pp))

            err_base = np.abs(_render(paths, "base") - ref_arr).max(axis=2)
            cur_valid = valid
            for it in range(alpha_refine):
                cand = paths + build_rings(cur_valid)
                err_cur = np.abs(_render(cand, "it%d" % it) - ref_arr).max(axis=2)
                from scipy.ndimage import uniform_filter
                # 用 3x3 局部均值比较：软化本来就是把误差摊开，逐像素比较会把所有环都判死
                worse = (uniform_filter(err_cur.astype(np.float32), 3)
                         > uniform_filter(err_base.astype(np.float32), 3) + alpha_tol).astype(np.uint8) * 255
                worse = np.asarray(Image.fromarray(worse).resize(lab.shape[::-1], Image.NEAREST)) > 127
                new_valid = cur_valid & ~worse
                dropped = int(cur_valid.sum() - new_valid.sum())
                print("[vectorize] alpha-refine pass %d: dropped %d px" % (it + 1, dropped), file=sys.stderr)
                if dropped == 0:
                    break
                cur_valid = new_valid
            ring_paths = build_rings(cur_valid)
        else:
            ring_paths = build_rings(valid)
        paths.extend(ring_paths)
        print("[vectorize] alpha-soften: %d ring(s) of %d mask px -> %d paths"
              % (alpha_soften, ring_step, len(ring_paths)), file=sys.stderr)

    sketch = ""
    if sk_thr > 0:
        g = np.asarray(img.convert("L"), dtype=np.float32)
        gx = np.zeros_like(g); gy = np.zeros_like(g)
        gx[:, 1:-1] = np.abs(g[:, 2:] - g[:, :-2]); gy[1:-1, :] = np.abs(g[2:, :] - g[:-2, :])
        edge = ((gx + gy) > sk_thr)
        sk_d = native_path(edge, ss, 6, 1.0, 2)
        if sk_d is None:
            sk = []
            for cv in potrace.Bitmap(~edge).trace(turdsize=6, alphamax=1.0, opticurve=True, opttolerance=0.2):
                sk.append(curv_d(cv, scale=ss))
            sk_d = "".join(sk)
        if sk_d:
            sketch = ('<path fill="#33445c" fill-opacity="0.5" data-step="0" d="%s"/>' % sk_d)
    svg = ('<svg id="art" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
           'data-total="%d">%s%s</svg>') % (W, H, W, H, MAXSTEP, sketch, "".join(paths))
    if edge_soften > 0:
        print("[vectorize] --edge-soften is legacy (blur filter); --alpha-soften keeps geometry "
              "and is preferred for deliverables", file=sys.stderr)
        if seal_flag:
            print("[vectorize] --seal is skipped when --edge-soften is set; "
                  "softening must be the final pass", file=sys.stderr)
            seal_flag = False
        svg = svg_postprocess.soften_svg(svg, edge_soften)
    if compact_flag:
        svg = svg_compact.compact_svg(svg, compact_precision)
    if out.lower().endswith(".svg"):
        # A static SVG has no player: data-step / data-total belong to the HTML
        # playback branch only and must not leak into the deliverable.
        clean = re.sub(r'\sdata-(?:step|total)="\d+"', "", svg)
        doc = '<?xml version="1.0" encoding="UTF-8"?>\n' + clean + '\n'
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(doc)
        if elements_refine > 0 and element_specs and not compact_flag and edge_soften == 0 and alpha_soften == 0:
            refine_elements(out, base_img, element_specs, elements_refine, turd, alphamax,
                            sample_n, ss, K, shared_centers, region_feather,
                            base_centers, region_snap, region_snap_tol, W, H)
            doc = open(out, encoding="utf-8").read()
        if seal_flag:
            seal_output(out, ref, W, H)
        print(json.dumps({"out": out, "W": W, "H": H, "K": K, "layers": len(paths),
                          "compact": compact_flag,
                          "kb": round(len(doc.encode("utf-8")) / 1024, 1)}))
        return
    html = """<!doctype html><html><head><meta charset="utf-8"><title>vector redraw</title>
<style>html,body{margin:0;background:#fff}#art{display:block}
#art [data-step]{display:none}#art [data-step].on{display:inline}
#bar{position:fixed;right:8px;bottom:8px;font:12px/1.6 system-ui;background:#000000b0;color:#fff;
padding:6px 10px;border-radius:6px;cursor:pointer;user-select:none}</style></head><body>
__SVG__<div id="bar">step <span id="n"></span>/<span id="t"></span></div>
<script>
var art=document.getElementById('art'),N=+art.dataset.total,
    els=art.querySelectorAll('[data-step]:not([data-step="0"])'),
    sk=art.querySelectorAll('[data-step="0"]');
document.getElementById('t').textContent=N;
function show(n){n=Math.max(0,Math.min(N,n));art.dataset.cur=n;
document.getElementById('n').textContent=n;
els.forEach(function(el){el.classList.toggle('on',+el.dataset.step<=n)});
sk.forEach(function(el){el.classList.toggle('on',n===0)});}
var qs=new URLSearchParams(location.search).get('step');
show(qs===null?N:+qs);
document.getElementById('bar').onclick=function(){show((+art.dataset.cur%N)+1)};
</script></body></html>""".replace("__SVG__", svg)
    open(out, "w", encoding="utf-8").write(html)
    prev = out.rsplit(".", 1)[0] + ".preview.html"
    open(prev, "w", encoding="utf-8").write(
        '<!doctype html><html><head><meta charset="utf-8"><title>preview</title>'
        '<style>html,body{margin:0;background:#fff}#art{display:block}#art [data-step]{display:inline}'
        '</style></head><body>' + svg + '</body></html>')
    if seal_flag:
        seal_output(out, ref, W, H)
    print(json.dumps({"out": out, "W": W, "H": H, "K": K, "layers": len(paths),
                      "compact": compact_flag,
                      "kb": round(len(html) / 1024, 1)}))

main()
