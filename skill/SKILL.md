---
name: generic-pixel-svg-rebuild
description: Use when a bitmap (PNG/JPEG, <=2MP) must be rebuilt as a self-contained HTML of pure flat-color SVG paths at pixel-level fidelity — illustration or photo to "<path>" reproduction with a discrete step player, a measurable per-pixel error budget, and acceptance by screenshot diff rather than by eye.
---

# 像素级 SVG 重绘（与素材无关）

把任意位图重绘成**单文件自包含 HTML**：纯 SVG 平涂 `<path>` + 离散步骤播放器；用逐像素误差闭环验收，不靠目视判断。

## 契约

- 输入：PNG/JPEG，宽×高 ≤ 2MP。JPEG 先解码并**固化无损副本**，之后所有比较只对这份副本（解码器不同结果不同）。
- 输出：`out.html`（播放器）+ `out.preview.html`（同图全量、无 UI，几何评测专用）。`viewBox="0 0 W H"`，每个颜色类一组带 `data-step` 的 `<path>`，内联 JS 播放器（`?step=N` 直达，`data-total` 记录总步数）。
- 禁止：Canvas、位图/base64 内嵌、外链、`<linearGradient>`/`url(#`、任何 CSS transition、抗锯齿（必须 `shape-rendering="crispEdges"`）。

## 脚本（A→G 全链路已固化）

```bash
# 推荐入口：K 自动翻倍 + 逐帧渲染 + 指标，产出 outdir/final.html
python <skill>/scripts/autotune.py ref.png outdir --k-start 1024 --max-k 8192 --gain 3

# 分步
python <skill>/scripts/build_svg.py ref.png out.html [K] [sketchThr]   # A→F，默认 K=2048 sketch=16
python <skill>/scripts/render.py out.html shot.png W H "?step=11&v=1"  # 精确尺寸截图（零依赖，调系统 Edge/Chrome）
node   <skill>/scripts/render.js  out.html shot.png W H "?step=11&v=1"  # 同上，走 playwright-core（需要它时用）
python <skill>/scripts/metric.py  ref.png shot.png                     # mean / ≤3 / ≤5 / ≤10 / ≤20 / uncovered
python <skill>/scripts/steps.py   out.html outdir                      # 逐帧截图 + 拼版 + 单调性/空步检查

# 形态二：真矢量重绘（potrace 曲线平涂，无像素矩形，适合"不要像素感"）
python <skill>/scripts/vectorize.py ref.png out.html [K] [turdsize] [alphamax] [dilate] [blur]
#   K=128 turdsize=12 alphamax=1.0 dilate=1 blur=0.8 为本机照片级源的最佳档

# 形态三：平滑矢量的另一条路（vtracer 样条，颜色更多、路径上万）
python <skill>/scripts/smooth_svg.py ref.png out.svg [precision] [speckle] [layer] [corner]
```

依赖：Python 3 + numpy + Pillow + scipy + potracer（曲线分支）。**截图默认 `render.py`，零依赖**——直接调系统 Edge/Chrome 的 `--headless --screenshot`，找不到时看 `BROWSER_BIN` 环境变量；`render.js`（playwright-core）只在需要它时作为备选，按 `PW_CORE` → cwd/DSH_HOME/DSH_CHECKOUT 向上找 `.pnpm/playwright-core@*`。

回归测试：`python <skill>/tests/run_tests.py`（首次基线用 `--record`）。用例 = 平涂源逐像素、照片源逐像素、照片源曲线、**透明 PNG 曲线**，断言 `uncovered` 不超基线、mean 不超基线 +1.0、以及"单文件/无位图/无渐变/有路径"四条结构检查。

透明图的 uncovered 口径不同：只统计**源图 alpha≥250 的像素**里被渲染成纯白的部分（alpha 128~249 是抗锯齿描边，本来就该柔化），并允许 ≤8 px 的孤立噪点（turdsize 会丢弃 alpha>250 的零星碎片）。

