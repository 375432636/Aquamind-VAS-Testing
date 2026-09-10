# Aquamind VAS Testing

向已经部署的 VAS 发送多轮语音，记录客户端与服务端时序、临时回复、正式回复和打断状态。一个场景文件对应一个 session，文件中的多轮对话共享上下文。报告支持完整 session 回放、等待区间和拖动测量。

## 在 GitHub Actions 中运行

1. 打开 **Actions → VAS 语音测试 → Run workflow**。
2. 选择 `dev` 或 `main`。设备 MAC 可以留空，复用仓库变量 `VAS_DEVICE_ID`；也可以临时填写覆盖。
3. 保持 `source: saved_files`，在 `scenario_path` 选择文件夹（如 `scenarios/smoke`）或一个 YAML/JSON 文件。
4. 点击 **Run workflow**。所有场景按文件名顺序运行；一个 session 失败后仍继续下一场景。
5. 在 **Summary** 查看结果并下载完整 artifact，解压后打开 `index.html`，选择 session 回放。

当前只支持**手动批量运行**，没有启用定时任务。平时只需维护 [`scenarios/`](scenarios/) 中的文件，运行时无需重复粘贴对话。

| 场景目录 | 内容 |
| --- | --- |
| [`scenarios/smoke`](scenarios/smoke/) | 多轮上下文、正式回答播放后 2 秒打断、打断后继续对话 |
| [`scenarios/vad`](scenarios/vad/) | 连续发送语音和底噪，由后台 VAD 判断结束 |

新增一个 session，例如 `scenarios/custom/my-session.yaml`：

```yaml
name: 会议耳机上下文与打断
input_mode: manual
turn_timeout_seconds: 90
turns:
  - text: 推荐两款适合会议录音转文字的耳机
  - text: 详细介绍第一款
    interrupt_after_seconds: 2
    output_kind: answer
  - text: 它和第二款有什么区别？
```

需要临时输入时，把 `source` 改成 `inline_json`，在 `turns_json` 粘贴：

```json
[
  {"text": "推荐一款适合会议录音转文字的耳机"},
  {"text": "详细介绍第一个", "interrupt_after_seconds": 2, "output_kind": "answer"},
  {"text": "它支持哪些语言？"}
]
```

上例第二轮从**正式回答开始播放**计时，2 秒后发送打断，结束本轮后接着发送第三句话。其他轮次等待回答播放结束再继续。历史上下文由同一 VAS 连接维护，不需要把之前的回答重新塞进输入。

| 参数 | 用途 |
| --- | --- |
| `environment` | `dev` 或 `main`；切换连接地址 |
| `device_id` | 可留空，默认使用仓库变量 `VAS_DEVICE_ID` |
| `source` | `saved_files` 批量运行已保存场景；`inline_json` 临时运行一个 session |
| `scenario_path` | `scenarios/` 内的文件或文件夹；文件夹会递归读取 YAML/JSON |
| `turns_json` | 仅 `inline_json` 使用，支持逐轮打断 |
| `input_mode` | 仅 `inline_json` 使用；保存场景分别使用文件中的 `manual` 或 `vad` |
| `diagnostics` | `stage`：环节计时；`frame`：额外记录逐帧信息 |
| `turn_timeout_seconds` | 仅 `inline_json` 使用；保存场景分别读取各文件的配置，默认 90 秒 |

`interrupt_after_seconds` 可省略；`output_kind` 可选 `answer`（正式回答）、`filler`（临时回复）、`pre_speech`（工具过渡语）或 `any`（任意语音）。如果指定类型没有出现，或回复在打断时间前已结束，报告会记录未触发打断，而不会把它算作成功。

每个 session 支持 1–30 个 turn，单轮超时范围为 5–120 秒。批量入口支持 1–100 个文件；Actions 整个 job 最长 120 分钟。程序先校验所有文件、准备音频，全部成功后才连接 VAS，避免部分错误场景执行到一半才被发现。

Actions 使用 **eSpeak NG 中文语音**把文本转为 WAV，再实时发送音频。这是离线合成，声音较机械，适合跑通链路和比较时序；识别准确率回归建议使用固定的真人录音。客户端按音频时长模拟播放，不依赖 runner 的扬声器。

## 环境设置

默认连接地址：

