# LEExtractor 部署手册（v0.9.13）

面向比赛用的**真实服务器**，不是抽象云部署：

```
Region : Singapore
OS     : Ubuntu Server 24.04 LTS 64-bit
CPU    : 2 vCPU
RAM    : 2 GB
Storage: 40 GB
Network: Public Internet
```

架构只有一层反向代理，没有微服务、没有 Kubernetes / Redis / Celery：

```
Internet → DuckDNS → 新加坡公网 IP → HTTPS → Caddy → LEExtractor 容器
                                                    ├── FastAPI + 打包好的 Web UI
                                                    ├── 翻译 / Embedding 模型
                                                    ├── provider 客户端
                                                    └── /data /models /cache /logs
```

> 本文档中的命令按顺序执行即可。**标 ⚠ 的步骤必须实测**，不能因为"容器起来了"就跳过。

---

## 1. 初始环境

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo timedatectl set-timezone Asia/Singapore     # 日志时间与比赛时区一致

# 只开必要端口（22/80/443）；应用端口 8012 只在 loopback，不对外
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw --force enable && sudo ufw status

free -h && nproc && df -h /
```

腾讯云/阿里云还需在**控制台安全组**里放行 80/443（ufw 只管系统内）。

## 2. Swap（2 GB 机器的安全垫）

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
sudo sysctl -w vm.swappiness=10 && echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
free -h   # 确认 Swap 总量 >= 2G
```

⚠ Swap **是安全缓冲，不是正常运行依赖**。若 `free -h` 长期显示大量 swap 在用，
说明 `LEEXTRACTOR_MAX_ACTIVE_JOBS` 或模型配置过高，必须降下来，而不是加大 swap。

## 3. Docker 与 Compose

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker "$USER" && newgrp docker
docker --version && docker compose version
```

## 4. 目录与权限

```bash
sudo mkdir -p /opt/leextractor && cd /opt/leextractor
sudo mkdir -p data models cache logs caddy/data caddy/config
# 容器内以 uid 10001 运行（Dockerfile 里的 leextractor 用户）
sudo chown -R 10001:10001 data models cache logs
chmod 755 /opt/leextractor
```

`data` 与 `.env` 是必须备份的两样东西；`models` 丢了可以重下，`cache` 丢了只是变慢。

## 5. 取得代码并配置

```bash
cd /opt/leextractor
git clone https://github.com/26Wesly3/LEExtractor.git repo
cd repo && git checkout feature/v0.9.13        # 固定到具体分支/commit，不要用 latest
git rev-parse HEAD | tee /opt/leextractor/DEPLOYED_COMMIT   # 记入部署记录
cd /opt/leextractor && cp repo/.env.example .env && chmod 600 .env && nano .env
```

`.env` 里必须改的几项：

```ini
LEEXTRACTOR_DOMAIN=你的子域.duckdns.org
LEEXTRACTOR_PUBLIC_HOSTS=你的子域.duckdns.org   # 不填则 API 只接受本机 Host（会 403）
LEEXTRACTOR_MAX_ACTIVE_JOBS=1                   # 2 GB 机器的起点，压测后再谈 2
LEEXTRACTOR_EMBEDDING_THREADS=2
LEEXTRACTOR_EMBEDDING_BATCH=8
LEEXTRACTOR_LOG_FORMAT=json
S2_API_KEY=...                                  # 可选，但会显著减少 429
```

compose 文件在仓库里，从 `/opt/leextractor` 运行即可（volumes 是相对当前目录的 `./data` 等）：

```bash
cd /opt/leextractor && cp repo/docker-compose.yml . && cp -r repo/deploy .
```

## 6. 模型准备

**模式 A（比赛推荐）：预装模型**

```bash
# 在开发机下载好后 scp 上来，放到 /opt/leextractor/models
scp -r ./models/* ubuntu@<server>:/opt/leextractor/models/
sudo chown -R 10001:10001 /opt/leextractor/models
sha256sum /opt/leextractor/models/**/* | head     # 与开发机比对
```

优点：首次启动稳定、不受 Hugging Face 网络波动影响、不重复下载、可提前核对哈希。

**模式 B：自动下载**：`LEEXTRACTOR_MODEL_DIR` 指向持久化目录即可，重启不会重复下载；
下载失败会如实报错，不会静默降级成"没有模型"。

## 7. DuckDNS

1. 在 <https://www.duckdns.org/domains> 用 GitHub/Google 登录，创建子域（例 `leextractor-demo`），
   把它指向服务器公网 IP。
2. **token 只能放 root-only 文件，绝不进 Git / 前端 / 日志 / `/api/health`**：

```bash
sudo mkdir -p /etc/duckdns
sudo tee /etc/duckdns/duckdns.ini >/dev/null <<'EOF'
domains=leextractor-demo
token=在此填入你的token
EOF
sudo chown root:root /etc/duckdns/duckdns.ini && sudo chmod 600 /etc/duckdns/duckdns.ini

sudo tee /etc/systemd/system/duckdns.service >/dev/null <<'EOF'
[Unit]
Description=DuckDNS updater
[Service]
Type=oneshot
EnvironmentFile=/etc/duckdns/duckdns.ini
ExecStart=/bin/sh -c 'curl -fsS "https://www.duckdns.org/update?domains=${domains}&token=${token}&ip="'
EOF

