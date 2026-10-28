---
name: generic-pixel-svg-rebuild
description: >-
  Redraw / vectorize a bitmap (PNG/JPEG, any size) as real SVG - flat-color <path> geometry,
  no <image>, no base64, no gradients - delivered as a static .svg or a single-file HTML step
  player, with a measured per-pixel error (mean / percentile / uncovered) instead of eyeballing.
  Use it whenever the job is "turn this picture into SVG", including plain "重绘这个 / 转成SVG" requests.
whenToUse: >-
  The default choice for image-to-SVG redraws - 重绘, 重绘这个, svg重绘, 矢量化, 转SVG, SVG 化,
  描摹, 去掉像素感, 生成可播放的分层绘制过程, 把图片做成纯 path 的单文件 HTML, 批量 PNG/GIF 转 SVG,
  or judging whether a redraw is faithful. English - redraw as SVG, vectorize, trace this image,
  bitmap to SVG, rebuild this screenshot as paths, convert PNG to SVG. Prefer this over generic
  vision or OCR skills when the deliverable is SVG artwork (those are for UI restoration, text
  extraction and element location, not for faithful raster-to-path reproduction).
---

# 像素级 SVG 重绘（与素材无关）

把任意位图重绘成**单文件自包含 HTML**：纯 SVG 平涂 `<path>` + 离散步骤播放器；用逐像素误差闭环验收，不靠目视判断。

## 什么时候就是本 skill（别绕路）

用户说「重绘这个 / svg重绘 / 转成SVG / 矢量化 / 去掉像素感」并给了图，**直接走本 skill**：

| 情况 | 归属 |
|---|---|
| 要一张 SVG 画（保真、可分层播放、带误差数字） | **本 skill**（`vectorize.py` / `build_svg.py`） |
| 要 UI 还原、按钮/表单，或从截图生成前端代码 | `vision-skills` |
| 要 OCR、元素定位、像素比对、取色 | `vision-skills` 或视觉工具 |
| 要"凭空画一张图"（无参考图） | 都不适用，手写 SVG |

`vision-skills` 里的 SVG 描摹是**通用素材工具**；本 skill 多了三件事它没有：**可度量的误差闭环**（mean / ≤5 / uncovered）、**分层步骤播放器**、**批量巡检与回归测试**。同样一张图，先跑这里的 `metric.py` 再决定要不要换路线。

## 契约

- 输入：PNG/JPEG，宽×高 ≤ 2MP。JPEG 先解码并**固化无损副本**，之后所有比较只对这份副本（解码器不同结果不同）。
- 输出：`out.html`（播放器）+ `out.preview.html`（同图全量、无 UI，几何评测专用）。`viewBox="0 0 W H"`，每个颜色类一组带 `data-step` 的 `<path>`，内联 JS 播放器（`?step=N` 直达，`data-total` 记录总步数）。
- 禁止：Canvas、位图/base64 内嵌、外链、渐变（`<linearGradient>`/`<radialGradient>`/`fill|stroke="url(...)"`）、任何 CSS transition。像素分支必须 `shape-rendering="crispEdges"`；曲线分支用 `geometricPrecision`。
- 可选后处理 `--edge-soften=N` 会引入一个 `feGaussianBlur` 滤镜，是唯一的 `url(#)` 例外，默认关闭；它不改变路径几何。

## 默认入口：先跑 auto.py（别手调参数）

**除非用户明确给了参数，否则不要自己猜 K / ss / turd。** 一条命令跑完并打印测量对比：

```bash
python <skill>/scripts/auto.py ref.png out.svg
```

它会依次跑 3 个预设（illustration / portrait / photo），每档都真实渲染 + 算 mean / ≤5 / uncovered，按 `score = mean + 0.35·log2(kb/100)` 选一个（精度与体积的加权，不是纯精度），把最好的复制到 `out.svg`，并输出：

```json
{"picked": "photo", "mean": 2.935, "kb": 11252,
 "candidates": [{"preset": "illustration", "mean": 3.343, "kb": 9866, "score": 5.66},
                {"preset": "portrait", "mean": 3.323, "kb": 30000, "score": 6.20},
                {"preset": "photo", "mean": 2.935, "kb": 11252, "score": 5.32}]}
```

| 预设 | K | ss | 额外 | 适合 |
|---|---|---|---|---|
| `icon` | 32 | 2 | — | 图标、扁平插画（`--try-all` 才跑） |
| `lineart` | 24 | 3 | — | 线稿、墨线（`--try-all` 才跑） |
| `illustration` | 128 | 2 | — | 二次元/角色画，均衡默认 |
| `portrait` | 96 | 2 | alpha 环 + 真实色 6 级 + refine 1 遍 | 柔光人像、皮肤过渡 |
| `photo` | 192 | 2 | — | 照片、写实 |

单独跑某档：`auto.py ref.png out.svg --preset portrait --keep runs/`（`--keep` 保留所有候选文件，便于人工对比）。

区域预算也能透传，同一套框会喂给每个候选（否则比较不公平）：

```bash
python <skill>/scripts/auto.py ref.png out.svg --region 150,200,330,430:384 --region-mask hand.png:256,3
```

`--region` / `--region-mask` 都可重复。实测 400×711 人像，illustration 预设：脸区 mean 8.17 → **4.98（-39%）**，体积 5.78MB → 6.94MB，背景不变（5.33）。输出 JSON 会多一个 `regions` 字段回显实际用的框。

**报错或结果明显不对时**：先看 `candidates` 里每档的 `mean`，不是看 `picked`。某档 `mean` 异常高通常说明该预设不适合这类图（例如线稿跑 photo），换预设比调参更快。

## 区域预算：脸用高色数，背景用低色数

```bash
python <skill>/scripts/vectorize.py ref.png out.svg 48 6 1.0 1 0.4 2 0 --region 150,200,330,430:384
```

