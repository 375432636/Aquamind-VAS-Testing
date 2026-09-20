# 5090 Zoomi 最终 ASR 准入验收

测试程序 `zoomi-final-chat.py` 使用设备 `C2:B8:56:7B:95:7C`，连接本地
`ws://127.0.0.1:19553/looomyn/v1/` SSH 转发入口，目标是 5090 常规 VAS。
程序显式选择该地址，不使用场景 YAML 的云端 `environment` 字段。

场景位于 `scenarios/continuation/zoomi-5090/05-final-chat-ptt.yaml` 和
`06-final-chat-vad.yaml`。每种模式共十轮，使用同一批 Piper WAV，保留同一
WebSocket 会话和对话上下文。包含门店、耳机、会员、上下文追问、正式回复
播放两秒后打断，以及三段续说。

在准备好指向常规 VAS 的 SSH 转发后运行：

```bash
PYTHONPATH=. python experiments/zoomi-final-chat.py --root artifacts/zoomi-final-chat --prepare
PYTHONPATH=. python experiments/zoomi-final-chat.py --root artifacts/zoomi-final-chat --mode manual
PYTHONPATH=. python experiments/zoomi-final-chat.py --root artifacts/zoomi-final-chat --mode vad
```

macOS 的 Opus 运行库可通过 `DYLD_LIBRARY_PATH=/opt/homebrew/lib` 指定。
每次重跑使用新的 `--attempt`，程序拒绝覆盖已有结果。
输出包含原始客户端/VAS 事件、音频、静态 HTML 和 `evaluation.xlsx`。

2026-09-20 验收：两种模式各十轮均收到音频，诊断完整，完整 Chat 各十次，
最终 ASR 之前的业务 LLM 请求为零。两种模式各有五轮未满足知识库调用
断言，不能只依据命令退出码或收到音频判为业务通过，应读取 `report.json`。

多段 VAD 用例会等待诊断端点来调度下一段，诊断消息可能迟到；设置的
`resume_after_endpoint_ms` 不是最终的人声检测间隔。边界验收需查看服务端
实际端点与续说事件。本次另用预先拼好的连续音轨复核：第一句后增加
0.180 秒静音，其余音频采样保持原样，测得两次续说间隔为 0.170 / 0.421 秒，
三段成功合并，完整识别全文并调用 RAG。所有失败补测也保留在报告中。

本地验收记录：
`/Users/john/Documents/project/python/Aquamind/side-reports/zoomi-final-chat-20260920/`。
部署记录为 `deploy-receipt.json`，入口报告为 `static/index.html`。

## 十二轮速度组合复测

`experiments/scenarios/zoomi-fast-preset.yaml` 保存后续十二问：人设、门店、
防水/价格/录音耳机、会员、视频、上下文总结，第八轮正式回复播放两秒后打断。
它是 5090 专用实验输入，不会加入云端每日冒烟。两种模式各使用一个独立
WebSocket session，每个 session 内保留十二轮上下文。

```bash
PYTHONPATH=. python experiments/zoomi-final-chat.py \
  --root artifacts/zoomi-fast-preset \
  --prepare --scenario experiments/scenarios/zoomi-fast-preset.yaml
PYTHONPATH=. python experiments/zoomi-final-chat.py \
  --root artifacts/zoomi-fast-preset --mode manual --attempt first
PYTHONPATH=. python experiments/zoomi-final-chat.py \
  --root artifacts/zoomi-fast-preset --mode vad --attempt first
```

先按原有部署方式建立 `127.0.0.1:19553` 到 5090 常规 VAS 的转发。
更换问题文件时必须使用新的 `--root`，避免复用前一组问题的缓存 WAV；
同一组问题复测只更换 `--attempt`，保留相同音频以便比较。
这不是通用环境选择入口，地址和设备仍明确固定为上文的 5090 / Zoomi。

长会话需要服务端 `audio_diagnostics.max_capture_seconds: 1200`；
默认 300 秒可能在测试结束前停止采集。该设置仅调整诊断上限，不修复
WebSocket 心跳，也不改变 VAD/续说阈值。运行前核实 5090 有效配置
`VAS_ASR_SPECULATIVE_CHAT=0`：脚本中的同名元数据是实验预设，不能作为
服务端开关已生效的证据，应同时保存部署回执和检查逐轮事件顺序。

2026-09-20 本轮结果：PTT/VAD 各十二轮均收到音频，完整 Chat 各十二次，
最终 ASR 之前没有业务 LLM 请求；原始诊断序号连续、采集完整。
自动工具/音频断言分别通过 9/12、7/12。PTT 第七、九轮虽然调用 RAG，
却只播报查询过渡语，没有最终答案；部分“奢音”被识别为“摄影/摄音”，
部分正式回复类型缺失或误标。工具调用通过不能代替回答质量验收。
两种模式第八轮都按计划打断，不能把其未完成 RAG 判为打断机制失效。

本地原始记录与静态/Excel 报告：
`side-reports/zoomi-fastest-rerun-20260920/{verified,static}/`。
首轮诊断到期和一次心跳超时的失败记录保留在 `run/`；后续通过不证明
心跳问题已修复。服务端功能、A/B 对比及采用决策见
[VAS PR #110](https://github.com/deepedge-ai-tech/Aquamind-VAS/pull/110)。
