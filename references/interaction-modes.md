# 交互模式

当用户已经明确说明要看什么时，使用这个参考。目标是选最小但足够有用的回答模式，不要每次都强行输出完整报告。

## Time window requests

识别类似请求：

- `25-40s`
- `133-147s`
- `100s 左右发生了什么`
- `看这段时间`

默认回答结构：

- 明确写出所用时间窗
- 说明该时间窗内的 sample count 与 complete-path count
- 给出窗口内 RT / Data Age / Planning 的 `p50`、`p95`、`p99`
- 给出窗口内 deadline miss 计数或比例
- 给出窗口内 drops
- 如果相关，给出窗口内 lidar/radar alignment delta
- 点名 top anomaly frames
- 与 steady-state 或全局基线做对比

## Frame requests

识别类似请求：

- `看最坏一帧`
- `看 fusion_trace_id=...`
- `为什么这一帧慢`

默认回答结构：

- 给出从 sensor origin 到 control 的完整账本
- 拆分各段贡献
- 给出 planning total、planner、runonce、wait、reuse
- 说明附近是否有 drops 或 miss windows
- 给出一个 attribution label，并用数字支撑
- 如果有价值，比较同一 fusion output 下的 lidar/radar parent

## Module or edge requests

识别类似请求：

- `看 planning`
- `看 control`
- `看 prediction 到 planning`
- `看 lidar_detection`

默认回答结构：

- 给出模块或 edge 的统计摘要
- 包含 `p50`、`p95`、`p99`、miss rate
- 标出 hotspot windows 和 top slow samples
- 判断更像 compute、wait、queue，还是 freshness 问题
- 如果相关，说明与 anomalies 或 drops 的重叠

## Anomaly requests

识别类似请求：

- `看 RT 异常`
- `看 Data Age 异常`
- `看 planning spike`
- `为什么高频会变差`

默认回答结构：

- 写明 anomaly rule
- 给出各级严重度计数
- 给出代表样本
- 给出归因模式聚类
- 如果问题和频率有关，比较 period、service time、queue 或 wait 的增长

## Drop requests

识别类似请求：

- `看 drops`
- `看 planning_to_control drops`
- `drop 和 RT 对不对齐`

默认回答结构：

- 说明 drop type 和 break-stage 范围
- 给出 counts、gap、missed periods
- 把 drop timeline 和 latency timeline 对齐
- 如果相关，把 drop 或 miss windows 和 downstream alignment-delta windows 对齐
- 判断它们更像同步、lead-lag，还是基本独立