> 注意渲染器影响指标：同一份 HTML 用 playwright 与 Edge CLI 各截一张，mean 相差约 1.6——所以一个项目内必须固定同一个渲染器再做横向比较。

`autotune.py` 停止规则 = 「`≤5/255` 占比增益 < `--gain` 个百分点」，每档打印一行 JSON（`K/classes/paths/MB/mean/≤5/≤10/uncovered`），最后一行的 `runs` 就是交付指标表。

## 三种模式：先问清楚要哪一种

同一张 750×434 照片级立绘的实测（源 `ref-girl.png`）：

| 模式 | 产物 | mean | ≤5 | 观感 | 适合 |
|---|---|---|---|---|---|
| 像素精确（默认 `build_svg.py`） | 游程矩形 4133 path / 4.06MB | **2.16** | 96.7% | 与源几乎一致，**放大有像素台阶** | 逐像素验收、无损可缩放图 |
| 真矢量（`vectorize.py`，K=192 + 2× 超采样） | 193 条曲线 path / 7.36MB / 11 步 | 8.35 | 44.6% | 平涂插画，**无台阶**，细节简化 | "要矢量感"、可缩放插画 |
| 真矢量高保真（K=384 + 3× 超采样） | 385 条曲线 path / 19.7MB | **6.36** | 61.0% | 接近原图，暗部有层次 | 能接受 20MB 的交付 |
| vtracer 样条（`smooth_svg.py`） | 12 034 path / 3.47MB | 7.20 | 65.1% | 接近原图但极碎 | 高保真矢量化 |

互斥关系要讲清楚：`uncovered=0 + mean<2` 只有像素矩形能做到（台阶是游程几何的本质）；曲线拟合必然偏离像素，所以 README 的"平涂路径 + mean<2"对照片级源**不可能同时成立**，只对平涂源成立。用户说"有像素感"就切 `vectorize.py`，并把 mean 从 2.16 变到 11.8 的代价一起说明。

`vectorize.py` 管线：median-cut 调色板 → 每色掩码 → **膨胀 1px**（否则层间留白缝）→ **2× 超采样后描摹再把坐标除回去**（曲线更顺、边界更准，实测 mean 11.8→9.37）→ potrace 拟合曲线 → 按色族分批、族内按面积从大到小叠放 → 底层垫主色 `<rect>` → 每层一个 `data-step`。

关键调参（同一张照片级源，逐项实测）：

| 改动 | mean | 说明 |
|---|---|---|
| K=128, 无超采样 | 11.8 | 基线 |
| + 2× 超采样 | 9.37 | 只增加描摹分辨率，路径数不变 |
| + K=192 + 色族排序 | 8.35 | 体积 7.36MB |
| + K=384 + 3× 超采样 | **6.36** | 体积 19.7MB / 385 层，暗部误差 6.96→4.75 |

**分层色板（暗区单独切）是可选旋钮，不是必要修复**：暗部丢层次的真解是提 K + 超采样（仓库 §B/§5 的唯一路径，实测把暗部误差 6.96→4.75）。`vectorize.py` 的第 10 个参数 `dark_k` 只在对暗部有额外要求时再开，K=192 的对照：单色板 mean 6.87/暗部 5.40 → 96/96 分层 mean 7.25/暗部 4.42（整体略升换暗部更细）。默认关。

**调色板不要换 Lab**（实测否决）：同一张图 K=192，RGB median-cut 的 mean 6.87 / 暗部 5.40，Lab 空间是 8.43 / 6.05——Lab 的 L 跨度大会让切分偏向亮度层，暗区反而分到更少色阶。仓库坚持 RGB median-cut 是对的；暗部层次的唯一有效手段就是提 K（或对暗区单独再切一层色板，属未验证扩展）。

瓶颈不在拟合参数，而在**颜色数**：平涂矢量每少一档颜色，渐变区就多一条能看见的色带。README 的 mean<2 只有像素矩形能达到。

## 七阶段

