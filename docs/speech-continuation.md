# 测试 500 ms 内续说

用例保存在 `scenarios/continuation/`，未加入每天的云端冒烟目录。
每个文件一个 session，包含普通输入、两段续说、三段续说。
只连接已启用 `speech_turn` 的 VAS；旧服务不会产生续说端点，VAD 用例会明确超时失败。

```yaml
turns:
  - chunks:
      - text: 请给我介绍一下
      - text: 红茶和绿茶有什么区别
        resume_after_endpoint_ms: 150
```

一个 `chunks` 数组算同一客户端 turn，不等待前一段回复再发送。
每段支持 `text` 或单声道 16 kHz PCM16 `audio`；2–8 段，首段不设置间隔，后续间隔 0–5000 ms。
普通 `text` / `audio` / `sensor` 用法不变，不能和 `chunks` 同时设置。

PTT 每段发独立 start / stop。VAD 只发一次 auto start，语音结束后继续上传底噪；
接到服务端 `speech_chunk_ended`，按校准后的端点时间安排下一段。
没有有效校时则使用端点到达时间，并记录 `chunk_gap_uncalibrated`。
音频每 60 ms 发送一帧，因此目标间隔与实际 VAD 检测间隔可能不同；最终以服务端事件为准。

报告保留完整客户端音轨。一个客户端 turn 关联全部 PTT listen ID；
“续说窗口与回复版本”轨道可以展开查看累计文本、撤销原因、ASR commit 与正式回复放行时间。
LLM 轨道的 G 编号区分同一 turn 内重启的回复版本。

使用已有批量入口，选择 `scenarios/continuation`。在本地指定实例时覆盖对应环境 URL，
例如经 SSH 隧道连接 5090 的常规 VAS，使用 `VAS_DEV_URL=ws://127.0.0.1:19450/looomyn/v1/`；
不要将本地地址提交为仓库默认 DEV。

运行测试：`python -m pytest tests/test_speech_chunks.py -q`。
VAS 侧边界、取消与实际工具保护由对应仓库的回归负责，报告不能凭模型措辞代替事件验收。

## Zoomi RAG 实测用例

`scenarios/continuation/zoomi-5090/` 有 4 个独立 session、14 轮，设备为 `C2:B8:56:7B:95:7C`。
覆盖 PTT/VAD 门店与产品检索、两段/三段续说、改口以及复用前文资料的追问。
新资料查询检查 `rag-lightrag_search` 正常完成；纯上下文追问用 `tool: LLM`，不强制重复检索。
知识库数量沿用原 Zoomi 用例的 4，是用例元数据，不是运行时自动发现值。

先建立到常规 VAS 的 SSH 隧道，再手动批量运行：

```bash
VAS_DEV_URL=ws://127.0.0.1:19450/looomyn/v1/ \
  python -m voice_scenarios.batch_run \
  --scenarios scenarios/continuation/zoomi-5090 --output artifacts/zoomi-5090
```

这些场景未加入云端定时冒烟；`environment: dev` 使用上述 URL 覆盖后才指向 5090。
报告摘要采用最后收到的累计识别文本，原始修订事件全部保留。
旧记录中 RAG 分类为 `other` 时，按已经核实的精确工具名补齐知识库次数，不修改原始日志。
诊断客户端会在结束时最多等待 25 秒，以接收 Memory 写回的成功/失败尾部；该等待不计入回复耗时。

续说例子还检查 `expect: {speech_utterances: 1, speech_chunks_min: 2}`（三段用 3）。
发送间隔不等于 VAD 检测间隔：WAV 起始静音、帧粒度和 VAD 检测都会增加延迟。
窗口内 VAD 用例使用 `resume_after_endpoint_ms: 0`，但仍由上述断言检查实际是否合并，不能仅凭成功回复通过。
首音等待允许负数，表示临时回复在最后一段输入结束前已播放；不改动任何客户端时间戳。
多段 PTT 后的普通 turn 同样使用显式 server/client 轮次映射关联 TTS，原始服务端 listen ID 保留。
