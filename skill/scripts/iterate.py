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
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


PATH_RE = re.compile(r"<path\b[^>]*/>", re.S)


def tag_attrs(tag):
    return dict(re.findall(r'([\w-]+)="([^"]*)"', tag))


def inject_ids(text):
    """给每条 path 一个稳定 id（发射顺序），后面的 drop/recolor/redraw 才有把手。"""
    n = [0]

    def repl(m):
        n[0] += 1
        tag = m.group(0)
        if 'id="' in tag:
            return tag
        return tag.replace("<path", '<path id="p%d"' % n[0], 1)

    return PATH_RE.sub(repl, text)


def path_selected(attrs, op):
    if op.get("ids"):
        wanted = set("p%d" % int(i) for i in op["ids"])
        return attrs.get("id") in wanted
    w = op.get("where") or {}
    if "fill" in w:
        return attrs.get("fill", "").lower() == str(w["fill"]).lower()
    if "id" in w:
        return attrs.get("id") == ("p%d" % int(w["id"])) or attrs.get("id") == str(w["id"])
    return False


def apply_path_op(text, op):
    """drop / recolor / redraw / reorder —— 一次扫描完成，避免下标漂移。"""
    kind = op["op"]
    out, last, moved = [], 0, []
    for m in PATH_RE.finditer(text):
        attrs = tag_attrs(m.group(0))
        if not path_selected(attrs, op):
            continue
        tag = m.group(0)
        if kind == "drop":
            out.append(text[last:m.start()])
        elif kind == "recolor":
            out.append(text[last:m.start()])
            out.append(re.sub(r'fill="[^"]*"', 'fill="%s"' % op.get("to", "#000000"), tag, count=1))
        elif kind == "redraw":
            out.append(text[last:m.start()])
            if op.get("d"):
                tag = re.sub(r'd="[^"]*"', 'd="%s"' % op["d"], tag, count=1)
            if op.get("fill"):
                tag = re.sub(r'fill="[^"]*"', 'fill="%s"' % op["fill"], tag, count=1)
            out.append(tag)
        elif kind == "reorder":
            out.append(text[last:m.start()])
            moved.append(tag)
        last = m.end()
    out.append(text[last:])
    text = "".join(out)
    if kind == "reorder" and moved:
        close = text.rfind("</svg>")
        if close > 0:
            text = text[:close] + "".join(moved) + text[close:]
    return text


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
    ap.add_argument("--base", default="", help="vectorize positional args: K turd alphamax dil blur ss sk_thr（从零作画/续画时可省）")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--grid", type=int, default=8)
    ap.add_argument("--top", type=int, default=4, help="worst tiles patched per round")
    ap.add_argument("--k-mult", type=float, default=2.0, help="auto patch: region K = global K * mult^round")
    ap.add_argument("--k-max", type=int, default=1024)
    ap.add_argument("--min-gain", type=float, default=0.05)
    ap.add_argument("--patch", default="", help="JSON patch file with {'ops': [...]}")
    ap.add_argument("--blank", default="", help="WxH: start from an empty canvas (no reference, draw-only)")
    ap.add_argument("--from-svg", default="",
                    help="以一张已有 SVG 为底稿继续改（从零画的第二轮及以后用它）")
    ap.add_argument("--mode", choices=["ask", "auto"], default="ask",
                    help="ask=跑到决策点停下，把 JSON+预览交给模型（默认）；auto=脚本自己挑最差块加预算")
    ap.add_argument("--keep", default="")
    a = ap.parse_args()

    if not a.base:
        if not (a.blank or a.from_svg):
            ap.error("--base 必填（除非 --blank / --from-svg）")
        a.base = "2 6 1.0 0 0 1 0"
    base = a.base.split()
    global_k = int(base[0])
    if a.blank:
        W, H = [int(v) for v in a.blank.lower().split("x")]
    elif a.from_svg:
        with open(a.from_svg, "r", encoding="utf-8") as fh:
            head = fh.read(600)
        mv = re.search(r'width="(\d+)"\s+height="(\d+)"', head) or re.search(r'viewBox="0 0 (\d+) (\d+)"', head)
        W, H = (int(mv.group(1)), int(mv.group(2))) if mv else (400, 600)
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
            elif kind in ("remap", "draw", "drop", "recolor", "redraw", "reorder"):
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
            elif op.get("op") == "draw":
                extra = ('<path fill="%s" fill-rule="evenodd" data-step="99" d="%s"/>'
                         % (op.get("fill", "#000000"), op["d"]))
                close = text.rfind("</svg>")
                if close > 0:
                    text = text[:close] + extra + text[close:]
            elif op.get("op") in ("drop", "recolor", "redraw", "reorder"):
                text = apply_path_op(text, op)
        return text

    def build(tag):
        svg = os.path.join(tmp, tag + ".svg")
        png = os.path.join(tmp, tag + ".png")
        if a.blank or a.from_svg:
            if a.from_svg:
                with open(a.from_svg, "r", encoding="utf-8") as src, open(svg, "w", encoding="utf-8") as fh:
                    fh.write(src.read())
            else:
                with open(svg, "w", encoding="utf-8") as fh:
                    fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
                             '<rect width="%d" height="%d" fill="#ffffff"/></svg>' % (W, H, W, H, W, H))
            b = None
            if text_ops:
                with open(svg, "r", encoding="utf-8") as fh:
                    t = inject_ids(fh.read())
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
            need_ids = any(op.get("op") in ("drop", "recolor", "redraw", "reorder") for op in text_ops)
            with open(svg, "r", encoding="utf-8") as fh:
                t = fh.read()
            if need_ids:
                t = inject_ids(t)     # 只在需要动刀时才加 id，默认输出不变
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

    if a.mode == "ask" and (a.blank or a.from_svg):
        # 从零作画/续画：没有参考图可算误差，但仍要在决策点停住给模型看
        with open(svg, "r", encoding="utf-8") as fh:
            txt = fh.read()
        n_paths = txt.count("<path")
        fills = re.findall(r'fill="(#[0-9a-fA-F]{6})"', txt)
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        shutil.copyfile(svg, a.out)
        preview = os.path.splitext(os.path.abspath(a.out))[0] + ".preview.png"
        if os.path.exists(png):
            shutil.copyfile(png, preview)
        print(json.dumps({
            "mode": "ask", "blank": bool(a.blank), "from_svg": a.from_svg or None,
            "out": a.out, "paths": n_paths, "kb": round(os.path.getsize(svg) / 1024, 1),
            "colours": len(set(fills)),
            "preview_png": preview,
            "patch_schema": {"ops": [
                {"op": "redraw", "ids": [7], "d": "M ...", "fill": "#rrggbb"},
                {"op": "drop", "ids": [12, 13]},
                {"op": "draw", "fill": "#rrggbb", "d": "M ..."}]},
            "next": "iterate.py - %s --from-svg %s --patch <file>" % (a.out, a.out),
        }, ensure_ascii=False))
        return
    if a.mode == "ask" and not a.blank:
        # 决策点：渲染 + 分块误差，停在这里等模型回补丁（这是本模式的默认行为）
        tiles = tile_errors(a.ref, png, a.grid)[:a.top]
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        shutil.copyfile(best_svg, a.out)
        preview = os.path.splitext(os.path.abspath(a.out))[0] + ".preview.png"
        if os.path.exists(png):
            shutil.copyfile(png, preview)
        k_next = min(a.k_max, int(round(global_k * a.k_mult)))
        print(json.dumps({
            "mode": "ask", "out": a.out, "mean": m["mean"], "kb": rounds[-1]["kb"],
            "applied_regions": len(regions),
            "worst_tiles": [dict(t, suggest_k=k_next) for t in tiles],
            "preview_png": preview,
            "patch_schema": {"ops": [{"op": "region", "box": [100, 100, 200, 200], "k": 384, "ss": 2}]},
            "next": "iterate.py %s %s --base \"%s\" --patch <file>%s"
                    % (a.ref, a.out, a.base, " --blank " + a.blank if a.blank else ""),
        }, ensure_ascii=False))
        return
    if a.blank or a.from_svg:
        a.rounds = 0        # 没有参考图可迭代，只有模型给的图层
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