`--region x0,y0,x1,y1:K[,SS]` 可重复；每块区域用自己的色数、可选自己的超采样**独立重描**，覆盖在全局层之上（内部用 `<g transform="translate(x0,y0)">` 定位，不动全局几何）。全局用低 K 省体积，只给主体/脸花钱。

**K 可以写绝对值，也可以写倍数**：`:384` = 固定 384 色；`:3x` = 全局 K × 3（预设换成 photo/K=192 时它自动变 576）。想“固定脸的精细倍数”用 `Nx`，想“固定输出体积”用绝对值。两者实测一致：全局 K=48 时 `:8x` 与 `:384` 得到完全相同的脸部 mean 5.92。

**非矩形区域**用遮罩：`--region-mask face.png:384,3`（白色=要重绘的像素，黑色=不动；遮罩按原图分辨率给，自动取包围盒后裁剪重描）。

**过程分支同样支持**：`--process-final ... --region 80,40,240,150:96` 会把区域层追加到过程最终画面之上（实测 320×180 冒烟正常，227KB）。

实测 400×711 人像（全局 K=48，ss=2）：

| 配置 | 体积 | 全图 mean | **脸部 mean** | 背景 mean |
|---|---|---|---|---|
| K=48 | 2.5MB | 8.23 | 11.22 | 7.72 |
| K=48 + 脸区 K=384（ss 继承 2） | **3.55MB** | 7.46 | **5.92（-47%）** | 7.72（不动） |
| K=48 + 脸区 K=384, **ss=3** | 4.28MB | 7.36 | **5.24** | 7.72 |
| K=48 + 脸区 K=384, **ss=4** | 5.1MB | 7.31 | **4.90** | 7.72 |
| K=48 + 遮罩区 K=384, ss=2 | 3.31MB | 7.59 | 6.79 | 7.72 |
| K=96（全局提升） | 4.02MB | 6.35 | 8.94 | 5.90 |

**同样的体积，区域预算把脸做到 5.93；全局加 K 只到 8.94。** 背景变好是全局加 K 的副作用，代价是脸仍然糊。人像/角色画应该用前者。

注意：区域按**原图像素坐标**，它不识别五官——**框/遮罩由模型或人给，脚本只执行**。多块区域依次叠上去，后写覆盖先写。

## 第四入口：iterate.py（渲染 → 定位偏差 → 打补丁）

前三个入口都是"给参数、生成一次"；这个入口**看着自己的输出改**。

```bash
# 自动档：最差分块自动加预算
python <skill>/scripts/iterate.py ref.png out.svg --base "48 6 1.0 1 0.4 2 0" --rounds 3

# 模型档：补丁由模型看完分块报告后手写，脚本只执行
python <skill>/scripts/iterate.py ref.png out.svg --base "..." --patch patch.json
```

补丁是数据，不是代码，所以模型可以直接产出：

```json
{"ops": [{"op": "region", "box": [150, 200, 330, 430], "k": 384, "ss": 2},
         {"op": "region", "box": [120, 440, 300, 560], "k": 192}]}
```

补丁支持四类 op：

| op | 作用 | 典型用途 |
|---|---|---|
| `region` | 局部加色数/超采样 | 脸、手、主体 |
| `style` | 用新参数**整幅重跑**（K/turd/alphamax/dil/blur/ss） | 换画风：平涂、描边、粗线 |
| `remap` | 改颜色：`from/to/tol` 或整幅贴合 `to_palette` | 统一色调、海报化、限定色板 |
| `draw` | 注入模型自己写的 path `d` | 补细节、加签名、**从零作画** |
| `drop` | 删路径：`ids:[..]` 或 `where:{fill:"#rrggbb"}` | 去掉描歪的线、噪点层 |
| `recolor` | 改路径颜色（同上选择器） | 局部修色 |
| `redraw` | 替换某条路径的 `d`（和可选 `fill`） | 改一根线的走向 |
| `reorder` | 把选中的路径移到最上层 | 修遮挡顺序 |

**路径句柄**：只有出现 `drop/recolor/redraw/reorder` 时才给每条 `<path>` 注入 `id="pN"`（发射顺序），默认输出**一字节不变**。`ids` 用的就是这个 N。

`redraw` 只换几何**不换层级**——它留在原 z 序上，所以“补一条盖住别人的线”要用 `draw`，“改一根已有线的走向”才用 `redraw`。

**从零作画**：`--blank 420x300` 从空白画布起步，内容全靠 `draw` 给（不需要参考图、不跑描摹）：

```bash
python <skill>/scripts/iterate.py - out.svg --base "2 6 1.0 0 0 1 0" --blank 420x300 --patch draw.json
```

实测 7 条 path / 0.8KB 出一张完整风景（天空、太阳、云、两层山、两只鸟）。**能画的是符号化的东西**：图标、logo、UI 元素、扁平插画、示意图；照片级和复杂人像仍然不行（token 带宽 + 没有先验）。

**默认是 ask 模式**（模型在环）：跑完一版就停下，吐决策点，不自己往下改。

```json
{"mode":"ask","mean":8.2331,"kb":2520.7,
 "worst_tiles":[{"box":[200,356,267,474],"mean":17.083,"suggest_k":96}],
 "preview_png":"out.preview.png",
 "patch_schema":{"ops":[{"op":"region","box":[100,100,200,200],"k":384,"ss":2}]},
 "next":"iterate.py ref.png out.svg --base \"48 6 1.0 1 0.4 2 0\" --patch <file>"}
```

模型看 `worst_tiles` + 预览图 → 写补丁 → 再跑一次 → 又拿到新决策点。

**补丁不跨轮累积**：每轮都是"底图参数 + 本轮全部补丁"重建，后面几轮必须把之前的区域一起带上（`--patch` 可重复给多个文件，按顺序叠加）；ask 输出里的 `applied_ops` 就是本轮实际生效的补丁，下一轮直接拼接它。


