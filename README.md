# Aquamind VAS Testing

向已经部署的 VAS 发送多轮语音，记录客户端与服务端时序、临时回复、正式回复和打断状态。一个场景文件对应一个 session，文件中的多轮对话共享上下文。报告支持完整 session 回放、等待区间和拖动测量。

会话总览上方展示用户语音、回复播放与等待，下方展示所有轮次的 VAS 时序，包含欢迎语等会话级阶段。VAS 复用单轮详细页的分组、节点和模型信息，共用缩放、播放游标与划线测量。点击语音可定位回听，点击 VAS 节点只查看详情。

总览与单轮“链路时序”均按两端各自的系统时间绘制，统一显示**北京时间（Asia/Shanghai，UTC+8）**，不做时钟偏移校准。客户端在会话开始时记录本机系统时间与单调时钟的对应关系，之后不增加逐帧读取系统时间的开销。请求耗时和回归断言仍使用各端单调时钟。划线测量的是图上间距，跨端间距可能包含时钟误差。

旧数据缺少客户端绝对时间时，图中各来源独立从 0 秒开始，明确提示不能跨来源相减；会话总览的同一 VAS 时钟跨轮次保留间隔，不逐轮归零，也不按客户端轮次起点对齐。不会按服务端时间或文件创建时间补算。重新运行一次即可采集完整时间。若绝对时钟记录明显不连续，同样回退到相对时间。Unix 时间戳无需加 8 小时，只有显示时转换时区；Mac、Linux 和浏览器所在时区不影响北京时间显示。

VAS 发送 `type: image` 消息或 `type: display` 中的 `items`（`kind: image` / `video`）时，报告在会话播放时间轴和单轮“链路时序”的客户端行中显示图片 / 视频标记。相近的媒体项合并显示数量，点击查看每项的接收时间和链接；不按 URL 去重。一条 `display` 内的多张图片和视频共享该消息的客户端接收时间。标记不代表 VAS 发送、媒体加载完成或视频开始播放，不推断所属 LLM 请求。打开报告不自动下载媒体，点击“查看图片 / 视频”才打开原始 HTTP(S) 链接；链接失效仍保留时间记录。旧报告中已有原始媒体事件时，重新生成 HTML 即可显示，无需修改或重跑 VAS。

本次新增五个人设冒烟和 Excel 导出，配置、设备绑定状态及 mock 运行方式见[人设冒烟与 Excel](docs/persona-smoke.md)。

## 在 GitHub Actions 中运行

1. 打开 **Actions → VAS 批量语音测试 → Run workflow**。
2. 在用例文件中填写必填的 `device_id`（MAC）和 `environment`（`dev` 或 `main`）。
3. 在 `scenario_path` 填写文件夹（如 `scenarios/smoke`）或一个 YAML/JSON 文件路径。
4. 点击 **Run workflow**。各 session 逐个运行；一个 session 失败后仍继续后面的场景。
5. 在 **Summary** 查看全部会话结果，下载一个批量 artifact。解压后打开 `index.html` 选择 session，或打开 `evaluation.xlsx` 查看汇总。

**每天北京时间 07:00 自动运行全部冒烟测试。** [`VAS 批量语音测试`](.github/workflows/voice-test.yml) 使用默认分支 `main` 的最新已合并版本，读取 [`scenarios/smoke/`](scenarios/smoke/) 下的全部场景。当前是 9 个 session、62 轮：原 4 组 DEV 场景，加上 5 个人设、49 问的 Main 场景；各自使用文件中的环境和 MAC，诊断级别为 `frame`。新增到该目录的用例会自动纳入每日测试。

批量测试（定时和手动）运行在 GitHub 托管的 `ubuntu-24.04` Runner，复用 Docker 与 GitHub 构建缓存；暂不使用 5090 Runner。定时和手动批量测试共用串行执行和报告流程；某个 session 失败后继续后续场景。结果在 **Actions → VAS 批量语音测试 → 对应运行 → Summary** 查看，每个 session 保留独立页面，整批报告与无损压缩音频一次下载，保留 14 天。GitHub 按 UTC 调度（`0 23 * * *`），实际启动可能因队列繁忙延迟。

批量测试和临时对话 Action 仍可手动运行。批量测试只需维护 [`scenarios/`](scenarios/) 中的文件，运行时无需重复粘贴对话。

