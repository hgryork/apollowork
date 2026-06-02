# 指标定义

## 核心运行时公式

运行时延统一使用 `mono_ns`。

- `phase_latency_ms = (end_ns - start_ns) / 1e6`
- `module_total_ms = (output_pub_ns - proc_enter_ns) / 1e6`
- `handoff_ms = (mono_ns_dst_in - mono_ns_src_out) / 1e6`
- `sensor_to_fusion_ms = (fusion_output_ns - sensor_origin_ns) / 1e6`
- `reaction_time_ms = (first_control_consume_ns - sensor_origin_ns) / 1e6`
- `first_output_latency_ms = (first_control_output_ns - sensor_origin_ns) / 1e6`
- `data_age_ms = (last_control_output_ns - sensor_origin_ns) / 1e6`
- `reuse_tail_ms = (last_control_output_ns - first_control_output_ns) / 1e6`

## 分位数集合

不要只看 `p95`。

默认角色定义：

- `p50`：典型运行水平
- `p95`：高位运行水平
- `p99`：长尾风险

对于 RT、Data Age、关键模块总耗时，推荐最少同时报告：

- `p50`
- `p95`
- `p99`
- max
- sample count

解释规则：

- `p95` 高，说明高位运行状态已经退化
- `p99` 高但 `p95` 中等，说明主要问题在长尾
- miss rate 高且 `p99 - p95` 差很大，通常表示重复超时或不稳定窗口

## Deadline 与 miss rate

每个 deadline 必须显式说明：

- 对象
- 起止锚点
- 阈值
- 阈值来源

示例：

- `planning_total_deadline = proc_enter -> output_pub < 80ms`
- `planning_to_control_deadline = planning_out -> control_in < 15ms`
- `e2e_rt_deadline = sensor_origin -> first_control_consume < 150ms`

这些只是例子，不是通用常数。canonical builder 默认会根据 observed planning period 推断阈值，并在 `deadline_metrics.csv` 里记录 `threshold_source` 和 `base_period_ms`。如果项目已有固定 SLA 或频率预算，应该用 override。

miss rate：

- `miss_rate = miss_count / eligible_count`
- 作图时优先使用 `miss_rate_pct = 100 * miss_count / eligible_count`

解释 miss rate 时要注意：

- miss rate 是阈值指标，不是分位数
- miss rate 不能表述成 “p95 变差了”
- miss rate 往往更接近 tail 行为、更接近重复超时，因此通常和 `p99` 的关联强于 `p95`
- miss rate 高时，必须同时给出阈值、`p99`、代表窗口或代表帧

对 miss rate 相关图的默认要求：

- 不能只画 `miss_rate` vs `p95`
- 至少要检查 `miss_rate` 曲线与对应 `p99` 曲线是否同窗抬升
- 如果 `miss_rate` 与 `p99` 对齐，但和 `p95` 不对齐，默认解释应偏向长尾超时，而不是高位整体退化
- 如果 `miss_rate`、`p95`、`p99` 三者都同步抬升，才能说高位运行水平和长尾都在恶化

如果 incomplete-path 样本被混入 strict miss rate，必须明确说明。

## Jitter 与 spread

本 skill 支持两个实用的 spread 定义：

- `std`：时间窗内样本标准差
- `jitter = p95 - p50`

当目标是看高位 spread 且不想让单个极值主导时，优先使用 `jitter = p95 - p50`。当目标是看总体波动时，使用 `std`。

模块图建议至少联合展示：

- `p50`
- `p95`
- `p99`
- `jitter`
- miss rate

## Sensor to fusion

定义：

- `sensor_to_fusion_ms = (fusion_output_ns - sensor_origin_ns) / 1e6`

它适合用来：

- 比较 lidar 与 radar 的新鲜度差异
- 判断某一路 parent 在 fusion 前是否系统性变旧
- 基于 freshness 或 deadline 阈值计算 sensor-side miss rate
- 判断 RT/Data Age 变差是否在 prediction / planning 之前就已经出现

高频场景下，不能停在 `sensor_to_fusion_ms` 本身，还要继续拆成：

- upstream compute
- 重模块前的 queue 或 wait
- 重模块自身 compute
- post-module handoff
- fusion compute

## Alignment delta

当用户关心下游窗口里的 lidar/radar 时间错位时，使用 alignment delta。

同一 `fusion_trace_id` 下定义：

- `rt_alignment_delta_ms = abs(reaction_time_ms_lidar - reaction_time_ms_radar)`
- `data_age_alignment_delta_ms = abs(data_age_ms_lidar - data_age_ms_radar)`

推荐默认聚合方式：

- 按 `relative_s` 做 1 秒时间窗
- 每个时间窗内取 alignment delta 的 `p95`

默认这么做的原因：

- 1 秒窗便于和 drop timeline、deadline-miss timeline 对齐
- `p95` 能更好反映窗口内持续错位，不容易被均值稀释
- 每秒成对 lidar/radar 样本数通常不算太大，`p95` 仍然可解释

这是实用分析口径，不是工业界唯一标准。如果项目使用其他 bin size 或 summary statistic，必须明确说出来。

## Raw 与 steady-state

如果启动行为重要，就看 raw。若要判断稳定运行性能，就看 steady-state。若启动期或采集边界明显扭曲图轴，必须把 raw 和 steady-state 分开。

canonical builder 会输出 `steady_state_summary.csv`，包含 `raw` 与 `steady` 两个 scope。默认 steady start 取第一次完整闭环 control consume，除非调用方通过 `--steady-start-s` 覆盖。

## 异常规则

异常规则必须显式定义。常见示例：

- RT anomaly：`reaction_time_ms > p99` 或超过绝对阈值
- Data Age anomaly：`data_age_ms > p99` 或超过绝对阈值
- Planning spike：`planning_total_ms > p99` 或超过某个业务阈值
- Drop burst window：`drop_count_total` 相对邻近窗口或全局基线异常偏高
- Alignment anomaly：某时间窗内 `rt_alignment_delta_ms` 或 `data_age_alignment_delta_ms` 的 `p95` 超过可接受跨传感器 skew

使用基于分位数的异常规则时，必须同时给出样本数。

对于 drop 规则，高 `control_reuse_count` 不能单独作为 `soft_drop` 证据。`soft_drop` 需要有更新的 planning output 没被消费、被 stale-replace，或可证明旧 trace 持续被用而新 trace 没接管。

## 高频回归的根因提示

如果更高输入频率使 RT/Data Age 变差，至少逐项检查：

- service time 是否已经逼近或超过 observed period
- 重模块前的 queue 或 wait 增长是否快于模块 compute 自身增长
- parent origin 是否缺失或在 fusion 前就已严重滞后
- 问题是否集中在某一路 sensor
- drops 和 deadline misses 是否同时制造了 downstream alignment mismatch windows

不要停在 “p95 变高了”。skill 应继续说明这次退化更像：

- tail-only
- threshold-driven
- queue-driven
- compute-driven
- alignment-driven
