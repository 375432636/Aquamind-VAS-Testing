# Aquamind VAS Testing

向已经部署的 VAS 发送多轮语音，记录客户端与服务端时序、临时回复、正式回复和打断状态。一个场景文件对应一个 session，文件中的多轮对话共享上下文。报告支持完整 session 回放、等待区间和拖动测量。

## 在 GitHub Actions 中运行

1. 打开 **Actions → VAS 批量语音测试 → Run workflow**。
2. 在用例文件中填写必填的 `device_id`（MAC）和 `environment`（`dev` 或 `main`）。
3. 在 `scenario_path` 填写文件夹（如 `scenarios/smoke`）或一个 YAML/JSON 文件路径。
4. 点击 **Run workflow**。各 session 逐个运行；一个 session 失败后仍继续后面的场景。
5. 在 **Summary** 选择需要的 session，点击该会话的下载链接。每个 artifact 只包含一个 session，解压后直接打开 `report.html`。

**每天北京时间 07:00 自动运行全部冒烟测试。** [`VAS 批量语音测试`](.github/workflows/voice-test.yml) 使用默认分支 `main` 的最新已合并版本，读取 [`scenarios/smoke/`](scenarios/smoke/) 下的全部场景。当前是 3 个 session、10 轮，沿用各文件的 DEV 环境和 MAC 配置，诊断级别为 `frame`。新增到该目录的用例会自动纳入每日测试。

定时和手动测试共用 Docker 缓存、串行执行和报告流程；某个 session 失败后继续后续场景。结果在 **Actions → VAS 批量语音测试 → 对应运行 → Summary** 查看，各 session 的报告与无损压缩音频独立下载，保留 14 天。GitHub 按 UTC 调度（`0 23 * * *`），实际启动可能因队列繁忙延迟。

批量测试和临时对话 Action 仍可手动运行。批量测试只需维护 [`scenarios/`](scenarios/) 中的文件，运行时无需重复粘贴对话。

三个 Action（含 CI）共用 Docker 测试环境。首次构建安装系统与 Python 依赖，之后通过 GitHub BuildKit 缓存复用镜像层；修改场景或业务代码不会重新安装依赖，修改 `pyproject.toml` 或基础环境时才重建对应层。缓存受 GitHub 分支可见性和回收规则限制，缓存失效时会正常重建。设备认证只在运行容器时传入，报告和生成音频不进入构建缓存。

| 场景目录 | 内容 |
| --- | --- |
| [`scenarios/smoke`](scenarios/smoke/) | 多轮上下文、正式回答播放后 2 秒打断、打断后继续对话、新闻黄历、肢体互动描述与产品介绍 |
| [`scenarios/vad`](scenarios/vad/) | 连续发送语音和底噪，由后台 VAD 判断结束 |
| [`scenarios/products`](scenarios/products/) | 会议耳机上下文、正式回答后 2 秒打断 |
| [`scenarios/sensors`](scenarios/sensors/) | 摸头、摸手、摇晃、抛起四种标准传感器指令 |

新闻、黄历、摸头和产品介绍保存在 [`03-news-almanac-interaction-products.yaml`](scenarios/smoke/03-news-almanac-interaction-products.yaml)，使用 DEV 设备 `30:ED:A0:A6:23:A4`，四轮共享一个 session。语音采用 VAD 模式，说完后继续发送底噪；摸头直接发送标准传感器消息。每轮分别检查识别意图、工具、回复必要信息及可解码非空音频，保留 LLM/TTS 次数检查。新闻要求调用 `News-getTodayNewsByTopic`，黄历要求 `Tung-Shing-get-tung-shing`；新闻误识别为“心”后调用抽签工具会失败。

