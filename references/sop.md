# Apollo Perf Trace 全链路时延分析 SOP

本文档用于固定 Apollo 端到端性能打点的标准分析流程。SOP 只依赖三张原始表：`events`、`message_context`、`fusion_inputs`。所有后续的模块耗时、模块间传递、端到端时延、控制复用、异常帧归因、丢帧位置分布、丢帧时间线与时延时间线相关性分析，都必须能够回溯到这三张原始表，不依赖额外的黑盒结论。这样做的目的不是把一次分析写得很复杂，而是把整套能力固定下来，使每次实验都能用同一套步骤、同一套口径、同一套输出物来判断系统状态，并且让不同 run 之间具备可比性。

## 1. 目标、适用范围与分析原则

这套 SOP 面向 Apollo 闭环链路性能分析，核心目标有四个。第一，给出稳定、可复现的模块级和端到端时延统计，回答“系统这次整体快不快”。第二，对异常帧做逐帧归因，回答“为什么慢，是谁慢，慢在计算、等待、复用还是链路缺段”。第三，把丢帧从简单计数升级为“位置分布 + 时间分布 + 与时延尖峰的关系”，回答“丢在什么地方、丢的时候系统发生了什么、丢帧和时延尖峰是否同一时段集中出现”。第四，把每次分析结果固定成标准表和标准报告，形成长期可回归的工程基线。

适用范围是基于 `mono_ns` 的运行时链路分析，不关注 `write` 相关统计，也不把 CPU profiler、线程调度、系统负载等系统级观测作为本 SOP 的前置依赖。换句话说，这套体系解决的是“链路行为”和“模块行为”的问题，而不是操作系统层面的根因剖析。只要原始采集目录中存在 `events/`、`message_context/`、`fusion_inputs/`，就应当能够跑完整个 SOP，并输出统一结果。

分析时必须遵守三个原则。第一，原始表不直接给结论，必须先派生成标准中间表。第二，所有指标必须写明锚点，也就是“从什么时候算到什么时候”，避免同一个名字在不同文档里含义不同。第三，任何异常判断都必须同时给出数值证据和时间上下文，不能只说“有异常”而不说明“异常发生在哪一段、离正常值差多少、是否伴随丢帧或复用”。

除了这三个总原则，还要增加两个执行原则。其一，SOP 中所有“建议输出”的内容，如果要长期回归，就必须进一步收敛为“必选输出”或“可选输出”，不能永远停留在建议层。其二，任何派生表一旦进入正式分析口径，就要固定其一行语义、主键语义、时间锚点和缺失值定义，否则不同脚本作者会不自觉地用出不同版本。

## 2. 三张原始表的角色、字段语义与分析边界

### 2.1 `events`：模块内部时间点表

`events` 的一行表示某个模块在某一时刻触发了一个 phase 事件。它是模块内分析的基础，也是多数派生表的时间骨架。最重要的字段是：

| 字段 | 含义 | 作用 |
| --- | --- | --- |
| `module` | 模块名 | 定位事件属于哪个组件 |
| `phase` | phase 名，如 `proc_enter`、`planner_enter`、`output_pub` | 形成 enter/exit 配对 |
| `trace_id` | 帧 ID | 将事件与链路帧关联 |
| `proc_id` | 处理周期 ID，尤其对 control 重要 | 识别同一控制周期 |
| `mono_ns` | 单调时钟纳秒 | 计算时长的统一时间基准 |
| `event_id` | 当前事件行 ID | 与 `message_context` 输入输出边界对齐 |

基于 `events` 能直接得到模块总耗时、子阶段耗时、周期抖动、phase 长尾、模块级 deadline miss rate，以及启动阶段极端值样本。`events` 的局限也要写清：它只能说明“模块内部发生了什么”，不能单独说明“消息为什么没有到下游”，也不能单独说明“同一 fusion 帧到底由哪些 sensor parent 组成”。因此 `events` 不能脱离其他两张表单独使用。

在执行上还要额外强调一点：`events` 里很多 phase 不是天然成对出现的，必须通过配置把“哪些 phase 可以配对、哪些只能当标记点、哪些需要特殊处理”固定下来。否则同一个模块不同人会配出不同的 `total`，最后连基本分位值都无法对齐。

### 2.2 `message_context`：输入输出边界表

`message_context` 只记录边界事件，也就是 `edge=in` 和 `edge=out`。它与 `events` 通过 `event_id` 一一对齐，用于描述消息到达、消费和输出的上下文。关键字段如下：

| 字段 | 含义 | 作用 |
| --- | --- | --- |
| `module` | 模块名 | 标识边界归属 |
| `edge` | `in` 或 `out` | 区分输入和输出 |
| `channel` | Cyber topic | 用于定义 handoff 边 |
| `trace_id` | 帧 ID | 做上游输出与下游输入匹配 |
| `proc_id` | 周期 ID | control 使用分析 |
| `mono_ns` | 边界发生时间 | 计算 handoff latency |
| `input_seq` / `output_seq` | 消息序号 | 检查跳序、替换型丢帧 |
| `data_ts_ns` | 业务或传感器时间戳 | 仅用于对齐，不用于模块耗时差分 |
| `event_id` | 对齐 `events` 事件 ID | 建立边界与 phase 的对应 |

`message_context` 负责回答“消息在模块之间怎么流动”。基于它可以做模块间 handoff latency、边界 deadline miss rate、未匹配 handoff、输入输出跳序、消息消费延迟等分析。它不能单独说明模块内部哪一段慢，也不能在没有 `fusion_inputs` 的情况下识别多传感器 parent 关系。

这里有一个常见误区需要提前在 SOP 中写死：`data_ts_ns` 可以用来判断传感器时间域的新鲜度，但不能代替 `mono_ns` 做模块内耗时计算。模块内性能、handoff 性能、控制复用分析都应统一使用 `mono_ns` 这个运行时单调时间域，否则一旦不同消息的业务时间戳存在抖动或跨模块不完全一致，会直接污染时延统计。

### 2.3 `fusion_inputs`：sensor parent 到 fusion frame 的桥接表

`fusion_inputs` 每一行表示某个 fusion 输出帧引用了一路 sensor 输入。它是把多传感器源头与统一 fusion trace 接上的关键表。主要字段如下：