### 元素级预算（推荐，替代矩形/多边形）

```bash
# 自动把画面切成连通色块（元素），按面积+内部复杂度给每块独立色数
vectorize.py ref.png out.svg 8 6 1.0 1 0.4 1 0 --elements 16:800:384,2
#                              颜色数:最小面积:单元素上限[,ss]
```

边界落在真实色块轮廓上，所以**没有矩形接缝**。实测 ref-star.png：55 个元素 / mean 9.14 / 2.9MB（矩形区域方案 11.09，平铺 6.87）。
代价：分割错了会把一块底色当成元素高预算描（额头/嘴部会出现杂色条），需要调 `最小面积` 与 `单元素上限`。


### 元素级再细化（`--elements-refine R`）

```bash
# 元素预算 + 只重描残差最大的 25%，可迭代 R 轮
vectorize.py ref.png out.svg 24 6 1.0 1 0.4 2 0 --elements 16:800:512,2 --elements-refine 1
```

每轮：渲染 → 按元素掩码量残差 → 最差 25%（误差 >6 级）把 K 翻倍、ss+1 重描 → 覆盖到原图之上。只重画没画好的部分，成本远低于整体重跑。

| 方案 | mean | 体积 |
|---|---|---|
| 一次性 K=48/ss2 | 8.23 | 2.5MB |
| 平铺 2×3 + 共享调色板 | 6.87 | 3.6MB |
| 元素（K=8 底图） | 9.14 | 2.9MB |
| 元素 + refine×2（K=8 底图） | 8.98 | 3.4MB |
| **元素 + refine×1（K=24 底图）** | **6.65** | 3.9MB |

### 接缝默认处理（有区域时自动生效）

- `--region-snap-tol 10`：区域颜色若底图能表达（距底图色 ≤10）就吸附成底图色 → 边界不跳色
- `--region-feather 2`：掩码外扩 2px，把边界推进到颜色一致处
- `--no-region-snap` / `--region-feather 0` 可关闭

### 矩形边界 / 接缝 / 不规则区域

区域默认**各自算调色板**，所以叠在低 K 底图上会看到矩形色差。三种解法：

```bash
# ① 整幅平铺：没有"低预算底图"，接缝只落在同等预算的相邻块之间
vectorize.py ref.png out.svg 8 6 1.0 1 0.4 1 0 --tile 2x3:192,2 --palette-shared=192

# ② 不规则区域：多边形（点用 x,y 空格分隔）或任意 PNG 遮罩
vectorize.py ref.png out.svg 8 6 1.0 1 0.4 1 0 \
  --region-poly "170,180 250,160 300,320 280,470 195,480 150,350:384,2" --region-feather 4
vectorize.py ref.png out.svg 8 6 1.0 1 0.4 1 0 --region-mask face.png:384,2

# ③ 共享调色板：多个区域都用同一份 palette，边界不再跳色
--palette-shared=192     # 取整幅图 192 色作为所有区域（含平铺）的公共调色板
--region-feather 4       # 掩码外扩 4px，把接缝推进到颜色一致的地方
```

实测：2×3 平铺 + 共享调色板，mean 6.87（比一次性 K=48 的 8.23 还低），3.5MB；只给脸做多边形区域（底图 K=8）时 mean 20.56，但脸是清晰的——预算花在哪儿一目了然。

临摹一张图的推荐节奏：低 K 底稿（`--base "8 6 1.0 1 0.4 1 0"`）→ ask 看最差块 → 按 `suggest_k` 给区域加预算 → 每轮带上全部区域 → 需要更锐利时再叠 `ink.py --style line --line-occlude` 线稿层。

**从零作画也走同一个闭环**（没有参考图，改为写/改自己的 path）：

```bash
# 第一轮：空画布 + 全部图层
iterate.py - yy1.svg --blank 420x600 --patch spec.json
# 之后每轮：以上一轮的 svg 为底稿，只发改动
iterate.py - yy2.svg --from-svg yy1.svg --patch fix.json
```

**定形 + 量比例**（没有参考图时唯一的"尺子"）：

```bash
python scripts/form.py yy4.svg --out form.json --guides preview.png
# ratios: head_w_over_h / eye_line / neck_over_head / shoulder_over_head
#         head_on_canvas / head_over_canvas_w / hair_over_face
# failed: 哪几条超出容差
```

`--guides` 写一张带「头高刻度尺 + 中轴 + 脸框 + 眼线」的预览图，只用于看，不进成品。比例不对时用 `scale` / `move` 补丁整体调形体，而不是重画每个点。

ask 在有参考和无参考两种情况下都会停住，输出 `preview_png`、路径数、颜色数，以及下一轮命令；`--base` 可省。想回到脚本自己挑块（无模型），加 `--mode auto`。

自动档规则：按 `--grid`（默认 8）分块算渲染误差，每轮挑最差 `--top` 块，预算取 `global_K × k_mult^round`（默认 2×，上限 `--k-max`），收益低于 `--min-gain`（默认 0.05）就停。已覆盖的块不会重复补。最终只保留最好的一轮。

实测 400×711 人像（base K=48 ss=2）：

| 轮次 | mean | 体积 | 补丁 |
|---|---|---|---|
| 0（无补丁） | 8.23 | 2.5MB | — |
| 1 | 7.48 | 3.1MB | 4 块 K=96 |
| 2 | **6.80** | 3.9MB | 4 块 K=192 |

模型补丁档（两个框：脸 384/ss2 + 上身 192）：8.23 → **6.91**，3.9MB，一轮完成。

## 前置分层：ink.py（线稿 / 黑白海报）

不是 vectorize 的开关，而是**给模型调用的辅助件**：一次只出一种层，打印 JSON（前景像素 / 路径数 / 体积），模型比完数字再决定要不要叠。

