# Example Prompts And Expected Outputs

Use this file when the user needs concrete examples of how to talk to the skill, what kinds of questions are supported, and what shape the response should take. These are not rigid command syntaxes. They are natural-language examples that the skill should understand.

## 1. Full-Run Overview

### Example prompt

`$apollo-perf-trace-analysis 分析这个 run，先给我一个全链路总览`

### Expected response shape

- State whether the run data is usable and whether confidence is reduced by missing paths or poor coverage.
- Give RT / Data Age / Planning / handoff headline statistics.
- Say where the main bottleneck appears to be.
- Summarize drop distribution and whether drops align with latency spikes.
- Suggest one or two next drill-downs.

### Example output snippet

`本次 run 数据质量可用，trace 覆盖率接近完整，steady-state RT 和 Data Age 整体可控，但存在若干 planning 尖峰和 control reuse 拉长的长尾。当前最值得继续下钻的是 planning 总耗时热点窗口，以及 drop 与 RT 同窗抬升的几个时间段。`

### Good follow-up prompts

- `只看 planning`
- `看 drop 和 RT 的关系`
- `看最差的 5 帧`

## 2. Time-Window Analysis

### Example prompt

`$apollo-perf-trace-analysis 看 133 到 147 秒这一段的 RT、Data Age、Planning 和 drop`

### Expected response shape

- Quote the exact window used: `133s-147s`.
- Report the number of samples in the window.
- Give RT / Data Age / Planning summary in that window.
- State the drop count and dominant break stages in that window.
- Compare the window against steady-state or full-run baseline.
- If a plot is requested or helpful, generate or reference a local chart.

### Example output snippet

`133s-147s 窗口内共有 182 个样本，RT p95 和 Data Age p95 均高于 steady-state 基线；Planning total p95 也同步抬升。该窗口内 drop 主要集中在 planning_to_control 和 control_consume_or_reuse，属于值得优先排查的热点区间。`

### Good follow-up prompts

- `把这段 RT 最高那一帧拆开`
- `这段和 50 到 71 秒比怎么样`
- `给我画这段 planning_total_p95 和 drop_count_total 的图`

## 3. Frame-Level Drill-Down

### Example prompt

`$apollo-perf-trace-analysis 分析 fusion_trace_id=17293855537271669162 这一帧为什么慢`

### Expected response shape

- Print the full time ledger from sensor origin to first and last control output.
- Break down contribution by segment:
  - sensor to fusion
  - fusion to prediction handoff
  - prediction compute
  - prediction to planning handoff
  - planning compute
  - planning to control wait
  - control reuse tail
- Compare the frame to normal steady-state samples.
- Give an attribution label and justify it numerically.

### Example output snippet

`该帧的主要问题不是前段 handoff，而是 planning 计算段显著抬升；同时 control reuse 对 Data Age 有二次放大。综合判断，这是一帧“planning compute spike + reuse tail”组合型异常。`

### Good follow-up prompts

- `同时间窗里正常帧是什么样`
- `这帧是不是 planning spike`
- `附近有没有 drop`

## 4. Module Or Edge Analysis

### Example prompt

`$apollo-perf-trace-analysis 只看 planning，给我 total、planner、runonce 的时间线和长尾样本`

### Expected response shape

- Report module-level statistics and key sub-phase statistics.
- Highlight hotspot windows and top slow samples.
- Explain whether the module issue is compute-dominant or wait-dominant.
- If a chart is requested, prefer a time-series view plus a trimmed or zoomed view when startup outliers distort the axis.

### Good chart prompts

- `给我画 planning total p95 和 jitter 图`
- `画 control total 和 miss rate`
- `把 planning total 做一个去掉启动异常点后的图`

### Example prompt

`$apollo-perf-trace-analysis 看 prediction 到 planning 这一跳有没有明显问题`

### Expected response shape

- Report handoff p50 / p95 / p99 / max and unmatched rate.
- Show whether spikes overlap with anomaly windows.
- State whether the edge is likely a major bottleneck or a secondary symptom.

### Example output snippet

`prediction→planning 边整体 handoff 很快，绝大多数窗口处于低毫秒或亚毫秒级；如果窗口内仍有 RT 长尾，则更应怀疑 planning 自身计算或下游等待，而不是这一跳本身。`

## 5. Drop Analysis

### Example prompt

`$apollo-perf-trace-analysis 看 planning_to_control 的 drop，顺便对齐 RT 时间线`

### Expected response shape

- Restrict the scope to `break_stage=planning_to_control`.
- Report drop count, average gap, top gaps, and missed periods.
- Align that drop series with RT timeline bins.
- State whether drops are synchronized with RT spikes, lead them, or mostly appear independently.

### Good chart prompts

- `给我画 break stage 堆叠图`
- `画 drop_count_total 和 rt_p95 的双轴图`
- `看 planning_to_control drop 和 Data Age 的双轴图`

### Good follow-up prompts

- `再对齐 Data Age`
- `给我看 drop 最多的三个时间窗`
- `画一张 drop_count_total 和 rt_p95 双轴图`

## 6. Plot Requests In Natural Language

The skill should treat requests for charts as first-class requests, not as optional add-ons.

### Supported prompt styles

- `给我画 25 到 40 秒的 RT 和 Data Age 图`
- `画 133 到 147 秒的 planning_total_p95`
- `看这个时间段的 drop_count_total 和 planning_wait_p95`
- `把 drop 和 RT 对齐画出来`
- `给我一张 control reuse 和 Data Age 的趋势图`
- `画 planning total 的抖动图`
- `给我一张 planning miss rate 图，deadline 设成 40ms`
- `看 lidar 的 sensor 到 fusion p95 图`
- `画 radar 和 lidar 的 sensor→fusion 对比图`

### Expected behavior

- Infer the time window if one is given.
- Infer the metrics from natural language.
- Prefer canonical tables if they already exist.
- If a plot can be rendered, return the local file path and explain what the plot shows.
- If a plot cannot be rendered, specify exactly which chart should be produced and which data series and axes it should use.

### Plot guidance

- Use timeline plots for time-window behavior.
- Use raw + trimmed views when startup outliers distort the y-axis.
- Use dual-axis plots only when the scale difference is large enough that a single axis would hide one series.
- Use module charts when the user asks for execution duration, jitter, or miss rate.
- Use sensor→fusion charts when the user asks about parent freshness, sensor-side delay, or radar/lidar asymmetry.
- Use stacked drop charts when the user asks where drops are concentrated over time.
- Always accompany a chart with a brief explanation sentence.

## 7. Standard Report Request

### Example prompt

`$apollo-perf-trace-analysis 基于这次 run 生成一份完整分析文档，重点写 RT、Data Age、Planning、drop 和对齐关系`

### Expected response shape

- Use the report template.
- Organize the write-up as a single coherent document.
- Include data quality, global latency, anomalies, drops, alignment, and conclusions.
- Insert or reference charts when they materially help the explanation.

## 8. Prompting Tips For Teammates

- If you know the window, say the window directly.
- If you know the frame, give `fusion_trace_id` or `parent_trace_id`.
- If you are unsure where to start, ask for an overview first.
- If you want a figure, ask for it directly in natural language; do not worry about a rigid syntax.
- If you care about drops, explicitly ask for alignment with RT or Data Age instead of looking at drop counts alone.
