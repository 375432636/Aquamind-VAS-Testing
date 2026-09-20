# Console 音频逻辑的独立适配

来源：工作区 Aquamind-Console 的 `public/opus/libopus.js`、`src/utils/opus/index.ts`、录音/播放 hooks。

- `libopus.js` 原样保留 Console 所用实现，包含其原有版权声明。
- `opus.mjs` 是 Console TypeScript 编解码封装的 JavaScript 版本，只调整配置 import 和静态资源路径。
- `audio.mjs` 将录音和连续音频调度从 React 生命周期抽离，并记录 AudioContext 播放区间；停止时仅保存确实播放过的样本。
- `app.mjs` 管理同一 VAS 连接内的轮次、文字、收音、打断、诊断和记录侧通道。

## 仅对话开关

连接前关闭“记录日志并生成报告”，即可直接使用文字、PTT 或连续 VAD 对话。此时不建立本地记录 WebSocket、不发送 VAS diagnostics/clock_sync、不编码用于日志保存的 PCM，也不创建会话目录或报告。OTA 认证与实际语音收发保持不变，VAS 自身的业务日志不受此开关影响。结束时直接断开；下次连接前可重新开启记录，沿用完整报告流程。

## VAD 持续上传

点击开始后，只发送一次 `listen/start`（`mode: auto`），同一个 Recorder 持续编码并发送语音和静音帧。STT 只是识别文本更新，TTS 状态只控制播放；收到 STT、播放回复、回复播放完成均不关闭或重建麦克风。开始 VAD 时不会打断正在播放的欢迎语；点击“打断回复”也保留已开启的连续收音。再次点击停止、结束会话或连接断开才关闭 Recorder。

这沿用 Console 的 `useAudioRecorder → sendAudioData` 与独立 `onAudioData/onAudioEnd → 播放器` 分工。Console 当前 `startListening` 仍使用 manual 协议；Testing 的连续 VAD 明确使用 auto，不照搬这个手动结束字段。不发送伪造的周期性 start/stop 或空音频代替真实麦克风。

一次连续收音区间保持同一个客户端 listen 分组，区间内的累计 STT、多个回复和服务器 utterance/workflow 原始事件全部保留；不会把每条流式 STT 当作一个新用户 turn。PTT 仍在松开时先排空尾帧，再发送一次 stop。持续收音时发送文字需先停止录音，避免把同一音频流切成 manual 模式。

回归入口：`node --test tests/live_controller.test.cjs`，验证播放期间上传静音与语音、回复结束后继续上传、欢迎语期间开始、手动打断仍收音，以及停止后不再接受旧回调。

维护时重点验证：尾帧先于 listen/stop、VAD 不发送手动 stop、欢迎语/打断前后归属、解码取消、播放样本连续性、诊断最后一批以及记录断线。首版客户端不提供真实机器人硬件 MCP tools；服务端 MCP 调用仍正常由 VAS 执行。
