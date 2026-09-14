# 场景与回归测试

## 手动批量运行保存的 session

将用于 **VAS 批量语音测试** Action 的对话保存在 `scenarios/` 中。一个 YAML 或 JSON 文件对应一个独立 session，文件夹中的会话逐个运行；一个文件中的所有 turn 使用同一连接和上下文。

```yaml
# scenarios/custom/context.yaml
name: 产品上下文与打断
device_id: "FF:FF:FF:FF:FF:11"
environment: dev
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

文件必须设置 `device_id`（冒号分隔的 MAC）、`environment`（`dev` 或 `main`）和 `turns`；可选 `name`、`input_mode`、`turn_timeout_seconds`。省略名称使用文件名，省略输入模式和超时使用 `manual` / 90 秒。这些用例配置不会被本机默认环境或设备覆盖；每轮可设置 `id`、`text` 或 `audio`、`interrupt_after_seconds`、`output_kind`、`expect`。未知字段、重复 turn ID 和非法阈值都会在连接 VAS 之前被拒绝。

```bash
export VAS_DIAGNOSTICS=frame
python -m voice_scenarios.batch_run \
  --scenarios scenarios/custom \
  --output artifacts/custom-001
```

`--scenarios scenarios/custom/context.yaml` 只运行一个文件；`--prepare-only` 只校验全部场景并准备音频。`input_mode` 和超时由各文件设置，省略时分别使用 `manual` 和 90 秒，批量 Action 不提供这两个参数。

临时粘贴对话 JSON 请使用独立的 **VAS 临时对话测试** Action，其中可设置 `turns_json`、`input_mode` 和 `turn_timeout_seconds`，示例见 [README](../README.md#在-github-actions-中运行)。批量 Action 只有场景路径一个输入，诊断固定 `frame`。临时 Action 的环境和 MAC 必填，默认 `frame`，可选择 `stage`；本地 `batch_run` / `ci_run` 仍可通过 `VAS_DIAGNOSTICS` 调整诊断粒度。

每个 session 会重新连接 VAS，逐个执行；某个 session 超时、断连或断言失败后，继续执行后面的文件。批量退出码：全部通过为 `0`，任一执行失败为 `1`，准备或参数错误为 `2`。文件之间没有共享的 WebSocket 历史；设备级长期 Memory 是否保留由 VAS 决定。

输出目录结构：

```text
index.html / report.html      批量入口
batch.json                   每个 session 的来源、结果和报告链接
junit.xml                    汇总所有 session 的测试结果
sessions/<场景路径标识>/       独立的报告、原始记录和音频
```

批量 Action 默认运行 `scenarios/smoke`，也可选择 `scenarios/vad`、`scenarios/products`，或 `scenarios` 一次运行全部。每个 session 单独上传 FLAC 压缩报告，两种手动 Action 共用执行队列。两个测试 Action 均没有定时触发，由用户手动运行。

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

## 可选业务断言

`expect.business` 对 Actions 文本场景和底层 WAV 场景通用，省略后沿用旧行为。下面的工具名必须替换为**该场景设备当前配置**中的真实名称，不能根据另一人设或一次错误调用猜测：

```yaml
expect:
  llm_requests_min: 1
  tts_requests_min: 1
  business:
    recognition:
      contains_all: [[新闻, 要闻, 资讯]]
    tools:
      required:
        - name: 实际新闻工具名
          # 可选：只验证必要参数；键名与值须来自该工具契约
          arguments: {topic: today}
      forbidden: [DrawLots-drawLot]
    reply:
      contains_all: [[新闻, 要闻, 资讯]]
      output_kind: answer
    audio:
      ending: normal
      min_duration_ms: 1