| 字段 | 含义 | 作用 |
| --- | --- | --- |
| `fusion_trace_id` | fusion 输出帧 ID | 连接下游统一帧 |
| `parent_trace_id` | 某一路 sensor parent 帧 ID | 连接感知源头 |
| `is_main_sensor` | 是否主 sensor | 做主辅传感器分析 |
| 其他派生识别字段 | 由 trace kind 或模块名补全 | 区分 `lidar` / `radar` / `camera` |

基于 `fusion_inputs` 可以回答“这帧 fusion 到底用了哪些 parent”“某一路 parent 是否缺位”“同一 fusion 帧中不同 sensor 的起点时间为什么不同”“为什么 radar 的 RT/Data Age 可以显著低于 lidar”。它本身不提供时间差，必须与 `events` 中的 `proc_enter`、`output_pub` 组合后，才能得到 `sensor_origin_ns` 和 `fusion_output_ns`。

因此 SOP 必须规定：凡是分析到 sensor 维度、parent 维度、主辅传感器差异、跨传感器时间偏斜，必须明确使用了 `fusion_inputs` 做 parent 绑定，不能只在 `e2e_frame_table` 上看数值后拍脑袋归因。

### 2.4 三张原始表组合后的能力边界

这三张原始表组合起来，已经足以完成以下分析：模块内 phase 耗时、模块间 handoff、sensor 到 fusion 的桥接、E2E 时延、控制复用、异常帧归因、丢帧位置分布、丢帧时间线、丢帧与时延时间线相关性。也就是说，只要这三张表是完整的，就能形成一套闭环链路画像。SOP 的重点不是继续堆更多指标，而是把这套能力固定成统一结构，并保证每个派生结论都能追溯到原始表。

不过能力边界也要说实话。仅靠这三张表，我们能定位到“哪一段慢、哪一段断、哪一段复用高、这些现象是否同时发生”，但还不能直接回答“CPU 被谁抢了、线程是否被 OS 抢占、writer 是否阻塞、系统负载是否是根因”。这些属于更低层系统观测，不在本 SOP 的必选范围内。把边界先写清楚，反而能避免后续误用。

## 3. 派生表体系：标准中间表、表结构与功能定义

原始表不直接作为报告输入，而应先派生为标准中间表。标准中间表的作用有两个。第一，统一口径，避免不同脚本重复做一遍配对逻辑，最后口径不一致。第二，给后续异常分析、丢帧分析、时间线对齐提供标准接口。推荐固定以下九张派生表。

### 3.0 派生表的通用设计规则

为了避免“表名一样但粒度不一样”，所有派生表都必须先写清楚一行代表什么。`module_phase_table` 的一行代表一个 phase 配对样本，`message_handoff_table` 的一行代表一条上游输出到下游输入的匹配边，`trace_link_table` 的一行代表一个 `parent_trace_id -> fusion_trace_id` 关系，`e2e_frame_table` 的一行代表一个 `(parent_trace_id, fusion_trace_id)` 的端到端生命周期，`drop_event_table` 的一行代表一次独立的丢帧事件，而不是一条原始日志。

所有派生表都建议带上以下通用字段中的适用部分：`run_id`、`source_shard`、`build_version`、`build_time`、`relative_s`。其中 `relative_s` 统一表示相对本 run 起点的秒数，用于时间线和场景切片。这样做的目的不是增加字段数量，而是让结果可追踪、可重算、可验证。后续如果发现某个结论有问题，能够快速回到具体 shard、具体脚本版本和具体时间窗。

### 3.1 `module_phase_table`

该表来源于 `events`，用于模块内 phase 配对。建议结构如下：

| 字段 | 含义 |
| --- | --- |
| `module` | 模块名 |
| `trace_id` | 帧 ID |
| `proc_id` | 周期 ID |
| `phase_label` | 规范化阶段名，如 `total`、`planner`、`solve` |
| `enter_phase` / `exit_phase` | 实际配对的 phase 名 |
| `start_ns` / `end_ns` | 配对两端时间 |
| `latency_ms` | 阶段耗时 |
| `pair_method` | `trace_id` / `time_window` / `seq` |
| `is_complete_pair` | 是否完整配对 |

作用是提供模块内耗时明细、分位统计和异常样本。后续所有 “planning total p99”“planner 是否形成长尾”“control solve 是否稳定” 这类分析都应来自该表，而不是直接在原始 `events` 上重复写脚本。

为了让这张表真正可执行，还要把生成规则写清楚。对于明确成对的 phase，优先用 `trace_id` 严格配对；若 `trace_id` 缺失且配置允许，可退化为时间窗配对，但必须在 `pair_method` 中显式标明。对于 `control` 这种同时有 `proc_id` 和 `trace_id` 的模块，要规定 `total` 和子 phase 是优先按 `proc_id` 还是按 `trace_id` 构建，避免一个分析以周期为主、另一个分析以帧为主，最后统计口径冲突。

### 3.2 `message_handoff_table`

该表来源于 `message_context`，用于上游 `out` 与下游 `in` 的匹配。建议字段如下：

| 字段 | 含义 |
| --- | --- |
| `edge_name` | 如 `perception_to_prediction` |
| `src_module` / `dst_module` | 上下游模块 |
| `channel` | 关联 topic |
| `trace_id` | 匹配键 |
| `mono_ns_src` / `mono_ns_dst` | 上游输出和下游输入时间 |
| `handoff_ms` | 模块间传递耗时 |
| `matched` | 是否匹配成功 |
| `unmatched_reason` | 如 `no_src_out`、`no_dst_in`、`trace_mismatch` |

作用是提供模块间 handoff 分布、边界 miss rate、丢帧断点定位以及消息级别的时间上下文。凡是“这条边是不是卡住了”“这条边是不是开始丢帧了”，都应基于该表。

这张表的关键不只是算出 `handoff_ms`，还要保留失败匹配。很多团队只保留 matched 样本，结果 handoff 分位值看起来很好，却完全看不到真正的断链问题。SOP 要求 `matched=0` 的记录必须保留，且 `unmatched_reason` 必须有限集合化，至少区分 `no_src_out`、`no_dst_in`、`multi_match_conflict`、`trace_mismatch` 四类。

### 3.3 `trace_link_table`

该表来源于 `fusion_inputs + events`，用于把 sensor parent 和 fusion trace 连接起来。建议字段如下：

