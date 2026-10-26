import sys, json, os, ctypes, time, re
from pathlib import Path
import numpy as np
import potrace
from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_loader  # noqa: E402  shared ctypes bindings (see native_loader.py)


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
    full_palette = "--full-palette" in raw_args
    seal_flag = "--seal" in raw_args
    sample_n = 240000
    args = []
    for a in raw_args:
        if a in ("--full-palette", "--seal"):
            continue
        if a.startswith("--palette-sample="):
            sample_n = int(a.split("=", 1)[1])
            continue
        args.append(a)
    ref, out = args[0], args[1]
    K = int(args[2]) if len(args) > 2 else 128
    if len(args) <= 2:
        print("[vectorize] no K given, using default K=128; pass 256-384 for final output", flush=True)
    turd = int(args[3]) if len(args) > 3 else 6
    alphamax = float(args[4]) if len(args) > 4 else 1.0
    dil = int(args[5]) if len(args) > 5 else 1        # 掩码膨胀像素数：消除层间白缝
    blur = float(args[6]) if len(args) > 6 else 0.0      # 先轻微高斯模糊：边界更干净，曲线更顺
    ss = int(args[7]) if len(args) > 7 else 1          # 超采样：2~3 倍放大再描，曲线更顺、边界更准
    sk_thr = float(args[8]) if len(args) > 8 else 0.0   # 线稿层：边缘梯度阈值，0=不要线稿
    dark_k = int(args[9]) if len(args) > 9 else 0      # 分层色板：暗区(L<80)单独分配多少色，0=不分层
    med = int(args[10]) if len(args) > 10 else 0         # 中值滤波半径(3/5)：压掉边缘暗碎块，0=关
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
    if out.lower().endswith(".svg"):
        # A static SVG has no player: data-step / data-total belong to the HTML
        # playback branch only and must not leak into the deliverable.
        clean = re.sub(r'\sdata-(?:step|total)="\d+"', "", svg)
        doc = '<?xml version="1.0" encoding="UTF-8"?>\n' + clean + '\n'
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(doc)
        if seal_flag:
            seal_output(out, ref, W, H)
        print(json.dumps({"out": out, "W": W, "H": H, "K": K, "layers": len(paths),
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
                      "kb": round(len(html) / 1024, 1)}))

main()