```bash
# 黑白海报层：实心块直出，最省
python <skill>/scripts/ink.py ref.png --style poster --method sauvola --out poster.svg

# 线稿层：边缘 → 细化 → 描边；dilate=0 是发丝线
python <skill>/scripts/ink.py ref.png --style line --lo 110 --hi 240 --dilate 0 --out line.svg

# 两层叠起来（poster 打底，line 压上）；不给 --out 时写 outdir/composed.svg，不覆盖单层
python <skill>/scripts/ink.py ref.png --style both --outdir layers

# 彩色 poster 层（k 色调色板，按面积分层）
python <skill>/scripts/ink.py ref.png --style poster --poster-colors 8 --turd 8 --out pc8.svg

# 遮挡/冗余裁剪：底图已经还原好的地方，线就不再画（可选再给前景遮罩）
python <skill>/scripts/ink.py ref.png --style line --line-per-stroke \
  --line-occlude base.svg --line-occlude-tol 10 --line-clip foreground.png \
  --compose base.svg --out comic.svg

# 彩色底 + 彩色线：先把彩色底生成出来，再把无背景的线层叠上去
python <skill>/scripts/vectorize.py ref.png base.svg 16 4 1.0 1 0.3 2 0
python <skill>/scripts/ink.py ref.png --style line --line-per-stroke --compose base.svg --out comic.svg
```

实测 400×711 人像：

| 调用 | 前景 px | 路径 | 体积 | 观感 |
|---|---|---|---|---|
| poster（sauvola） | 47,851 | 147 | 82.2KB | 黑白海报/木刻，实心块 |
| line（canny 60/160, dilate 1） | 41,908 | 121 | 70.8KB | **偏重**：暗部纹理糊成块 |
| line（canny 110/240, dilate 0） | 9,427 | 168 | 73.2KB | **真线稿**，发丝细线 |
| both 合成 | — | 268 | 153.0KB | 漫画感 |

**线不必是黑的**。三种取色方式：

| 参数 | 效果 |
|---|---|
| `--line-color "#334455"` | 整层统一色 |
| `--line-colors 4` | 在**线条像素**上按参考图聚类，一类线一种色（彩色线稿） |
| `--darken 0.85` | 彩色时压暗，保住"线"的分量感 |
| `--poster-color "#5a3d2b"` | 海报层也可以染色（单色印刷/棕褐） |

第四种是**逐线取色**：`--line-per-stroke` 把 mask 拆成连通块，每块单独描、单独取参考图上的中位色——**每条线颜色都不同**，不用先聚类。

`--line-source edges`（默认）直接描 Canny 边缘，线**更完整**；`skeleton` 走细化，能控制粗细但会切断细线。

实测 400×711 人像：

| 模式 | 路径 | 颜色数 | 体积 | 前景 px |
|---|---|---|---|---|
| 黑线（edges, dilate 0） | 231 | 1 | 109.7KB | 13,872 |
| 4 色聚类 | 774 | 4 | 222.7KB | — |
| **逐线取色（edges）** | 231 | **177** | 109.3KB | 13,872 |

**线宽分段**（不是全局加粗）：`--line-width auto` 给每段线单独算宽度——用该线处的 Sobel 梯度强度（强轮廓粗、纹理细），并加了三道保护：小闭环（眼睛/鼻孔）不加粗、加粗后会碰到邻线的不加粗、`area<150` 或跨度 `<40px` 的不加粗。实测 238 段线里只有 17 段变成 3px，体积 +2.5KB（102.2KB），其余保持 1px。

**完整性的两个杀手**：`--dilate 1`（把细线加粗成块）和 `--min-stroke-px 25`（把小笔画整条丢掉）。现在默认是 `dilate=0` + `min-stroke-px=1`，产出与"裸 Canny 直接描"一致（参考：239 路径 / 115.4KB / 14,013 px）。

注意：逐线取色比 4 色聚类**体积小一半**（94.3KB vs 222.7KB）——聚类会把同色但不相连的线段反复描，逐线则每个连通块只描一次。

**参数坑**：柔光照片默认阈值（60/160）会把头发纹理全当边，细化后再膨胀就是一片黑。线稿必须先 `--dilate 0` 看骨架，再按需加粗；阈值下限抬到 110 以上才干净。

## 归因：attribute.py（别让模型猜 id）

```bash
python <skill>/scripts/attribute.py ref.png out.svg --top 8
```

做法：把整幅图**再渲染一次**，每条 `<path>` 换成唯一索引色 → 得到"这个像素归谁"的地图；再用真实渲染与参考图算误差，**只把像素记在它真正可见的那条路径上**。一次渲染出全表。

每行给：`id` / `fill` / `px` / `mean` / `sum` / `share` / `inner` / `ring` / `guess` / `gain` / `suggest_fill` / `box`。

| 字段 | 含义 |
|---|---|
| `guess=shape` | 最优填充色与现值差很远，或轮廓误差 > 内部误差 → **位置/形状错** |
| `guess=colour` | 轮廓正常、内部整体偏色 → **换色即可** |
| `gain` | 换成 `suggest_fill` 能回收多少误差总和（按它排序） |

**受控验证**：把画好的场景里太阳 path 挪到别处再跑归因——$id=2$ 排第一，mean 126.26，`guess=shape`，`suggest_fill` 直接给出“这块底下其实是背景色”的证据。

**真实人像上的发现**：48 色量化后，最差的 path 是**颜色类**（bbox 横跨整幅），误差集中在轮廓（`ring > inner`）——所以换色几乎没用（mean 8.23 → 8.13），该加的是预算（`--region`），不是手术。**路径手术只对手绘/线稿类有意义**，对量化照片没有“一条线”可修。

闭环用法：`attribute.py` 看排名 → 组补丁 → `iterate.py --patch` → 复测（mean 与 `total_err_sum` 都看）。

