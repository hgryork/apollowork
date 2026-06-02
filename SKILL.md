---
name: apollo-perf-trace-analysis
description: 用于基于 `events`、`message_context`、`fusion_inputs` 分析 Apollo perf trace run 目录，重点覆盖 RT/Data Age、分位数与 miss rate、planning spike、handoff delay、control reuse、丢帧位置分布、丢帧与时延对齐、下游对齐影响，以及按帧、按时间窗、按模块、按异常类型、按 break stage 的 drill-down。
---

# Apollo Perf Trace Analysis

## 概述

当任务是分析 Apollo perf trace run 目录，而且事实来源是 `events`、`message_context`、`fusion_inputs` 三类原始表时，使用这个 skill。

这套 skill 的目标不只是给出几个时延统计，而是解释清楚：

- 全链路时延在 `p50`、`p95`、`p99` 上分别是什么状态
- miss rate 对应的阈值是什么，反映的是高位超时还是长尾超时
- 丢帧发生在什么位置、什么时间、和时延尖峰是否同窗出现
- deadline miss 之后，下游窗口里 lidar/radar 是否出现时间新鲜度错位
- 高频输入变差时，主因是 compute、queue、handoff、reuse，还是 parent-origin 缺失

这套 skill 面向交互式分析，既支持 full-run 总览，也支持按时间窗、按帧、按模块、按 drop 的定向下钻。

## 适用场景

满足以下任一条件时应使用本 skill：

- 用户想基于 Apollo perf trace 输出做全链路性能分析
- 用户提到 `events`、`message_context`、`fusion_inputs`、`analysis/tables` 或 `perf_trace`
- 用户问 RT、Data Age、planning spike、handoff delay、control reuse、lidar/radar 差异
- 用户想看异常归因、丢帧位置分布、丢帧-时延对齐、下游对齐影响
- 用户想要标准报告、SOP 一致结论、回归对比结果
- 用户想知道为什么高频输入下会出现慢帧、旧帧、错位帧

不要把这个 skill 用在通用 CPU profiler、内核调度、火焰图分析等与 Apollo perf trace 主链路无关的问题上，除非结论仍然以这三类原始表为中心。

## 必要输入

优先要求 run 目录中存在以下原始输入：

- `events/`
- `message_context/`
- `fusion_inputs/`

如果已经存在派生分析表，可以用来加速分析，但仍应把三类原始表视为事实来源。若本次分析依赖已有派生表而不是从原始表重建，必须明确说出来。

在给出强结论前，先检查：

1. 三类原始表是否齐全
2. shard 是否非空且结构可读
3. trace coverage 与 complete-path coverage 是否足够支撑 E2E 结论
4. parent-origin 链接和关键 handoff match 是否足够支撑 sensor 级归因

若某些原始输入缺失，必须降级分析范围，并明确说明哪些结论不再可靠。

## 默认分析口径

除非用户明确要求其他约定，否则使用以下默认口径：

- RT、Data Age、关键模块总耗时一律联合报告 `p50`、`p95`、`p99`
- `p50` 表示典型水平，`p95` 表示高位运行水平，`p99` 表示长尾风险
- miss rate 是阈值指标，不是 `p95` 的替代物
- miss rate 要和阈值来源、`p99`、tail 样本数、代表窗口一起解释
- 只要画 miss rate 相关图，至少要检查 miss rate 曲线是否和对应 `p99` 曲线同窗抬升，不能只和 `p95` 对齐
- 只要 lidar/radar 可能分化，就按 `sensor_kind` 拆开看
- 高频回归场景下，看到 `sensor_to_fusion` 或长 edge 变高后，必须继续拆 compute 和 wait/queue
- 涉及 drops 或 deadline misses 时，必须把它们放到时延时间线上做对齐，不能孤立分析

## 交互模式

### 1. Overview Mode

适用于用户想看 run 总览、首次体检、全局健康状态。