```

`contains_all` 外层各组都要命中，组内任一同义表达即可；匹配前统一 Unicode 宽度、大小写和空白，不做全文精确匹配，也不把这种规则检查当成事实核验。复杂回复可列多个必要信息组，例如黄历的 `[[宜, 适宜], [忌, 不宜]]`。新闻时效、事实正确性仍需额外核验。

`tools.required` 要求观察到指定工具开始及成功结束；`arguments` 仅检查列出的标量关键参数。`tools.forbidden` 检查不应调用的工具。两者都为空时明确显示不适用，不宣称已验证工具行为。缺少参数采集、执行结束或完整诊断时显示“未知”，不会把没有记录当成没有调用；已观察到的禁止工具调用即使诊断不完整也会功能失败。VAS 默认不记录工具参数，需要在服务端公共诊断配置的 `audio_diagnostics.tool_argument_allowlist` 显式允许对应工具的必要键，例如 `{Tung-Shing-get-tung-shing: [days, includeHours]}`。不要加入 Prompt、认证信息、用户私密资料或完整自由文本参数。

`reply` 默认只检查通过音频包顺序归属的正式回答，开场白与临时播报不能满足必要信息断言。未采集到正式回复归属时显示未知；明确要检查所有本轮播报时可设置 `output_kind: any`。`audio` 校验接收文件可解码、PCM 与记录的帧一致、本轮非空及最小时长，排除开场白、其他轮次和打断后丢弃的帧。`ending: normal` 要求服务端结束和本地播放排空；预期打断使用 `ending: interrupted`，并要求已请求打断、本地停播和服务端停止确认。文件缺失属于采集证据缺失，音频损坏或错误结束属于功能失败。

传感器输入没有 ASR。摸头轮次可写 `recognition: {sensor: touch-head}`；检查实际 `sensor_sent` 和可用的服务端 `detected_action.sensor_name` 回显。匹配时识别项显示“不适用（传感器指令已匹配）”，命令不符仍失败，发送证据缺失显示未知；实际人设的互动响应由 `reply` 检查。不要用服务端回显的互动 Prompt 冒充识别文本。

尚未核实的设备要求显式写成：

```yaml
tools:
  verified: false
  reason: 当前设备的新闻工具契约尚未核实
```

每项可附 `source` 记录无敏感信息的配置证据。`verified: false` 返回 `configuration_unverified`，不会通过。确认配置后填入真实要求并移除该标记；更换设备或人设时必须同步更新断言。

`report.json` 的每个 check 保留兼容的 `name / expected / actual / passed`，业务项另有 `category`（识别、工具、回复、音频）、`status`、`failure_kind` 和 `reason`。功能错误是 `functional`，证据缺失是 `diagnostic_missing`，设备契约未确认是 `configuration_unverified`；延迟阈值单独归类。非通过结果使报告失败，但原因不会混成一个“链路失败”。识别和音频断言在诊断关闭时仍能利用客户端证据；无法证明的内部工具及回答归属保持未知。

反例回归 `tests/test_business_assertions.py::test_news_misrecognized_as_heart_wrong_tool_cannot_pass_successful_llm_tts` 固定“新闻 → 心 → 抽签工具”：即使 LLM/TTS 请求成功且收到有效音频，识别、工具和回复断言仍失败。

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
| `fake-milestones.yaml` | `milestones.example.yaml` | 两轮上下文、护栏向量、空 LLM 首块、文本分片、天气工具、TTS 空音频块、连接复用 |

公共时序节点开发可把上面两条命令中的脚本换成 `config/fake-milestones.yaml`、场景换成 `config/milestones.example.yaml`，并使用支持新节点的 VAS。Fake `/v1/embeddings` 支持固定向量及延迟/错误；LLM 支持 `llm_metadata_first`、每轮 `text_chunks`；TTS 支持 `headers_delay_seconds`、`empty_audio_chunks`、`force_close`。这些配置只影响本地 Fake 服务。新增真实 VAS 回归为 `tests/test_local_stack.py::test_real_stack_public_milestones_and_guardrail`。

真实 VAS 的集成回归需要上述 checkout，公开 CI 默认不拉取私有服务端：

```bash
VAS_TEST_ROOT=/absolute/path/Aquamind-VAS \
python -m pytest --cov=voice_scenarios --cov-fail-under=80
```

`stack` 的服务日志保存在 `artifacts/stack-<端口>/`，按 Ctrl-C 仅停止本次启动的子进程。

### 图片与视频冒烟

需要验证返回媒体时，每轮只需增加 `expect: {image_items_min: 1}` 或 `expect: {video_items_min: 1}`。统计本轮客户端收到的 `image` 及 `display.items`，按媒体项计数，不按 URL 去重；一条 `display` 可以包含多个项。这两项不依赖 VAS 内部诊断。没有收到时检查失败，单有文字回复不能通过。计数不验证链接有效性、下载完成或真实视频播放。
