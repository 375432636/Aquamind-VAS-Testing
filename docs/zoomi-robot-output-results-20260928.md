# Zoomi `robot_output` 冒烟测试：2026-09-28

## 测试范围与证据

运行 [两套十轮场景](zoomi-robot-output-smoke.md)：DEV 用已同步的 Zoomi（粉）设备 `AC:A7:04:EB:EA:48`，5090 用 `C2:B8:56:7B:95:7C`。每套十轮在同一真实会话中按顺序发语音，经过 ASR、设备人设和上下文、意图分类、RAG、LLM、VAS 工具处理，并收集客户端 WebSocket 控制消息。每轮预期的 8B/27B 档位由业务问题定义，实际模型以 `llm_request_started.model` 为准。

首次运行用了“应用体验区”和“充电桩”作为两端共用导航目标。之后核对设备组，确认它们不在 5090 测试设备绑定的地图中，因此修正为各自地图中的有效地点并完整复跑。下表和逐轮结果均为修正后的第二次运行。

**导航结论复核（旧报告限制）：**这些运行使用的 Testing 客户端 Hello 只声明 `features.mcp=true`，没有声明 `features.scene_navigation=true`。VAS 因而不会向模型提供 `navigation_zone_name` 候选；表中的导航 0/2 是当时测试链路的结果，不能据此判断 8B 或 27B 在收到真实地图候选时会不会导航。5090 场景现已声明该能力，须以新运行重新评价导航。

| 运行目标 | 实际模型（十轮） | `robot_output` 调用 / VAS 校验 | 预期动作 | 预期商品图 | 预期导航 |
| --- | --- | --- | --- | --- | --- |
| DEV 当前入口 | `qwen3.8-max` 10/10 | 诊断不支持，均为未知 | 客户端共收到 6 条动作，不能归因于函数 | 客户端共收到 4 条图片，不能归因于函数 | 客户端 0/2 |
| 5090 常规 VAS | `qwen3-8b` 10/10 | 报告均为未知；运行代码证实这条 8B 路由关闭了函数 | 0/2 | 0/4 | 0/2 |
| 5090 隔离对照：按当前业务路由，接模型网关 | `ro-test-8b` 10/10，含 `smart` 八轮 | **10/10 实际调用，10/10 校验通过** | **1/2**，另有一次多余动作 | 0/4 | 0/2 |
| 5090 隔离对照：强制 27B | `ro-test-27b` 10/10 | **10/10 实际调用，10/10 校验通过** | 2/2 | 0/4 | 0/2 |

强制 27B 是协议对照组：其前三个简单问题也被送至 27B，因此不能作为阶梯路由正确的证据。两个隔离实例只服务测试，常规 5090 VAS、DEV VAS 和 Aquamind 部署没有因本轮测试而更改。表格中动作、图片、导航的分母只计算相应场景的明确要求；函数通过校验并不表示这些行为已经完成。

隔离对照的模型网关把 `ro-test-8b`/`ro-test-27b` 转到物理模型，将 system 消息放在前面，要求模型选择工具，并关闭 thinking；8B 路径移除 `speech.maxLength` 以避开 llama.cpp 工具语法编译问题。网关对最终输出按原始 schema 校验，规范化可为空字段的字符串 `"null"`，且仅在用户明确请求且地点名称匹配时允许导航。常规 5090 未使用这些别名和规则。网关的 `tool_choice=required` 只能保证选择某个工具，不能保证先查 RAG、生成图片引用或选出导航目标。

逐轮的原始调用事件、参数、ASR 文本、RAG 返回和 WebSocket 消息在本地测试产物中：