默认输出：

- 数据质量结论
- 全局 RT/Data Age 总览，包含 `p50`、`p95`、`p99`、miss rate
- 模块与 handoff 总览
- Top 异常计数与严重度
- 丢帧位置分布概览
- 丢帧-时延对齐概览
- 如果相关，再给出下游 alignment impact 概览
- 下一步最值得下钻的方向

### 2. Time Window Mode

适用于用户指定某个时间段。

默认输出：

- 窗口内 RT/Data Age `p50`、`p95`、`p99`
- 窗口内 deadline miss 计数与比例
- 窗口内 Planning 和 handoff 统计
- 窗口内丢帧数与 break-stage 分布
- 如果相关，给出窗口内 lidar/radar alignment delta
- Top 异常帧
- 与 steady-state 或全局基线对比

### 3. Frame Mode

适用于用户指定某一帧、最坏一帧、某个 `fusion_trace_id`。

默认输出：

- 从 sensor origin 到 control outputs 的完整账本
- 分段贡献拆解
- planning total / planner / runonce / wait / reuse 细节
- 与 steady-state 的相对严重度
- 一个归因标签和数字证据
- 附近是否存在 drops 或 miss windows
- 如果需要，比较同一 fusion output 下的 lidar/radar parent

### 4. Module Or Edge Mode

适用于用户关注某个模块或某条边。

默认输出：

- 模块总耗时或 edge handoff 统计
- `p50`、`p95`、`p99`、miss rate、jitter
- hotspot windows 和 top slow samples
- 与异常帧或 drops 的重叠情况
- 问题更像 compute、wait、queue，还是 freshness 问题

### 5. Anomaly Mode

适用于用户想看长尾、异常样本、尖峰问题。

默认输出：

- 异常规则
- 各级严重度样本数
- `p99`、top windows、代表帧证据
- 归因模式聚类
- 建议的下一步下钻方向

### 6. Drop Mode

适用于用户专门问 drops、misses、chain break。

默认输出：

- drop type 与 break-stage 分布
- gap 与 missed-period 汇总
- drop 时间线
- 与 RT / Data Age / Planning 时间线的对齐结果
- 如果相关，和 lidar/radar downstream mismatch windows 对齐
- 说明它们是同步、lead-lag，还是基本独立

## 标准工作流

除非用户明确只想要更窄的分析，否则遵循以下顺序：

1. 校验 run 目录和原始表
2. 判断使用现有派生表还是从原始表重建 canonical view
3. 先过数据质量门，再给强结论
4. 启动期和 steady-state 分开看
5. 根据用户问题选择合适的 interaction mode
6. 只加载当前模式所需的最小派生表集合
7. 所有结论都要有数字证据
8. 一律联合看 `p50`、`p95`、`p99` 和 miss rate，不能只看 `p95`
9. 讨论 miss rate 时必须写明阈值来源，并用 `p99`、代表帧或代表窗口解释 tail
10. 只要涉及 miss rate 时间线，就至少要检查 miss-rate curve 与对应 `p99` curve 是否同窗对齐；仅看 `p95` 不够
11. 涉及 drops 时，必须做 drop timeline 和 latency timeline 对齐
12. 高频输入不稳定时，要比较 service time 和 observed period，并判断 queue growth 是否比 compute growth 更主导
13. 用户要求报告时，使用 `references/report-template.md`

## Scripts

在需要标准化输出而不是只做叙述时，优先使用脚本：

- `scripts/validate_run.py <run_dir>`
  用于先验证 run 目录和分析表是否存在、结构是否可用