工具与产品能力来自 2026-09-11 该设备的 UMS 配置及 MCP 工具清单。运行时需核对 DEV 的实际配置来源，换设备或人设时同步修改预期。摸头检查实际传感器发送及服务端回显；当前配置未明确固定回复规则，默认要求回应摸头或“大笨蛋”的调侃，禁止误触发抽签，具体人设规则确认后可收紧。产品介绍检查占星、星盘及黄历/抽签/MBTI 能力，不强制其他人设的产品答案或工具流程。新闻事实时效与黄历事实正确性仍需进一步核验。配置方法见[可选业务断言](docs/scenarios.md#可选业务断言)。

传感器步骤使用 `sensor` 字段，与 `text`、`audio` 三选一。四种标准值为 `touch-head`（摸头）、`touch-hand`（摸手）、`shake-body`（摇晃身体）、`throw-it-up`（抛起／跌落）。例如：

```yaml
turns:
  - text: 你好
  - sensor: touch-head
  - text: 刚才我碰了哪里？
```

临时 Action 同样接受 `[{"text":"你好"},{"sensor":"touch-head"}]`。传感器通过当前连接发送 `{"type":"sensor","mode":"touch-head","state":"stop"}`，不合成或上传语音，也不发送 `listen/start` 或 `listen/stop`。回复仍参与整段 session 回听，首音等待从指令发出开始计时。服务端通过 STT 消息回显的互动描述显示为“服务端事件回显”，不计作 ASR 识别结果。

单独触发一次摸头：

```bash
python main.py run --sensor touch-head \
  --url wss://lumin-vas-aquamind-dev.deep-edge.cn/looomyn/v1/ \
  --device-id 30:ED:A0:A6:23:A4 --diagnostics frame \
  --output artifacts/sensor-head
```

复用现有 VAS 协议，无需修改 VAS。设备侧必须启用对应互动回复；未产生回复会按超时失败处理。混合会话利用同一 WebSocket 上的 TTS 控制顺序关联内部记录，保留原始服务端收音轮次；若控制记录不完整或无法匹配，报告明确失败，不按时间接近程度猜测。旧报告仍可读取。

新增一个 session，例如 `scenarios/custom/my-session.yaml`：

```yaml
name: 会议耳机上下文与打断
device_id: "FF:FF:FF:FF:FF:11"
environment: dev
turns:
  - text: 推荐两款适合会议录音转文字的耳机
  - text: 详细介绍第一款
    interrupt_after_seconds: 2
    output_kind: answer
  - text: 它和第二款有什么区别？
```

需要临时输入时，打开另一个 Action：**Actions → VAS 临时对话测试 → Run workflow**，选择必填的环境和设备，在 `turns_json` 粘贴：

```json
[
  {"text": "推荐一款适合会议录音转文字的耳机"},
  {"text": "详细介绍第一个", "interrupt_after_seconds": 2, "output_kind": "answer"},
  {"text": "它支持哪些语言？"}
]
```

上例第二轮从**正式回答开始播放**计时，2 秒后发送打断，结束本轮后接着发送第三句话。其他轮次等待回答播放结束再继续。历史上下文由同一 VAS 连接维护，不需要把之前的回答重新塞进输入。

| 参数 | 使用入口 | 用途 |
| --- | --- | --- |
| `environment` | 用例文件必填；临时 Action 必填 | 只允许 `dev` 或 `main` |
| `device_id` | 用例文件必填；临时 Action 必填 | MAC 地址，如 `FF:FF:FF:FF:FF:11`，不再回退仓库变量 |
| `diagnostics` | 临时 Action | 默认 `frame`，可选 `stage`；批量 Action 固定 `frame` |
| `scenario_path` | VAS 批量语音测试 | `scenarios/` 内的文件或文件夹；文件夹会递归读取 YAML/JSON |
| `turns_json` | VAS 临时对话测试 | 一个 session 的对话 JSON，支持逐轮打断 |
| `input_mode` | VAS 临时对话测试 | `manual` 或 `vad`，默认 `manual` |
| `turn_timeout_seconds` | VAS 临时对话测试 | 单轮超时，默认 90 秒 |

批量 Action 只保留 `scenario_path` 一个输入。每个用例必须写 `device_id` 和 `environment`，本机同名环境变量不会覆盖它们。`name` 可省略并使用文件名；`input_mode: manual`、`turn_timeout_seconds: 90` 是默认值，通常不必写。测试 VAD 或调整超时时再添加。工作流文件分别为 [`voice-test.yml`](.github/workflows/voice-test.yml) 和 [`voice-inline-test.yml`](.github/workflows/voice-inline-test.yml)。

`interrupt_after_seconds` 可省略；`output_kind` 可选 `answer`（正式回答）、`filler`（临时回复）、`pre_speech`（工具过渡语）或 `any`（任意语音）。如果指定类型没有出现，或回复在打断时间前已结束，报告会记录未触发打断，而不会把它算作成功。

每个 session 支持 1–30 个 turn，单轮超时范围为 5–120 秒。批量入口支持 1–100 个文件；Actions 每个 session job 最长 120 分钟。Actions 先统一校验全部场景，再用 `max-parallel: 1` 逐会话准备音频、连接 VAS，失败时继续下一会话；本地批量入口仍先准备全部音频再依次连接。

Actions 使用 **eSpeak NG 中文语音**把文本转为 WAV，再实时发送音频。这是离线合成，声音较机械，适合跑通链路和比较时序；识别准确率回归建议使用固定的真人录音。客户端按音频时长模拟播放，不依赖 runner 的扬声器。

镜像使用 Debian Trixie 的 eSpeak NG 1.52。Ubuntu 24.04 自带的 1.51 在这些中文输入上会读出拼音字母和声调数字，不能用于本测试的中文合成。连接后的欢迎语保留在 session 回放中，但不计作第一问的回答，不触发第一问的播放计时或定时打断。

## 环境设置

默认连接地址：

| 环境 | WebSocket 地址 |
| --- | --- |
| `dev` | `wss://lumin-vas-aquamind-dev.deep-edge.cn/looomyn/v1/` |
| `main` | `wss://lumin-vas-aquamind.deep-edge.cn/looomyn/v1/` |

可在 **Settings → Secrets and variables → Actions** 设置：

- **Variables**：`VAS_DEV_URL`、`VAS_MAIN_URL` 覆盖对应环境的连接地址。已有 `VAS_DEVICE_ID` 变量可删除，两个 Action 均不再读取它。
- **Secrets**：VAS 需要设备认证时设置 `VAS_TOKEN`。不要把 token 填到工作流输入、URL 或场景文件中。

VAS 需要支持现有 WebSocket 诊断协议。测试程序连接已有服务，不会部署 VAS，也不会修改服务端配置；两个手动测试工作流使用同一个执行队列，避免批量与临时测试同时占用设备。

报告与音频保留 14 天。GitHub 的运行页面显示 Markdown 摘要，完整 HTML 通过 artifact 下载查看，未启用 GitHub Pages。仓库位于 `deepedge-ai-tech` 组织，当前为私有仓库。

## 报告怎么读

- **会话入口**：Actions 中每个 session 一个下载链接；本地批量运行仍提供 `index.html` 选择会话。
- **会话回放**：在同一个时间轴播放全部 turn，保留用户输入、首音等待、过渡语与正式回答之间的空档，以及打断位置。
- **区间测量**：会话时间轴和轮次页的“链路时序”均支持拖动划线，显示起点、终点和 Δ 秒数；也可直接输入秒数、调整边界，按 Esc 或“清除”重置。链路图的 VAS / 客户端选区独立，不能跨时钟相减。
- **轮次详情**：保留各轮问题、回复文字、关键等待时间和 VAS 内部时序。
- **链路节点**：LLM 请求、First Token、首个播报文本、实际分句，以及 TTS 建连/复用、发送、响应头和首音频，以原有行内小菱形展示。相邻节点合并为数量标记，点击或键盘选择可看精确秒数。护栏 Embedding 单独一行，模型和供应商显示在行名下。

First Token 指 VAS 公共包装收到首个有效输出增量，可能是工具参数，详情会区分；首个播报文本可能受结构化解析和护栏等待影响。TTS 分句点复用实际分句，HTTP 响应头与首个有效音频分开。只出现连接复用时不假定发生建连或模型冷启动。新版节点需要部署对应 VAS 改动；旧数据仍能生成报告，但无法补出当时未采集的时间。

会话回放以客户端实际记录的音频发送与模拟播放时钟为准，跨 turn 保留真实间隔，统一用秒显示。它重现客户端按音频时长播放的节奏，不代表扬声器硬件实测。VAS 的单独时钟不能直接与客户端时间相减计算网络延迟。

默认播放及下载的 `session.mixed.wav` 同时包含用户语音与 VAS 回复，单声道设备也能完整回听。原始左右分轨文件 `session.played.wav` 保留用于诊断，可在“播放与计时口径”中下载。两个文件使用相同时间轴；旧报告执行 `python main.py report <结果目录>` 即可生成混音版，无需重跑 VAS。

## 下载与音频压缩

Actions 为每个 session 单独上传一个 artifact，不再要求下载整批音频。同一文件中的多轮对话仍在同一个连接中执行，回听也是完整 session。

下载包把 WAV 转为 **FLAC 无损音频**，相同内容只存一份。采样率、声道、PCM 采样、静音间隔和客户端时间轴不变，默认播放器仍同时播放用户和 VAS 声音。原始诊断 JSON/JSONL、每轮指标、失败信息和分轨音频均保留。压缩失败时 Action 会标为失败并上传原始诊断包。

本地运行仍保存 WAV。压缩某个已有 session：

```bash
python -m voice_scenarios.report_archive artifacts/my-run --output artifacts/my-download
# 批量结果则逐个指定 artifacts/my-batch/sessions/<session-id>
```

导出不会改动原结果目录。`audio-manifest.json` 保存原音频名称与 FLAC 文件的映射；解压即能用浏览器回听，不需要 Python。需要重新分析时，`python main.py report artifacts/my-download` 会通过 FFmpeg 还原 WAV 后重新生成报告，无需重跑 VAS（还原后目录会变大）。

## 本地运行

有 Docker 时可以直接复用 Actions 的环境，无需在主机安装 Python、FFmpeg 或语音库：

```bash
docker build -t aquamind-vas-testing:local .
bash .github/actions/test-image/run.sh python -m voice_scenarios.batch_run \
  --scenarios scenarios/smoke --output artifacts/docker-batch
```

在仓库目录运行上述命令，结果保存在主机的 `artifacts/docker-batch/`。临时对话可设置 `VAS_TURNS_JSON`，将入口换成 `python -m voice_scenarios.ci_run --output artifacts/docker-inline`。本地重复构建使用 Docker 本地层缓存，GitHub 缓存由 Actions 自动配置。

也可以直接从源码运行：

要求 Python 3.11+。先安装系统依赖：

```bash
# Debian Trixie；其他发行版须确认 espeak-ng --version >= 1.52
sudo apt-get install -y espeak-ng ffmpeg libopus0

# macOS
brew install espeak-ng ffmpeg opus
```

从源码安装：

```bash
git clone https://github.com/deepedge-ai-tech/Aquamind-VAS-Testing.git
cd Aquamind-VAS-Testing
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

使用保存的场景运行与 Actions 相同的批量流程：

```bash
export VAS_DIAGNOSTICS=frame
python -m voice_scenarios.batch_run \
  --scenarios scenarios/smoke \
  --output artifacts/my-batch
```

将 `--scenarios` 换成单个文件只运行该 session。`--prepare-only` 只校验场景并生成输入 WAV，不连接 VAS。每次使用新的输出目录，避免覆盖历史结果。

临时文本输入使用：

```bash
export VAS_ENVIRONMENT=dev
export VAS_DEVICE_ID='你的测试设备 MAC ID'
export VAS_TURNS_JSON='[{"text":"推荐一款会议耳机"},{"text":"第一个有什么特点？"}]'
export VAS_INPUT_MODE=manual
export VAS_DIAGNOSTICS=frame
export VAS_TURN_TIMEOUT_SECONDS=90
python -m voice_scenarios.ci_run --output artifacts/my-run
```

`batch_run` 和 `ci_run` 在未设置 `VAS_DIAGNOSTICS` 时均默认使用 `frame`；需要只记录环节计时时，可改为 `stage`。

已有 WAV 也可以直接发送：

```bash
python main.py run \
  --url wss://lumin-vas-aquamind-dev.deep-edge.cn/looomyn/v1/ \
  --device-id '你的测试设备 MAC ID' \
  --audio /absolute/path/question.wav \
  --diagnostics frame \
  --output artifacts/wav-run
```

多轮 WAV、验收阈值和本地 Fake 服务见 [场景与回归测试](docs/scenarios.md)。

报告可以直接用浏览器打开，也可在结果目录启动预览：

```bash
python -m http.server 8080 --bind 127.0.0.1 --directory artifacts/my-run
```

随后访问 <http://127.0.0.1:8080/report.html>。已有结果升级报告不需要重跑 VAS：

```bash
python main.py report artifacts/my-run
```

## 开发验证

```bash
python -m pytest --cov-config=.coveragerc-client --cov=voice_scenarios --cov-fail-under=80
python -m black --check main.py voice_scenarios tests
python -m isort --check-only main.py voice_scenarios tests
```

提交和 PR 自动运行独立 CI：场景解析、WebSocket 协议、Fake 服务、计时、打断、会话回放、报告和 Actions 输入处理。批量测试通过本地 WebSocket 验证每个文件建立独立连接、同文件各轮共享连接、失败后继续，以及全部场景在联网前校验。CI artifact 包含单会话和批量示例报告；新增回归检查压缩前后 PCM 一致、音频去重、会话隔离、失败报告保留、还原后重新分析和 Actions 会话列表校验。客户端覆盖率门槛为 80%，只排除需要 VAS checkout 的两个启动模块。真实 VAS 的本地集成测试通过 `VAS_TEST_ROOT` 显式开启，使用包含全部模块的覆盖率配置，见[开发文档](docs/scenarios.md#本地-vas--fake-服务)。

GitHub 官方参考：[手动运行工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)、[下载运行 artifacts](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)。
