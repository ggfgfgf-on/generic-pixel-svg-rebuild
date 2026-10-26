import sys, os, json, re, subprocess, argparse
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))

def nonwhite_ratio(p):
    a = np.asarray(Image.open(p).convert("RGB"))
    return float((a < 250).any(axis=2).mean())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html"); ap.add_argument("outdir")
    ap.add_argument("--steps", type=int, default=0, help="0 = read data-total from the SVG")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--render", default='node "' + HERE.replace("\\", "/") + '/render.js" {html} {png} {w} {h} "{suffix}"')
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    head = open(a.html, encoding="utf-8").read(6000)
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', head)
    if not m:
        print(json.dumps({"error": "viewBox not found"})); sys.exit(1)
    W, H = int(m.group(1)), int(m.group(2))
    mt = re.search(r'data-total="(\d+)"', head)
    if not a.steps:
        a.steps = int(mt.group(1)) if mt else 12

    shots, ratios = [], []
    for n in range(a.steps + 1):
        p = os.path.join(a.outdir, "step-%02d.png" % n)
        r = subprocess.run(a.render.format(html=a.html, png=p, w=W, h=H,
                                           suffix="?step=%d&v=%d" % (n, os.getpid())),
                           shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            print(json.dumps({"error": r.stderr[:300], "step": n})); sys.exit(1)
        shots.append(p); ratios.append(round(nonwhite_ratio(p), 4))

    # monotonic: from step 2 on (step 0 may be a separate sketch layer)
    bad = [i for i in range(2, len(ratios)) if ratios[i] + 0.002 < ratios[i - 1]]
    flat_tail = sum(1 for x in ratios[:-1] if abs(x - ratios[-1]) < 1e-4)

    # step-to-step visual change (nonwhite ratio saturates for vector layers that cover the canvas)
    deltas = []
    prev = None
    for p in shots:
        cur = np.asarray(Image.open(p).convert("RGB"), dtype=np.int16)
        deltas.append(0.0 if prev is None else round(float(np.abs(cur - prev).mean()), 3))
        prev = cur
    stalled = [i for i in range(1, len(deltas)) if deltas[i] < 0.5]

    cell, cols = 380, a.cols
    rows = (len(shots) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), "white")
    for i, p in enumerate(shots):
        im = Image.open(p).convert("RGB")
        im.thumbnail((cell - 8, cell - 8))
        sheet.paste(im, ((i % cols) * cell + 4, (i // cols) * cell + 4))
    out = os.path.join(a.outdir, "contact-sheet.png")
    sheet.save(out)
    print(json.dumps({"steps": a.steps, "nonwhite_ratio": ratios, "step_delta": deltas,
                      "monotonic_violations": bad, "flat_tail_steps": flat_tail,
                      "stalled_steps": stalled, "final_cover": ratios[-1],
                      "contact_sheet": out, "window": [W, H]}, ensure_ascii=False))

main()
