# 5090 部署

仓库 main 的 **CI 成功后**，`Deploy Voice Lab to 5090` 在专用组织 Runner `aquamind-vas-testing-5090` 上构建并部署。只接受本仓库 main 的 push，PR 不执行部署。手动运行必须选择 main；旧版本的延迟 CI 结果会跳过部署。

- 入口：`https://aquamind-vas-testing.cpolar.cn/`
- 本地监听：`127.0.0.1:19225`，由独立 cpolar 隧道提供 HTTPS。
- 数据：`/home/deep-edge/aquamind-vas-testing/data/reports`，容器更新不删除报告。
- 登录配置：`/home/deep-edge/aquamind-vas-testing/private/web.env`（仅宿主机保存）。
- 版本记录：`/home/deep-edge/aquamind-vas-testing/current.env`。
- 当前 main 没有网页对话模块时提供报告目录；以后 `voice_scenarios.web_server` 合入 main 后自动启用网页对话及录音入口，沿用同一份报告数据和登录保护。

## 首次准备

Runner 以 `deep-edge` 用户运行，标签为 `self-hosted, Linux, X64, aquamind-vas-testing-5090`。注册 token 仅安装时使用，不存入 workflow。用户服务已启用 linger，可在退出 SSH 后以及重启后启动。

```bash
systemctl --user status aquamind-vas-testing-runner
systemctl --user status aquamind-vas-testing-tunnel
journalctl --user -u aquamind-vas-testing-runner -n 50
```

Clash 为 global 模式，GLOBAL 选择 sg。Runner 代理写在 `private/runner-proxy.env`；本地和 5090 地址直连。未开启 TUN 或修改系统路由，Clash 的 global 模式作用于进入 Clash 的流量。

登录信息只保存在服务器，不提交 Git。需要查看或修改时在 5090 打开 `private/web.env`。修改后重新部署服务。

## 手动部署及回退

在已检出的、经过测试的 main 源码目录中：

```bash
APP_REVISION=$(git rev-parse HEAD) bash scripts/deploy_5090.sh
```

构建复用本机 Docker 层缓存；构建失败不会替换运行中的容器。新容器健康检查失败时恢复先前记录的镜像及 Compose 配置。初次部署没有可回退版本。服务更新有短暂中断，请避开正在录音的会话；当前录音未完成前不要手动触发部署。

持久化数据不包含在镜像或 GitHub 缓存中。不执行全局 Docker 清理。旧镜像和构建缓存由管理员按磁盘空间安排清理。

## 在 5090 跑测试

使用部署的镜像运行已有场景；`--output` 必须位于挂载的 `/data/reports` 内，才会保留并出现在网页报告目录。

```bash
cd /home/deep-edge/aquamind-vas-testing
set -a
. current.env
set +a
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/data/reports:/data/reports" \
  "$APP_IMAGE" python main.py --help
```

具体场景参数沿用仓库 README。不要把真实设备 token 放进公开日志、workflow 输入或 Git。
