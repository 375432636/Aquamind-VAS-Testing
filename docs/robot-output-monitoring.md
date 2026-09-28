# `robot_output` 逐轮监测

## 实时测试页

在 `/live/` 开启“记录日志并生成报告”后，勾选“监测 robot_output”。函数调用、动作、商品图片、表情、导航各有“仅记录 / 必须返回 / 不得返回”三档。配置在连接时固定，应用于本次会话的每一轮。报告的“Robot output 监测”面板逐项显示预期、观察值、证据来源和结果。

**证据口径：**

- 函数调用：VAS 诊断流的 `robot_output_evaluated`，含 VAS 协议校验 `valid`；`robot_output_monitor_ready` 表示该轮 VAS 支持监测，并使“没有调用”可判定。
- 动作：本轮 WebSocket `action` 的 `state=start` 和工具参数中的动作名一致。
- 商品图片：工具参数 `product_refs` 非空，同时本轮收到 `display.items` 中 `kind=image` 的图片。普通 `image` 消息缺少商品来源标识，只列为无法归因，不算 `product_refs` 成功。
- 表情：本轮 `expression.expression.key` 与工具参数一致。
- 导航：本轮 `navigation` 的动作和 `zone_id` 与工具参数一致；`stop` 也算导航消息。

报告中的“已观察到”指测试客户端收到匹配的控制消息，**不证明**真实机器人完成了动作、图片成功渲染或到达目的地。旧 VAS 如果没有新增诊断事件，即使诊断流完整，也只能显示“证据不足”，不能把普通对话或任意控制消息冒充 `robot_output` 调用。

## 自动场景

在每轮的 `expect.business` 下设置：

```yaml
expect:
  business:
    robot_output:
      monitor: true
      called: true
      action: true
      product_refs: false
      expression: true
      navigation: false
```

各项目的 `true` 表示必须观察到，`false` 表示不得观察到。省略该项目表示仅记录，不参与通过/失败判定。只设置 `monitor: true` 可生成观察记录而不要求函数调用。自动场景需开启 `stage` 或 `frame` 诊断；`off` 会在运行前拒绝此配置。

## VAS 最小补充

dev 与 WTCC-5090 分别添加同名、同结构的两条诊断事件：每次 LLM 对话调用一次 `robot_output_monitor_ready`，每次真实函数包络验证后一次 `robot_output_evaluated`。后者只记录 `valid`、`reason` 与已验证的四类控制参数；不记录 `speech` 或原始会话上下文。前者的 `enabled=false` 表示此轮 VAS 没开放 `robot_output`（例如 WTCC-5090 的常规本地 8B 路由）。这两条事件属于 VAS 原有 WebSocket 诊断流，不改变设备控制协议。