| 字段 | 含义 |
| --- | --- |
| `parent_trace_id` | sensor parent 帧 |
| `fusion_trace_id` | fusion 输出帧 |
| `sensor_kind` | `lidar` / `radar` / `camera` |
| `is_main_sensor` | 是否主 sensor |
| `sensor_origin_ns` | sensor 侧起点时间，通常为对应模块 `proc_enter` |
| `fusion_output_ns` | fusion `output_pub` 时间 |
| `sensor_to_fusion_ms` | sensor 到 fusion 的耗时 |

该表的核心价值是提供 “一帧下游 F 到底来自哪些 parent，以及这些 parent 在融合时有多新” 的解释能力。之前 radar 比 lidar RT 更低的分析，本质上就是由该表得到 `sensor_origin_ns` 差异后才得出的。

如果某一路 parent 找不到 `sensor_origin_ns`，不能直接丢弃，而应保留并标为 `origin_missing`。因为 parent 缺源头本身就是一类重要数据质量问题，也可能是 `parent_missing` 丢帧的线索。

### 3.4 `e2e_frame_table`

该表是全链路主表，来源于 `trace_link_table + message_handoff_table + module_phase_table + control_usage_table`。建议结构如下：

| 字段 | 含义 |
| --- | --- |
| `fusion_trace_id` | 下游统一帧 ID |
| `parent_trace_id` | 对应 sensor parent |
| `sensor_kind` | 传感器类别 |
| `sensor_origin_ns` | sensor 起点 |
| `fusion_output_ns` | fusion 输出 |
| `prediction_input_ns` / `prediction_output_ns` | 预测输入输出 |
| `planning_input_ns` / `planning_output_ns` | 规划输入输出 |
| `first_control_consume_ns` | control 首次消费该 planning 帧 |
| `first_control_output_ns` | control 首次输出 |
| `last_control_output_ns` | control 末次输出 |
| `reaction_time_ms` | `first_control_consume_ns - sensor_origin_ns` |
| `first_output_latency_ms` | `first_control_output_ns - sensor_origin_ns` |
| `data_age_ms` | `last_control_output_ns - sensor_origin_ns` |
| `complete_path` | 关键节点是否完整 |
| `missing_stage` | 若不完整，缺的是哪一段 |

这是对外最重要的一张表。E2E 统计、异常帧筛选、场景分段分析、不同 sensor 对比、RT/Data Age 长尾分析，全部应以它为中心。

SOP 里必须再补一条：`e2e_frame_table` 的一行语义必须固定为一个 `(parent_trace_id, fusion_trace_id)` 组合，而不是只按 `fusion_trace_id` 聚合。因为同一个 fusion 帧可能同时存在 lidar、radar、camera 三行，它们的下游输出时间相同，但起点不同。如果把它们硬合并为一行，就会丢失 sensor 侧差异，也无法解释不同 sensor 的 RT/Data Age 差异。

### 3.5 `control_usage_table`

该表用于描述一帧 planning 结果如何被 control 消费。建议字段如下：

| 字段 | 含义 |
| --- | --- |
| `fusion_trace_id` | planning 所属下游帧 |
| `first_control_consume_ns` | 首次被 control 消费时间 |
| `first_control_output_ns` | 首次控制输出时间 |
| `last_control_output_ns` | 最后一次控制输出时间 |
| `control_reuse_count` | 被多少个 control 周期复用 |
| `consume_proc_ids` | 消费它的 `proc_id` 列表 |
| `reuse_tail_ms` | `last_control_output_ns - first_control_output_ns` |

该表的意义是把“数据到了 control 之后发生了什么”单独剥离出来。很多时候 RT 不高但 Data Age 很高，本质不是前段慢，而是 control 长时间复用了旧 planning。没有这张表，就无法把“计算慢”和“复用久”拆开。

为了让 reuse 分析不产生歧义，SOP 要规定：`control_reuse_count` 至少为 1，表示该 planning 帧至少被一个 control 周期消费过；如果某个 planning 帧从未被 control 有效消费，应单独标为 `unused_by_control`，不能简单记为 `reuse=0` 后混在普通分布里。

### 3.6 `quality_table`

`quality_table` 用于数据质量验收，不是为了展示炫目的统计，而是为了决定后续结果能不能信。建议至少包含：

| 字段 | 含义 |
| --- | --- |
| `metric_name` | 指标名 |
| `metric_value` | 指标值 |
| `threshold` | 验收阈值 |
| `status` | `pass` / `warn` / `fail` |
| `notes` | 说明 |

至少要固定输出 `trace_coverage`、`complete_path_ratio`、`handoff_unmatched_rate`、`missing_stage_topk`、`startup_unstable_duration_s`。任何正式报告都必须先过这一关，否则后面的时延结论都需要打上置信度折扣。

建议再补两个质量项：`phase_pair_fallback_ratio` 和 `origin_missing_ratio`。前者表示有多少模块内样本不是靠严格 trace 配对，而是靠时间窗兜底；后者表示多少 parent 找不到可靠的 source origin。它们虽然不是性能指标，但直接决定某些结论是不是足够稳。

### 3.7 `drop_event_table`

这是本次 SOP 必须新增的核心表，用于把丢帧分析固定成标准产物。它不是简单的“丢了几帧”，而是“在哪一级丢、丢时空窗多长、等价丢了几个周期、证据是什么”。建议字段如下：

| 字段 | 含义 |
| --- | --- |
| `drop_event_id` | 丢帧事件 ID |
| `drop_type` | `hard_drop` / `soft_drop` / `replace_drop` / `parent_missing` |
| `break_stage` | 如 `perception_to_prediction`、`planning_internal`、`planning_to_control` |
| `anchor_trace_id` | 关联 trace，可为 `fusion_trace_id` 或上游 trace |
| `parent_trace_id` | 若适用，关联 parent |
| `break_ns` | 判定断点的时间 |
| `last_good_ns` | 前一条正常样本时间 |
| `resume_ns` | 下一条恢复正常样本时间 |
| `gap_ms` | 中间空窗时长 |
| `expected_period_ms` | 该链路期望周期 |
| `missed_period_count` | 约等于丢了多少拍 |
| `evidence_type` | `no_dst_in`、`no_output_pub`、`seq_skip`、`stale_reuse` 等 |
| `evidence_detail` | 原始字段证据说明 |

