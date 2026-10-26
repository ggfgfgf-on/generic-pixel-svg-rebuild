"""回归测试：每个用例 = 源图 + 模式 + 期望区间。

用法:  python tests/run_tests.py [--record]
  --record  记录当前指标为基线（写 tests/expected.json）
"""
import os, sys, json, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
PY = sys.executable
CASES = [
    # name, fixture, mode, extra args, expected(mean_max, uncovered=0)
    dict(name="flat-pixel-K256", fixture="flat-320x200.png", mode="pixel", args=["256"],
         note="平涂源：逐像素分支应接近无损"),
    dict(name="photo-pixel-K256", fixture="photo-320x180.png", mode="pixel", args=["256"],
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
            failed += 0 if row["pass"] else 1
        results.append(row)
        print(json.dumps(row, ensure_ascii=False))
    if record:
        json.dump(expected, open(exp_path, "w"), indent=2, ensure_ascii=False)
        print("baseline recorded ->", exp_path)
    else:
        print("RESULT:", "PASS" if failed == 0 else "FAIL (%d)" % failed)
        sys.exit(0 if failed == 0 else 1)

main()