| 环境 | WebSocket 地址 |
| --- | --- |
| `dev` | `wss://lumin-vas-aquamind-dev.deep-edge.cn/looomyn/v1/` |
| `main` | `wss://lumin-vas-aquamind.deep-edge.cn/looomyn/v1/` |

可在 **Settings → Secrets and variables → Actions** 设置：

- **Variables**：`VAS_DEVICE_ID` 保存常用测试设备；`VAS_DEV_URL`、`VAS_MAIN_URL` 覆盖对应地址。
- **Secrets**：VAS 需要设备认证时设置 `VAS_TOKEN`。不要把 token 填到工作流输入、URL 或场景文件中。

VAS 需要支持现有 WebSocket 诊断协议。测试程序连接已有服务，不会部署 VAS，也不会修改服务端配置；同一环境和设备的工作流会排队执行。

报告与音频保留 14 天。GitHub 的运行页面显示 Markdown 摘要，完整 HTML 通过 artifact 下载查看，未启用 GitHub Pages。**当前仓库是公开仓库，工作流输入、日志和 artifacts 应按公开测试资料使用，请使用测试账号与非敏感对话。**

## 报告怎么读

- **批量首页**：每个 session 的结果与报告入口。
- **会话回放**：在同一个时间轴播放全部 turn，保留用户输入、首音等待、过渡语与正式回答之间的空档，以及打断位置。
- **区间测量**：在时间轴拖动选择起点和终点，读取选中区间的秒数。
- **轮次详情**：保留各轮问题、回复文字、关键等待时间和 VAS 内部时序。

会话回放以客户端实际记录的音频发送与模拟播放时钟为准，跨 turn 保留真实间隔，统一用秒显示。它重现客户端按音频时长播放的节奏，不代表扬声器硬件实测。VAS 的单独时钟不能直接与客户端时间相减计算网络延迟。

## 本地运行

要求 Python 3.11+。先安装系统依赖：

```bash
# Ubuntu / Debian
sudo apt-get install -y espeak-ng ffmpeg libopus0

# macOS
brew install espeak-ng ffmpeg opus
```

从源码安装：

```bash
git clone https://github.com/375432636/Aquamind-VAS-Testing.git
cd Aquamind-VAS-Testing
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

使用保存的场景运行与 Actions 相同的批量流程：

```bash
export VAS_ENVIRONMENT=dev
export VAS_DEVICE_ID='你的测试设备 MAC ID'
export VAS_DIAGNOSTICS=stage
python -m voice_scenarios.batch_run \
  --scenarios scenarios/smoke \
  --output artifacts/my-batch
```

将 `--scenarios` 换成单个文件只运行该 session。`--prepare-only` 只校验场景并生成输入 WAV，不连接 VAS。每次使用新的输出目录，避免覆盖历史结果。

临时文本输入仍可使用：

```bash
export VAS_ENVIRONMENT=dev
export VAS_DEVICE_ID='你的测试设备 MAC ID'
export VAS_TURNS_JSON='[{"text":"推荐一款会议耳机"},{"text":"第一个有什么特点？"}]'
export VAS_INPUT_MODE=manual
export VAS_DIAGNOSTICS=stage
export VAS_TURN_TIMEOUT_SECONDS=90
python -m voice_scenarios.ci_run --output artifacts/my-run
```

已有 WAV 也可以直接发送：

```bash
python main.py run \
  --url wss://lumin-vas-aquamind-dev.deep-edge.cn/looomyn/v1/ \
  --device-id '你的测试设备 MAC ID' \
  --audio /absolute/path/question.wav \
  --diagnostics stage \
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

提交和 PR 自动运行独立 CI：场景解析、WebSocket 协议、Fake 服务、计时、打断、会话回放、报告和 Actions 输入处理。批量测试通过本地 WebSocket 验证每个文件建立独立连接、同文件各轮共享连接、失败后继续，以及全部场景在联网前校验。CI artifact 包含单会话和批量示例报告。客户端覆盖率门槛为 80%，只排除需要 VAS checkout 的两个启动模块。真实 VAS 的本地集成测试通过 `VAS_TEST_ROOT` 显式开启，使用包含全部模块的覆盖率配置，见[开发文档](docs/scenarios.md#本地-vas--fake-服务)。

GitHub 官方参考：[手动运行工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)、[下载运行 artifacts](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)。