这张表是“丢帧位置分布”和“丢帧时间线”分析的基础。没有它，就只能得到笼统的丢帧率，无法说明发生在哪一级，也无法和时延时间线做对齐。

这里最容易偷懒的地方，就是只产出一个丢帧计数，不产出 `evidence_detail`。SOP 明确要求每条 drop event 至少能用一句结构化文字说清楚它为什么被识别为 drop，例如“prediction out 存在，planning in 缺失，最近正常样本在 132.4s，恢复样本在 132.64s，空窗 240ms，约缺 2 拍”。没有这层证据，`drop_event_table` 的可解释性就不够。

### 3.8 `latency_timeline_table`

该表用于固定化时间轴统计，把分散在单帧上的数值汇总到统一时间窗中。建议以 1 秒或 5 秒为时间 bin。字段建议如下：

| 字段 | 含义 |
| --- | --- |
| `time_bin_s` | 相对 run 起点的时间窗 |
| `sample_count` | 窗内帧数 |
| `rt_p50 / rt_p95 / rt_p99` | RT 分位 |
| `data_age_p50 / data_age_p95 / data_age_p99` | Data Age 分位 |
| `planning_total_p50 / planning_total_p95 / planning_total_p99` | Planning 总耗时 |
| `planning_wait_p95 / planning_wait_p99` | `planning->control` 等待分位 |
| `reuse_p95 / reuse_p99` | control 复用高位值 |
| `rt_miss_rate / data_age_miss_rate / plan_ctrl_miss_rate` | 窗口级 miss rate |

这张表的目的，是把“这一段时间系统状态如何”从逐帧视角转换为时间窗视角，从而和丢帧时间线进行对齐。

为了方便不同 run 比较，建议额外保留窗口内 `complete_count`、`anomaly_count_rt`、`anomaly_count_age`。这样每次看到某一段 p95 抬升时，不仅知道数值高，还知道该段有没有明显增加异常帧密度。与此同时，SOP 要明确增加一条：只要窗口里出现 miss rate 图，就不能只看 `miss_rate vs p95`，至少还要看 `miss_rate vs p99` 是否同窗抬升。因为 miss rate 是阈值指标，更贴近 tail 超时，很多时候它和 `p99` 的同步性强于和 `p95` 的同步性。

### 3.9 `latency_drop_alignment_table`

这是专门为“丢帧时间线与时延时间线对齐”设计的汇总表，也是整个 SOP 的关键增强点。建议字段如下：

| 字段 | 含义 |
| --- | --- |
| `time_bin_s` | 时间窗 |
| `drop_count_total` | 窗内丢帧总数 |
| `drop_count_by_stage` | 各断点数量汇总 |
| `rt_p95 / rt_p99` | 同窗 RT 高位值与长尾值 |
| `data_age_p95 / data_age_p99` | 同窗 Data Age 高位值与长尾值 |
| `planning_total_p95 / planning_total_p99` | 同窗 Planning 高位值与长尾值 |
| `planning_wait_p95 / planning_wait_p99` | 同窗 Planning 到 Control 等待高位值与长尾值 |
| `reuse_p95 / reuse_p99` | 同窗控制复用高位值与长尾值 |
| `rt_miss_rate / data_age_miss_rate` | 同窗 miss rate |
| `corr_tag` | 该时间窗是否属于“丢帧与时延同时抬升” |
| `lead_lag_tag` | 丢帧领先时延、同步发生、还是滞后发生 |

这张表的作用，是把“丢帧”和“慢帧”放到同一个时间轴上看。只有建立这张表，才能回答诸如“是不是每次 RT 尖峰附近都伴随 planning_to_control 断点”“是不是 Data Age 长尾集中出现在 control stale reuse 的时间窗”“丢帧先发生，后面才出现时延抬升，还是二者同时爆发”。

如果团队后续只保留一张“问题关联总表”，那就应当保留这张表。因为它几乎把全链路分析最核心的时序关系都汇总到了一个地方。

### 3.10 派生表的生成顺序与依赖关系

派生表不是并列独立生成的，而应按依赖关系构建。推荐顺序如下：

1. 从 `events` 构建 `module_phase_table`
2. 从 `message_context` 构建 `message_handoff_table`
3. 从 `fusion_inputs + events` 构建 `trace_link_table`
4. 从 `events + message_context` 构建 `control_usage_table`
5. 从 `trace_link_table + message_handoff_table + module_phase_table + control_usage_table` 构建 `e2e_frame_table`
6. 从上述表和原始表共同生成 `quality_table`
7. 从 `message_handoff_table + module_phase_table + control_usage_table + trace_link_table` 生成 `drop_event_table`
8. 从 `e2e_frame_table + module_phase_table + control_usage_table` 生成 `latency_timeline_table`
9. 从 `drop_event_table + latency_timeline_table` 生成 `latency_drop_alignment_table`

这样规定顺序的好处是，任何上游表如果重算，下游依赖表就知道必须同步重算，避免出现“E2E 表已经更新了，但 drop 表还是旧口径”的情况。SOP 要求每次 run 都在元数据里写出这些表的生成版本和依赖版本，确保结果可追溯。

## 4. 固定指标体系：模块、链路、端到端、复用与质量

### 4.1 核心指标公式必须固定

如果指标名字统一但公式不统一，SOP 就失去意义。因此建议把核心公式直接写死在文档里。

模块内阶段耗时：

`phase_latency_ms = (end_ns - start_ns) / 1e6`

模块总耗时：

`module_total_ms = (output_pub_ns - proc_enter_ns) / 1e6`

模块间传递耗时：

`handoff_ms = (mono_ns_dst_in - mono_ns_src_out) / 1e6`

Sensor 到 fusion：

`sensor_to_fusion_ms = (fusion_output_ns - sensor_origin_ns) / 1e6`

反应时间：

`reaction_time_ms = (first_control_consume_ns - sensor_origin_ns) / 1e6`

首个控制输出时延：

`first_output_latency_ms = (first_control_output_ns - sensor_origin_ns) / 1e6`

数据年龄：

`data_age_ms = (last_control_output_ns - sensor_origin_ns) / 1e6`

复用拖尾：

