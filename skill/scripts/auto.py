#!/usr/bin/env python3
"""One command, measured result: pick a preset, render, compare, keep the best.

  python scripts/auto.py ref.png out.svg            # best of the recommended presets
  python scripts/auto.py ref.png out.svg --try-all  # also try icon/lineart
  python scripts/auto.py ref.png out.svg --preset portrait

Every candidate is rendered and scored against the reference, so the caller does
not have to guess K / ss / turd. The winner is copied to *out*; the report lists
all candidates with mean / pct_le5 / size so a bad preset is visible immediately.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# name -> (vectorize positional args after <ref> <out>, extra flags)
PRESETS = {
    # flat art / icons: few colours, crisp edges, small files
    "icon": (["32", "2", "1.0", "0", "0", "2", "0"], []),
    # line art / inked sketches: few colours, high supersampling
    "lineart": (["24", "1", "1.0", "0", "0", "3", "0"], []),
    # character art / illustration: the balanced default
    "illustration": (["128", "4", "1.0", "1", "0.3", "2", "0"], []),
    # soft-lit faces and skin: rings with true local colours, one refine pass
    "portrait": (["96", "4", "1.0", "1", "0.3", "2", "0"],
                 ["--alpha-soften", "2", "--alpha-true-color=6",
                  "--alpha-refine", "1", "--alpha-tol", "0.5"]),
    # photographic: most colours, strongest denoise
    "photo": (["192", "6", "1.0", "1", "0.4", "2", "0"], []),
}
ORDER = ["illustration", "portrait", "photo"]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def metric(ref, png):
    r = run([PY, os.path.join(HERE, "metric.py"), ref, png])
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"error": (r.stderr or r.stdout).strip()[-160:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("out")
    ap.add_argument("--preset", action="append", default=None,
                    help="repeatable; default = illustration,portrait,photo")
    ap.add_argument("--try-all", action="store_true")
    ap.add_argument("--keep", default="", help="directory to keep every candidate in")
    a = ap.parse_args()

    names = a.preset or (list(PRESETS) if a.try_all else ORDER)
    unknown = [n for n in names if n not in PRESETS]
    if unknown:
        raise SystemExit("unknown preset(s): %s (have %s)" % (unknown, list(PRESETS)))

    W, H = 0, 0
    try:
        from PIL import Image
        W, H = Image.open(a.ref).size
    except Exception:
        pass

    tmp = tempfile.mkdtemp(prefix="auto-svg-")
    rows, best = [], None
    for name in names:
        pos, extra = PRESETS[name]
        svg = os.path.join(tmp, name + ".svg")
        png = os.path.join(tmp, name + ".png")
        cmd = [PY, os.path.join(HERE, "vectorize.py"), a.ref, svg] + pos + list(extra)
        b = run(cmd)
        if not os.path.exists(svg):
            rows.append({"preset": name, "error": (b.stderr or b.stdout).strip()[-160:]})
            continue
        if not (W and H):
            try:
                info = json.loads(b.stdout.strip().splitlines()[-1])
                W, H = info["W"], info["H"]
            except Exception:
                pass
        r = run([PY, os.path.join(HERE, "render.py"), svg, png, str(W), str(H)])
        if not os.path.exists(png):
            rows.append({"preset": name, "error": (r.stderr or r.stdout).strip()[-160:]})
            continue
        m = metric(a.ref, png)
        if "error" in m:
            rows.append({"preset": name, "error": m["error"]})
            continue
        kb = round(os.path.getsize(svg) / 1024, 1)
        row = {"preset": name, "mean": m["mean"], "pct_le5": m["pct_le5"],
               "uncovered": m["uncovered"], "kb": kb, "svg": svg}
        rows.append(row)
        if best is None:
            best = row

    if best is None:
        print(json.dumps({"error": "every preset failed", "candidates": rows}, ensure_ascii=False))
        raise SystemExit(2)

    # 精度优先，但体积也计入：score = mean + 0.35*log2(kb/100)。
    # 平整图不值得为 0.2 的 mean 付 3 倍体积；皮肤/照片则反过来值。
    import math
    ok = [r for r in rows if "mean" in r]
    for r in ok:
        r["score"] = round(r["mean"] + 0.35 * math.log2(max(1.0, r["kb"]) / 100.0), 4)
    best = min(ok, key=lambda r: r["score"])
    best_svg = os.path.join(tmp, best["preset"] + ".svg")
    best["svg"] = best_svg

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    shutil.copyfile(best["svg"], a.out)
    if a.keep:
        os.makedirs(a.keep, exist_ok=True)
        for row in rows:
            if "svg" in row:
                shutil.copyfile(row["svg"], os.path.join(a.keep, os.path.basename(row["svg"])))
    for row in rows:
        row.pop("svg", None)
    print(json.dumps({"out": a.out, "picked": best["preset"],
                      "mean": best["mean"], "kb": best["kb"],
                      "candidates": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
