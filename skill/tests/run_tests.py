"""回归测试：每个用例 = 源图 + 模式 + 期望区间。

用法:  python tests/run_tests.py [--record]
  --record  记录当前指标为基线（写 tests/expected.json）
"""
import os, sys, json, subprocess, tempfile
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
PY = sys.executable
CASES = [
    # name, fixture, mode, extra args, expected(mean_max, uncovered=0)
    dict(name="flat-pixel-K256", fixture="flat-320x200.png", mode="pixel", args=["256"], gate=False,
         note="平涂源：逐像素分支应接近无损"),
    dict(name="photo-pixel-K256", fixture="photo-320x180.png", mode="pixel", args=["256"], gate=False,
         note="照片源：逐像素分支，误差由量化上界决定"),
    dict(name="photo-vector-K64", fixture="photo-320x180.png", mode="vector", args=["64", "8", "1.0", "1", "0.4", "1", "0", "0"],
         note="曲线分支：无台阶，误差明显高于逐像素分支"),
    dict(name="alpha-vector-K64", fixture="alpha-cutout-320x180.png", mode="vector", args=["64", "8", "1.0", "1", "0.4", "1", "0", "0"],
         note="透明 PNG：应输出无底色 rect 的透明 SVG，指标按白底合成比较", unc_tol=8),
    dict(name="photo-vector-soften", fixture="photo-320x180.png", mode="vector",
         args=["64", "8", "1.0", "1", "0.4", "1", "0", "0", "--edge-soften=0.3"],
         note="可选边缘后处理：必须保留纯 path，并只引入滤镜引用"),
    dict(name="photo-vector-compact", fixture="photo-320x180.png", mode="vector",
         args=["64", "8", "1.0", "1", "0.4", "1", "0", "0", "--compact"],
         note="无损路径重写：相对坐标 + 去掉重复属性，不删除路径"),
    dict(name="photo-process-final", fixture="photo-320x180.png", mode="vector",
         args=["--process-final", "--process-palette=64", "--compact"],
         note="默认矢量入口调用过程算法，只输出 final 静态图并做无损压缩"),
    dict(name="photo-process-final-detail", fixture="photo-320x180.png", mode="vector",
         args=["--process-final", "--process-palette=64", "--process-base-scale=0.4",
               "--process-mid-scale=0.6", "--process-detail-scale=1.1",
               "--process-min-areas=12,6,2", "--process-blurs=0.8,0.4,0.1",
               "--process-focus-box=0.2,0.1,0.8,0.9", "--process-focus=1.0,4,0,1",
               "--process-turd=6", "--process-alphamax=1.0", "--process-dil=1",
               "--process-ss=1.0", "--compact"],
         note="过程 final 可调层尺度/最小面积/模糊/焦点框/追踪参数，并保持压缩"),
    dict(name="photo-process-final-from-front", fixture="photo-320x180.png", mode="vector",
         args=["64", "7", "1.1", "1", "0.4", "2", "0", "--process-final",
               "--process-palette=64", "--process-from-front", "--compact"],
         note="默认从前排继承 turd/alphamax/dil/ss，显式 --process-* 优先"),
    dict(name="elements-budget", fixture="photo-320x180.png", mode="vector",
         args=["32", "6", "1.0", "1", "0.4", "2", "0", "--elements", "12:300:192,2"],
         note="元素级预算：按连通色块分配色数"),
    dict(name="elements-refine", fixture="photo-320x180.png", mode="vector",
         args=["24", "6", "1.0", "1", "0.4", "1", "0", "--elements", "12:300:192,1", "--elements-refine", "1"],
         note="元素残差细化：渲染后只重描最差元素"),
    dict(name="tile-budget", fixture="photo-320x180.png", mode="vector",
         args=["16", "6", "1.0", "1", "0.4", "1", "0", "--tile", "2x2:96,1"],
         note="整幅平铺：无低预算底图"),
    dict(name="tile-nondiv", fixture="photo-320x180.png", mode="vector",
         args=["16", "6", "1.0", "1", "0.4", "1", "0", "--tile", "3x3:96,1"],
         note="非整除平铺：靠 2px 重叠盖接缝"),
    dict(name="poly-region", fixture="photo-320x180.png", mode="vector",
         args=["16", "6", "1.0", "1", "0.4", "1", "0", "--region-poly", "40,20 280,20 280,160 40,160:128,1"],
         note="不规则多边形区域"),
    dict(name="photo-process-final-full-palette", fixture="photo-320x180.png", mode="vector",
         args=["320", "6", "1.0", "1", "0.4", "1", "0", "--process-final",
               "--process-palette=320", "--process-palette-sample=12000",
               "--full-palette", "--compact"],
         note="过程 k-means 色板抽样 12k；--full-palette 从前排继承后覆盖为全部像素"),
]

def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise SystemExit("FAILED: %s\n%s" % (" ".join(cmd), (r.stderr or r.stdout)[-800:]))
    return r.stdout.strip().splitlines()[-1]

