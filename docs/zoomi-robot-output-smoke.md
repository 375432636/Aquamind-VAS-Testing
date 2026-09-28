# Zoomi 的 `robot_output` 冒烟测试

`scenarios/robot-output-zoomi/01-dev.yaml` 和 `02-5090.yaml` 是两组可重复运行的测试。两组覆盖同样的十种意图和行为预期，但导航问题分别使用各自已绑定地图中的真实区域；其余八条提问相同。两端连接各自已注册的 Zoomi 设备。同一个文件内的十轮沿用同一真实会话；VAS 自己加载设备的人设、意图分类、知识库和历史上下文。

| 轮次 | 预期模型档位 | 需要客户端收到的行为 |
| --- | --- | --- |
| `basic-greeting-8b` | 8B | `robot_output`、表情；无商品图、导航 |
| `basic-wave-8b` | 8B | `robot_output`、挥手动作、表情；无商品图、导航 |
| `basic-wave-again-8b` | 8B | `robot_output`、再次挥手、表情；无商品图、导航 |
| `product-waterproof-27b` | 27B | `robot_output`、防水耳机商品图；无导航 |
| `product-x7air-27b` | 27B | `robot_output`、指定耳机商品图；无导航 |
| `product-gift-27b` | 27B | `robot_output`、送礼耳机商品图；无导航 |
| `product-nothing-27b` | 27B | `robot_output`、Nothing 耳机商品图；无导航 |
| `navigation-experience-27b` | 27B | `robot_output`、DEV 产品体验区 / 5090 数码体验区导航；无商品图、附加动作 |
| `navigation-second-zone-27b` | 27B | `robot_output`、DEV 充电桩 / 5090 顾客服务台导航；无商品图、附加动作 |
| `product-no-navigation-27b` | 27B | `robot_output`；不得下发导航 |

这里的 8B/27B 是**业务路由预期**，不是强制模型参数。每轮报告应查看 `intent_classification_finished` 的分类标签和 `llm_request_started.model`，不能把 `smart` 标签直接当成 27B 已调用。合并结果可用 `scripts/summarize_zoomi_robot_output.py` 导出，额外列出 ASR 文本、RAG 调用、VAS 校验、真实 WebSocket 控制消息和无法归因的情况。

## 运行

准备好项目 Python 环境后，分别运行两个文件。DEV 使用场景内的真实 Zoomi（粉）设备 `AC:A7:04:EB:EA:48`；5090 使用 `C2:B8:56:7B:95:7C`。两端设备硬件型号和配置不同，跨环境差异不能仅归因于模型。

```bash
export VAS_DIAGNOSTICS=frame
python -m voice_scenarios.batch_run \
  --scenarios scenarios/robot-output-zoomi/01-dev.yaml \
  --output artifacts/zoomi-dev-$(date +%Y%m%d-%H%M%S)

# 为 5090 准备只监听本机回环地址的 SSH 端口转发后，指向目标 VAS。
export VAS_MAIN_URL=ws://127.0.0.1:18048/looomyn/v1/
python -m voice_scenarios.batch_run \
  --scenarios scenarios/robot-output-zoomi/02-5090.yaml \
  --output artifacts/zoomi-5090-$(date +%Y%m%d-%H%M%S)
```

`VAS_MAIN_URL` 可以指向常规 5090 VAS，也可以指向隔离的 8B/27B 对照实例；报告中必须记下实际目标。每次使用新的输出目录，不覆盖真实录音和诊断。`--prepare-only` 只校验配置与合成输入，不连接服务。

5090 场景的 `features.scene_navigation: true` 会随 WebSocket Hello 发送。VAS 只有同时收到这个客户端能力声明、设备又启用了场景导航并配置了地图区域时，才会把 `navigation_zone_name` 放进 `robot_output` 工具 schema。未声明该能力的旧报告不能用来判断模型是否会选择地图区域。未配置 `features` 的场景仍发送原有的 `mcp: true`。

输入文字由 Piper 合成为语音，通过真实 WebSocket、ASR、设备配置、意图分类和 RAG 执行。它不是在请求里直接塞入文字；因此报告中的 ASR 文本可能与原始提问不同。十轮在同一会话中顺序执行，保留 VAS 的历史上下文。

```bash
python scripts/summarize_zoomi_robot_output.py \
  --run DEV=artifacts/<dev-run>/sessions/<session>/report.json \
  --run 5090=artifacts/<5090-run>/sessions/<session>/report.json \
  --output artifacts/zoomi-comparison.md
```

## 判读

- **未调用**：完整 VAS 诊断中有 `robot_output_monitor_ready`，但无 `robot_output_evaluated`。旧 VAS 没有监测事件时只能写“未知”；`call_count=0` 不能单独证明没调用。
- **调用但行为缺失**：有通过 VAS 校验的 `robot_output_evaluated`，却没有匹配的 `action`、`display.items kind=image`、`expression` 或 `navigation` WebSocket 消息。报告分别给出参数值与收到的消息。
- **图片数据缺失**：`product_refs` 需要带 URL 的实体媒体候选。RAG 只返回商品文字或商品链接、图片列为空时，模型不能凭商品名称生成可展示的图片引用。
- **语音输入失真**：逐轮对比 `input_text` 和 `metrics.asr_text`；型号被误识别时，产品调用失败不能全部归咎于 LLM。
- **物理执行**：WebSocket 消息只证明测试客户端收到控制指令，不证明实体设备完成动作或导航。

2026-09-28 的实际执行结果和修复顺序见 [结果与分析](zoomi-robot-output-results-20260928.md)。