`reuse_tail_ms = (last_control_output_ns - first_control_output_ns) / 1e6`

任何脚本如果使用了不同锚点，例如把 RT 锚到 fusion output 而不是 sensor origin，必须明确改名，不能继续叫 `reaction_time_ms`。这是防止团队后续出现“名字一样、含义不同”的最关键约束。

### 4.2 deadline 与 miss rate 的统计口径

本 SOP 不强制所有 run 使用同一个固定阈值，但强制统一统计方式。每一个 deadline 指标都必须写成三元组：对象 + 起点/终点 + 阈值，并记录阈值来源。阈值可以来自当前 run 的观测周期推断，也可以由项目 SLA 或实验频率显式覆盖。例如：

`planning_total_deadline = proc_enter -> output_pub < 80ms`

`planning_to_control_deadline = planning_out -> control_in < 15ms`

`e2e_rt_deadline = sensor_origin -> first_control_consume < 150ms`

对应的 miss rate 统一定义为：

`miss_rate = miss_count / eligible_count`

其中 `eligible_count` 必须写清楚是“所有样本”还是“complete_path=1 的样本”。对于 incomplete 样本，建议单独统计 `missing_rate`，不要混入 miss rate。否则会把“超时”和“断链”混成一个指标，解释会非常混乱。

这里要再补一条执行规则：凡是讨论 miss rate，不能只把 miss rate 和 `p95` 放在一起看。miss rate 是阈值越界频率，更接近 tail 行为，因此必须至少同步检查 miss rate 曲线与对应 `p99` 曲线是否同窗抬升。若 `miss rate` 和 `p99` 对齐、但和 `p95` 不对齐，应优先解释为长尾超时变多，而不是整体高位运行都在退化。若 `miss rate`、`p95`、`p99` 三者都同时抬升，才能说明高位与长尾都在恶化。

### 4.3 指标输出时必须带的辅助字段

任何 `p95`、`p99` 指标都必须同时给出 `count` 和 `max`。如果样本数很低，单独的高分位值没有解释力。对于场景切片或时间窗切片，还必须保留 `sample_count`、`complete_count`、`drop_count_total` 三个字段。这样看到某个窗口 `p99` 特别高时，才能判断它是稳定异常，还是因为样本太少导致分位值漂移。

第一类是模块内指标，全部来自 `module_phase_table`。至少包括 `count`、`p50`、`p90`、`p95`、`p99`、`max`、`std`、`deadline_miss_rate`。对 planning、control 这类关键模块，还应进一步下钻子 phase，比如 `planner`、`runonce`、`solve`、`cmd_write` 之前的 `post_solve` 段。模块内指标回答的是“算得慢不慢，抖得厉不厉害，慢在总耗时还是慢在某一个子阶段”。

第二类是模块间 handoff 指标，全部来自 `message_handoff_table`。固定输出 `handoff_ms` 的各分位、未匹配率、边界 deadline miss rate，以及 `unmatched_reason` 分布。模块间指标回答的是“不是模块算得慢，而是消息传递或者调度等待变长了没有”。

第三类是端到端指标，全部来自 `e2e_frame_table`。必须固定输出 `reaction_time_ms`、`first_output_latency_ms`、`data_age_ms` 的分位、完整路径比例、异常帧比例、分 sensor 统计。端到端指标回答的是“从源头到控制输出到底用了多久”。

第四类是 control 使用指标，全部来自 `control_usage_table`。至少包括 `control_reuse_count` 分布、`reuse_tail_ms` 分布、高复用样本比例，以及 `reuse_count` 与 `data_age_ms` 的相关性。它回答的是“数据为什么老，是不是因为 control 长时间在复用旧 planning”。

第五类是质量指标，来自 `quality_table`。包括 trace 覆盖率、完整帧比例、缺段分布、未匹配 handoff 比例、启动不稳定时长。它回答的是“这批数据能不能作为正式结果使用”。

这五类指标已经构成一套稳定的时延分析骨架。之后无论是转弯段、路口段还是其他场景切片，都应该在这个骨架上做子集统计，而不是临时发明一套新口径。

## 5. 丢帧分析体系：分类、位置分布、时间分布与证据链

### 5.1 必须先定义什么叫“丢帧”

在这套 SOP 中，丢帧不能只定义为“没看到某一帧”。必须把丢帧至少拆成四类。

第一类是 `hard_drop`，即链路某一级明确断掉。典型证据是上游 `out` 存在、下游 `in` 不存在，或者模块有 `proc_enter` 但没有 `output_pub`。第二类是 `soft_drop`，即帧没有完全消失，但错过了进入有效闭环的时机，例如 control 持续复用旧 planning，新 planning 虽然存在却没有被及时消费。第三类是 `replace_drop`，即旧帧在下游消费前被更新帧跨过去，常通过 `seq_skip` 或 trace 跳跃识别。第四类是 `parent_missing`，即 fusion 帧在 parent 层出现缺路或严重时间偏斜，导致某一路 sensor 在业务上等价于缺席。

只有先把这四类分开，后面的“丢帧位置分布”和“丢帧相关性”才有意义。否则把所有情况混成一个丢帧率，只会掩盖真正的问题位置。

### 5.1.1 四类丢帧的标准识别规则

为了让不同 run 的丢帧结果可比，建议把识别规则固定成规则矩阵，而不是靠人工解释。

`hard_drop` 的识别建议至少覆盖三种情况。第一，上游 `out` 存在且下游 `in` 缺失，`message_handoff_table.matched=0` 且 `unmatched_reason=no_dst_in`。第二，模块存在 `proc_enter` 但在合理时间窗内找不到 `output_pub`，并且不是配置允许的“无输出周期”。第三，E2E 主链在某一级节点缺失，且缺失前后上下游样本连续，说明不是全局停机，而是局部断链。

`soft_drop` 的识别建议绑定业务闭环。典型规则是：某个新的 `fusion_trace_id` 已经到达 planning 输出，但在后续若干个 control 周期内没有成为 `first_control_consume_ns` 对应的最新消费对象，同时旧 trace 仍在被重复使用。如果新帧存在但没有进入有效控制闭环，应记为 `soft_drop` 或 `stale_replaced`。