1. **A 归一化** — 无损 RGB，记录 W,H；每次任务一个 ref/ 目录，源/预览/截图/diff 分开。
2. **B 调色板** — median-cut（脚本内置），**不要 k-means**（照片级分布会留 30+/255 的坏中心）。K 从 1024 起步翻倍，判据是「≤5 占比」与 mean，不是 K 本身；增益 <3pp 即停。
3. **C 打标** — 最近中心全图赋值（cKDTree）。
4. **D 几何** — **逐像素游程矩形**（`M x y h w v1 h-w Z`，整数坐标，`crispEdges`）。按构造精确复现掩码，互不重叠所以不需要 z-order；未覆盖恒为 0。单 `<path>` 子路径 ≤300（否则渲染器静默截断，P3）。
5. **E 步骤语义** — **必须按色族推进**：先把 K 个调色板中心聚成 ~8 个色族，按色族总面积排序（背景族=1，其余 2..11）；**按单类面积排序会得到噪声帧**（详见 P8）。末步必须全量；亮肤色判定排在「近白」之前。
6. **F 播放器** — 只切 `class`，零过渡；`#art [data-step]{display:none}` + `.on{display:inline}`；`data-step` 打在 `<path>` 上而不是包 `<g>`；步数从 `data-total` 读，别硬编码。
7. **G 验收** — 无头浏览器截图 → `metric.py` → 按瓶颈归因（§调参决策树见 references/manual.md）。

线稿层（可选，`sketchThr>0`）：对亮度做梯度阈值取边缘游程，单独一层 `data-step="0"`，只在 step 0 显示（`#33445c` @0.55）。它是独立的「起稿」帧，不参与 1..N 的累加，所以单调性检查从第 2 步起算。

## 验收（必须贴实测数字，不许写「已验证」）

| 指标 | 目标 |
|---|---|
| uncovered（渲染纯白 ∧ 源不纯白） | **0** |
| mean 通道误差 | **< 2/255** |
| ≤5/255 像素占比 | ≥ 90% |
| ≤10/255 像素占比 | 100%（**平涂源**；见反锯齿例外） |

实测（本机，两档源）：

| 源 | K | 产物 | mean | ≤5 | ≤10 | uncovered |
|---|---|---|---|---|---|---|
| 1280×800 平涂插画 | 1024 | 1134 path / 4.05MB | 0.43 | 99.72% | 99.75% | 0 |
| 750×434 游戏立绘（照片级+AA） | 4096 | 4085 path / 4.06MB | **2.16** | **96.67%** | 99.89% | 0 |
| 1200×675 透明 PNG（RGBA，74% 透明），曲线 | 192（2× 超采样） | 193 path / 7.55MB | **3.74** | **83.03%** | 91.53% | 3 |

透明源的两条硬规则（都踩过）：① 直接把 RGBA `convert("RGB")` 会把透明区变成**黑底**，必须先按 alpha 合成白底再量化；② 透明模式输出**不带底色 `<rect>`**，SVG 背景真透明（验收方法：把 SVG 垫在纯洋红/棋盘格上截图，角落像素必须是底色本身）。

**反锯齿例外（重要）**：源图带 AA 边缘时「100% ≤10」不可达——整数游程无法复现亚像素渐变，只能逼近。此时换口径写清：`uncovered=0、mean<3、≤5 ≥95%`，并说明瓶颈在源图 AA 与量化上界，而不是硬凑数字。

## 截图三要素（缺一必错）

1. 缩放强制 1:1（系统 DPI 会把右缘整条裁白）；
2. 窗口高 ≥ 图高 + 120（视口不够会在画面中部整体截断，伪装成内容缺失）；
3. URL 带版本号（`file://` 缓存会让指标「冻结」）。

`render.js` 已内置这三条，且尺寸取自 `viewBox`（`steps.py` 同理，不靠截图反推）。

## 逐帧验收（steps.py）

对 `0..data-total` 逐个渲染，输出每步非白占比、`monotonic_violations`、`flat_tail_steps`、拼版 `contact-sheet.png`。判据：