三个 Action（含 CI）共用 Docker 测试环境。首次构建安装系统与 Python 依赖，并将 Piper 中文模型预下载到 `/opt/piper`，之后通过 GitHub BuildKit 缓存复用依赖和模型镜像层；修改场景或业务代码不会重新安装依赖或下载模型，修改 `pyproject.toml` 或基础环境时才重建对应层。缓存受 GitHub 分支可见性和回收规则限制，缓存失效时会正常重建。设备认证只在运行容器时传入，报告和生成音频不进入构建缓存。

| 场景目录 | 内容 |
| --- | --- |
| [`scenarios/smoke`](scenarios/smoke/) | 多轮上下文、正式回答播放后 2 秒打断、打断后继续对话、新闻黄历、肢体互动描述与产品介绍，以及图片 / 视频展示 |
| [`scenarios/vad`](scenarios/vad/) | 连续发送语音和底噪，由后台 VAD 判断结束 |
| [`scenarios/products`](scenarios/products/) | 会议耳机上下文、正式回答后 2 秒打断 |
| [`scenarios/sensors`](scenarios/sensors/) | 摸头、摸手、摇晃、抛起四种标准传感器指令 |

新增的 [`04-media-display.yaml`](scenarios/smoke/04-media-display.yaml) 使用 DEV 设备 `FF:FF:FF:FF:FF:11`，依次推荐产品、查看同款图片、查看同款演示视频，三轮共享上下文。后两轮各有一条检查：`image_items_min: 1` / `video_items_min: 1`。它们要求本轮实际收到对应媒体项，未返回就失败；不要求固定工具名或产品名。人设需要具备相应媒体资源和展示能力。计数验证收到消息，不保证媒体链接可访问或视频已播放。

新闻、黄历、摸头和产品介绍保存在 [`03-news-almanac-interaction-products.yaml`](scenarios/smoke/03-news-almanac-interaction-products.yaml)，使用 DEV 设备 `30:ED:A0:A6:23:A4`，四轮共享一个 session。语音采用 VAD 模式，说完后继续发送底噪；摸头直接发送标准传感器消息。

