#!/usr/bin/env python3
"""Iterate: render -> per-tile error -> patch -> retrace only the bad tiles.

  python scripts/iterate.py ref.png out.svg --base "48 6 1.0 1 0.4 2 0" --rounds 3
  python scripts/iterate.py ref.png out.svg --base "..." --patch patch.json

This is the 4th entry point (peer of vectorize / --process-final / auto.py): it
does not replace the flat trace, it keeps it as the base and spends extra colour
budget exactly where the rendered result is measurably wrong. A patch is data,
so a model can author it after looking at the tile report:

  {"ops": [{"op": "region", "box": [x0, y0, x1, y1], "k": 384, "ss": 2}]}

With no --patch the script patches itself: the worst tiles get a region at
global_K * k-mult, which is the cheap baseline the model can then refine.
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def render(svg, png, W, H):
    run([PY, os.path.join(HERE, "render.py"), svg, png, str(W), str(H)])
    return os.path.exists(png)


def metric(ref, png):
    r = run([PY, os.path.join(HERE, "metric.py"), ref, png])
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"error": (r.stderr or r.stdout).strip()[-160:]}


def tile_errors(ref_path, png, grid):
    ref = np.asarray(Image.open(ref_path).convert("RGB"), dtype=np.int16)
    cur = np.asarray(Image.open(png).convert("RGB"), dtype=np.int16)
    if ref.shape != cur.shape:
        cur = np.asarray(Image.fromarray(cur.astype(np.uint8)).resize((ref.shape[1], ref.shape[0]), Image.LANCZOS), dtype=np.int16)
    err = np.abs(ref - cur).max(axis=2)
    H, W = err.shape
    out = []
    for gy in range(grid):
        for gx in range(grid):
            x0 = int(round(gx * W / grid)); x1 = int(round((gx + 1) * W / grid))
            y0 = int(round(gy * H / grid)); y1 = int(round((gy + 1) * H / grid))
            t = err[y0:y1, x0:x1]
            out.append({"box": [x0, y0, x1, y1], "mean": round(float(t.mean()), 3),
                        "pct_le5": round(float((t <= 5).mean() * 100), 2)})
    out.sort(key=lambda r: -r["mean"])
    return out


def covered(box, regions):
    x0, y0, x1, y1 = box
    for r in regions:
        a, b, c, d = r["box"]
        if a <= x0 and b <= y0 and c >= x1 and d >= y1:
            return True
    return False


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("out")
    ap.add_argument("--base", required=True, help="vectorize positional args: K turd alphamax dil blur ss sk_thr")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--grid", type=int, default=8)
    ap.add_argument("--top", type=int, default=4, help="worst tiles patched per round")
    ap.add_argument("--k-mult", type=float, default=2.0, help="auto patch: region K = global K * mult^round")
    ap.add_argument("--k-max", type=int, default=1024)
    ap.add_argument("--min-gain", type=float, default=0.05)
    ap.add_argument("--patch", default="", help="JSON patch file with {'ops': [...]}")
    ap.add_argument("--blank", default="", help="WxH: start from an empty canvas (no reference, draw-only)")
    ap.add_argument("--keep", default="")
    a = ap.parse_args()

    base = a.base.split()
    global_k = int(base[0])
    if a.blank:
        W, H = [int(v) for v in a.blank.lower().split("x")]
    else:
        W, H = Image.open(a.ref).size
    tmp = tempfile.mkdtemp(prefix="iterate-svg-")
    regions, rounds = [], []

    model_ops = []
    text_ops = []          # remap / draw：只改文本，不重跑描摹
    if a.patch:
        with open(a.patch, "r", encoding="utf-8") as fh:
            model_ops = json.load(fh).get("ops", [])
        idx = {"K": 0, "turd": 1, "alphamax": 2, "dil": 3, "blur": 4, "ss": 5, "sk_thr": 6}
        for op in model_ops:
            kind = op.get("op")
            if kind == "region":
                regions.append({"box": list(op["box"]), "k": op.get("k"), "ss": op.get("ss"),
                                "source": "model"})
            elif kind in ("remap", "draw"):
                text_ops.append(op)
            elif kind == "style":
                # 整幅按画风重跑：只覆盖给定的项，其余继承 base
                for key, pos in idx.items():
                    if key in op:
                        base[pos] = str(op[key])
                        if pos == 0:
                            global_k = int(op[key])

    def apply_text_ops(text, ops):
        """remap / draw 只改 SVG 文本，不重跑描摹，所以是瞬时的。"""
        import re
        for op in ops:
            if op.get("op") == "remap" and op.get("to_palette"):
                pal = [tuple(int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) for c in op["to_palette"]]
                pal = np.array(pal, dtype=np.float32)

                def snap(m):
                    h = m.group(1)
                    rgb = np.array([int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)], dtype=np.float32)
                    i = int(np.argmin(((pal - rgb) ** 2).sum(1)))
                    return 'fill="#%02x%02x%02x"' % tuple(int(v) for v in pal[i])
                text = re.sub(r'fill="(#[0-9a-fA-F]{6})"', snap, text)
            elif op.get("op") == "remap" and op.get("from") and op.get("to"):
                src = np.array([int(op["from"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)
                dst = op["to"]
                tol = float(op.get("tol", 24))

                def swap(m):
                    h = m.group(1)
                    rgb = np.array([int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)], dtype=np.float32)
                    return ('fill="%s"' % dst) if np.abs(rgb - src).max() <= tol else m.group(0)
                text = re.sub(r'fill="(#[0-9a-fA-F]{6})"', swap, text)
        draws = [op for op in ops if op.get("op") == "draw"]
        if draws:
            extra = "".join('<path fill="%s" fill-rule="evenodd" data-step="99" d="%s"/>'
                            % (op.get("fill", "#000000"), op["d"]) for op in draws)
            close = text.rfind("</svg>")
            if close > 0:
                text = text[:close] + extra + text[close:]
        return text

    def build(tag):
        svg = os.path.join(tmp, tag + ".svg")
        png = os.path.join(tmp, tag + ".png")
        if a.blank:
            with open(svg, "w", encoding="utf-8") as fh:
                fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
                         '<rect width="%d" height="%d" fill="#ffffff"/></svg>' % (W, H, W, H, W, H))
            b = None
            if text_ops:
                with open(svg, "r", encoding="utf-8") as fh:
                    t = fh.read()
                with open(svg, "w", encoding="utf-8") as fh:
                    fh.write(apply_text_ops(t, text_ops))
            if not render(svg, png, W, H):
                return None, None, "render failed"
            return svg, png, {"mean": None, "pct_le5": None, "uncovered": None}
        cmd = [PY, os.path.join(HERE, "vectorize.py"), a.ref, svg] + base
        for r in regions:
            spec = "%d,%d,%d,%d:%d" % (r["box"][0], r["box"][1], r["box"][2], r["box"][3], int(r["k"]))
            if r.get("ss"):
                spec += ",%d" % int(r["ss"])
            cmd += ["--region", spec]
        b = run(cmd)
        if not os.path.exists(svg):
            return None, None, (b.stderr or b.stdout).strip()[-200:]
        if text_ops:
            with open(svg, "r", encoding="utf-8") as fh:
                t = fh.read()
            with open(svg, "w", encoding="utf-8") as fh:
                fh.write(apply_text_ops(t, text_ops))
        if not render(svg, png, W, H):
            return None, None, "render failed"
        m = metric(a.ref, png)
        if "error" in m:
            return None, None, m["error"]
        return svg, png, m

    svg, png, m = build("r0")
    if svg is None:
        print(json.dumps({"error": m}, ensure_ascii=False))
        raise SystemExit(2)
    rounds.append({"round": 0, "mean": m["mean"], "pct_le5": m["pct_le5"],
                   "uncovered": m["uncovered"], "kb": round(os.path.getsize(svg) / 1024, 1),
                   "regions": len(regions), "png": png})
    best = rounds[0]
    best_svg = svg

    if a.blank:
        a.rounds = 0        # 空白画布没有参考可迭代，只有模型给的图层
    for it in range(1, a.rounds + 1):
        tiles = tile_errors(a.ref, rounds[-1]["png"], a.grid)
        if model_ops:
            picks = []
        else:
            picks = [t for t in tiles if not covered(t["box"], regions)][:a.top]
            if not picks:
                break
            for t in picks:
                regions.append({"box": t["box"],
                                "k": min(a.k_max, int(round(global_k * (a.k_mult ** it)))),
                                "ss": None, "source": "auto"})
        svg2, png2, m2 = build("r%d" % it)
        if svg2 is None:
            rounds.append({"round": it, "error": m2})
            break
        gain = rounds[-1]["mean"] - m2["mean"]
        rounds.append({"round": it, "mean": m2["mean"], "pct_le5": m2["pct_le5"],
                       "uncovered": m2["uncovered"], "kb": round(os.path.getsize(svg2) / 1024, 1),
                       "regions": len(regions), "gain": round(gain, 3),
                       "patched": [r["box"] for r in regions if r["source"] == "auto"][-len(picks):] if picks else [],
                       "png": png2})
        if m2["mean"] < best["mean"]:
            best = rounds[-1]
            best_svg = svg2
        if gain < a.min_gain:
            break

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    import shutil
    shutil.copyfile(best_svg, a.out)
    if a.keep:
        os.makedirs(a.keep, exist_ok=True)
        for r in rounds:
            if r.get("png") and os.path.exists(r["png"]):
                shutil.copyfile(r["png"], os.path.join(a.keep, "round%d.png" % r["round"]))
    for r in rounds:
        r.pop("png", None)
    print(json.dumps({"out": a.out, "base": base, "picked_round": best["round"],
                      "mean": best["mean"], "kb": best["kb"],
                      "regions": [{k: v for k, v in r.items() if k in ("box", "k", "ss", "source")}
                                  for r in regions],
                      "rounds": rounds}, ensure_ascii=False))


if __name__ == "__main__":
    main()
