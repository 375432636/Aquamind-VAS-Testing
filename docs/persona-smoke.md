# 人设冒烟与 Excel

`scenarios/smoke/personas/` 保存本次表格的 49 个问题，每个文件是一个独立 session；同一文件依次完成全部提问，使用同一连接保留上下文。五个文件均配置 Main、语音输入和 VAD 模式。

| 文件 | 形象 | 知识库数量 | 问题数 | MAC | Main 设备组 |
| --- | --- | ---: | ---: | --- | --- |
| `01-zoomi.yaml` | Zoomi（粉） | 4 | 11 | `00:00:00:00:00:21` | 已加入 Zoomi设备组（ID 265，全功能型） |
| `02-looomyn.yaml` | Looomyn | 1 | 9 | `00:00:00:00:00:22` | 已加入 Looomyn设备组（ID 269，全功能型） |
| `03-mai-mei-yi.yaml` | 麦美仪 | 6 | 11 | `00:00:00:00:00:23` | 已加入 麦美仪设备组（ID 257，互动感应型） |
| `04-doraemon.yaml` | 多啦A梦 | 3 | 13 | `00:00:00:00:00:24` | 待选择同名设备组，尚未添加 |
| `05-einstein.yaml` | 爱因斯坦 | 1 | 5 | `00:00:00:00:00:25` | 待选择同名设备组，尚未添加 |

`…:20` 已被 Main 占用，因此从 `…:21` 开始分配；没有移动原有设备，`…:26` 未使用。知识库数量、测试重点来自用户提供的表格，不是从服务器实时统计。正式连接前应完成后两组设备绑定，并核实占星工具名。

## 只跑 mock

```bash
python -m voice_scenarios.batch_run \
  --scenarios scenarios/smoke/personas \
  --mock --output artifacts/persona-mock
```

不实例化 VAS 连接，不调用远端 ASR、LLM、TTS 或 MCP。输入和模拟回复使用本地 eSpeak NG 合成；时间由固定模拟时钟生成，不需要按真实会话时长等待。录制样例在 `voice_scenarios/mock_run.py`，检查规则在 `voice_scenarios/evaluation.py`，两者独立，错误工具不会因为与预期共用同一份配置而自动通过。

Mock 为每个人设独立记录 session ID，逐轮保留 `mock_history`，并产生 ASR、Memory、LLM、工具、TTS、播放和 VAD 结束事件；图文／视频问题附带明确的示例媒体链接。它验证报告、工具断言、计时和 Excel 导出，**不验证真实模型的上下文理解、VAD 算法或服务性能**。当前 mock 不支持传感器或定时打断，用在这类场景时会明确报错。

已知工具使用历史采集到的准确名称。占星 MCP 的 Main 工具名尚未核实，保留空映射；该行显示“待核实”，整批返回退出码 1，仍生成全部 49 行报告。不要为了绿灯填写猜测名称。核实后仅需更新 `evaluation.py` 中的 `TOOL_NAMES`，再修改独立 mock 样例的该工具名称。

这次新增的 CI 回归无需访问 Main / DEV，验证全部 49 个问题、独立 session、错误工具、工具报错、诊断缺失、时间口径和 Excel；CI 的 `python-test-results` artifact 包含 `persona-example/run/` 下的模拟报告。真实 Actions 批量测试仍按场景文件中的 Main / DEV 执行，**`--mock` 不会改变定时任务的执行方式**。

## 配置与检查

```yaml
name: Zoomi（粉）· 产品出图
device_id: "00:00:00:00:00:21"
environment: main
input_mode: vad
evaluation:
  persona: Zoomi（粉）
  knowledge_base_count: 4
  focus: 产品出图情况
turns:
  - {tool: 人设, text: 你是谁}
  - {tool: 知识库-图文, text: 上海有多少家门店}
```

`evaluation` 只为原表提供三项说明；每个 turn 只多一个 `tool` 分类，不必填写嵌套 `expect`。分类支持人设、Prompt、LLM、知识库-文字／图文／视频、MCP-占星／黄历／抽签／天气／新闻／音乐，以及 Skill。

人设／Prompt／LLM 不检查工具。其他分类要求至少一次预期工具正常结束；实际未调用、调错或全部执行失败显示“否”；缺少诊断或只有开始没有结束显示“未采集”；未验证配置显示“待核实”。检查不验证工具参数、返回内容正确性、知识库命中质量或链接可播放性。`Skill` 目前检查 `invoke_skill` 正常完成，不区分具体技能名。

## Excel

批量 `index.html` 可下载合并的 `evaluation.xlsx`；单 session `report.html` 可下载该人设的 Excel。Actions 每个会话的下载包也包含此文件，压缩音频不影响 Excel。

主表只有原五列和新增三列：

| 形象 | 知识库数量 | 测试重点 | 调用工具 | 问题 | 是否调用了正确的工具 | 首次回复时间（秒） | 临时回复结束到正式回复开始（秒） |
| --- | --- | --- | --- | --- | --- | --- | --- |

- 首次回复时间：客户端输入结束 → 第一段非欢迎语回复开始播放。VAD 输入结束取 WAV 发送结束，之后仍持续发送底噪，不等同于人工标记的最后一个发声采样。
- 正式回复等待：最后一段临时回复／工具过渡语播放结束 → 首段正式回复开始播放；直接正式回答显示“不适用”，临时回复未完整播放或缺少正式回复显示“未采集”。
- 使用客户端记录的播放时间，不用 TTS 请求时间代替；数值为可排序、筛选的 Excel 数字，显示三位小数。
- 第二张表“会话信息”列出环境、MAC、数据来源和结果。MOCK 标记出现在每份报告与 Excel 中。
- 若中途失败，尚未执行的问题仍在 Excel 中显示“未执行”；不会丢掉后续待测题目。

导出使用随包发布的 XLSX 模板和 Python 标准库；Linux / macOS 均可运行，不需要 Office、额外电子表格依赖或修改 VAS。
