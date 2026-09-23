# 5090 Zoomi RAG 验收

配套 VAS PR #110，部署代码 `60398695`。设备 `C2:B8:56:7B:95:7C`，常规 5090 VAS。

保存的场景位于 [`scenarios/continuation/zoomi-5090/`](../scenarios/continuation/zoomi-5090/)。四个文件各一个会话，共 14 轮，覆盖 PTT/VAD、门店与产品 RAG、上下文、两段/三段续说、修正最贵为最便宜。最终 14 轮通过，10 次 RAG 均成功；四份诊断完整、校时成功、Memory 会话回写成功，回复音轨保存为无损 FLAC。

使用方法见 [续说测试](speech-continuation.md)。这些是 5090 专用验收场景，不加入云端每日 smoke 默认目录；执行前必须按文档覆盖 VAS 地址。

## 修复的报告问题

- 识别摘要展示最后一个累计 STT 结果，不再停在最初的半句。
- 旧 VAS 的标准 `rag-lightrag_search` 即使缺少分类也能识别为知识库，保留原事件内容。
- 诊断收尾等待 25 秒，允许 VAS 20 秒的收尾窗口记录现有 Memory 15 秒超时。
- 多段 PTT 后普通单段 turn 的显式 server listen ID 同样用于音频注释映射，不改原始服务器编号或时间。
- 临时回复早于最后一段输入结束时，首音耗时保留负值。
- 用 `expect.speech_utterances: 1`、`speech_chunks_min` 检查实际续说合并，避免仅 RAG 成功而跨出 500 ms 窗口仍误通过。

首次 VAD 150 ms 发送间隔的样本实际在端点后 536 ms 才侦测到续说，服务器正确拆成两个 utterance。窗口内场景调整为端点后立即发送；必须以服务器检测事件判定窗口，而不是把客户端配置间隔当成实际 VAD 间隔。

## 时间与限制

最终端点 → ASR commit 为 0.500–0.510 秒，14 次完整发言各只有一次 ASR 连接。普通单段首音约 0.086–0.285 秒；正式首音距最后输入结束 1.728–9.925 秒。RAG 单次耗时 0.565–1.145 秒，不代表正式回复全部等待时间。

专名仍有“静安→金安／金岸”“奢音→摄影”的识别偏差；断言通过不代表全部回答事实正确。部分工具后完整文本回退回复缺少精确 LLM 请求归属，时间轴保留“归属未采集”，不猜测。

Testing Python：411 passed，客户端覆盖率 91.51%，6 个 opt-in 集成默认跳过；前端：80 passed。本次另行启用这 6 项真实 VAS + Fake 集成，全部通过（135.68 秒）。VAS 必需 CI：810 passed + 154 subtests，公共诊断模块覆盖率 90.01%。

本地原始证据位于 `side-reports/zoomi-rag-continuation-20260920/`。最终报告汇总为 `static/index.html`，Excel 为 `static/evaluation.xlsx`，逐轮验收为 `static/acceptance-audit.json`。完整输入、原始诊断和回听仅保存在本地测试产物，未提交到 Git。

[VAS 逐项验收与镜像](https://github.com/deepedge-ai-tech/Aquamind-VAS/blob/feature/funasr-streaming-5090-20260919/docs/zoomi-rag-acceptance-20260920.md)
