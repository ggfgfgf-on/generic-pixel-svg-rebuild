# generic-pixel-svg-rebuild
Asset-agnostic pixel-level SVG reconstruction skill: turn any bitmap (PNG/JPEG, �?MP) into one self-contained HTML with pure SVG flat-color paths and a discrete step player. Verified per-pixel: 0 uncovered, 100% �?0/255, �?0% �?/255, mean &lt;2/255. No Canvas, embedded bitmaps, external links, gradients, or transitions.

一个与素材无关的像素级 SVG 重绘通用 Skill。输入任意位图（PNG/JPEG，≤2MP），输出单文件自包含 HTML：纯 SVG 平涂路径 + 离散绘制步骤播放器。逐像素验收：未覆盖像素为 0�?00% 像素通道误差 �?0/255，≥90% 像素 �?/255，平均通道误差 <2/255。无 Canvas、无位图内嵌、无外链、无 SVG 渐变、无 CSS 过渡�?

使用方法:直接扔给agent,要求他按手册方法进行svg重绘.

## Reference implementation (GPL)

This fork adds `skill/`: a runnable implementation of the manual above �?pixel-run and
curve branches, metric loop, per-step verification, regression suite.

### Install

Download the release zip and run the installer:

```powershell
# Windows
Expand-Archive generic-pixel-svg-rebuild-1.0.0.zip -DestinationPath .
.\generic-pixel-svg-rebuild\install.ps1
```

```bash
# macOS / Linux
unzip generic-pixel-svg-rebuild-1.0.0.zip
./generic-pixel-svg-rebuild/install.sh
```

The installer copies the folder into `$DSH_HOME/skills/generic-pixel-svg-rebuild`
(default `~/.dsh-home/skills`) and installs the Python deps from `requirements.txt`.
Restart DSH afterwards and the skill is available.

### Use

```bash
python <skill>/scripts/vectorize.py ref.png out.html 192   # curve redraw + step player
python <skill>/scripts/vectorize.py ref.png out.svg  192   # static SVG
python <skill>/scripts/metric.py   ref.png shot.png        # mean / <=5 / uncovered
python <skill>/tests/run_tests.py                          # regression (expect PASS)
```

Entry point: `skill/SKILL.md`. Design notes and measured results: `skill/IMPLEMENTATION.md`.

### License

GPL-2.0-or-later: the tracing core is potrace, both as the compiled `trace_core.dll`
and as the pure-Python `potracer` fallback. See `skill/NOTICE` for the exact split.
`vtracer` (smooth mode) is MIT and outside the GPL path.