- `final_cover` 必须等于全量渲染（否则有 path 被漏掉）；
- `monotonic_violations` 为空（步骤只能累加；step 0→1 因线稿层单列，不计）；
- `flat_tail_steps` 为 0（末尾若连着多步占比相同 = 步骤阈值没铺满，P8）；
- 目视拼版：每一步应该像「多刷一类颜色」，而不是随机噪点或整块缺失。

## 失败模式对照

| 症状 | 根因 | 修复 |
|---|---|---|
| 全图边界细亮线 | 像素中心 vs 格点差半像素 | 整数边界矩形，或坐标 −0.5 |
| 一侧固定宽度全白 | 截图继承 DPI 缩放 | 强制 scale=1 |
| 某大色域整块缺失 | 单 path 子路径过多被静默截断 | 分块 ≤300 |
| 横向/纵向整块缺失 | 无头窗口小于画面 | 窗口 ≥ 图高+120 |
| 中间步像随机噪点 | 按单类面积排序（类极碎） | 按色族排序 |
| 末尾两步画面相同 | 步骤阈值铺不到末步 | 步数按 data-total 动态算 |
| 指标随 K 变大反而变差 | 几何方案对碎片敏感 | 换游程矩形 |
| 平涂区色差大 | 聚类欠收敛 | median-cut |
| 改参数指标不变 | file:// 缓存 | 版本号 |
| uncovered>0 但目视全覆盖 | 类均值四舍五入成纯白 | 类内非纯白时钳到 254 |
| 不同工具比较结果不同 | JPEG 解码器不一致 | 固化无损副本 |
| 矢量版整张只剩一个底色 | potracer 约定 True=白底（描 0 值区），掩码没取反 | `Bitmap(~mask)` |
| 矢量版层间白色描边/缝隙 | 相邻层各自拟合，边界不重合 | 掩码膨胀 1px + 底层垫主色 rect |
| 矢量中间步几乎无变化 | 用非白占比当进度指标（底色覆盖全画布） | 改看相邻步像素差 `step_delta` |
| 透明 PNG 输入后背景变黑块 | RGBA 直接转 RGB，丢掉 alpha | 先按 alpha 合成白底再量化 |
| 透明 PNG 输出仍带白底/方块 | 透明模式照样铺底色 rect | 检测 alpha 后跳过底色 rect，透明像素标 `-1` 不参与描摹 |
| 大 K 曲线分支 `ZeroDivisionError` | 调色板有空类（无像素选中的中心）面积 0 | 先剔空类，面积加权再除 `max(1, area)` |
| 透明图 uncovered 虚高 | 拿半透明描边像素当"漏画" | 只统计 alpha≥250 的像素 |

## 本仓库踩过的坑

- **PIL `Image.quantize(colors=K)` 上限 256 色**，K=1024 直接抛错——必须自己写 median-cut。
- 用 `%s` 拼 SVG 到 HTML 时，JS 里的 `cur%12` 会被当格式符 → `TypeError: not enough arguments`。用 `str.replace` 占位符。
- 技能目录在 checkout 内，`package.json` 的 `"type":"module"` 会让 `render.js` 的 `require` 报 ESM 错误——`scripts/package.json` 已声明 `commonjs`。
- 460~4000 类、1~4k path、4MB 单文件属正常量级（游程编码体积增长很缓），不要因此降 K。
- 纯白保护：某类均值四舍五入成 `#ffffff` 但类内含非白像素时，渲染出的纯白会被记成 uncovered（实测 34px）——钳到 254 即归零。
- 透明图的"白底"和"透明"在 PNG 里看不出来：验收必须换底色（洋红/棋盘格）截图，角落像素 = 底色才算真透明。
- 透明源的对照图必须是**白底合成版**（浏览器截图不带 alpha），否则 black-vs-white 会把 mean 拉到几十。

完整原始手册（伪代码 + 调参决策树 + 坐标约定）：`references/manual.md`。
