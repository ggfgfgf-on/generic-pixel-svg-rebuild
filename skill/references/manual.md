# 像素级 SVG 重绘通用手册（与素材无关，供任何 AI 调用）

> **任务范式**：给定任意参考位图（插画、动漫贴纸、照片；PNG/JPEG，任意 ≤2MP 尺寸），产出
> ① 一个**单文件自包含 HTML**：内含纯 SVG（`<path>` 平涂多边形 + 可选描边线稿）、按"线稿 → 固有色 →
> 阴影高光 → 收口"离散回放的绘制流程播放器；无 Canvas、无位图内嵌、无外链、无 SVG 渐变、无 CSS 过渡。
> ② 一套**可复现的数值验收**：逐像素通道误差分布。实测可达：未覆盖 = 0，100% 像素 ≤10/255，
> ≥90% 像素 ≤5/255，平均通道误差 <2/255（56 万像素与 92 万像素两档图像上均验证过）。
>
> 本手册与具体素材无关：不含任何示例图像、示例文件或历史产物引用；所有规则可直接套用于新图。

---

## 1. 输入 / 输出契约

**输入**：位图路径；读入后记录 `W,H`，转成无损 RGB（若为 JPEG，先解码并**固化保存为无损副本**，
后续所有比较都针对这份副本——不同解码器结果不同，这是误差归因的前提）。

**输出**：
- `viewBox = "0 0 W H"`，SVG 等比自适应；
- 每个颜色类 → 一组带 `data-step` 的平涂 `<path>`；
- 一个纯内联 JS 播放器：11 步左右（0=线稿，中间按内容语义，末步=完成），任意步可经 `?step=N` URL 参数直达；
- 全部离散显隐，零过渡动画。

**核心思想**：颜色先无损量化，几何用**构造上零缝隙**的平铺，误差用**同基线截图**闭环测量，
调参只在指标指出瓶颈时进行。全流程只有两个真正的精度旋钮：调色板 K 与坐标对齐；其余全是工程陷阱。

---

## 2. 七阶段流程

```
A 归一化参考图 → B 调色板 → C 打标 → D 填充几何 → E 语义步骤 → F 播放器 → G QA 闭环
```

### A. 归一化
无损副本 + 记录宽高。工作目录按"每次任务一个 ref/ 目录"组织（源、预览、截图、diff 分开），不跨任务混用。

### B. 调色板：median-cut（不要用 k-means）
k-means 对照片级颜色分布收敛差（实测会留下把平涂区错到 30+/255 的坏中心）；median-cut 无随机性、一次成型：

```python
def median_cut(px, k):                      # px: (N,3)
    boxes = [px]
    while len(boxes) < k:
        cand = []
        for i, bx in enumerate(boxes):
            if len(bx) < 2: continue
            rng = bx.max(0) - bx.min(0); ax = int(rng.argmax())
            cand.append((rng[ax], i, ax))
        if not cand: break
        _, i, ax = max(cand)
        bx = boxes.pop(i)
        s = bx[bx[:, ax].argsort(kind='stable')]
        boxes.extend([s[:len(s)//2], s[len(s)//2:]])
    return boxes
cent = np.array([b.mean(0) for b in median_cut(arr.reshape(-1, 3), K)])
```

**K 的选择（迭代规则，不查固定表）**：从 `K = 2048` 起步跑指标；若"≤5/255 占比"距目标（≈90%）不足，
K 翻倍重跑（4096 → 8192 → …），直到收益 <3 个百分点即停。经验：
- 无噪/低噪源（平涂动漫、无损 PNG）：2048 附近收敛；
- 高噪源（JPEG、照片、水彩纹理）：需要 8192 量级；
- 每翻倍的成本：生成时间 ×约1.5–2，文件体积 +10~30%（游程矩形下增长很缓），浏览器渲染不敏感（元素数与
  矩形总数线性相关，万级 `<path>`、20MB HTML 实测无压力）。
- **判据始终是"≤5/255 占比"与平均误差，不是 K 本身。**

### C. 最近中心打标
逐中心全图更新（避免 N×K 大内存）：

```python
lab_full = np.zeros((H, W), np.int32); best = np.full((H, W), np.inf)
for k in range(len(cent)):
    d = ((arr - cent[k]) ** 2).sum(-1)
    upd = d < best; best[upd] = d[upd]; lab_full[upd] = k
```

