# 客户端音轨独立与可选校时

VAS 基线：2026-09-19 从 GitHub 拉取的 dev `396f4caa`。
Testing 基线：main `6616795`，保留本地修复 `40e12a7`，整合此前尚未提交的 clock-sync worktree。

## 数据约束

- 客户端连接起点、上行和实际播放时间独立保存。报告不修改原始事件。
- 客户端与 VAS 音频序号是两个独立计数器，不能用大小比较推断轮次，更不能据此删音频。
- VAS 输出类别只有在完整句协议及会话、轮次等证据校验后作为独立注释关联。
- 浏览器 v2 输出时钟逐区间验证。坏区间不影响后续已确认区间；PCM 偏移仍按原始追加顺序累计。
- 第一帧未知时，后续可靠帧不能冒充首次播放。未知的句尾也不能用于计算正式回复前的等待。
- 原始 WAV 完整保留；无法可靠定位的区间不伪装为实际时间，可单独回听。
- 校准只调整 VAS 坐标，客户端起点与音轨、等待及 WAV 不变。相同端内的耗时仍使用其单调时钟。
- 未采样、超时、时钟漂移或不同 session 的样本都自动回退到原始时间，UTC+8 仅是展示时区。
- 诊断结束等待最多 15 秒，记录 requested / confirmed / timeout；缺少最后一批诊断与客户端录制结果分开处理。

## 校准使用

Python 开启诊断时默认后台校准；YAML `clock_sync: false` 关闭。
Actions 环境变量 `VAS_CLOCK_SYNC=0` 关闭。网页“自动校准客户端与 VAS 时间”复选框控制采样。
报告可切换原始时间和校准时间；原始文件不会被改写。旧报告无历史校时样本，不能事后声称已校准。

## 验证

```bash
python -m pytest tests/test_client_audio_independence.py tests/test_clock_sync.py
node --test tests/*.test.cjs
python -m pytest --cov-config=.coveragerc-client --cov=voice_scenarios --cov-fail-under=80
VAS_TEST_ROOT=/absolute/latest-vas-worktree python -m pytest tests/test_local_stack.py
```

真实本地集成使用 VAS 业务代码和 Fake 外部服务，涵盖 PTT、VAD、多轮工具、打断及诊断 off/stage/frame；PTT/VAD 验证实际返回了可用校时样本。

原始问题报告 `c1f29b00dbbd4372a505eb35f394481c`：第 3 轮完整 18.96 秒已恢复入会话；第 1 轮已确认音频从 0.36 秒恢复至 5.10 秒。剩余约 0.128 秒播放时刻不确定，完整采样仍在原始 WAV。

2026-09-19 最终验收：

- Testing 完整回归：390 通过、4 个显式启用的集成测试跳过；覆盖率 91.78%。
- 上述 4 个真实 VAS + Fake 外部服务集成测试单独启用后全部通过；PTT、VAD 各采集 10 个实际校时样本。
- JavaScript：78 通过。
- VAS 现有 CI 测试清单和新增校时测试：658 通过，另有 145 个 subtest 通过。
- 23 个原始日志、结果及输入/回复 WAV 文件的 SHA-256 与原报告一致。
- 浏览器实际切换原始/校准视图：8 个客户端输入、等待、回复片段的标签、位置、宽度全部不变。
- 旧报告第 3 轮可回听，但缺少可靠句协议关联，正式回复类别仍标为未知；没有用猜测补齐。

本地预览目录：`side-reports/client-clock-independent-20260919`，端口 `19342`。
旧报告重建与本地集成报告分开保存；本地集成结果不代表云端 DEV 性能。此次仅本地实现与验证，尚未推送或部署。

[架构](diagrams/architecture.md) · [数据流](diagrams/data-flow.md) · [时序](diagrams/sequence.md) · [模块](diagrams/modules.md) · [技术栈](diagrams/tech-stack.md) · [交付顺序](diagrams/roadmap.md)