## 脚本（A→G 全链路已固化）

**默认用 `vectorize.py` 做 SVG 重绘；`build_svg.py` 的像素矩形模式已弃用**，只保留作逐像素误差参考，不再作为默认或交付。`paint_process.py` 是显式可选分支，用户没有点名“作画过程 / 回放”时不要调用；但 `vectorize.py --process-final` 可复用它的**最终画面算法**（默认关闭，无回放）。

```bash
# 默认：SVG 矢量重绘（高配基准 K=384 / 3× 超采样，可继续扫参）
python <skill>/scripts/vectorize.py ref.png out.html 384 6 1.0 1 0.4 3 0

# 常规档：K=192 / 2× 超采样
python <skill>/scripts/vectorize.py ref.png out.html 192 6 1.0 1 0.4 2 0

# 静态 SVG 交付：输出名用 .svg，自动去掉 data-step / data-total
# 播放标记只属于 HTML 分支；过程模式才按绘画阶段分 step
python <skill>/scripts/vectorize.py ref.png out.svg 128 6 1.0 1 0.4 1 0
# K 不给时默认 128 并打印提示；交付档一律显式写 256~384

# 补缝：把层与层之间的细缝用最近色填上（dilate=0 或低 K 有明显白缝时用）
# 只填"细"缝（距离变换 ≤1.5px）和小噪点；要求有不透明底色 rect，透明图会明确跳过
python <skill>/scripts/vectorize.py ref.png out.html 128 6 1.0 0 0.4 1 0 --seal
python <skill>/scripts/seal_gaps.py out.svg shot.png out-sealed.svg   # 也可独立对已有产物跑

# 可选边缘后处理（默认关闭）：feGaussianBlur 柔化最终边缘，不改变几何；sigma 约 0.15~0.6
python <skill>/scripts/vectorize.py ref.png out.svg 192 6 1.0 1 0.4 2 0 --edge-soften=0.35

# 慢速版：几何 alpha 环软化色阶断层（默认关闭，与 --edge-soften 互斥）
python <skill>/scripts/vectorize.py ref.png out.svg 384 6 1.0 1 0.4 3 0 --alpha-soften 2 --alpha-strength 0.25
python <skill>/scripts/svg_postprocess.py out.svg out-soft.svg --soften 0.35
# 注意：--edge-soften 与 --seal 同用时，seal 会跳过（柔化必须是最后一层）

# 过程算法只出 final（默认关闭）：不生成批次/播放器，只把最终画面写成静态 SVG
python <skill>/scripts/vectorize.py ref.png final.svg --process-final --process-palette=256 \
  --process-line-direction=line-direction.json
# 过程 final 的追踪参数全部可调，这些是参数自身的默认值：
#   --process-base-scale=0.34 --process-mid-scale=0.52 --process-detail-scale=0.85
#   --process-min-areas=24,8,2  --process-blurs=1.0,0.5,0.18
#   --process-focus-box=0.10,0.08,0.90,0.92 --process-focus=1.0,4,0,1
#   --process-dil=1 --process-turd=1 --process-alphamax=1.0 --process-ss=1.0
# --process-from-front 默认开启：继承前排 turd/alphamax/dil/ss，以及 --full-palette /
# 显式 --palette-sample。显式 --process-* 优先；显式给了 --process-*-scale 时不再乘 ss。
# blur 不继承（过程是逐层 1.0/0.5/0.18）；sk_thr/dark_k/med 没有过程对应。
# 旧版固定行为复现加 --no-process-from-front；要显式控制倍率用 --process-ss=N。
# 过程全像素色板：--process-full-palette（等价 --process-palette-sample=0）；
# 也可 --process-palette-sample=N 指定抽样像素；--no-process-full-palette 关闭继承。
# 色数上限默认 1024：--process-palette-max=N（paint_process.py 直连用 --palette-max=N）。
# <=256 色无论是否 full 都用整图 PIL median cut。

# 无损压缩（默认关闭）：绝对路径改相对坐标、去掉重复属性；不删路径、不改几何
python <skill>/scripts/vectorize.py ref.png out.svg 192 6 1.0 1 0.4 2 0 --compact
python <skill>/scripts/svg_compact.py out.svg out-compact.svg
# 可与 --process-final / --edge-soften 组合；--compact-precision=N 控制小数位（默认 6）

# 极致色板：全量像素做 median cut（Windows x64 原生 palette_core.dll）
# 默认是抽样 24 万像素 + cKDTree 全量分配；--palette-sample=N 可调抽样数
python <skill>/scripts/vectorize.py ref.png out.html 384 6 1.0 1 0.4 3 0 --full-palette

# 已弃用：像素矩形，仅历史/逐像素误差对照；运行会打印 DEPRECATED
python <skill>/scripts/build_svg.py ref.png out.html [K] [sketchThr]   # A→F，默认 K=2048 sketch=16

python <skill>/scripts/render.py out.html shot.png W H "?step=11&v=1"  # 精确尺寸截图（零依赖，调系统 Edge/Chrome）
node   <skill>/scripts/render.js  out.html shot.png W H "?step=11&v=1"  # 同上，走 playwright-core（需要它时用）
python <skill>/scripts/metric.py  ref.png shot.png                     # mean / ≤3 / ≤5 / ≤10 / ≤20 / uncovered
python <skill>/scripts/steps.py   out.html outdir                      # 逐帧截图 + 拼版 + 单调性/空步检查

# 形态三：平滑矢量的另一条路（vtracer 样条，颜色更多、路径上万）
python <skill>/scripts/smooth_svg.py ref.png out.svg [precision] [speckle] [layer] [corner]

# 形态四（特殊可选，默认不启用）：画师分层过程模拟
# 只有当用户明确要求“作画过程 / 过程回放 / 绘画图灵测试”时才使用；
# 平时的 SVG 交付走 vectorize.py；像素/autotune 已弃用，不要再作为默认。
python <skill>/scripts/paint_process.py ref.png out.html --size 876x1904 --duration 58 --title "她与金线" --palette 256
# 阶段：起稿勾线 → 铺大色 → 分区平涂 → 二分光影 → 深入细化 → 遮罩细化 → 闭塞塑形 → 藏色 → 罩染调色 → 提亮补色 → 收线高光
# 输出 out.html + out.preview.html；支持 ?static=1&ui=0、?step=N
# 这是算法过程模拟，页面必须保留「AI 生成 / 算法路径模拟」标注，不能包装成真人作画录像
# 注：以上数值是基准建议，不是固定档位；最高档需按目标（静态保真 / 过程可信）和预算扫参，取 Pareto 最优点。
```

