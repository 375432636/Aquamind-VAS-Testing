# 知识库返回内容

在会话总览或单轮时间轴点击 `rag-lightrag_search` 时间条展开，再打开
“知识库 · 查看查询与返回内容”。每次工具调用独立显示查询词与工具返回正文，
同一轮多次调用不会互相覆盖。返回正文中的引用保持原文显示，不额外推断来源或 LLM 是否采用。

需要 VAS 公共工具入口发送以下诊断事件，无需修改各个 Provider：

- `knowledge_lookup_result`：调用结果状态。
- `knowledge_context_part`：`content_kind=query|returned`、`part_index`、`text`。
- `knowledge_context_complete`：`content_kind`、`part_count`、`available`、
  `total_bytes`、`captured_bytes`、`truncated`。

使用事件原有 `clock_id + span_id` 关联到工具请求，轮次沿用原有上下文。
仅已开启的诊断会话采集知识库工具，关闭诊断仍直接执行原工具。
查询最多 8 KiB，正文最多 32 KiB，每片最多 768 UTF-8 字节。
采集失败不能改变工具返回或异常，输出内容只进入已有诊断流，不进入普通日志。

“已返回”表示工具有正文，并不判定检索命中；“空返回”也不等同知识库没有相关资料。
旧记录没有正文时显示“结果未采集”。分片丢失、冲突、截断会明确提示，不能用新查询
补写旧记录。需更新 VAS 并重新录制，才能看到新采集的真实内容。
