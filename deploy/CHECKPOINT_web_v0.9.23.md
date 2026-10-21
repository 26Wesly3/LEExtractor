# 网页版冻结点 · LEExtractor v0.9.23

> 冻结时间：2026-10-21（Asia/Shanghai）
> 软件版本：**0.9.23**（`litsearch/version.py`）
> 提交：`33edb81` — `release: v0.9.23 — Windows portable package and README.en version alignment`
> 标签：`v0.9.23`（发布）、`v0.9.23-web-checkpoint`（**本冻结清单所在提交**，网页版以它为准）
> 用途：比赛演示期间网页 UI 不再漂移；任何重新部署都必须检出本 tag。

---

## 1. 冻结了什么

| 对象 | 位置 | 冻结方式 |
|---|---|---|
| 前端源码 | `web/src/**`、`web/package.json`、`web/vite.config.js` | git tag `v0.9.23-web-checkpoint` |
| 前端构建产物 | `litsearch/web/static/**`（FastAPI 直接服务） | 同上，随包提交 |
| 便携版 | `download/LEExtractor_v0.9.23_Windows_portable.zip` | 同上，随包提交 |
| 线上部署 | `https://leextractor.duckdns.org` | **仍为 v0.9.14，尚未同步**（见第 3 节） |

本次用 `npm run build` 重新构建后，产物与已提交的静态目录**逐字节一致**（文件名哈希不变、`index.html` SHA-256 相同），所以 v0.9.23 的网页版与源码是同步的，不需要额外提交前端变更。

## 2. 冻结产物的 SHA-256

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `litsearch/web/static/index.html` | 689 | `5667770a0b55c8058c31b1611bf9c05371e21feb02f83df027e228183be156d4` |
| `litsearch/web/static/assets/index-BsKYdPkZ.js` | 702 291 | `a40ace783cc8d1869af5a58499552be6e82b3fbae38b200849a78037fbf35da1` |
| `litsearch/web/static/assets/index-BX2HR8SV.css` | 420 180 | `453518cb89a7afb721073b00cd3fb9d7f9bd707b901ad4f321088f87d0d793d1` |
| `litsearch/web/static/assets/anton.ttf` | 130 324 | `fd9301f2837c99df2be07cc7f0be66e9470f5354d8fcf1606027c67fcd637a53` |
| `litsearch/web/static/assets/qiushi-characters.png` | 1 455 628 | `f3cdfa877af0b7190863a22f28467b5333b7620c6795252146ad47c44dbd1640` |
| `litsearch/web/static/assets/zju-wordmark.png` | 15 183 | `d2abb7210f087f30c8ed7339f39af012b38607dcfdf16e34bbdfd0fa11c3ddb5` |
| `download/LEExtractor_v0.9.23_Windows_portable.zip` | 12 211 247 | `bffeab6bed4c23a6e0577801b202712175053a1cd6897000b0513737113c1b8c` |

校验方式：

```powershell
Get-FileHash litsearch/web/static/index.html -Algorithm SHA256
Get-FileHash download/LEExtractor_v0.9.23_Windows_portable.zip -Algorithm SHA256
```

## 3. 线上现状

- 域名：<https://leextractor.duckdns.org>（Caddy + Let's Encrypt 正常）
- 服务器：`43.156.2.250`（新加坡，Ubuntu 24.04，Docker Compose）
- 部署目录：`/opt/leextractor`（compose 在此，源码在 `./repo`，数据在 `./data ./models ./cache ./logs`）

### 3.1 更新前（2026-10-21 16:38 +08 实测）

```json
{"status":"ok","app_name":"LEExtractor","version":"0.9.14","ui":"vue","local_only":false,
 "runtime":{"python":"3.12.15","host":"0.0.0.0","port":8012},
 "storage":{"data":"writable","cache":"writable","logs":"writable","models":"present"}}
```

### 3.2 更新后（2026-10-21 16:52 +08，已部署）

**线上已是 0.9.23**，部署来源为本 tag（`v0.9.23-web-checkpoint`，提交 `c681fd3`）：

- 在 `/opt/leextractor/repo` 检出该 tag，`docker compose --profile proxy up -d --build` 重建镜像与 `leextractor-leextractor-1` 容器；Caddy 继续服务，`data/ models/ cache/ logs` 未动
- 线上自检：`/api/health` → `version: 0.9.23`、`ui: vue`、`local_only: false`、`translation.ready: true`、`embedding.ready: true`；`/api/health/live` → `{"status":"ok"}`；首页标题 `LEExtractor — 惟学无际`；`/api/projects` → `total: 25`（历史项目全部保留）
- **注意：线上前端哈希与第 2 节不同**。Dockerfile 会在镜像内重新执行 `npm run build`，同一份源码在容器 Node 版本下产出不同内容哈希：线上为 `assets/index-DyH0PlE1.js`（702 291 B，SHA-256 `b7951e207afcd24f4e0d08ba03aa311dec486a336791b10e9e4c7e86e2e387f6`）与 `assets/index-Zqlpy4dr.css`。第 2 节的哈希只用于校验**仓库/便携版**里的静态包，不能拿去比对容器内重建的产物。

## 4. 复现这次更新（在服务器上执行）

```bash
ssh ubuntu@43.156.2.250
cd /opt/leextractor/repo
git fetch --tags
git checkout v0.9.23-web-checkpoint      # 冻结版；不要用 latest
git rev-parse HEAD | tee /opt/leextractor/DEPLOYED_COMMIT

cd /opt/leextractor
docker compose build leextractor
docker compose up -d                     # 有 Caddy 时：docker compose --profile proxy up -d
docker compose ps                        # 期望 leextractor = healthy

curl -s localhost:8012/api/health | head -c 400
curl -s https://leextractor.duckdns.org/api/health | head -c 400
```

验收：`/api/health` 的 `version` 从 `0.9.14` 变为 `0.9.23`，`ui` 仍为 `vue`，`models.translation.ready` 与 `embedding.ready` 均为 `true`（线上模型目录已预装，不需要重下）。

## 5. 回滚（不动 data / models / cache / logs）

```bash
cd /opt/leextractor/repo
git checkout v0.9.14
cd /opt/leextractor
docker compose build leextractor && docker compose up -d
```

镜像与数据都留在宿主目录，回滚不会丢项目、不会重下模型。若只是容器级问题，先试 `docker compose --profile proxy up -d`（秒级重建），再考虑重建镜像。

## 6. 本冻结点之后

- 网页/前端任何新改动都不再属于这一版演示；要上线新版请另建 checkpoint 并重新验证。
- 便携版直接使用仓库里的 `litsearch/web/static`；容器版用同一份源码在镜像内重新构建。两者 UI 代码一致，但构建产物哈希不同（见 3.2），比对时不要拿第 2 节的哈希去要求容器产物一致。
- 若线上更新后需要对比，用 `/api/health` 的 `version` 与 `score_context_id` 前缀即可判断是否为新版排序器。