依赖：Python 3 + numpy + Pillow + scipy + potracer（曲线分支）；原生 DLL 的 ctypes 绑定集中在 `scripts/native_loader.py`（两个脚本共用，缺失/DLL 报错时自动回退纯 Python，结果曲线一致：实测 Δmean 0.02）；`--full-palette` 另需 Windows x64 的 `scripts/native/palette_core.dll`，缺失会回退纯 Python 全量 median cut（很慢）；`--palette > 256` 另需 scikit-learn。**截图默认 `render.py`，零依赖**——直接调系统 Edge/Chrome 的 `--headless --screenshot`，找不到时看 `BROWSER_BIN` 环境变量；`render.js`（playwright-core）只在需要它时作为备选，按 `PW_CORE` → cwd/DSH_HOME/DSH_CHECKOUT 向上找 `.pnpm/playwright-core@*`。

回归测试：`python <skill>/tests/run_tests.py`（首次基线用 `--record`）。用例 = 平涂源逐像素、照片源逐像素、照片源曲线、**透明 PNG 曲线**、**曲线 + 边缘柔化**、**曲线 + 无损压缩**、**过程算法 final + 压缩**、**过程算法 final 可调追踪参数**、**过程算法 final 从前排继承**、**过程算法 final 全像素色板**；断言 `uncovered` 不超基线、mean 不超基线 +1.0，以及"单文件/无位图/无渐变（可选柔化滤镜除外）/有路径"四条结构检查。

透明图的 uncovered 口径不同：只统计**源图 alpha≥250 的像素**里被渲染成纯白的部分（alpha 128~249 是抗锯齿描边，本来就该柔化），并允许 ≤8 px 的孤立噪点（turdsize 会丢弃 alpha>250 的零星碎片）。

> 注意渲染器影响指标：同一份 HTML 用 playwright 与 Edge CLI 各截一张，mean 相差约 1.6——所以一个项目内必须固定同一个渲染器再做横向比较。

`autotune.py` 停止规则 = 「`≤5/255` 占比增益 < `--gain` 个百分点」，每档打印一行 JSON（`K/classes/paths/MB/mean/≤5/≤10/uncovered`）。注意它驱动的是**已弃用的像素模式**，只作历史误差对照，不再作为默认调参入口。

## SVG 重绘模式（默认） + 可选过程模拟（默认不启用）

同一张 750×434 照片级立绘的实测（源 `ref-girl.png`）：

| 模式 | 产物 | mean | ≤5 | 观感 | 适合 |
|---|---|---|---|---|---|
| 像素精确（`build_svg.py`，**已弃用**） | 游程矩形 4133 path / 4.06MB | **2.16** | 96.7% | 与源几乎一致，**放大有像素台阶** | 仅逐像素误差对照 |
| 真矢量（`vectorize.py`，K=192 + 2× 超采样） | 193 条曲线 path / 7.36MB / 11 步 | 8.35 | 44.6% | 平涂插画，**无台阶**，细节简化 | "要矢量感"、可缩放插画 |
| 真矢量高保真（K=384 + 3× 超采样，**高配基准**，可继续扫参） | 385 条曲线 path / 19.7MB（750×434 源） | **6.36** | 61.0% | 接近原图，暗部有层次 | 能接受大文件的交付 |
| vtracer 样条（`smooth_svg.py`） | 12 034 path / 3.47MB | 7.20 | 65.1% | 接近原图但极碎 | 高保真矢量化 |

> 表内是 750×434 源。876×1904 的 K=384/3× 实测 **385 层 / 139.7MB / 约 50s**；尺寸不同，别把 19.7MB 当成这张图的体积。默认抽样色板与全量色板在这张图上 mean 分别是 5.4917 / 5.4885，差距很小；追求极致再用 `--full-palette`。

> K 是**颜色层数**，不是绘画阶段数。过程模式的 11 个阶段是 `paint_process.py` 专有；`vectorize.py` 的 384 层是 384 个色块路径。静态 `.svg` 不写 `data-step`，只有 HTML 播放器/过程模式需要播放标记。

### 默认关闭的可选开关

**`--edge-soften=SIGMA`：边缘柔化后处理（legacy，保留可用）。** 新交付优先用下面的 `--alpha-soften`；这个开关留着只是因为快速出图时它更省事（一步模糊，不用扫参）。 它直接作用于已完成 SVG（或 HTML 里的 SVG），只增加一个 `feGaussianBlur` 和一层包装，不改路径坐标、不改层序。适合“放大很多倍不要让 1px 阶梯很扎眼”的交付；副作用是整体轻微变软。想真正提高轮廓分辨率，应该提高 `ss`（如 3×），`--edge-soften` 只是最终视觉后处理。`svg_postprocess.py` 可独立对已有 SVG 跑，参数为 `--soften N`。

**`--alpha-soften=N`：几何式边界软化（默认 0 = 关）。** 对每个色带在**外侧**补 N 圈 `<path>`，纯几何实现，不引入滤镜或渐变，因此与 `--edge-soften` 互斥（同开直接报错）。两种模式：