### D. 填充几何：逐像素游程矩形（唯一推荐）
对每个类掩码 `m = lab_full == k` 按行做游程编码，每行游程 = 一个 1×w 整数矩形：

```python
def row_runs(row):
    r = np.concatenate(([0], row.astype(np.int8), [0])); dd = np.diff(r)
    return [(int(s), int(e - s)) for s, e in
            zip(np.flatnonzero(dd == 1), np.flatnonzero(dd == -1))]

CHUNK = 300   # 单个 <path> 的矩形上限，见 §4 陷阱 P3
d = ' '.join('M %d %d h %d v 1 h %d Z' % (x, y, w, -w) for (x, y, w) in chunk)
# <path fill="#RRGGBB" shape-rendering="crispEdges" data-step=".." d=".."/>
```

为什么是它（等值线描摹 + winding 填充被否决的理由，通用结论）：
- 游程矩形**按构造**精确复现掩码：无奇偶/绕序簿记、无细带/相切/孤立点退化情形、互不重叠所以**不需要 z-order**；
- 未覆盖像素恒为 0，指标只反映量化误差，调参目标单一；
- 代价只是轮廓呈像素阶梯——对"像素级重合"目标这恰是正确形态。

**整数坐标 + `shape-rendering="crispEdges"`**（禁用抗锯齿）是达成像素级的前提：原图像素本就是阶梯，
再叠一层 AA 反而制造边界色差。

### E. 语义步骤分类
步骤语义仅供"绘制过程"演示，允许中间态不完美；末步必须全量。对新图按以下步骤**从零推导**规则：

1. 从调色板中心聚类出有限色族（背景/主体/肤色/深/浅/点缀），用直方图 + 空间质心辅助；
2. 写成纯函数 `step_of(color, area, centroid)`，判定顺序固定为：
   **小面积兜底 → 色相族 → 肤色(高亮暖色) → 近白 → 兜底背景**；
3. 两条通用纪律：
   - "小面积 → 最后一步"的像素阈值**必须随 K 缩放**（K 越大类越碎；固定大阈值会把主体吞进细节步）；
   - 亮肤色常被"近白"规则截走——肤色判定必须在近白之前；
   - 同色族歧义（如衣物白 vs 水面反光）用色偏方向（b>g / g≥b）+ 质心位置框二次守卫。

### F. 播放器（通用规格）
- CSS：`svg#art [data-step]{display:none} svg#art [data-step].on{display:inline}`；无任何 transition；
- 线稿层：描边路径（`fill:none; stroke:<深灰蓝>; stroke-width≈1; opacity≈.5`）放入
  `g[data-step="sketch"]`，仅 step 0 时显示，step 0 画布底给浅色（深色 UI 上深线稿不可见）；
- JS：`querySelectorAll('svg [data-step]')` 逐个 `classList.toggle('on', +el.dataset.step <= cur)`；
  播放=约 900ms/步定时器；`?step=N` 支持外部截图定位；
- 给每个 `<path>` 直接打 `data-step`，**不要按步骤包 `<g>`**（对任何非纯游程方案都安全）。

### G. QA 闭环（通用协议）
1. **预览页**与播放器分离：预览页 = 同一份 SVG、全平涂层显示、无 UI——几何评测不受播放器干扰。
2. 无头浏览器截图三要素（缺一不可）：
   - 强制缩放 1:1（防系统 DPI 裁切右缘）；
   - 窗口高 = 图像高 + ≥120（视口小于画面会在画面中部**整体截断**并伪装成内容缺失）；
   - 预览 URL 加版本号参数或换文件名（防缓存导致指标"冻结"）。
3. 指标口径固定：
   ```
   diff_px = max over channels |src − render|；报告 mean、≤3/≤5/≤10/≤20 占比；
   uncovered = 渲染纯白 ∧ 源不纯白（必须先排除源自身纯白）。
   ```
4. **归因方法**（先算再改）：
   - 计算"量化上界" = 每像素到其所属调色板中心的距离分布；
   - 实测 ≈ 上界 → 瓶颈在调色板 → 提 K（阶段 B）；
   - 实测 ≫ 上界 → 瓶颈在几何/管线 → 查坐标契约与元素截断（§4）；
   - diffmap 上**沿所有边界出现细亮线** = 半像素错位；**成块空白** = 元素被丢弃或未覆盖。

