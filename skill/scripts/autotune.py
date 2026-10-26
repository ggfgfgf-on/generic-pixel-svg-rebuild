import sys, os, json, subprocess, argparse
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))

def metric(ref_p, ren_p):
    a = np.asarray(Image.open(ref_p).convert("RGB"), dtype=np.int16)
    b = np.asarray(Image.open(ren_p).convert("RGB"), dtype=np.int16)
    if a.shape != b.shape:
        return {"error": "size mismatch", "ref": list(a.shape), "render": list(b.shape)}
    d = np.abs(a - b).max(axis=2)
    white = (a >= 255).all(axis=2)
    unc = int(((b >= 255).all(axis=2) & ~white).sum())
    return {"mean": round(float(d.mean()), 4),
            "pct_le5": round(float((d <= 5).mean() * 100), 3),
            "pct_le10": round(float((d <= 10).mean() * 100), 3),
            "pct_le20": round(float((d <= 20).mean() * 100), 3),
            "uncovered": unc}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref"); ap.add_argument("outdir")
    ap.add_argument("--k-start", type=int, default=1024)
    ap.add_argument("--max-k", type=int, default=8192)
    ap.add_argument("--gain", type=float, default=3.0, help="停止阈值：<=5/255 占比增益百分点")
    ap.add_argument("--render", default='node "' + HERE.replace("\\", "/") + '/render.js" {html} {png} {w} {h}')
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    runs, best, best_k5, K = [], None, -1.0, a.k_start
    while K <= a.max_k:
        html = os.path.join(a.outdir, "k%d.html" % K)
        png = os.path.join(a.outdir, "k%d.png" % K)
        b = subprocess.run([sys.executable, os.path.join(HERE, "build_svg.py"), a.ref, html, str(K)],
                           capture_output=True, text=True)
        if b.returncode != 0:
            runs.append({"K": K, "error": b.stderr.strip().splitlines()[-1] if b.stderr else "build failed"}); break
        info = json.loads(b.stdout.strip().splitlines()[-1])
        cmd = a.render.format(html=html, png=png, w=info["W"], h=info["H"])
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            runs.append({"K": K, "error": "render failed: " + (r.stderr.strip()[:200] or "")}); break
        m = metric(a.ref, png)
        row = {"K": K, "classes": info["classes_used"], "paths": info["paths"],
               "mb": round(info["bytes"] / 1048576, 2), **m}
        runs.append(row)
        print(json.dumps(row), flush=True)
        if m.get("pct_le5", 0) > best_k5:
            best_k5, best = m["pct_le5"], html
        if len([x for x in runs if "pct_le5" in x]) >= 2:
            prev = [x for x in runs if "pct_le5" in x][-2]
            if m["pct_le5"] - prev["pct_le5"] < a.gain:
                break
        K *= 2
    if best:
        import shutil
        final = os.path.join(a.outdir, "final.html")
        shutil.copyfile(best, final)
    print(json.dumps({"best_k5": best_k5, "best_html": best, "runs": runs}))

main()
