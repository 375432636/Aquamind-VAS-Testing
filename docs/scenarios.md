# 场景与回归测试

## 手动批量运行保存的 session

将用于 **VAS 批量语音测试** Action 的对话保存在 `scenarios/` 中。一个 YAML 或 JSON 文件对应一个独立 session，文件夹中按路径排序逐个运行；一个文件中的所有 turn 使用同一连接和上下文。

```yaml
# scenarios/custom/context.yaml
name: 产品上下文与打断
input_mode: manual
turn_timeout_seconds: 90
turns:
  - id: recommend
    text: 推荐两款适合会议录音转文字的耳机
    expect:
      max_first_playback_ms: 6000
  - id: interrupt-details
    text: 详细介绍第一款的各项功能
    interrupt_after_seconds: 2
    output_kind: answer
  - id: follow-up
    text: 它和第二款有什么区别？
```

`text` 会离线合成为中文 WAV。也可把某轮换成 `audio: fixtures/my-question.wav`；这里的 WAV 路径相对于**仓库根目录**，必须留在仓库内。每轮恰好填写 `text`、`audio` 之一。固定真人录音可以保存在 `scenarios/audio/`；文件夹递归加载只读取 YAML/JSON，不会把 WAV 当成场景。

文件可设置 `name`、`input_mode`、`turn_timeout_seconds`、`turns`；每轮可设置 `id`、`text` 或 `audio`、`interrupt_after_seconds`、`output_kind`、`expect`。未知字段、重复 turn ID 和非法阈值都会在连接 VAS 之前被拒绝。

```bash
export VAS_ENVIRONMENT=dev
export VAS_DEVICE_ID='你的测试设备 MAC ID'
export VAS_DIAGNOSTICS=frame
python -m voice_scenarios.batch_run \
  --scenarios scenarios/custom \
  --output artifacts/custom-001
```

`--scenarios scenarios/custom/context.yaml` 只运行一个文件；`--prepare-only` 只校验全部场景并准备音频。`input_mode` 和超时由各文件设置，省略时分别使用 `manual` 和 90 秒，批量 Action 不提供这两个参数。

