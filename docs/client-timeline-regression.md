# 客户端时间轴回归

验收原则：客户端发送、接收、浏览器输出使用同一客户端时间基准；不会为了和 VAS 对齐而移动客户端事件。首包、实际首音、估计时刻分别处理。

测试使用合成 PCM 和可控时钟，不访问 MAIN/DEV，不使用麦克风，不下载 TTS 模型。

## 自动化场景

| 场景 | 操作 | 必须满足 |
| --- | --- | --- |
| 三轮连续播放 | 同一播放器连续处理三轮，音频时钟分别落后客户端 0、4、13 秒 | 每轮记录当前实际输出时间，音频样本和轮次不串；每轮只结束一次 |
| 完整报告流程 | PTT 录音记录 → 保存 → 静态 HTML、Excel、整段 WAV | 连接开始为 0；第 1 轮首音等待 1.25 秒、过渡等待 1.50 秒；第 2 轮首音等待 2.50 秒 |
| 双端时钟不同 | 模拟 VAS 系统时间快 7 秒 | 客户端指标、等待和回听位置不随服务端移动；单轮页继续使用会话时间 |
| 等待中打断 | 第 3 轮松开后 2.25 秒打断，尚未收到回复 | 保留等待条，结束于打断时刻，不消失、不延伸到本轮清理完成 |
| 历史时钟错误 | 第 2 轮播放记录早于同包接收 | 只显示真实的 2 秒首包等待；首音未知，Excel 不填写虚假数值；原始回复仍可单独回听 |
| 打断尾部不确定 | 第 2 轮首帧已确认，尾帧只有估计且时间倒退 | 保留 2.50 秒首音等待；整段回听只放确认部分，原始完整音频保留 |
| 导出和重新生成 | 对同一份记录重新生成报告 | 原始事件和 WAV 哈希不变；总览、单轮和重建后的等待区间一致 |
| 播放详情提示 | 分别渲染有效、无效、估计和部分确认的记录 | 不可确认时不提供虚假的会话播放定位；部分确认说明尾部限制 |

对应测试：

- `tests/live_player.test.cjs`：浏览器播放器、暂停、时钟漂移、打断、极短尾帧。
- `tests/test_live_timeline_regression.py`：三种时钟情况的三轮记录到完整报告流程。
- `tests/test_browser_timing.py`：首包回退、无回复、输入实际发送和 WAV 排队的区分。
- `tests/reply_timing.test.cjs`：回复详情的时间可信度展示。
- `tests/test_input_control.py`、`tests/test_session_timing.py`：PTT 标记和回听边界。

## 本地运行

在仓库根目录、已安装项目测试依赖的 Python 环境中运行：

```bash
python -m pytest tests/test_live_timeline_regression.py tests/test_browser_timing.py tests/test_interactive_session.py -q
node --test tests/live_player.test.cjs tests/reply_timing.test.cjs tests/session_trace.test.cjs
```

需要同时保存三轮示例报告：

```bash
CI_TIMELINE_REPORT_DIR=artifacts/timeline-clock-example python -m pytest tests/test_live_timeline_regression.py -q
```

打开 `artifacts/timeline-clock-example/report.html`；同目录有逐轮 HTML、`evaluation.xlsx`、原始事件和 WAV。示例明确标注 MOCK，耗时用于验证测试工具，不代表 VAS 服务性能。

## CI

现有 CI 的 Node 和 pytest 步骤会自动发现这些测试，无需设备 MAC 或服务认证。`python-test-results` artifact 增加 `timeline-clock-example/`，可以下载检查静态报告和 Excel。本地通过不代表 GitHub 已执行，需要提交后由 push/PR 触发。

自动测试模拟 Web Audio 输出时钟，不能替代扬声器声学实测。浏览器人工验收可补充：同一会话连续 PTT 三轮，中途打断一次；查看开始/结束标记、首句等待和原始回听，再在暂停页面音频后恢复，确认后续播放时间没有倒退。
