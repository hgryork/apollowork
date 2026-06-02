# 报告模板

当用户要求完整报告、长文档结论或 presentation-ready narrative 时，使用这个模板。

## 必选章节

1. 数据质量与置信度
2. 实验设置与阈值定义
3. 全局全链路时延总览，联合使用 `p50`、`p95`、`p99`、miss rate
4. 当 lidar/radar 可能分化时的分传感器总览
5. 模块与 handoff 总览
6. RT 与 Data Age 异常总览
7. 丢帧位置分布
8. 丢帧时间线与时延时间线对齐
9. deadline-miss 对下游 alignment windows 的影响
10. 代表异常帧归因
11. 如有必要，场景切片或时间窗切片分析
12. 结论与下一步建议

## 必选表格

- 核心总表：`p50`、`p95`、`p99`、miss rate、sample count
- 阈值表：列清每个 miss criterion 及其来源
- 当 sensor 可能分化时的 lidar/radar 分拆表
- drop-by-stage 表
- 代表异常帧归因表，含明确 attribution rule

## 必选图表

- RT raw vs steady-state
- Data Age raw vs steady-state
- 不能只依赖 `p95` 的 RT / Data Age percentile comparison 图
- 如果 miss rate 是重点，必须给出 miss-rate curve 与对应 `p99` curve 的对齐图，不能只对 `p95`
- 默认使用 `plot_trace_suite.py <canonical_dir> standard-suite` 生成最小标准图集，其中包含 RT/Data Age 分位图、drop 对齐图、miss rate vs p99 图和 drop stage 堆叠图
- Planning 关键图；若 startup outlier 会拉坏坐标轴，必须带 trimmed 或 zoomed 视图
- drop timeline 与 RT 或 Data Age 的对齐图
- 如果 drop 诊断重要，给出按 `break_stage` 堆叠的 drop timeline
- 如果 deadline miss 重要，给出 miss count 与 downstream alignment delta 的对齐图
- 如果高频 sensor 回归重要，给出 sensor-to-fusion 或主导模块的 compute vs wait/queue 图

## 报告规则

- 每张图都必须有一段解释，写清图里有什么、关键数字是什么、支撑什么结论
- 每个异常章节都必须包含数字证据
- 如果使用派生表而不是从原始表重建，必须明确说明
- 如果置信度因 coverage 差或 incomplete paths 降低，必须在开头就写出来
- 不能在做健康判断时只使用 `p95`
- 讨论 miss rate 时，必须写明 threshold source，并和 `p99` 或代表 tail windows 一起解释
- 讨论 miss rate 时，如果图上只对 `p95` 没对 `p99`，报告不完整
- 高频输入变差时，报告必须继续追到 compute、queue、handoff、reuse 或 parent-origin loss 谁主导
- 报告里只要说 drop 和 latency 有相关性，就必须有 aligned windows 或 aligned chart 作为证据