def main():
    record = "--record" in sys.argv
    exp_path = os.path.join(HERE, "expected.json")
    expected = {}
    if os.path.exists(exp_path) and not record:
        expected = json.load(open(exp_path))
    tmp = tempfile.mkdtemp(prefix="gpsr-test-")
    results, failed = [], 0
    for c in CASES:
        ref = os.path.join(HERE, "fixtures", c["fixture"])
        html = os.path.join(tmp, c["name"] + ".html")
        png = os.path.join(tmp, c["name"] + ".png")
        if c["mode"] == "pixel":
            info = json.loads(run([PY, os.path.join(SKILL, "scripts", "build_svg.py"), ref, html] + c["args"]))
        else:
            info = json.loads(run([PY, os.path.join(SKILL, "scripts", "vectorize.py"), ref, html] + c["args"]))
        run([PY, os.path.join(SKILL, "scripts", "render.py"), info.get("preview", html), png,
             str(info["W"]), str(info["H"])])
        m = json.loads(run([PY, os.path.join(SKILL, "scripts", "metric.py"), ref, png]))
        # 结构性断言：单文件、无位图内嵌、无渐变、有步骤或图层
        doc = open(html, encoding="utf-8").read()
        checks = {
            "no_image_tag": "<image" not in doc and "data:image" not in doc and "base64," not in doc,
            "no_gradient": ("linearGradient" not in doc and "radialGradient" not in doc
                            and 'fill="url(' not in doc and 'stroke="url(' not in doc),
            "has_paths": doc.count("<path") > 0,
            # 透明图允许极少数孤立噪点：turdsize 会丢弃 alpha>250 的零星碎片
            "uncovered_zero": m["uncovered"] <= c.get("unc_tol", 0),
        }
        row = dict(name=c["name"], mean=m["mean"], le5=m["pct_le5"], uncovered=m["uncovered"],
                   paths=doc.count("<path"), kb=round(len(doc) / 1024), checks=checks)
        if record:
            expected[c["name"]] = dict(mean_max=round(m["mean"] + 1.0, 2), uncovered=0,
                                       unc_tol=c.get("unc_tol", 0))
        else:
            e = expected.get(c["name"], {})
            row["pass"] = (all(checks.values())
                           and m["mean"] <= e.get("mean_max", 999)
                           and m["uncovered"] <= e.get("unc_tol", 0))
            if not c.get("gate", True):
                row["info_only"] = True      # deprecated branch: recorded, never gates
            elif not row["pass"]:
                failed += 1
        results.append(row)
        print(json.dumps(row, ensure_ascii=False))
    # paint_process.py：透明源必须裁到 alpha，而不是被涂成背景色
    import re as _re, subprocess as _sp, tempfile as _tf
    _scripts = os.path.join(SKILL, "scripts")
    _apng = os.path.join(_tf.gettempdir(), "alpha-proc.png")
    _ahtml = os.path.join(_tf.gettempdir(), "alpha-proc.html")
    _im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(_im).rectangle([10, 10, 43, 43], fill=(200, 60, 40, 255))
    _im.save(_apng)
    _sp.run([sys.executable, os.path.join(_scripts, "paint_process.py"), _apng, _ahtml,
             "--size", "100x100"], check=True, capture_output=True)
    _doc = open(_ahtml, encoding="utf-8").read()
    _m = _re.search(r'<clipPath[^>]*><path d="([^"]+)"', _doc)
    _nums = [float(x) for x in _re.findall(r'-?\d+\.?\d*', _m.group(1))] if _m else []
    _xs = [round(v, 1) for v in _nums[0::2]]
    _ys = [round(v, 1) for v in _nums[1::2]]
    _bbox = [min(_xs), min(_ys), max(_xs), max(_ys)] if _xs else []
    _alpha_ok = (bool(_m) and bool(_re.search(r'clip-path="url\(#\w+\)"', _doc))
                 and abs(_bbox[0] - 10) <= 2 and abs(_bbox[1] - 10) <= 2
                 and abs(_bbox[2] - 44) <= 2 and abs(_bbox[3] - 44) <= 2) if _bbox else False
    print(json.dumps({"case": "paint_process-alpha", "bbox": _bbox, "pass": _alpha_ok}))
    if not _alpha_ok:
        failed += 1
    # form.py：无参考图也要能给出比例量表（定形辅助）
    import subprocess as _sp
    import tempfile as _tf
    _scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
    _svg = os.path.join(_tf.gettempdir(), "form-selftest.svg")
    with open(_svg, "w", encoding="utf-8") as _fh:
        _fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">'
                  '<rect width="200" height="200" fill="#ff0000"/>'
                  '<path d="M40 40 H160 V120 H40 Z" fill="#f7d9bd"/>'
                  '<path d="M40 40 H160 V70 H40 Z" fill="#3a211d"/></svg>')
    _fr = _sp.run([sys.executable, os.path.join(_scripts, "form.py"), _svg],
                  capture_output=True, text=True)
    _ok = _fr.returncode == 0 and "ratios" in _fr.stdout and "head_w_over_h" in _fr.stdout
    print(json.dumps({"case": "form.py", "pass": _ok}, ensure_ascii=False))
    if not _ok:
        failed += 1

    if record:
        json.dump(expected, open(exp_path, "w"), indent=2, ensure_ascii=False)
        print("baseline recorded ->", exp_path)
    else:
        print("RESULT:", "PASS" if failed == 0 else "FAIL (%d)" % failed)
        sys.exit(0 if failed == 0 else 1)

main()