`replace_drop` 的识别建议依赖 `seq` 或下游 trace 跳跃。比如上游连续产生 A、B、C，而下游直接处理了 A、C，没有看到 B 的输入边界，也没有 B 的消费痕迹，则 B 可标记为替换型丢帧。若没有可靠的 `seq`，也可通过同一 `channel` 的 `trace_id` 单调序列和时间邻接关系做近似识别，但报告中应标注“弱证据”。

`parent_missing` 的识别建议从 `fusion_inputs` 出发。若某类传感器按配置应当参与但某窗口持续缺位，或者同一 `fusion_trace_id` 下 `parent` 数量明显少于正常工况，或者某一路 `parent` 虽存在但 `sensor_to_fusion_ms` 极端偏大、业务上已失去实时性，则可标为 parent 层丢帧或准丢帧。这里要强调，`parent_missing` 不等于 `fusion_trace` 缺失，而是源头层信息缺位。

### 5.2 丢帧位置分布如何统计

丢帧位置分布是本 SOP 的固定输出，不能省略。推荐至少分六个位置桶：

1. `sensor_to_fusion`
2. `fusion_to_prediction`
3. `prediction_to_planning`
4. `planning_internal`
5. `planning_to_control`
6. `control_consume_or_reuse`

统计方法不是简单数 trace，而是统计 `drop_event_table.break_stage`。每次 run 至少要输出：

- 各位置的丢帧事件数
- 各位置的占比
- 各位置的平均空窗时长 `gap_ms`
- 各位置的 `missed_period_count` 总量
- 各位置的代表样本 trace

这样做的价值很直接。如果某次 run 主要问题是 `planning_internal`，我们会看到规划内部 `no_output_pub` 或 time window 断裂占多数；如果主要问题是 `planning_to_control`，就会看到 handoff 断点和 stale reuse 集中在边界阶段。位置分布是丢帧归因的第一层，不需要太多解释，但必须是每次报告的固定图表。

### 5.3 丢帧时间分布如何统计

丢帧时间分布指的是丢帧不是均匀发生的，而是集中出现在某些时间窗。应当把 `drop_event_table.break_ns` 映射为 run 相对时间，然后按 1 秒或 5 秒 bin 汇总成时间线。时间线至少要包含：

- 每个时间窗的丢帧总数
- 每个时间窗各 `break_stage` 的丢帧数
- 每个时间窗累计 `missed_period_count`
- 每个时间窗最大 `gap_ms`

这条时间线的目的，不是看“总共有多少丢帧”，而是找“丢帧高发时段”。一旦时段被找出来，就能与 RT、Data Age、Planning total 的时间线叠加，判断丢帧是不是和时延尖峰同一时间出现。

### 5.4 丢帧事件的时间上下文必须保留

任何一条丢帧事件，都应尽量保留前后样本上下文。最少要有前一条正常样本时间 `last_good_ns`、断点时间 `break_ns`、恢复时间 `resume_ns`。在已知链路期望周期的情况下，应计算：

`missed_period_count = max(0, floor(gap_ms / expected_period_ms) - 1)`

例如链路周期约为 80ms，若前一条正常样本到下一条恢复样本间隔为 240ms，则可认为中间大约缺了 2 个周期。这个字段的作用非常大，它把“时间上空了多久”转换成“业务上大概丢了几拍”，让报告更直观，也更容易与控制周期、传感器频率对齐。

### 5.5 丢帧分析中容易误判的三种情况

第一种误判是把启动期不稳定当成丢帧。系统刚启动时，很多链路尚未形成稳定闭环，可能会出现 `no_dst_in` 或缺少完整配对，这种情况必须单独标记为 `startup_unstable`，不能直接并入稳态丢帧率。第二种误判是把 incomplete 样本一律当成 `hard_drop`。有些 incomplete 是因为采集开关开启或关闭边界造成的截断，不一定是真实断链。第三种误判是把高复用直接当成丢帧。高复用更准确地说是“新数据没有及时接管”，只有当新帧已经存在却未进入有效消费链时，才应计为 `soft_drop`，否则只是 reuse 行为本身偏高。

## 6. 丢帧时间线与时延时间线对齐：标准做法与相关性分析

这是这套 SOP 新增且必须固定化的部分。我们不是只想知道“既有慢帧，也有丢帧”，而是要知道“二者是否在同一时段集中出现，谁先发生，谁后发生，是否存在明显的阶段对应关系”。

### 6.1 统一时间锚点

要对齐两条时间线，必须先规定统一时间轴。推荐用 run 内相对时间轴，零点取全 run 最早的 `mono_ns`。之后把所有统计都映射到 `time_bin_s`。完整 E2E 帧用 `sensor_origin_ns` 作为帧级锚点，模块或 handoff 事件用各自的 `start_ns` 或 `mono_ns_src` 作为事件锚点，丢帧事件用 `break_ns` 作为锚点。这样同一秒钟内出现的 RT 尖峰、Planning 尖峰和丢帧事件才能被放进同一个 bin 中。

### 6.2 生成两条基础时间线

第一条基础时间线是 `latency_timeline_table`。它记录每个时间窗里的 RT、Data Age、Planning total、Planning wait、Reuse、RT miss rate、DataAge miss rate 等统计。第二条基础时间线是丢帧时间线，可由 `drop_event_table` 按时间窗汇总得到，包含 `drop_count_total`、`drop_count_by_stage`、`missed_period_count_sum`、`gap_ms_max` 等。然后把两者左连接到同一个 `time_bin_s` 上，即得到 `latency_drop_alignment_table`。

### 6.3 对齐后至少回答三个问题

第一，丢帧高发时间窗是否伴随 RT 或 Data Age 高位值抬升。如果某一时间窗 `drop_count_total` 明显升高，同时 `rt_p95` 和 `data_age_p95` 也明显抬升，则说明丢帧与慢帧不是两类独立问题，而是同一时间段的系统异常。第二，抬升的主要对应项是什么。如果 `planning_total_p95` 随着丢帧一起抬升，说明更像规划计算或规划内部异常；如果 `planning_wait_p95` 与 `reuse_p95` 同步抬升，则更像 control 侧等待或复用问题。第三，二者谁领先。可以用相邻时间窗的前后关系给 `lead_lag_tag` 打标签，标记为“drop_leads_latency”“latency_leads_drop”“coincident”。这在工程上很重要，因为它能帮助判断丢帧是根因、共因，还是结果。

