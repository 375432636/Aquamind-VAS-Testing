# Console 音频逻辑的独立适配

来源：工作区 Aquamind-Console 的 `public/opus/libopus.js`、`src/utils/opus/index.ts`、录音/播放 hooks。

- `libopus.js` 原样保留 Console 所用实现，包含其原有版权声明。
- `opus.mjs` 是 Console TypeScript 编解码封装的 JavaScript 版本，只调整配置 import 和静态资源路径。
- `audio.mjs` 将录音和连续音频调度从 React 生命周期抽离，并记录 AudioContext 播放区间；停止时仅保存确实播放过的样本。
- `app.mjs` 管理同一 VAS 连接内的轮次、文字、收音、打断、诊断和记录侧通道。

维护时重点验证：尾帧先于 listen/stop、VAD 不发送手动 stop、欢迎语/打断前后归属、解码取消、播放样本连续性、诊断最后一批以及记录断线。首版客户端不提供真实机器人硬件 MCP tools；服务端 MCP 调用仍正常由 VAS 执行。