---

## 3. 坐标系契约（通用，最关键规则）

- 掩码数组索引 (x,y) = 第 (x,y) 号像素的**中心**；
- 浏览器以像素中心 = n+0.5 判定内外；
- 游程矩形用**整数边界**坐标（`M x y h w v 1`），天然满足契约；
- 任何来自格点模型（marching squares 等）的坐标，若其采样点定义在整数格，则输出前必须 **−0.5**；
  若还在填充用了 pad 网格，先撤销 pad 偏移再做半像素校正——两处偏移都实测各值两位数百分点。

---

## 4. 通用失败模式对照表

| # | 症状 | 根因 | 修复 |
|---|---|---|---|
| P1 | 全图边界一圈细亮线、指标远低于量化上界 | 像素中心 vs 格点中心差半像素 | 坐标 −0.5 或改整数边界矩形 |
| P2 | 图像一侧固定宽度全白 | 截图继承系统 DPI 缩放 | 无头参数强制 scale=1 |
| P3 | 某些大色域完全缺失 | 单个 `<path>` 子路径过多被渲染器**静默截断** | 分块输出（≤约300 子路径/元素） |
| P4 | 画面某横向/纵向区域整体缺失 | 无头窗口小于画面尺寸被截断 | 窗口高=图高+≥120 |
| P5 | 边界细白环、1–2px 结构丢失 | winding/奇偶填充 + "小面积过滤"吃掉退化环 | 游程矩形填充；平涂不设面积过滤 |
| P6 | 指标随 K 增大反而变差 | 几何方案对碎片化敏感 | 换游程矩形后单调变好 |
| P7 | 平涂区域颜色系统性偏差大 | 聚类欠收敛（k-means 局部最优） | median-cut |
| P8 | 中间步骤语义崩塌 | 面积阈值未按 K 缩放；判定顺序错误 | 见阶段 E 纪律 |
| P9 | 改完参数指标一模一样 | file:// 缓存 | URL 版本号 / 换文件名 |
| P10 | "纯白=未覆盖"统计虚高 | 源图本身含纯白高光 | 先按源掩码排除 |
| P11 | 不同工具对同一图比较结果不同 | 解码器不一致（JPEG） | 固化无损副本作为唯一基线 |

---

## 5. 调参循环（决策树）

```
run(K) → G 全指标
├─ uncovered > 0            → 查 P1–P5（几何），不碰 K
├─ 边界细线(diffmap)        → 修坐标契约 P1
├─ 平坦区色差 ≈ 量化上界大  → K ← K×2，重跑
├─ 步骤画面语义乱           → 只改 step_of，不重生成几何（K/掩码不变时可快速迭代）
└─ 指标达标（uncovered=0；100%≤10；≥90%≤5；mean<2）→ 验收
```

---

## 6. 交付验收清单

1. 单文件；无外链/`<img>`/canvas/base64/`<linearGradient>`/`url(#`；
2. viewBox = 原图像素尺寸；播放器等比缩放；
3. 指标：uncovered=0；100% ≤10/255；≥90% ≤5/255；平均 <2/255；
4. 每个步骤 `?step=N` 截图目视：空白只应出现在"尚未绘制"的内容上；末步与预览页逐像素一致；
5. 全程离散切换、无渐变、无过渡；
6. 附逐像素指标表与并排对比图。

---

## 7. 通用管线伪代码（可直接移植任何语言）

```
src = decode_lossless(reference)                    # A
pal = median_cut(src.pixels, K); K0 = len(pal)      # B
lab = argmin_nearest_palette(src, pal)              # C
for k in 0..K0-1:                                   # D
    runs = row_runs(lab == k)
    emit_chunks(k, runs, chunk=300, crispEdges)
for L in labels: L.step = step_of(L)                # E  (顺序纪律见 E)
assemble(player_html, svg, steps, sketch_layer)     # F
loop:                                               # G
    shot = headless_screenshot(preview, scale=1, window≥(W,H+120))
    metrics = compare(shot, src_ref)                # 与 A 的无损副本比
    act(per §5 decision tree) until acceptance(§6)
```