| 模式 | 开关 | 环用什么颜色 | 治什么 | 实测（400×711 / K=96 / ss=1） |
|---|---|---|---|---|
| 混合 | `--alpha-soften 2` | 色带色 × alpha | 台阶（观感） | mean 6.06→**6.40**，强边 48,114→**40,757（-15%）**，23.8MB |
| 真实局部色 | `--alpha-soften 2 --alpha-true-color=6` | 环内参考像素按亮度切 6 段，每段用该段真实均值色 | 台阶 + 误差 | mean **5.65（-0.41）**，强边 43,873（-9%），63MB |

配套开关：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--alpha-width=PX` | 1.0 | 每圈宽度；实测 2 会明显变差（混合模式 mean 10.26） |
| `--alpha-strength=A` | 自动：混合 0.25 / 真实色 0.7 | 环的透明度。真实色模式带一点透明，能把子级间的残差再抹一层 |
| `--alpha-ramp` / `--no-alpha-ramp` | 自动：混合模式开、真实色模式关 | 只在参考图**缓变区**（5×5 跨度 6–72 且细梯度 ≤14）补环。混合模式下每千条边的 mean 代价从 0.082 降到 0.046；真实色模式则相反，全边界覆盖才够 |
| `--alpha-refine=N` | 0（关） | 生成 → 渲染 → 剔除"加了反而更差"的环像素 → 重建，重复 N 次。用 **3×3 局部窗口**比较误差（逐像素比较会把所有环都判死，实测一次删掉 50 万像素）。K=32 皮肤区实测（tol 默认 1.0）：mean 5.30 → **5.15**，体积 43.7 → 38.6MB；tol 0.5 可到 5.12 / 35.7MB，tol 3 为 5.24 / 43.2MB |
| `--alpha-tol=T` | 1.0 | refine 的容差：局部均值允许变差的量（越小越激进，0.5 时多删一半环、mean 再降 0.03） |

**怎么判断"台阶变弱"？别用强边计数。** 真实色 6 级在同一张皮肤图上把原图 12.05 万条 2–4 级细台阶恢复到 12.89 万，同时把 >20 的硬跳变从 1.39 万压到 1.16 万——观感明显更连续；但"强边（>10）计数"反而从 30,153 变成 30,585，因为 8–12 这一档被算进了"强边"。判台阶要看**分档直方图**（>20 / 12–20 / 8–12 / 4–8 / 2–4），不能只看一个阈值。

**实测：真实柔光人像**（同一张立绘裁 760×900 皮肤区，K=32，色阶断层肉眼可见）：基准 mean 5.81 / 皮肤误差 7.49 / 强边 30,153；`--alpha-true-color=6` → 5.30 / 6.79（-9.4%）/ 30,585，43.7MB；混合 2 环 → 5.85 / 7.63 / 25,854（-14%），12.8MB。同一张图 K=96 时基准已是 3.68，混合 2 环反而 +0.27（收益不明显）。**所以它只在色阶断层明显的低 K（约 ≤64）才值得开；K 高时直接加 K 更划算。**

**为什么混合模式会抬高 mean？** mean 是逐像素 RGB 偏差，而环把边界像素推向邻色：参考图那里本来就是纯色时属于净损失，只有真是渐变时才赚。想要 **mean 下降**必须用真实局部色模式——它补的是该处缺失的中间色，等价于在边界临时提高色阶密度。但注意：单纯降 mean 用 K 更划算（K=192 → 4.82 / 9.6MB，比真实色环的 5.65 / 63MB 更好更小），环的价值只在「不想整体加层、只想修边界」。

**`--process-final`：用过程模式算法只出最终图。** 默认关闭；打开后不执行普通颜色层描摹，而是调用 `paint_process.py` 的同一套平涂/二分/细化/罩染/收线算法，直接输出静态 SVG。不生成 11 阶段、884 批次、播放器或 `data-step`。可选 `--process-palette=N`（默认 256；若给了位置参数 K 则用 K）和 `--process-line-direction=FILE`。`--process-palette` 为 257–上限（默认 1024，可用 `--process-palette-max=N` 提高）时默认只在约 8 万像素上拟合 k-means；`--process-full-palette` 或 `--process-palette-sample=0` 改为全部像素，`--process-palette-sample=N` 指定抽样像素。同一张图、同一 palette/线稿参数下，最终画面与过程模式的完成帧一致；区别只是没有过程。输出 `.svg` 得到静态 SVG，输出 `.html` 得到无播放器的静态 HTML。

过程 final 的追踪参数现在全部暴露；参数自身默认值仍是 `--process-base-scale/--process-mid-scale/--process-detail-scale`（0.34/0.52/0.85，1.0=原图分辨率）、`--process-min-areas=24,8,2`、`--process-blurs=1.0,0.5,0.18`、`--process-focus-box=0.10,0.08,0.90,0.92`、`--process-focus=scale,min_area,blur,dil`（1.0,4,0,1）、`--process-dil=1`、`--process-turd=1`、`--process-alphamax=1.0`、`--process-ss=1.0`。`--process-from-front` **默认开启**：自动把普通模式前面的 `turd/alphamax/dil` 继承到过程层（`dil` 同时用于 region 和 focus），`ss` 只在没有显式给任何 `--process-*-scale` 时作为统一倍率继承，`--full-palette` 和显式 `--palette-sample` 也会继承；显式 `--process-*` 始终优先，`--no-process-from-front` 回到旧版固定值。`blur` 不继承（过程是逐层 1.0/0.5/0.18），`sk_thr/dark_k/med` 没有过程对应。`--process-min-areas` 是过程版的碎块过滤（角色类似普通模式的 `turd`），`--process-turd` 则直接传给 Potrace。实测 876×1904、384 色：旧版固定行为（`--no-process-from-front`）为 54,887 path、mean 11.8843；显式 0.50/0.75/1.25 + `--process-turd=6` 为 103,991 path、压缩后 29.72MB，mean 10.8098，脸部边缘密度 34.31%→34.70%；同一无头渲染约 3.0s→7.0s，所以它是“细节换速度”，不是压缩提速版。同一组 0.50/0.75/1.25 + 384 色下，全像素色板为 104,841 path / 29.57MB / mean 10.7668 / 脸部边缘 35.03% / 6.9s，抽样色板为 103,991 path / 29.72MB / mean 10.8098 / 34.69% / 7.0s；全像素只值这么一点，别指望质变。

**`--compact`：无损路径数据压缩。** 默认关闭；把 `M/L/C/Z` 绝对坐标改写成相对坐标、去掉每个 path 上重复的 `shape-rendering`，**不删路径、不改层序、不改 fill/opacity/fill-rule**。实测 876×1904 过程 final：28,550,352 → 18,121,227 字节（-36.5%），渲染 2.92s → 2.39s，逐像素 mean 0.0213/255（只是相对坐标的浮点抗锯齿差，不是几何变化）。它不解决“被完全遮住的 path”问题：同一文件全遮挡剪枝只找到 954 / 50,927 条（1.9%），单独做遮挡剪枝省不到 0.4MB；把 topmost 画面重描成 192 条 path 虽然能到 20.4MB，但 mean 8.43/255，不算无损，所以没有默认启用。

`paint_process.py` 是**过程演示分支**，不参与上面的误差竞赛：先描线稿，再按连通色块平涂/二分/细化，脸部与手足再用遮罩裁切后单独高分辨率矢量化，最后藏色、罩染、提亮补色与收线。实测 876×1904（palette 0 烟测）：35,838 path / 26.44MB / 836 batches / 11 阶段；palette 256 仍是 28MB 量级。旧 256 色 mean 11.83 / ≤5 25.56% 是线稿层改版前的参考值，改版后未重测 mean。`--palette` 从原图采全局色板：默认 256，`512/1024` 走 k-means（需 scikit-learn，512 实测 mean 10.81 但 74,674 path），`0` 关闭；257 色以上默认在约 8 万像素上拟合，`--palette-full`/`--palette-sample=0` 改为全像素，`--palette-sample=N` 指定抽样像素；色数硬上限默认 1024，可用 `--palette-max=N` 提高。原生追踪（Windows x64）默认启用：`scripts/native/trace_core.dll`（potrace 1.16，GPL）；同尺寸 438×952 从 40.4s 降到 2.61s，876×1904/256 色生成约 10.5s；`vectorize.py` 也走同一 DLL（876×1904/K48 约 5.8s；K=384/3× 约 45s）。DLL 缺失或 `PAINT_NATIVE_TRACE=0` 时自动回退纯 Python，输出 SVG 不变。它换的是"能看懂作画顺序"，不是像素保真；静态高保真用 `vectorize.py`，像素模式已弃用。

### 过程模式线稿：agent 决策，脚本执行

skill 不替每张图决定“哪里是主体”。它提供 step0 线稿、区域过滤、描边、DLL 参数、`--line-only` 和验收；区域与取舍由 agent 看图写。

**流程**

1. 看参考图，判断“大形 / 次级结构 / 纹理细节”。
2. 写这张图专用的 `line-direction.json`，不要复用上一张图的区域。
3. 用 `--line-only` 只出线稿（约 1.5MB、2–3 批），看 `?step=1`。
4. 缺哪块补哪块：只调该区域 `bbox / min_diag / density_exempt`，不要全局调一个阈值。
5. 层级满意后再跑全量。

```bash
python scripts/paint_process.py ref.png line.html --size 876x1904 --palette 0 \
  --line-direction line-direction.json --line-only