这组冒烟只保留两条内容检查：新闻按占星人设检查正式回答是否包含星象、星盘、星座或示例星象关键词；黄历检查识别文本是否包含“黄历／通胜／老黄历”，避免“黄历 → 管理”的识别偏差被忽略。摸头与产品介绍不限制回复用词。不要求固定工具或调用次数；时间轴、工具调用和音频仍正常采集。关键词仅用于轻量检查，不验证星象或黄历的事实正确性。严格业务评测需要时另行配置，见[可选业务断言](docs/scenarios.md#可选业务断言)。

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

每个 session 支持 1–30 个 turn，单轮超时范围为 5–120 秒。批量入口支持 1–100 个文件；Actions 整批 job 最长 120 分钟。Actions 与本地使用同一流程：先统一静态校验全部场景，再逐会话准备音频、连接 VAS、生成报告。静态错误会在合成和联网前终止整批；单轮执行失败后，停止上行和播放、发送 abort，最多等待 5 秒；收到新的停止确认并静默至少 0.25 秒后，继续同一连接的下一轮。恢复失败或断线时，剩余轮次标记“未执行”，后面的会话继续；不会重连并冒充原上下文。失败轮保留原始错误和恢复记录，整批最终仍为失败，但报告照常上传。超大批次请拆分目录运行。

Actions 和本地文本用例使用 **Piper（`piper-tts==1.8.0`）**，默认中文模型 `zh_CN-huayan-medium`，将测试文本离线合成为输入 WAV，再实时发送音频给 VAS。模型下载完成后，合成过程在本机或容器内运行；VAS 回复仍由 VAS 自身的 TTS 生成。识别准确率回归建议使用固定的真人录音。客户端按音频时长模拟播放，不依赖 runner 的扬声器。

模型来自公开的 [rhasspy/piper-voices 中文 Huayan medium 目录](https://huggingface.co/rhasspy/piper-voices/tree/main/zh/zh_CN/huayan/medium)；该声音的[模型卡](https://huggingface.co/rhasspy/piper-voices/blob/main/zh/zh_CN/huayan/medium/MODEL_CARD)将训练数据集许可标为 `Unknown`。

连接后的欢迎语保留在 session 回放中，但不计作第一问的回答，不触发第一问的播放计时或定时打断。第一问等待欢迎语的 `tts_stop`、客户端播放排空和 `settle_seconds` 后才发送；欢迎语超时会使测试失败，第一问不会发出。

普通场景的 `greeting_wait_seconds` 默认为 0.5 秒，CI 准备器默认为 5 秒（`VAS_GREETING_WAIT_SECONDS` 可配置）；这是没有观察到欢迎语时的等待窗口。观察到欢迎语生成或音频后，等待其播完，最长由 `greeting_timeout_seconds` 控制，默认 60 秒（CI 可用 `VAS_GREETING_TIMEOUT_SECONDS` 配置）。

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

- **会话入口**：Actions 与本地均提供 `index.html` 选择独立会话页面。
- **会话回放**：在同一个时间轴播放全部 turn，顶部保留用户输入、首音等待、过渡语与正式回答之间的空档，以及打断位置；下方展示各轮 VAS 的 ASR、Memory、护栏、LLM、工具和 TTS，维度与详细页一致。
- **区间测量**：会话时间轴和轮次页的“链路时序”均支持拖动划线，显示起点、终点和 Δ 秒数；也可直接输入秒数、调整边界，按 Esc 或“清除”重置。总览中的选区贯穿语音与 VAS 行。跨来源间距未经时钟校准，不能作为网络延迟。
- **轮次详情**：保留各轮问题、回复文字、关键等待时间和 VAS 内部时序。
- **Thinking / Unthinking**：每次 LLM 请求在总览、轮次详情的行名、节点详情和 LLM 请求证据中标注模式。读取该次请求记录的 `parameters.enable_thinking`：布尔值 `true` 为 Thinking、`false` 为 Unthinking；缺失或格式不明显示“未采集”。同一 session 可有不同模式，不根据模型名推断。该标记表示请求配置，不证明提供方实际进行了多少推理。
- **链路节点**：总览与详情共用同一份标记，包括 ASR 请求开始、ASR 首字（首个非空中间结果）、ASR 最终结果、LLM 请求、First Token、首个播报文本、实际分句，以及 TTS 建连/复用、发送、响应头和首音频，以原有行内小菱形展示。相邻节点合并为数量标记，点击或键盘选择可看精确秒数。护栏 Embedding 单独一行，模型和供应商显示在行名下。

ASR 首字取每次请求第一个非空中间结果的到达时点；空结果不计入。旧诊断没有字符数时保留“首个中间结果（字数未采集）”，不补造首字时间。所有轮次共享会话时间轴，节点保留轮次、请求身份及距该请求开始的耗时，静态 HTML 与在线展示一致。

First Token 指 VAS 公共包装收到首个有效输出增量，可能是工具参数，详情会区分；首个播报文本可能受结构化解析和护栏等待影响。TTS 分句点复用实际分句，HTTP 响应头与首个有效音频分开。只出现连接复用时不假定发生建连或模型冷启动。新版节点需要部署对应 VAS 改动；旧数据仍能生成报告，但无法补出当时未采集的时间。

会话回放以客户端实际记录的音频发送与模拟播放时钟为准，跨 turn 保留真实间隔，统一用秒显示。它重现客户端按音频时长播放的节奏，不代表扬声器硬件实测。VAS 的单独时钟不能直接与客户端时间相减计算网络延迟。

默认播放及下载的 `session.mixed.wav` 同时包含用户语音与 VAS 回复，单声道设备也能完整回听。原始左右分轨文件 `session.played.wav` 保留用于诊断，可在“播放与计时口径”中下载。两个文件使用相同时间轴；旧报告执行 `python main.py report <结果目录>` 即可生成混音版，无需重跑 VAS。

## 下载与音频压缩

Actions 每批只准备一次 Docker，在一个 Linux job 中串行执行，并上传一个 artifact。同一文件中的多轮对话仍在同一个连接中执行，回听也是完整 session。

下载包把 WAV 转为 **FLAC 无损音频**，相同内容只存一份。采样率、声道、PCM 采样、静音间隔和客户端时间轴不变，默认播放器仍同时播放用户和 VAS 声音。原始诊断 JSON/JSONL、每轮指标、失败信息和分轨音频均保留。压缩失败时 Action 会标为失败并上传原始诊断包。

本地运行仍保存 WAV。压缩某个已有 session：

```bash
python -m voice_scenarios.report_archive artifacts/my-run --output artifacts/my-download
# 批量导出：逐个压缩，失败会话保留原始音频，后续继续
python -m voice_scenarios.report_archive artifacts/my-batch --batch --output artifacts/my-batch-download
```

导出不会改动原结果目录。`audio-manifest.json` 保存原音频名称与 FLAC 文件的映射；解压即能用浏览器回听，不需要 Python。需要重新分析时，`python main.py report artifacts/my-download` 会通过 FFmpeg 还原 WAV 后重新生成报告，无需重跑 VAS（还原后目录会变大）。

## 本地运行

有 Docker 时可以直接复用 Actions 的环境，无需在主机安装 Python、FFmpeg 或语音库：

```bash
docker build -t aquamind-vas-testing:local .
bash .github/actions/test-image/run.sh python -m voice_scenarios.batch_run \
  --scenarios scenarios/smoke --output artifacts/docker-batch
```

在仓库目录运行上述命令，结果保存在主机的 `artifacts/docker-batch/`。临时对话可设置 `VAS_TURNS_JSON`，将入口换成 `python -m voice_scenarios.ci_run --output artifacts/docker-inline`。本地重复构建使用 Docker 本地层缓存，GitHub 缓存由 Actions 自动配置。镜像中的 `/opt/piper` 已包含默认模型和配置，非 root 运行用户也可读取；容器启动后无需再次联网下载模型。运行脚本会传入调用方设置的 `PIPER_DATA_DIR`，覆盖时请使用容器内可访问的目录，例如已挂载仓库中的 `/app/artifacts/piper`。

也可以直接从源码运行：

要求 Python 3.11+。先安装系统依赖：

```bash
# Debian / Ubuntu
sudo apt-get install -y ffmpeg libopus0

# macOS
brew install ffmpeg opus
```

从源码安装：

```bash
git clone https://github.com/deepedge-ai-tech/Aquamind-VAS-Testing.git
cd Aquamind-VAS-Testing
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m voice_scenarios.speech --download
```

Linux 和 macOS 使用同一 Piper 合成流程，无需另装系统 eSpeak。首次下载模型需要联网，默认缓存目录为 `~/.cache/aquamind-vas-testing/piper`；可在下载和运行前设置 `PIPER_DATA_DIR` 指向其他目录。缓存同时包含 `zh_CN-huayan-medium.onnx` 与 `zh_CN-huayan-medium.onnx.json` 后即可离线合成输入语音；运行 VAS 对话仍需连接目标服务。

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
node --test tests/*.test.cjs
```

客户端计时专项覆盖三轮连续播放、首句与过渡等待、PTT、打断、旧时钟错误，以及 HTML/Excel/音频重新导出的一致性。运行方法与验收项见[客户端时间轴回归](docs/client-timeline-regression.md)；CI 的 `python-test-results` 增加三轮模拟示例报告 `timeline-clock-example/`。

提交和 PR 自动运行独立 CI：场景解析、WebSocket 协议、Fake 服务、计时、打断、会话回放、报告和 Actions 输入处理。批量测试通过本地 WebSocket 验证每个文件建立独立连接、同文件各轮共享连接、失败后继续，以及全部场景在联网前校验。CI artifact 包含单会话和批量示例报告；新增回归检查压缩前后 PCM 一致、音频去重、会话隔离、失败报告保留、还原后重新分析和 Actions 会话列表校验。客户端覆盖率门槛为 80%，只排除需要 VAS checkout 的两个启动模块。真实 VAS 的本地集成测试通过 `VAS_TEST_ROOT` 显式开启，使用包含全部模块的覆盖率配置，见[开发文档](docs/scenarios.md#本地-vas--fake-服务)。

GitHub 官方参考：[手动运行工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)、[下载运行 artifacts](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)。

### 批量 CI 的失败处理

执行顺序：全量静态校验 → 会话 1 准备/运行/报告 → 会话 2 → 批量压缩 → 上传报告 → 最终状态检查。

- `batch.json` 记录会话状态、完成/计划轮数及 `failure_stage`：`validation`、`prepare`、`connection`、`run`、`assertion`、`report`、`archive`。
- 准备或渲染失败仍生成 `result.json`、`report.json` 和简洁的 `report.html`；已有音频与诊断数据保留。
- 压缩失败的会话放在下载包的 `raw-fallback/<session-id>/`；其他会话继续无损压缩。原始运行目录不变。
- `continue-on-error` 仅用于允许后续报告上传。最后检查原始与下载包的批量状态、完整轮数和各步骤结果；有失败、缺失报告或上传失败，Action 仍然失败。
- 输入语音使用 Piper；串行连接和现有 BuildKit 缓存继续保留，模型随依赖层缓存。生成音频不缓存，VAS 实际回复耗时不变。

### 独立网页实时对话

```bash
python -m voice_scenarios web --port 19225 --output artifacts/live
```

打开 `http://127.0.0.1:19225/live/`，选择 MAIN/DEV、填写 MAC，点击“连接并开始记录”。在同一个连接中发送文字，或按住空格说话、松开结束（输入框内的空格仍正常输入）；VAD 模式持续发送录音和底噪，由 VAS 返回识别结果后停止本轮收音，回复播放完成后继续聆听。可用“打断回复”取消正在播放的回答，再发送下一轮。结束后生成独立静态 HTML、Excel、完整会话音频和原始事件。

手动模式：按下发送 `listen/start`（`mode: manual`），持续传送音频，松开后先发送录音尾帧，再发一次 `listen/stop`。客户端静音检测只用于标记时间，不会自动停录。报告“录音结束控制”分别核对 stop 发送与接收、ASR 自动断句请求与供应商回显、VAS 本地 VAD 活动；没有 VAD 事件不等于已经关闭。完全停止 VAS 本地 VAD 计算需要服务端支持，本工具不会将手动模式标成“所有 VAD 已关闭”。

总览和单轮时间轴的“用户语音”轨道常驻显示 **PTT 语音开始（空心圆）**、**PTT 语音结束（实心方块）**，分别对应客户端实际发送 `listen/start` 和 `listen/stop` 的时刻；点击查看精确秒数和北京时间，可作为磁吸与区间测量端点。停止发送失败时不会生成结束标记。已有录音重新生成报告也可补上这两个标记。

客户端收发时间使用 `performance.now()`，浏览器播放用近期的 Web Audio 输出时钟映射到同一时间基准，重新连接、播放间隔和音频时钟暂停后均重新采样，不复用整个会话的固定偏移。播放记录保留输出时钟快照、调度时间与独立回调观察时间，便于核查。浏览器未提供可靠输出时钟时明确标为估计。历史数据若出现“播放早于同包接收”，不据此计算首音或移动回复条；报告显示真实的停止到首包等待，原始回复音频单独回听，需重新录音才能取得可信的完整播放时序。输入时间轴保留实际发包时间，音频合成回放为保留样本作的排队不改变图上时间。

首次使用可先点击“检测麦克风”，选择输入设备并说话，再点击“停止检测并回听”。这段检测音频只保留在当前页面，不发送给 VAS。正式录音等麦克风就绪后才创建对话轮次；准备期间松开空格会取消，不产生空轮次。麦克风全零静音会在页面提示，并在导出报告中标记该轮失败。结束会话使用现有 VAS 的 `diagnostics/finish` 控制消息，收到最后一批诊断数据后导出。

网页是独立客户端，不嵌入报告。浏览器直连 VAS；本地 Python 只提供页面、OTA 校验和记录/导出服务。沿用 Aquamind Console 的 Opus 编解码、AudioWorklet 采音、Web Audio 连续调度；不改 Console 或 VAS。使用 Chrome 的 localhost 页面可访问麦克风；远程网页需要 HTTPS。设备 Token 仅用于本次连接，不写入报告或本地存储。关闭页面或断线会保存已接收记录，并标记采集未完成。

自动化测试的播放时间来自 Python 模拟播放器；网页对话来自浏览器 AudioContext 时钟映射，包含初始缓冲，不等同于扬声器的声学实测。网页 VAD 的“用户说完”以客户端能量估计标识，服务端 VAD 标记仍使用服务器事件；两端时间转换为北京时间显示，不做跨主机校时。精确 TTS 请求文本仍需服务端提供，本版不新增相关埋点。

开发期间修改报告生成 Python 代码或新增前端公共脚本后，需要重启本地 `web` 服务；该命令不热重载 Python 模块。已有静态报告需重新生成后刷新页面，避免旧进程与新资源混用。

### 时间轴与历史音频

- 总览最上方显示用户语音和回复，下面显示各轮 VAS 阶段。连接尝试为会话起点；旧记录缺失时保留原始基准。
- 每条 VAS 时间轴默认折叠，聚合显示事件数。单击时间条或左侧箭头展开全部事件，再次点击折叠；每行独立，缩放保留展开状态。VAD 与其他事件使用同样的标记，点选查看名称和精确时间。聚合标记保留异常提示。
- 滚动 VAS 阶段时，时间尺、用户语音、回复播放及对应标签固定在图表顶部。触控板双指捏合缩放、双指平移滚动；触摸屏双指捏合缩放、单指平移。缩放围绕手势位置，保留测量选区。
- Turn 页复用同一时间轴，默认显示本轮范围；沿用会话的时间与音频，不重新归零。点击“本轮”恢复完整本轮视图。
- 鼠标拖动或输入起止秒数测量，支持放大选区、播放选区、循环选区、定位当前轮及长等待。时间轴获得焦点后：空格播放、左右键定位、`+/-` 缩放、`0` 恢复全程（Turn 页恢复本轮）、`Esc` 清除。
- Python 模拟播放器改用样本游标连续计时，缓冲中的帧不会因调度迟到而产生零样本碎缝；真实音频迟到的等待保留。

旧报告需要**在新目录重建**，不要覆盖原始数据：

```bash
python -m voice_scenarios rebuild artifacts/old-session --output artifacts/rebuilt-session
```

仅当旧模拟播放拥有完整 PCM 就绪证据时重建；浏览器实播、缺失证据和旧打断片段不会重新计时。重建版明确标注“模拟播放重建”，保存 `result.original.json`，原目录保持不变。

本地无云端验收页面：`PYTHONPATH=. python tests/live_preview.py`，打开 `http://127.0.0.1:19226/live/` 并取消 OTA 校验。该模拟端返回固定测试音，不代表真实 VAS 性能。

### 跨端时间校准

开启诊断的 Python 自动测试默认在连接后后台采样 5 次 `clock_sync`，
每组总预算 1 秒，单次等待最多 0.25 秒；支持校准的服务在结束前再复测。
普通对话不等待校准，旧 VAS 不支持时报告显示未校准。
配置文件 `clock_sync: false` 或批量运行环境变量 `VAS_CLOCK_SYNC=0` 可关闭。
网页实时测试在「连接选项」中提供独立的时间校准开关。

报告默认使用有效校准，并可切换「原始时间／校准时间」。原始日志、客户端
播放时间和同端单调时钟耗时不改动。报告显示最小 RTT 样本的偏移与半 RTT
不确定范围；该范围只描述采样时刻，不是整个会话的精度保证。前后采样明显
不一致或服务端会话不同则停止应用校准。跨端时间差不等同于纯网络耗时。
校准样本保存在 `result.json` / `report.json` 的 `clock_sync` 及客户端事件日志中。

客户端回听、等待和 VAS 诊断已分离：详情与验证方式见[客户端音轨独立与可选校时](docs/client-audio-clock-independence.md)。


### Memory 与 LLM 并行验收

服务端启用流水线后，原 Memory 时间轴会显示命中、未命中、重复画像、超时或失败；
展开时间条，再点“查看返回内容与采用内容”读取本轮记忆。LLM 时间轴保留弃用候选和
补充 Memory 重发的请求，关键回复指标排除弃用候选。旧服务未采集的结果显示“未采集”。

本地真实 VAS + Fake 服务回归：

```bash
VAS_TEST_ROOT=/absolute/path/to/VAS/main/xiaozhi-server \
  python -m pytest tests/test_memory_pipeline_stack.py -v
```

场景：`config/memory-pipeline.example.yaml`；外部服务数据：`config/fake-memory-pipeline.yaml`。
PTT 和 VAD 各一个三轮会话，覆盖未命中复用、命中后丢弃错误音乐调用、工具递归只查询一次 Memory。
需要按本地开发说明准备 VAS 的 `.venv`；测试不连接线上外部服务。