临时粘贴对话 JSON 请使用独立的 **VAS 临时对话测试** Action，其中可设置 `turns_json`、`input_mode` 和 `turn_timeout_seconds`，示例见 [README](../README.md#在-github-actions-中运行)。两个 Action 以及 `batch_run`、`ci_run` 环境变量入口均默认使用 `frame` 诊断，记录环节计时和逐帧信息；可选择 `stage`，只记录环节计时。

每个 session 会重新连接 VAS，同设备顺序执行；某个 session 超时、断连或断言失败后，继续执行后面的文件。批量退出码：全部通过为 `0`，任一执行失败为 `1`，准备或参数错误为 `2`。文件之间没有共享的 WebSocket 历史；设备级长期 Memory 是否保留由 VAS 决定。

输出目录结构：

```text
index.html / report.html      批量入口
batch.json                   每个 session 的来源、结果和报告链接
junit.xml                    汇总所有 session 的测试结果
sessions/<场景路径标识>/       独立的报告、原始记录和音频
```

批量 Action 默认运行 `scenarios/smoke`，也可选择 `scenarios/vad`，或 `scenarios` 一次运行全部。两个测试 Action 均没有定时触发，由用户手动运行。

## 固定音频的多轮对话

下面是 `main.py run --scenario` 使用的底层 WAV 场景格式，与上面的 Actions 文本文件格式不同；音频路径相对于 YAML 所在目录。所有 turn 共用一个 session，上一轮完成或打断结束后才发送下一轮。

```yaml
name: 会议耳机上下文与打断
turn_timeout_seconds: 90
input:
  mode: manual
turns:
  - id: recommend
    audio: ../audio/recommend.wav
    expect:
      max_first_playback_ms: 6000
  - id: details
    audio: ../audio/details.wav
    interrupt:
      after_playback_seconds: 2
      output_kind: answer
    expect:
      max_interrupt_lateness_ms: 250
  - id: follow-up
    audio: ../audio/follow-up.wav
```

```bash
python main.py run \
  --url wss://lumin-vas-aquamind-dev.deep-edge.cn/looomyn/v1/ \
  --device-id '你的测试设备 MAC ID' \
  --scenario config/my-regression.yaml \
  --diagnostics frame \
  --output artifacts/regression-001
```

需要认证时，用不入库的本地连接文件传入 `token`，通过 `--config /absolute/path/private-connection.yaml` 使用。`ci_run` 使用环境变量 `VAS_TOKEN`。

```yaml
# private-connection.yaml；不要提交到 Git
url: wss://your-vas.example/looomyn/v1/
device_id: YOUR-TEST-DEVICE
token: YOUR-TOKEN
diagnostics: frame
```

`expect` 沿用原有机器格式，时间阈值单位为毫秒；报告展示为秒。除上例外，可断言 `asr_text`、`llm_requests`、`tools`、`memory_requests_min`、`pre_speech_outputs_min`、`filler_outputs_min`。完整示例在 [`config/regression.example.yaml`](../config/regression.example.yaml)；示例的 Fake 预期结果不适用于真实 DEV 的自由回答。

## VAD 场景

```yaml
input:
  mode: vad
  pre_roll_seconds: 0.3
  noise_dbfs: -55
  noise_seed: 0
```

VAD 模式按实时节奏持续发送音频，语音前后添加可复现的底噪，发送端不发送 `listen/stop`。服务端需要支持 VAD 端点判定以及相应诊断事件，报告才会显示 VAD 开始、结束和 ASR 后续阶段。底噪参数也会影响端点判定，比较不同运行时应保持一致。

## 回归结果

每次使用新的 `--output` 目录。保留整个目录，以免回听链接缺少音频：

```text
report.html / index.html   会话总览
turn-001.html ...          各轮独立页面
report.json / metrics.json 结构化评估结果
junit.xml                 自动化测试结果
result.json               客户端原始记录
vas-events.jsonl          VAS 诊断事件
*.wav / segments/         原始音频与逐段回听
manifest.json             输入摘要及运行信息
```

若超时、断连、断言失败或诊断收集不完整，进程返回非零退出码，已有记录仍用于生成失败报告。没有可靠包序号或完整 WAV 的片段会显示不可回听，不会猜测切片范围。

用相同场景和音频重复运行可比较抖动：

```bash
python main.py run \
  --config /absolute/path/private-connection.yaml \
  --scenario config/my-regression.yaml \
  --repeat 5 \
  --output artifacts/baseline
```

每次重复都建立新 session；该次重复内部的多轮对话保持上下文。重复结果额外提供 `summary.json` 的正式回答首音 P50 / P95。

## 本地 VAS + Fake 服务

这个仓库包含测试客户端和 Fake 服务，不包含 VAS 服务端。准备一个可访问的 VAS checkout，在 `main/xiaozhi-server/.venv` 安装该版本要求的服务端依赖，并准备其 Silero VAD 模型。具体安装要求以该 VAS 版本文档为准。

```bash
# 终端 1：启动真实 VAS + 本仓库的 Fake 外部服务
python main.py stack \
  --vas-root /absolute/path/Aquamind-VAS \
  --script config/fake-regression.yaml \
  --base-port 19080

# 终端 2：运行固定场景
python main.py run \
  --config config/local-fake.example.yaml \
  --scenario config/regression.example.yaml \
  --output artifacts/local-regression
```

Fake 服务通过 HTTP / WebSocket 模拟 UMS、Memory、ASR、LLM、TTS 和工具，不替换 VAS 内部业务方法。场景应成对使用：

| 外部服务脚本 | 客户端场景 | 覆盖内容 |
| --- | --- | --- |
| `fake-regression.yaml` | `regression.example.yaml` | 多轮、Memory、工具循环、打断、音乐 MCP |
| `fake-filler.yaml` | `filler.example.yaml` | 临时回复与正式回复 |
| `fake-vad.yaml` | `vad.example.yaml` | 连续音频、底噪、VAD 端点、多轮 |

真实 VAS 的集成回归需要上述 checkout，公开 CI 默认不拉取私有服务端：

```bash
VAS_TEST_ROOT=/absolute/path/Aquamind-VAS \
python -m pytest --cov=voice_scenarios --cov-fail-under=80
```

`stack` 的服务日志保存在 `artifacts/stack-<端口>/`，按 Ctrl-C 仅停止本次启动的子进程。