| 运行目标 | 测试报告 |
| --- | --- |
| DEV | `artifacts/zoomi-dev-valid-zones-20260928/sessions/robot-output-zoomi-01-dev-aacfa50d/report.html` |
| 5090 常规 | `artifacts/zoomi-5090-regular-valid-zones-20260928/sessions/robot-output-zoomi-02-5090-bdbe94e9/report.html` |
| 5090 网关 8B | `artifacts/zoomi-5090-routed-valid-zones-20260928/sessions/robot-output-zoomi-02-5090-bdbe94e9/report.html` |
| 5090 强制 27B | `artifacts/zoomi-5090-forced27-valid-zones-20260928/sessions/robot-output-zoomi-02-5090-bdbe94e9/report.html` |

本地汇总为 `artifacts/zoomi-robot-output-valid-zones-comparison-20260928.md`，可用 `scripts/summarize_zoomi_robot_output.py` 从各 `report.json` 重建。首次运行也保存在 `artifacts/zoomi-robot-output-comparison-20260928.md`。产物包含真实知识库片段与音频，因此不提交 Git；仓库保留测试定义、分析方法和结论。

以下逐轮结果指 5090 隔离网关对照；两组每一轮均实际调用一次函数并通过参数校验。常规 5090 同名十轮均没有收到对应的动作、图片或导航消息。

| 场景 | 预期 | 网关 8B | 强制 27B |
| --- | --- | --- | --- |
| `basic-greeting-8b` | 开心表情 | 表情收到 | 表情收到 |
| `basic-wave-8b` | 挥手、开心表情 | 两项收到 | 两项收到 |
| `basic-wave-again-8b` | 再次挥手、惊讶表情 | 仅表情，动作缺失；被分类为 smart | 两项收到 |
| `product-waterproof-27b` | 商品图；无导航 | RAG 返回，`product_refs=[]`，无图 | RAG 返回，`product_refs=[]`，无图 |
| `product-x7air-27b` | 指定商品图；无导航 | `product_refs=[]`，无图 | RAG 返回，`product_refs=[]`，无图；ASR 误听型号 |
| `product-gift-27b` | 送礼商品图；无导航 | `product_refs=[]`，无图 | RAG 返回，`product_refs=[]`，无图 |
| `product-nothing-27b` | 指定商品图；无导航 | `product_refs=[]`，无图 | RAG 返回，`product_refs=[]`，无图；ASR 误听型号 |
| `navigation-experience-27b` | 数码体验区导航；无动作 | `navigation=null`，无导航，却下发动作 | RAG 返回，`navigation=null`，无导航 |
| `navigation-second-zone-27b` | 顾客服务台导航；无动作 | `navigation=null`，无导航 | RAG 返回，`navigation=null`，无导航 |
| `product-no-navigation-27b` | 禁止导航 | 无导航，符合预期 | 无导航，符合预期 |

## 逐项结论

