# Memory / LLM 并行诊断报告

Memory 查询与 LLM 投机生成由 VAS 实现，Testing 沿用 WebSocket 诊断消费明确的选择证据。展开 Memory 行，再展开“查看返回内容与采用内容”，可对比格式化返回与本轮实际补充文本；缺失和截断不会显示成未命中。

LLM 实际请求分别保留，标注采用、弃用和补充 Memory 重发。弃用候选不参与有效回复关键点；客户端音频、等待与播放保持独立单调时钟，可选校准仅调整 VAS 坐标。没有用服务端时序删掉客户端收到的音频。

## 回归入口

```bash
python -m pytest --cov-config=.coveragerc-client --cov=voice_scenarios --cov-fail-under=80
node --test tests/*.test.cjs
VAS_TEST_ROOT=/absolute/Aquamind-VAS python -m pytest tests/test_memory_pipeline_stack.py tests/test_local_stack.py
```

最终 Python 395 通过、覆盖率 91.86%；默认跳过的 6 个真实 VAS + Fake 外部服务集成测试已单独启用并通过。前端 80 通过。新增覆盖 Memory 分片、缺失/截断、候选关联、弃用首包过滤、Memory 展开按钮不被时间轴拖动抢占。

集成包含 PTT/VAD 同会话三轮：未命中、命中后弃用潜在音乐工具、补充记忆后工具递归。候选没有放行前不能执行工具。定时打断采用与 Console 一致的 `wake_word_detected` 指令；Fake UMS 开启智能打断，避免只在智能打断关闭时测试。

## 5090 验收

VAS 合并提交 `a40866f0`，`VAS_LLM_MEMORY_PIPELINE=1`；设备 `30:ED:A0:A6:23:A4`。PTT/VAD 各四轮，其中第三轮正式回复开始一秒后打断，第四轮保持同一会话继续。最终两会话全部通过、诊断完整、校时成功；原始音频及 HTML/Excel 保留。

实际服务这八轮均命中 Memory：每轮两次 qwen-flash 请求，第一次弃用，第二次补记忆采用；LLM 与 Memory 重叠，正式 TTS 在放行之后开始。未命中及护栏阻拦由确定性集成覆盖，不声称真实服务也覆盖了这些分支。

由于 Mac 的 Tailscale 中转不稳定，最终验收在 5090 本机用缓存 Testing 镜像加载本分支 Python 代码运行。首遍 PTT 的会话末尾 Memory 写回超过 VAS 既有 10 秒收尾期限，报告如实失败；保留原报告，重跑后通过。没有修改超时去掩盖丢失事件。

具体版本、逐轮数值及限制见 VAS 的 `docs/memory-pipeline-acceptance-20260920.md`。原始证据在 5090 `aquamind-deploy/build/memory-pipeline-20260920/testing-acceptance/evidence/`，通过目录为 `manual-confirmed` 与 `vad-local`。Mac 静态输出在 `side-reports/memory-pipeline-5090-20260920/`。

## Independent Memory / guardrail decisions

Reports retain each candidate A/B/C and its actual span. They now distinguish
`llm_candidate_cancel_requested` (logical invalidation) from
`llm_candidate_transport_finished` (network worker cleanup). Transport completion
cannot overwrite a discarded candidate's decision or hide its end time.
Guardrail reply generations are labelled `护栏回复重发`; warmup is labelled
`LLM 连接预热（不生成回复）` and does not count as a chat generation. Existing
reports with no new events remain readable without invented timing data.


## 2026-09-20 独立决策与预热增量验收

Testing 功能提交 `2835405`、分词器预热标签 `a72d47b`。VAS 代码 `6ffb84c9` 已部署常规 5090，PTT/VAD 各 4 轮（第三轮打断、第四轮同会话继续）全部通过、诊断完整、校时成功。Memory 返回后 0.001–0.011 秒启动 B，比护栏完成早 0.136–0.338 秒；16 次实际 HTTP 请求均复用连接，没有新 TCP/TLS。实际均为 Memory 命中与护栏放行，BLOCK/C 改写由确定性回归验证。

最终 Python **396 passed、6 skipped，客户端覆盖率 91.83%**；需显式设置 `VAS_TEST_ROOT` 的 6 项真实 VAS + Fake 服务集成已单独全部通过；前端 **80 passed**。新测试确认请求取消和传输结束不能互相覆盖，也不能改写客户端音频时间。

最终报告：5090 `build/memory-independent-warmup-20260920-v2/evidence/`；Mac `side-reports/memory-independent-warmup-20260920/static/{manual,vad}/`。各有静态 HTML、Excel、分句文本、无损压缩回听和完整原始事件，原始 WAV 在旁边的 `final/` 留存。首次分词器初始化导致的 0.254 秒延迟也保留在 `first-acceptance/`，最终预热后首轮 Memory → B 为 0.007 秒。

详细复验脚本、逐轮断言、镜像校验与范围见 VAS 的 `docs/memory-independent-warmup-acceptance-20260920.md`。没有合并或部署云端 DEV。