- `scripts/build_canonical_tables.py <run_dir>`
  用于生成 skill 规范下的 canonical tables。当前 `v1` 会基于 `analysis/tables` 生成 `quality_table.csv`、`drop_event_table.csv`、`latency_timeline_table.csv`、`latency_drop_alignment_table.csv`、`steady_state_summary.csv`、`deadline_metrics.csv`、`anomaly_frame_table.csv` 等。
  deadline 阈值默认由 run 的 observed planning period 推断，也可通过 override 参数指定。`latency_timeline_table.csv` 默认包含 `p50/p95/p99`、窗口级 miss count、miss rate；`latency_drop_alignment_table.csv` 默认包含 miss rate 与对应 `p99` 的对齐标签。

- `scripts/align_drop_latency.py <canonical_dir>`
  当 canonical tables 已存在，只需重算 time-bin 视图或 drop-latency 对齐时使用

- `scripts/plot_canonical_metrics.py <canonical_dir> --metrics ... [--start-s ... --end-s ...]`
  用于按时间窗渲染基础图表

- `scripts/plot_trace_suite.py <canonical_dir> ...`
  用于更丰富的图表类型，包括：
  - RT / Data Age / Planning / drop-alignment timeline
  - `rt_p95` vs `drop_count_total` 双轴图
  - `rt_miss_rate_pct` vs `rt_p99`、`data_age_miss_rate_pct` vs `data_age_p99` 必查图
  - 含 `p50`、`p95`、`p99`、jitter、std、miss rate 的模块图
  - 按 `sensor_kind` 或 `sensor` 的 sensor-to-fusion 图
  - 按 `break_stage` 堆叠的 drop-count 图
  - miss count 与 alignment delta 的窗口对齐图
  - `standard-suite` 子命令，用于一次性生成报告最小标准图集

使用脚本后，要说明输出目录和关键假设，尤其是 deadline 阈值来源与 canonical builder 的版本行为。

## 分析守则

以下规则不能跳过：

- 模块运行时延只能用 `mono_ns`，不能用 `data_ts_ns`
- 任何 RT 或 Data Age 都必须说明时间锚点
- 任何 deadline miss rate 都必须说明阈值是 inferred 还是 override
- miss rate 不能当成 `p95` 解释
- 当 `p99`、miss rate、sample count 已经提示风险时，不能只用 `p95` 说系统健康
- miss rate 相关图如果没有同时检查 `p99` 曲线是否同窗抬升，结论不完整
- incomplete-path 样本不能混入 strict E2E 统计，除非明确说明
- 描述一帧是 drop 时，必须说明 drop type 和 break stage
- 不能仅凭高 control reuse 就判定 `soft_drop`
- 说 drops 和 latency spikes 有关时，必须给 aligned windows 证据
- 当窗口样本太少时，不能只拿单个极值样本下结论
- sensor-origin 差异重要时，不能把所有 sensor 混成一行
- 高频回归中，不能停在 “sensor_to_fusion 很高”；必须继续问 compute、queue、handoff、parent-origin 谁主导
- 不能只给图不给解释

## 输出契约

分析回答优先采用紧凑但证据充分的结构：

1. 说明 scope：full run、time window、frame、module、anomaly set 或 drop set
2. 说明数据来源：raw tables、derived tables，或二者都有
3. 说明所用 metrics、thresholds、counts
4. 解释 attribution 或 uncertainty
5. 若相关，明确 `p50`、`p95`、`p99`、miss rate、代表窗口合起来说明了什么
6. 若有必要，建议一两个高价值下一步下钻方向

若生成了文件，给出绝对路径。若图无法直接渲染，说明应插入什么图、坐标轴是什么、需要什么 overlay。

## References

按需加载：

- `references/sop.md`：完整 canonical workflow 和正式表系统
- `references/table-contracts.md`：中间表结构和校验
- `references/metric-definitions.md`：RT、Data Age、alignment delta、deadline、miss rate、anomaly threshold 定义
- `references/interaction-modes.md`：不同用户问题下的回答结构
- `references/example-prompts.md`：自然语言提问示例
- `references/report-template.md`：完整分析报告模板

不要默认把所有 references 一次性全读进来，保持上下文精简。
