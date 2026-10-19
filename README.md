# generic-pixel-svg-rebuild
Asset-agnostic pixel-level SVG reconstruction skill: turn any bitmap (PNG/JPEG, ≤2MP) into one self-contained HTML with pure SVG flat-color paths and a discrete step player. Verified per-pixel: 0 uncovered, 100% ≤10/255, ≥90% ≤5/255, mean &lt;2/255. No Canvas, embedded bitmaps, external links, gradients, or transitions.

一个与素材无关的像素级 SVG 重绘通用 Skill。输入任意位图（PNG/JPEG，≤2MP），输出单文件自包含 HTML：纯 SVG 平涂路径 + 离散绘制步骤播放器。逐像素验收：未覆盖像素为 0，100% 像素通道误差 ≤10/255，≥90% 像素 ≤5/255，平均通道误差 <2/255。无 Canvas、无位图内嵌、无外链、无 SVG 渐变、无 CSS 过渡。

使用方法:直接扔给agent,要求他按手册方法进行svg重绘.
