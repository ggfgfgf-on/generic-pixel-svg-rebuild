import sys, os, json
try:
    import vtracer
except ImportError:
    print(json.dumps({"error": "vtracer missing: python -m pip install vtracer"})); sys.exit(2)

def main():
    src, out = sys.argv[1], sys.argv[2]
    precision = int(sys.argv[3]) if len(sys.argv) > 3 else 6      # 颜色精度 1..8
    speckle = int(sys.argv[4]) if len(sys.argv) > 4 else 4         # 去噪小色块
    layer = int(sys.argv[5]) if len(sys.argv) > 5 else 16          # 层间色差（越小颜色越多）
    corner = int(sys.argv[6]) if len(sys.argv) > 6 else 60         # 拐角阈值（越小越多尖角）
    vtracer.convert_image_to_svg_py(
        src, out,
        colormode="color", hierarchical="stacked", mode="spline",
        filter_speckle=speckle, color_precision=precision, layer_difference=layer,
        corner_threshold=corner, length_threshold=4.0, max_iterations=10,
        splice_threshold=45, path_precision=3)
    svg = open(out, encoding="utf-8").read()
    # 包成单文件 HTML：内联同一份 SVG，无外链、无位图
    html = ('<!doctype html><html><head><meta charset="utf-8"><title>smooth svg</title>'
            '<style>html,body{margin:0;background:#fff}svg{display:block}</style></head><body>'
            + svg + '</body></html>')
    hp = os.path.splitext(out)[0] + ".html"
    open(hp, "w", encoding="utf-8").write(html)
    print(json.dumps({"out": out, "html": hp, "svg_kb": round(len(svg) / 1024, 1),
                      "paths": svg.count("<path")}))

main()