1. **常规 5090 无法在当前配置下验证 27B。** 第二次十轮分类为 fast 两次、smart 八次，但实际 LLM 全是 `qwen3-8b`；首次运行的分类是 fast 三次、smart 七次，第三条简单挥手问题的档位有波动。运行中容器的 `VAS_INTENT_SMART_MODEL` 和 `VAS_INTENT_FAST_BALANCED_MODEL` 都是 `qwen3-8b`。同一容器 `/opt/xiaozhi-esp32-server/core/lumin_connection.py` 第 1931–1945 行将 `provider=LocalLLM, model=qwen3-8b` 判为 `local_eight_b_route`，并设 `robot_output_enabled = is_robot_output_enabled(self) and not local_eight_b_route`。旧运行版本没有监控事件，因此测试报告的调用状态是“未知”；代码路径与实际模型结合，证明这些直连 8B 轮次没有启用函数。
2. **模型网关让 8B/27B 均能调用协议，但行为不稳定。** 隔离网关 8B 与强制 27B 在两次各十轮运行中均有 `robot_output_evaluated.valid=true` 且 `reason=ok`，每轮调用一次。第二次 8B 两次挥手仅成功一次，还在导航请求下发了多余动作；第一次两次挥手均成功。27B 第二次两次挥手成功。两组第二次各四次商品请求均返回 `product_refs=[]` 且没有图片 WebSocket 消息；各两次有效地点导航请求均返回 `navigation=null` 且没有导航消息。
3. **商品图片缺少可引用数据。** 27B 的防水耳机请求确实调用 `rag-lightrag_search`，返回的 Zoomi 产品表有产品名称、规格等，但 `产品图片` 列为空；X7Air 资料中的 `图片` 也为空，仅有天猫商品详情页链接。VAS 的媒体候选需要实体与有效 HTTP 图片 URL 配对；候选为空时 `robot_output` schema 的 `product_refs.maxItems` 也为零。先补齐图片 URL 和实体媒体标记，再验证模型能选出引用并收到 `display.items kind=image`。
4. **旧导航测试缺少客户端能力声明。** 5090 `C2:B8:56:7B:95:7C` 属于设备组 265，场景导航开关已开，绑定 `AquaMind-Mail` 地图；地图六个有效目标为商场主入口、中央中庭、时尚零售区、数码体验区、餐饮休息区和顾客服务台。DEV `AC:A7:04:EB:EA:48` 属于设备组 180，导航已开，绑定“六楼-原始地图”，其中有产品体验区和充电桩。地点名称虽有效，但 Testing 客户端当时没有在 Hello 声明 `scene_navigation`，使 VAS 的区域候选函数直接返回空列表。隔离网关的 `nav_suppressed=False` 只排除了网关清空导航，不能补足缺失的 schema 字段。需要用更新后的客户端重跑，才可评价模型的导航选择能力。
5. **DEV 不是本地 8B/27B 对照。** 实际十轮均是 `qwen3.8-max`，运行版本没有函数监控事件。客户端累计收到动作消息六条、表情十条、图片展示四条，但不能证明它们来自 `robot_output`。DEV 有效地点导航两轮均未下发。

部分型号在语音识别时被误听，如 X7Air 和 Nothing Ear 3；逐轮报告保留 ASR 原文。这个因素会影响指定商品查询，但无法解释更宽泛的防水耳机和送礼耳机请求也没有图片，以及知识库图片列为空的事实。

## 修改顺序与验收

1. **先用配置接入真实阶梯路由。** 5090 把 fast/balanced 指向网关 8B，smart 指向网关 27B，并核对 Aquamind/UMS 传给 VAS 的 `route.chat_model.mode`：smart 业务轮次必须选 smart 档。测试中仅把 smart 环境变量改成 27B，两次运行分别有七轮、八轮 smart 标签仍落在 8B；配置模式也必须核对。第三条简单挥手在两次运行中的 fast/smart 分类不同，还需调整分类训练或阈值。保持常规实例不变，先在隔离实例复跑，确认简单三轮 `llm_request_started.model=...8b`，知识库/导航七轮为 `...27b`，且逐轮有真实函数事件。
2. **补齐商品素材。** 给目标产品写入可访问的图片 URL，并在知识库工具结果中输出 VAS 可识别的实体与图片绑定。四个商品场景应同时满足非空 `product_refs`、VAS 校验通过、客户端收到对应图片消息。商品详情页 URL 不能当作图片 URL。
3. **用完整 Hello 能力重测导航。** 5090 场景发送 `features.scene_navigation=true` 后，核对模型请求中 `robot_output.navigation_zone_name` 是否包含地图区域；再检查非空导航参数和客户端导航消息。若候选仍为空，再查 Aquamind/UMS 配置同步；若候选存在而模型仍填 null，再调整模型侧工具说明。需要修改 VAS 诊断时，DEV 与 5090 分别更新。
4. **更新 DEV 监控运行版本并确认模型角色。** 在真实 DEV 路由可切换到 8B/27B 前，只将 DEV 结果当业务行为基线，不计入本地模型兼容率。

以上均是待实施方案，不能把隔离实例 10/10 函数调用写成正式环境的成功率。每次改完以同样的十轮套件复跑，并保留报告。
