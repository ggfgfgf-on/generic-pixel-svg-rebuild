import sys, json
import numpy as np
from PIL import Image

def median_cut(px, k):
    boxes = [px]
    while len(boxes) < k:
        best = None
        for i, bx in enumerate(boxes):
            if len(bx) < 2:
                continue
            rng = bx.max(0) - bx.min(0)
            ax = int(rng.argmax())
            score = float(rng[ax]) * len(bx)
            if best is None or score > best[0]:
                best = (score, i, ax)
        if best is None:
            break
        _, i, ax = best
        bx = boxes.pop(i)
        order = np.argpartition(bx[:, ax], len(bx) // 2)
        s = bx[order]
        h = len(s) // 2
        boxes.append(s[:h]); boxes.append(s[h:])
    return boxes

def assign(arr, cent):
    try:
        from scipy.spatial import cKDTree
        return cKDTree(cent).query(arr, workers=-1)[1].astype(np.uint16)
    except Exception:
        lab = np.empty(len(arr), np.uint16)
        step = 65536
        for i in range(0, len(arr), step):
            chunk = arr[i:i + step]
            d = ((chunk[:, None, :] - cent[None, :, :]) ** 2).sum(-1)
            lab[i:i + step] = d.argmin(1)
        return lab

def kmeans_families(cent, f, iters=15):
    """把 K 个调色板中心并成 f 个色族：步骤必须按色族推进，按单类面积推进会得到噪声帧（P8）。"""
    f = max(1, min(f, len(cent)))
    idx = np.linspace(0, len(cent) - 1, f).astype(int)
    cen = cent[np.argsort(cent.mean(1))][idx]
    lab = np.zeros(len(cent), np.int32)
    for _ in range(iters):
        d = ((cent[:, None, :] - cen[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        for j in range(f):
            m = lab == j
            if m.any():
                cen[j] = cent[m].mean(0)
    return lab

def row_runs(mask_y):
    row = mask_y.astype(np.int8)
    d = np.diff(np.concatenate(([0], row, [0])))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))

def color_of(vals):
    col = [int(round(v)) for v in vals.mean(0)]
    for i, v in enumerate(col):
        col[i] = min(255, max(0, v))
    # 近白保护：类内存在非纯白像素时，禁止把整类渲染成纯白（否则 uncovered 虚高，见 P10）
    if col == [255, 255, 255] and not (vals >= 255).all():
        col = [254, 254, 254]
    return "#%02x%02x%02x" % tuple(col)

def sketch_paths(arr, W, H, thr, chunk):
    lum = arr.reshape(H, W, 3).mean(2)
    edge = np.zeros((H, W), bool)
    edge[:, :-1] |= np.abs(np.diff(lum, axis=1)) > thr
    edge[:-1, :] |= np.abs(np.diff(lum, axis=0)) > thr
    runs = []
    for y in range(H):
        for s, e in row_runs(edge[y]):
            runs.append((int(s), y, int(e - s)))
    out = []
    for i in range(0, len(runs), chunk):
        d = " ".join("M%d %dh%dv1h-%dZ" % (x, y, w, w) for x, y, w in runs[i:i + chunk])
        out.append('<path fill="#33445c" fill-opacity="0.55" shape-rendering="crispEdges" data-step="0" d="%s"/>' % d)
    return out

PLAYER_CSS = """html,body{margin:0;background:#fff}#art{display:block}
#art [data-step]{display:none}#art [data-step].on{display:inline}
#bar{position:fixed;right:8px;bottom:8px;font:12px/1.6 system-ui;background:#000000b0;color:#fff;
padding:6px 10px;border-radius:6px;cursor:pointer;user-select:none}"""

def main():
    ref, out = sys.argv[1], sys.argv[2]
    K = int(sys.argv[3]) if len(sys.argv) > 3 else 2048
    sketch_thr = float(sys.argv[4]) if len(sys.argv) > 4 else 16.0
    chunk = 300
    img = Image.open(ref).convert("RGB")
    W, H = img.size
    arr2 = np.asarray(img, dtype=np.float32)
    arr = arr2.reshape(-1, 3)
    boxes = median_cut(arr, K)
    cent = np.array([b.mean(0) for b in boxes], dtype=np.float32)
    lab = assign(arr, cent).reshape(H, W)
    classes = [int(c) for c in np.unique(lab)]
    areas = {c: int((lab == c).sum()) for c in classes}
    border = set(lab[0].tolist()) | set(lab[-1].tolist()) | set(lab[:, 0].tolist()) | set(lab[:, -1].tolist())
    bg = max(border, key=lambda c: areas.get(c, 0)) if border else classes[0]
    body = sorted([c for c in classes if c != bg], key=lambda c: areas[c], reverse=True)
    MAXSTEP = 11                      # 0=线稿，1=背景色族，2..MAXSTEP 按色族面积由大到小
    fam = kmeans_families(cent, 8)
    fam_area = {}
    for c in classes:
        fam_area[int(fam[c])] = fam_area.get(int(fam[c]), 0) + areas[c]
    fam_bg = int(fam[bg])
    order = sorted([k for k in fam_area if k != fam_bg], key=lambda k: -fam_area[k])
    fam_step = {fam_bg: 1}
    for i, k in enumerate(order):
        fam_step[k] = 2 + int(round(i * (MAXSTEP - 2) / max(1, len(order) - 1)))
    steps = {c: fam_step[int(fam[c])] for c in classes}
    del body
    paths = []
    for c in classes:
        mask = lab == c
        color = color_of(arr2[mask])
        runs = []
        for y in range(H):
            for s, e in row_runs(mask[y]):
                runs.append((int(s), y, int(e - s)))
        for i in range(0, len(runs), chunk):
            d = " ".join("M%d %dh%dv1h-%dZ" % (x, y, w, w) for x, y, w in runs[i:i + chunk])
            paths.append('<path fill="%s" shape-rendering="crispEdges" data-step="%d" d="%s"/>' % (color, steps[c], d))
    sk = sketch_paths(arr, W, H, sketch_thr, chunk) if sketch_thr > 0 else []
    svg = ('<svg id="art" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
           'shape-rendering="crispEdges" data-total="%d">%s</svg>') % (W, H, W, H, MAXSTEP, "".join(sk + paths))
    html = """<!doctype html><html><head><meta charset="utf-8"><title>pixel svg</title>
<style>%s</style></head><body>
__SVG__<div id="bar">step <span id="n"></span>/<span id="t"></span></div>
<script>
var art=document.getElementById('art'),N=+art.dataset.total,
    base=art.querySelectorAll('[data-step]:not([data-step="0"])'),
    sk=art.querySelectorAll('[data-step="0"]');
document.getElementById('t').textContent=N;
function show(n){n=Math.max(0,Math.min(N,n));art.dataset.cur=n;
document.getElementById('n').textContent=n;
base.forEach(function(el){el.classList.toggle('on',+el.dataset.step<=n)});
sk.forEach(function(el){el.classList.toggle('on',n===0)});}
var qs=new URLSearchParams(location.search).get('step');
show(qs===null?N:+qs);
document.getElementById('bar').onclick=function(){show((+art.dataset.cur%N)+1)};
</script></body></html>""".replace("__SVG__", svg).replace("%s", PLAYER_CSS)
    open(out, "w", encoding="utf-8").write(html)
    prev = out.rsplit(".", 1)[0] + ".preview.html"
    open(prev, "w", encoding="utf-8").write(
        '<!doctype html><html><head><meta charset="utf-8"><title>preview</title>'
        '<style>html,body{margin:0;background:#fff}#art{display:block}'
        '#art [data-step]{display:inline}</style></head><body>' + svg + '</body></html>')
    print(json.dumps({"out": out, "preview": prev, "W": W, "H": H, "K": len(classes),
                      "paths": len(paths), "sketch_paths": len(sk), "bytes": len(html),
                      "mb": round(len(html) / 1048576, 2)}))

main()