python scripts/render.py line.html line-step1.png 876 1904 "?step=1&ui=0"

python scripts/paint_process.py ref.png process.html --size 876x1904 --duration 58 \
  --palette 256 --line-direction line-direction.json
```

```json
{
  "density_percentile": 52,
  "default_min_diag": 34,
  "trace": {"turdsize": 6, "alphamax": 1.0, "precision": 2},
  "style": {"color": "#33445c", "stroke_width": 0.5,
             "sketch_opacity": 0.95, "accent_opacity": 0.8},
  "regions": [
    {"name": "subject", "bbox": [0.08, 0.05, 0.92, 0.95], "min_diag": 28, "density_exempt": true},
    {"name": "background_detail", "bbox": [0.0, 0.0, 1.0, 0.35], "min_diag": 70}
  ]
}
```

| 字段 | 作用 |
|---|---|
| `density_percentile` | 局部密度分位；越低越严格，密集线进 `line-accent`。 |
| `default_min_diag` | 未命中区域的最小 bbox 对角线，按 876px 宽基准缩放。 |
| `trace` | DLL 的 `turdsize / alphamax / precision`，可按图调。 |
| `style` | 线色、线宽、起稿/收线透明度；线宽也按宽度缩放。 |
| `regions[].bbox` | 归一化 0..1，按顺序匹配，先命中先算。 |
| `regions[].min_diag` | 该区域最小结构；更小的进 `line-accent`。 |
| `regions[].density_exempt` | true 时不受密度过滤，适合眼窝/鼻翼/手部结构。 |

`name` 随意：人像可写 `face/hands`，风景可写 `sky/foliage`，产品可写 `label/edge`。skill 不内置任何语义区域。

**线稿规则**

- 线稿是 `fill="none"` 的描边，不填充；不会出现大色块填充区。
- 起稿只放“大形 + 指定保留的小结构”；过小结构、密集纹理留到 `line-accent`。
- 起稿层在颜色层下面，收线层在最上面；这是过程演示，不是像素保真模式。
- 全量仍约 27–30MB；`--line-only` 只用于看线稿，不是交付文件。

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
