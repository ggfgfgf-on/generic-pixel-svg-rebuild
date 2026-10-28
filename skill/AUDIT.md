# 审计报告（2026-02 会话内自查）

范围：`generic-pixel-svg-rebuild` 全部 17 个脚本 + 2 份文档 + 11 个测试用例。
方法：清单提取（行数/flag 数/被引用次数）+ 调用关系扫描 + 测试覆盖矩阵 + 文档-代码对照 + 本轮实测数据回归。

## 1. 结论摘要

| 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|
| 高 | `auto.py` 不认识新预算能力 | 只有 `--region`/`--region-mask`/`--alpha-*`；无 `--elements`/`--tile`/`--region-poly` | 预设择优无法使用元素级/平铺/多边形预算，用户拿到的是旧上限 |
| 高 | 新能力零回归测试 | `run_tests.py` 11 例只覆盖 `vectorize.py` + `build_svg.py`；`--elements`/`--elements-refine`/`--tile`/`--region-poly`/`--region-snap` 全无断言 | 任何改动都可能静默破坏元素预算（本轮就出过 ask 不写 `--out` 的 bug） |
| 中 | “弃用”的分支仍在门禁里 | `IMPLEMENTATION.md:24` 标 deprecated，但 `tests/run_tests.py:68` 仍用 `build_svg.py` 跑 2 例作为 PASS 条件 | 语义矛盾：既说不是交付物，又用它判红绿 |
| 中 | 孤立模块只存在于文档 | `smooth_svg.py`、`seal_gaps.py`、`steps.py` 仅被 SKILL.md/IMPLEMENTATION.md 提及，无脚本调用它们 | 文档承诺的能力没有入口，或已死代码 |
| 中 | 参数面板重复但语义不同 | `vectorize.py` 45 个 flag、`paint_process.py` 22 个、`iterate.py` 13 个；`--region` 在 auto/iterate/vectorize 三处各有一套解析 | 三处解析不一致的风险（本轮已出现补丁不累积） |
| 低 | 文档数字与实测有小漂移 | IMPLEMENTATION 记 `vectorize mean 3.74…8.35`；本轮默认参数实测 8.72 | 结论方向一致，数值需更新 |
| 低 | `iterate.py --keep` 未在 SKILL 说明 | 代码里保留每轮 PNG（`round%d.png`） | 使用者不知道有这个调试产物 |

## 2. 测试覆盖矩阵

| 模块 | 行数 | 被测试 | 备注 |
|---|---|---|---|
| vectorize.py | 1208 | 9 例 | 主路径有覆盖 |
| build_svg.py | 162 | 2 例 | 已弃用却仍参与判分 |
| form.py | 174 | 1 次自检 | 只验 JSON 可解析，不验比例正确性 |
| ink.py | 394 | ✗ | poster/line/遮挡/裁剪全无断言 |
| iterate.py | 407 | ✗ | ask/auto/patch/scale/move 全无断言 |
| attribute.py | 199 | ✗ | 归因排序无受控用例入库 |
| auto.py | 153 | ✗ | 预设择优无断言 |
| paint_process.py | 982 | 1 例 | 只测 final |
| svg_compact / svg_postprocess / native_loader / metric / render | 676 | 间接 | 通过主路径覆盖 |

## 3. 调用关系

- 被引用最多：`render.py`(9)、`vectorize.py`(9)、`metric.py`(5)、`build_svg.py`(5)、`iterate.py`(4)
- 只被文档引用：`smooth_svg.py`、`seal_gaps.py`、`steps.py`
- 已弃用但活跃：`build_svg.py`（测试门禁）、`autotune.py`（文档标注仅历史对照，驱动像素模式）

## 4. 文档-代码漂移

| 文档说法 | 代码事实 |
|---|---|
| “像素分支仅保留作历史对照” | 测试仍拿它当 PASS 条件之一 |
| “`--alpha-ramp` 自动在缓变区补环” | 已实现且自适应（5×5 跨度 6–72、细梯度 ≤14）→ 我提出的“自适应 alpha 环”是重复劳动 |
| “元素级/平铺/多边形可用” | 能用，但 `auto.py` 不暴露 |
| `main()` 内局部 `import shutil` 曾导致 UnboundLocalError | 已修（本轮） |

## 5. 修复状态（本轮已全部完成）

| 问题 | 状态 | 证据 |
|---|---|---|
| auto.py 无新预算透传 | 已修 | `--elements/--elements-refine/--tile/--region-poly/--palette-shared` 透传并在报告里回显 |
| 新能力零测试 | 已修 | 15 例，新增 elements-budget / elements-refine / tile-budget / poly-region，`RESULT: PASS` |
| 弃用分支仍在判分 | 已修 | 两例标 `gate=False` → `info_only`，跑但不算红绿 |
| 三个孤立模块 | 已处理 | `seal_gaps` 接入 `--seal-gaps`；`steps.py`/`smooth_svg.py` 在 SKILL.md 标明独立工具与依赖 |
| 发布脚本静默失败 | 已修 | `release.ps1`：`$ErrorActionPreference='Stop'` + 提交/推送/资产四项断言；原生命令单独取 `$LASTEXITCODE` |

实测收益：`auto.py --preset illustration --elements 16:800:384,2` 在 ref-star 上 mean **5.14**（8.2 MB），优于手调 6.08。

## 6. 建议修复顺序（历史，已完成）

1. `auto.py` 增加 `--elements`/`--tile`/`--region-poly` 透传（半天内可完成，收益最直接）
2. 给 `--elements` + `--elements-refine` 加 1 个回归用例（小图 + 断言 mean 下降、uncovered=0）
3. 更新 IMPLEMENTATION 的数字区间，并把 `build_svg.py` 从判分改成“只记录不判红”
4. 决定 `smooth_svg.py`/`seal_gaps.py`/`steps.py` 的去留：要么接进入口，要么从文档删除
5. `iterate.py --keep` 写进 SKILL.md 的调试小节

## 6.1 三个组合项：已实测，不再是未知

| 组合 | 结果 | 处置 |
|---|---|---|
| elements + palette-shared | 4.25 → 9.20（变差） | 忽略 + 告警 |
| tile 非整除 | 接缝误差 1.57× 内部 | 告警 + 建议因数；uncovered 仍为 0 |
| paint_process 透明源 | 透明孔被涂成 245,239,226 不透明 | 告警 + `--bg` |

## 7. 未验证项

- `--elements` 与 `--palette-shared` 同时使用的行为
- `--tile` 在非整除尺寸下的接缝（只测过 400×711 的 2×3）
- `paint_process.py` 的 detail 分支在透明源上的表现
