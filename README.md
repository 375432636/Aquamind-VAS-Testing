# Aquamind VAS Testing

向已经部署的 VAS 发送多轮语音，记录客户端与服务端时序、临时回复、正式回复和打断状态。每次运行保持同一个 session；报告首页比较各轮等待时间，每个 turn 使用独立页面查看细节与回听。

## 在 GitHub Actions 中运行

1. 打开 **Actions → VAS 语音测试 → Run workflow**。
2. 选择 `dev` 或 `main`，填写该环境中有效的设备 MAC ID。
3. 在 `turns_json` 填写对话，点击 **Run workflow**。
4. 运行结束后，**Summary** 显示逐轮关键时间和报告下载链接。下载并解压整个 artifact，打开 `report.html`。

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
| `device_id` | 对应环境中有效的设备 MAC ID |
| `turns_json` | 按顺序执行的文本数组，支持逐轮打断 |
| `input_mode` | `manual`：语音发完主动发送结束；`vad`：持续发送底噪，等待 VAS 判定结束 |
| `diagnostics` | `stage`：环节计时；`frame`：额外记录逐帧信息 |
| `turn_timeout_seconds` | 单轮最长等待秒数，默认 90 |

`interrupt_after_seconds` 可省略；`output_kind` 可选 `answer`（正式回答）、`filler`（临时回复）、`pre_speech`（工具过渡语）或 `any`（任意语音）。如果指定类型没有出现，或回复在打断时间前已结束，报告会记录未触发打断，而不会把它算作成功。

一次运行支持 1–30 个 turn，单轮超时范围为 5–120 秒。

Actions 使用 **eSpeak NG 中文语音**把文本转为 WAV，再实时发送音频。这是离线合成，声音较机械，适合跑通链路和比较时序；识别准确率回归建议使用固定的真人录音。客户端按音频时长模拟播放，不依赖 runner 的扬声器。

## 环境设置

默认连接地址：

| 环境 | WebSocket 地址 |
| --- | --- |
| `dev` | `wss://lumin-vas-aquamind-dev.deep-edge.cn/looomyn/v1/` |
| `main` | `wss://lumin-vas-aquamind.deep-edge.cn/looomyn/v1/` |

可在 **Settings → Secrets and variables → Actions** 设置：

- **Variables**：`VAS_DEV_URL`、`VAS_MAIN_URL` 覆盖对应地址。
- **Secrets**：VAS 需要设备认证时设置 `VAS_TOKEN`。不要把 token 填到工作流输入、URL 或场景文件中。

VAS 需要支持现有 WebSocket 诊断协议。测试程序连接已有服务，不会部署 VAS，也不会修改服务端配置；同一环境和设备的工作流会排队执行。

报告与音频保留 14 天。GitHub 的运行页面显示 Markdown 摘要，完整 HTML 通过 artifact 下载查看，未启用 GitHub Pages。**当前仓库是公开仓库，工作流输入、日志和 artifacts 应按公开测试资料使用，请使用测试账号与非敏感对话。**

## 报告怎么读

- **会话首页**：一行一个 turn，先比较说完后的首次回复、正式回复等待和打断状态。
- **Turn 页面**：本轮问题与回复文字、关键等待时间、逐段回听、VAS 时间轴。
- **音频片段**：默认回听实际播放部分；打断时可展开比较已收到但没有播放的音频。

页面上的时间统一以秒显示。客户端和 VAS 分别从本轮首个事件归零，各自统计耗时，不能跨两条时间轴相减计算网络延迟。

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

用文本运行与 Actions 相同的流程：

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

提交和 PR 自动运行独立 CI：场景解析、WebSocket 协议、Fake 服务、计时、打断、音频分段、报告和 Actions 输入处理。客户端覆盖率门槛为 80%，只排除需要 VAS checkout 的两个启动模块。真实 VAS 的本地集成测试通过 `VAS_TEST_ROOT` 显式开启，使用包含全部模块的覆盖率配置，见[开发文档](docs/scenarios.md#本地-vas--fake-服务)。

GitHub 官方参考：[手动运行工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)、[下载运行 artifacts](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)。