这里还要补充一条和 miss rate 有关的规则：如果某段时间 `rt_miss_rate` 或 `data_age_miss_rate` 上升，不能只去对 `rt_p95` 或 `data_age_p95`。至少还要同步检查对应 `rt_p99`、`data_age_p99` 是否同窗抬升。因为 miss rate 本质上反映阈值越界频率，更常见地和 tail 恶化同步，而不一定和 `p95` 同步。

### 6.4 相关性分析的标准输出

不建议只做肉眼看图，至少应固定四类量化结果。

第一类是同窗相关性，例如计算 `drop_count_total` 与 `rt_p95`、`rt_p99`、`data_age_p95`、`data_age_p99`、`planning_total_p95`、`planning_wait_p95`、`reuse_p95` 的 Pearson 或 Spearman 相关系数。第二类是阶段性相关性，例如 `planning_internal` 丢帧数与 `planning_total_p95` 的相关性、`planning_to_control` 丢帧数与 `planning_wait_p95` 的相关性。第三类是 lead/lag 对比，即比较 `drop_count_total(t)` 与 `rt_p95(t+1)`、`data_age_p95(t+1)` 的关系，判断丢帧是否领先下一个时间窗的时延恶化。第四类是 miss-rate 对齐相关性，例如比较 `rt_miss_rate(t)` 与 `rt_p99(t)`、`data_age_miss_rate(t)` 与 `data_age_p99(t)` 是否同窗同步抬升，用来判断 miss rate 的主解释更偏向长尾还是高位整体退化。

这里要强调一点：相关性不是因果性。但在这套只依赖三张原始表的体系下，时间对齐后的相关性已经能把排查范围缩小很多。它能明确告诉我们，是“某些时间段既掉帧又变慢”，还是“系统一直有慢帧，但和丢帧并不同步”，这两种情况的排查方向完全不同。

### 6.5 时间线对齐后的必选图表

为了避免每次报告只给表格而没有直观证据，建议固定六张图作为必选图。第一张是 `drop_count_total` 与 `rt_p95` 的双轴时间线图，用来看 RT 尖峰是否伴随丢帧高发。第二张是 `drop_count_total` 与 `data_age_p95` 的双轴时间线图，用来看数据年龄拉长是否伴随链路断点或 stale reuse。第三张是按 `break_stage` 堆叠的丢帧时间线图，用来看是哪个位置在某一时段集中恶化。第四张是 `planning_total_p95`、`planning_wait_p95`、`reuse_p95` 与丢帧数的组合图，用于区分“计算尖峰”“等待尖峰”“复用拖尾”三类模式。第五张是 `rt_miss_rate` 与 `rt_p99` 的对齐图。第六张是 `data_age_miss_rate` 与 `data_age_p99` 的对齐图。

图表本身不是结论，但它能把表格里不容易看出的同步性呈现出来。因此 SOP 不要求每次都画很多图，但这六张图应被视作最小集合。尤其是 miss rate 相关图，不能只和 `p95` 对齐；`miss rate vs p99` 是必选证据，`miss rate vs p95` 最多只能作为补充图。

## 7. 标准执行流程：每次 run 必须按同一顺序落地

### Step 1：原始数据收集与目录验收

确认 `events/`、`message_context/`、`fusion_inputs/` 是否齐全，run 目录是否统一，文件是否能正常读取。若缺失任一原始表，则报告必须标注分析降级，不能声称完成全链路分析。

### Step 2：原始表基础校验

检查是否存在空文件、异常 shard、时间戳逆序、明显重复 header、字段列数异常。这里不做深入结论，只确认“数据可读且结构基本正确”。

### Step 3：构建标准中间表

按固定配置生成 `module_phase_table`、`message_handoff_table`、`trace_link_table`、`e2e_frame_table`、`control_usage_table`、`quality_table`、`drop_event_table`、`latency_timeline_table`、`latency_drop_alignment_table`。这一步必须是机械化和可重复的，不能靠人工临时拼表。

真正执行时，建议每张表都输出一份构建摘要，包括输入行数、输出行数、过滤掉的样本数、关键缺失字段比例。这样一旦某张表在不同 run 之间样本量突然异常，可以第一时间发现问题不是算法本身，而是数据构建链路出了偏差。

### Step 4：质量验收

先读 `quality_table`，判断 trace 覆盖率、完整帧比例、未匹配 handoff 比例、启动不稳定区间是否在可接受范围内。只有这一关通过，后面的分位统计才可作为正式结果。若未通过，报告正文要明确指出“本次 run 结果可做趋势参考，但不作为严格基线”。

### Step 5：全局统计

输出模块内、模块间、E2E、复用、质量五类指标的全局汇总。这里是全 run 总览，回答“整体快不快、哪一跳最慢、Data Age 为什么高、丢帧主要集中在哪一级”。

这一步要再加一条明确要求：凡是输出 miss rate，总结时必须同时给出阈值来源、对应 `p99`、以及一张时间线证据，不能只给一个比例。

### Step 6：稳态窗口切分

把启动期和稳态期拆开。启动期极端值通常会拉坏图轴，也容易把系统尚未稳定阶段误判成算法尖峰。稳态窗口规则至少应包含：去掉明显启动不稳定时间段、优先使用 `complete_path=1` 样本、必要时单独输出 raw 与 steady 两套图。

如果某次 run 有明确工况切换，例如接管、复位、地图切换，也应把这些时段从稳态统计中剥离出来，并在报告中单列说明。否则后续横向比较会把“场景切换成本”误认为“算法退化”。

### Step 7：异常帧与丢帧事件识别

基于 `e2e_frame_table` 识别 RT、Data Age 异常帧，基于 `drop_event_table` 识别各类丢帧事件。异常帧必须能回查到 planning、handoff、reuse；丢帧事件必须能回查到具体断点、空窗时长和证据类型。

### Step 8：时间线对齐与相关性分析

生成 RT、Data Age、Planning total、Planning wait、Reuse、RT miss rate、DataAge miss rate 的时间线，再与丢帧时间线对齐。至少给出同窗高发段、同步抬升段、领先滞后关系和阶段相关性。此步骤的输出是判断“丢帧与时延是否同源”的关键。

