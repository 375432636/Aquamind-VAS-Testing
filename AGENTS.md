# Aquamind-VAS-Testing

## 5090 本地 HTTP/WS 实时测试

网页服务和到 5090 VAS 的端口转发是两件事。只启动网页时，`/live/` 可以打开，但选择“本地 · HTTP/WS”后 OTA 会失败。此模式需要以下仅监听本机回环地址的转发：

| 本机端口 | 5090 VAS 容器端口 | 用途 |
| --- | --- | --- |
| `127.0.0.1:8000` | `8004` | WebSocket 对话 |
| `127.0.0.1:8003` | `8003` | HTTP OTA 校验 |

从本项目目录启动网页服务（如已在运行，不要重复启动）：

```sh
.venv/bin/python -m voice_scenarios web --host 127.0.0.1 --port 19225 --output artifacts/live
```

另开一个终端启动转发，并保持该终端运行。每次先查询当前容器 IP；VAS 容器重建后 IP 可能变化，需要重新启动转发。

```sh
VAS_IP="$(ssh -o BatchMode=yes deep-edge@100.114.113.70 \
  'docker inspect -f "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}" aquamind-private-vas-1')"
test -n "$VAS_IP" && ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L "127.0.0.1:8000:${VAS_IP}:8004" \
  -L "127.0.0.1:8003:${VAS_IP}:8003" \
  deep-edge@100.114.113.70
```

检查两个转发和网页，三项均应返回 HTTP 200：

```sh
curl -fsS -o /dev/null -w 'OTA HTTP %{http_code}\n' http://127.0.0.1:8003/looomyn/ota/
curl -fsS -o /dev/null -w 'VAS HTTP %{http_code}\n' http://127.0.0.1:8000/
curl -fsS -o /dev/null -w '网页 HTTP %{http_code}\n' http://127.0.0.1:19225/live/
```

然后打开 `http://127.0.0.1:19225/live/`，在“环境”中选择“本地 · HTTP/WS”。该模式使用 `ws://127.0.0.1:8000/looomyn/v1/` 和 `http://127.0.0.1:8003/looomyn/ota/`。如果 OTA 显示 500，先检查 8003 转发；如果 OTA 成功而 VAS 连接失败，再检查 8000 转发。