sudo tee /etc/systemd/system/duckdns.timer >/dev/null <<'EOF'
[Unit]
Description=Update DuckDNS every 5 minutes
[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload && sudo systemctl enable --now duckdns.timer
sudo systemctl start duckdns.service && systemctl status duckdns --no-pager
dig +short 你的子域.duckdns.org        # 应返回服务器公网 IP
```

## 8. 启动

```bash
cd /opt/leextractor
docker compose build                    # 首次较慢；Node 只出现在构建阶段
docker compose up -d
docker compose ps                       # 期望 healthy
curl -s localhost:8012/api/health | head -c 400
```

HTTPS（二选一）：

```bash
# 方案 A：compose 内的 Caddy
docker compose --profile proxy up -d
# 方案 B：宿主机 Caddy/Nginx，应用只暴露 127.0.0.1:8012
```

## 9. 验收：健康检查

| 请求 | 期望 |
|---|---|
| `curl -s localhost:8012/api/health/live` | `{"status":"ok"}` |
| `curl -s localhost:8012/api/health/ready` | HTTP 200，`status=ready`；`data` 目录不可写时 HTTP 503 |
| `curl -s localhost:8012/api/health` | `status/version/runtime/storage/models` 齐全，**不含任何密钥或绝对路径** |
| `curl -s -H "Host: 你的子域.duckdns.org" localhost:8012/api/health` | 200（证明 `LEEXTRACTOR_PUBLIC_HOSTS` 生效） |
| `curl -s -H "Host: evil.example" localhost:8012/api/health` | **403**（Host 守卫仍在） |
| `curl -sI https://你的子域.duckdns.org` | 200/30x，且 `strict-transport-security` 存在 |

## 10. 更新与回滚

```bash
# 更新
cd /opt/leextractor/repo && git fetch && git checkout <新分支/commit>
git rev-parse HEAD | tee /opt/leextractor/DEPLOYED_COMMIT
cd /opt/leextractor && docker compose build && docker compose up -d
curl -s localhost:8012/api/health | head -c 200      # 验证新版本号

# 回滚（保留 volumes，不会丢数据）
cd /opt/leextractor/repo && git checkout <上一个 commit>
cd /opt/leextractor && docker compose build && docker compose up -d
```

部署记录至少保留：`git commit`、镜像 tag（`leextractor:0.9.13`）、部署时间。
**不允许比赛前只剩 `latest`。**

## 11. 日志与内存

```bash
docker compose logs -f --tail=200 leextractor     # 应用（JSON 单行，配 jq 更好读）
docker compose logs leextractor | jq -c 'select(.event=="job_finished")' 2>/dev/null
tail -f /opt/leextractor/logs/leextractor.log     # 若设置了 LEEXTRACTOR_LOG_DIR
docker stats --no-stream                          # 容器实时 CPU/内存
free -h                                           # 宿主内存 + swap
journalctl -k | grep -i 'killed process'          # 确认有没有 OOM killer
```

日志轮转已就位：容器 stdout 走 `json-file`（10 MB × 3），文件 sink 走 5 MB × 4。

## 12. 备份与恢复

```bash
# 备份（项目数据 + 配置 + 部署记录，几分钟）
sudo tar czf /root/leextractor-$(date +%F).tgz -C /opt/leextractor data .env docker-compose.yml DEPLOYED_COMMIT

# 恢复
sudo tar xzf /root/leextractor-<日期>.tgz -C /opt/leextractor
cd /opt/leextractor && docker compose up -d
```

模型不必频繁备份（可重下）；`data/` 丢了就是参赛演示数据丢了。

## 13. 常见错误

| 现象 | 原因 / 处理 |
|---|---|
| 网页 403、响应 `invalid_host` | `LEEXTRACTOR_PUBLIC_HOSTS` 没填或拼错；改 `.env` 后 `docker compose up -d` |
| `/api/health/ready` 503 | `data` 目录不可写：`chown -R 10001:10001 data`，或宿主磁盘满 |
| 启动很慢、日志反复下载模型 | 模式 A 没生效：确认 `LEEXTRACTOR_MODEL_DIR` 指向已放模型的目录 |
| 容器被内核杀死 | OOM：先看 `free -h` / `journalctl -k`，把 `MAX_ACTIVE_JOBS` 降到 1，或下调 compose 的 memory 之外的并发参数 |
| HTTPS 证书申请失败 | 80/443 未放行（含云控制台安全组），或 DuckDNS 未解析到本机；临时用 SSH 隧道兜底：`ssh -N -L 8012:127.0.0.1:8012 ubuntu@<server>` |
| 搜索结果"部分数据源不可用" | 这是设计行为：单个 provider 超时/429 只降级为 `partial`，不影响其他来源 |

## 14. 比赛三级保险

```
A. 云端：https://<子域>.duckdns.org      （本手册 §1–§12）
B. 本地：Windows 便携包双击 Start_LEExtractor.bat（见 README.zh-CN.md）
C. 录屏：完整产品演示视频
```

三级里任意一级失效都不影响展示；B 与 C 是 A 的兜底。