同时要求对 miss rate 额外做一层检查：`rt_miss_rate` 至少和 `rt_p99` 对齐一次，`data_age_miss_rate` 至少和 `data_age_p99` 对齐一次。若 miss rate 与 `p99` 对得上、但和 `p95` 对不上，结论中要明确写出“这更像长尾超时变多，而不是高位整体退化”。

### Step 9：场景切片分析

若 run 中存在转弯、路口、避障等时间窗标签，则在标准体系之上做场景子集统计。场景切片不是另起炉灶，只是在同样的 `e2e_frame_table`、`drop_event_table`、`latency_drop_alignment_table` 上筛选时间窗再做一次同口径统计。

### Step 10：形成标准报告与基线归档

每次 run 必须输出标准报告、标准图、标准中间表和标准基线文件。报告负责讲清楚结论与归因，中间表负责可追溯性，基线文件负责后续 run-to-run 对比。只要 SOP 固定下来，后续每次实验就不再需要从零思考“这次应该看什么”。

## 8. 标准报告结构与固定输出物

标准报告建议固定为一个主文档，不依赖附录也能完整读懂。正文至少包含以下内容：

1. 数据质量结论：本次数据是否可作为正式结论，启动不稳定持续多久。
2. 全局时延结论：模块内、模块间、E2E、复用指标总览。
3. 异常帧归因：Top 异常 RT、Top 异常 Data Age 的逐帧解释。
4. 丢帧位置分布：哪一级最常断，平均空窗多长。
5. 丢帧时间线：哪些时间窗是丢帧高发段。
6. 丢帧与时延对齐：丢帧与 RT/Data Age/Planning 是否在同窗同步抬升。
7. miss rate 与 p99 对齐：RT miss、DataAge miss 是否和对应 p99 曲线同窗抬升。
8. 稳态与启动对比：排除启动异常后，系统稳态画像如何。
9. 场景切片：如转弯段、路口段等特定工况下的变化。
10. 行动建议：优先优化模块、边界还是复用策略。

固定输出物建议至少包含：

- `module_phase_table.csv`
- `message_handoff_table.csv`
- `trace_link_table.csv`
- `e2e_frame_table.csv`
- `control_usage_table.csv`
- `quality_table.csv`
- `drop_event_table.csv`
- `latency_timeline_table.csv`
- `latency_drop_alignment_table.csv`
- `steady_state_summary.csv`
- `deadline_metrics.csv`
- `anomaly_frame_table.csv`
- `summary.md`

其中 `drop_event_table`、`latency_drop_alignment_table`、`steady_state_summary.csv`、`deadline_metrics.csv` 和 `anomaly_frame_table.csv` 是报告可信度的最小增强集合。后续所有“丢帧为什么发生、是不是和慢帧同一时段、启动期是否污染稳态、deadline miss 口径是什么、Top 异常帧先看谁”的问题都应先从这些表开始。

如果团队希望把报告进一步模板化，还可以额外固定几类“必须有解释文字”的图：一张全局 E2E 图、一张 planning 关键图、一张 drop-latency 对齐图、两张 miss rate vs p99 对齐图。图的数量不必多，但每张图下都必须写清“这张图要证明什么”，避免报告变成只堆图不解释。

## 9. 这套体系已经能够做到哪一步

基于三张原始表，这套 SOP 已经能够稳定做到以下层级。第一，知道模块内哪里慢、模块间哪一跳慢、整条链路从 sensor 到 control 输出有多慢。第二，知道 Data Age 高是因为前面慢，还是因为 control 复用旧 planning。第三，知道异常帧是规划计算尖峰、边界等待变长、复用拖尾，还是链路缺段。第四，知道丢帧不是抽象的“丢了”，而是具体丢在 `prediction_to_planning`、`planning_internal`、`planning_to_control` 还是 sensor parent 层。第五，知道丢帧高发段是否和 RT、Data Age、Planning total 的尖峰发生在同一个时间窗，以及谁先出现。第六，知道 miss rate 的抬升更偏向高位退化还是长尾超时，因为可以把它和对应 `p99` 曲线做同窗对齐。

换句话说，这套体系已经不是“做一个平均耗时表”，而是一套完整的链路级行为分析框架。它能够支持常规 run 评估，也能够支持专项问题分析，例如“为什么 radar 的 RT 比 lidar 低很多”“为什么某个转弯段 Planning p95 抬升”“为什么 Data Age 很高但 RT 还好”“为什么某段时间既掉帧又变慢”“为什么 miss rate 升高但 p95 看起来变化有限”。只要坚持所有结论都回溯到三张原始表，这套体系就具备足够的可解释性和可维护性。

这套体系之所以足够强，不是因为它把所有问题都解决了，而是因为它把最关键的链路级问题都拆成了可执行的数据对象：phase、handoff、parent link、E2E frame、control usage、drop event、alignment timeline。只要这些对象被固定，后续你们是做单次分析、做自动化日报、还是做回归比对，都会顺很多。

## 10. 命名规范、配置规范与验收清单

为避免后续脚本和报告口径漂移，建议把以下配置固定成单独文件并纳入版本管理：

1. `PHASE_PAIR_CONFIG`：定义各模块 `total` 和子 phase 的 enter/exit 配对。
2. `HANDOFF_EDGE_CONFIG`：定义标准边，如 `perception_to_prediction`、`prediction_to_planning`、`planning_to_control`。
3. `EXPECTED_PERIOD_CONFIG`：定义不同链路和模块的期望周期，用于 `missed_period_count`。
4. `DROP_RULE_CONFIG`：定义 `hard_drop`、`soft_drop`、`replace_drop`、`parent_missing` 的识别规则。
5. `TIME_BIN_CONFIG`：定义时间线统计使用 1 秒还是 5 秒窗口。

每次 run 完成后，应按以下清单验收：

- 原始三表齐全且可读。
- 标准中间表全部成功生成。
- `quality_table` 结果已写出并通过最低验收门槛。
- 全局时延指标已完成。
- 丢帧位置分布已完成。
- 丢帧时间线已完成。
- 时延时间线与丢帧时间线已完成对齐。
- miss rate 与对应 `p99` 的对齐检查已完成。
- 相关性结论已给出，并明确是同窗同步、领先还是滞后。
- 标准报告已输出，且正文能在不看附录的情况下说明结论。

满足以上条件，才能认为一次 run 的全链路时延分析闭环完成。
