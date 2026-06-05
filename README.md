# browser-use 自动化发文服务

本项目基于 `browser-use`、`Playwright`、`FastAPI` 和 LLM，实现头条号自动化发文。当前推荐入口是 `auto/` 服务：调用方只需要提交一次发文任务，系统会自动判断用户 Cookie 是否存在；如果没有 Cookie 或 Cookie 失效，会启动远程登录服务获取 Cookie，然后继续完成自动化发文。

## 核心能力

- 一体化发文入口：`POST /api/v1/auto/publish`
- 任务查询入口：`GET /api/v1/auto/jobs/{job_id}`
- 主动保存远程登录 Cookie：`POST /api/v1/auto/savecookie/{job_id}`
- `user_id` 隔离 Cookie：每个用户单独保存登录态
- Cookie 存在时：创建后台发文任务，接口立即返回 `job_id`
- Cookie 不存在或失效时：返回远程登录链接，用户登录后继续原任务
- 远程登录支持两种 Cookie 保存触发方式：关闭远程连接自动保存，或调用 `savecookie` 主动保存
- 已做幂等保护：同一个远程登录 session 不会因为重复触发而重复发文
- 每个任务都有独立日志，便于定位登录、Cookie、发文、文章 URL 获取问题

## 目录结构

```text
browser-use/
├─ auto/                         # 登录 + Cookie + 发文一体化服务
│  ├─ api.py                     # /api/v1/auto 路由
│  ├─ server.py                  # FastAPI app 入口
│  ├─ publish_agent.py           # Cookie 判断、远程登录、发文编排
│  ├─ cookie_store.py            # 按 platform/user_id 保存 Cookie
│  ├─ job_store.py               # 任务状态管理
│  ├─ logging_config.py          # 每个 job 独立日志
│  ├─ adapters/                  # 对原发文能力的适配
│  ├─ remote_cookie/             # 远程登录和 Cookie 获取
│  ├─ data/                      # 运行期 Cookie/Profile，已 gitignore
│  └─ logs/                      # 运行期日志，已 gitignore
├─ api/publish/                  # 原自动发文核心服务
├─ tools/browser_test/           # 远程浏览器查看器和 Cookie 获取工具
├─ src/                          # 平台、浏览器、发文相关基础代码
├─ tests/auto/                   # auto 服务测试
├─ requirements.txt
└─ .env.example
```

## 环境安装

以下命令以 Windows PowerShell 为例。

### 1. 进入项目

```powershell
cd C:\program001\browser_use_demo4\browser-use
```

### 2. 创建并启用虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 禁止执行脚本，可以先执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 3. 安装 Python 依赖

推荐使用国内镜像源：

```powershell
python -m pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 4. 安装 Playwright 浏览器依赖

```powershell
python -m playwright install chromium
```

### 5. 安装 cloudflared

`auto` 远程获取 Cookie 依赖 Cloudflare Tunnel。Windows 可以使用：

```powershell
winget install Cloudflare.cloudflared
```

确认可用：

```powershell
cloudflared --version
```

### 6. 配置环境变量

复制示例配置：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少配置：

```text
DEEPSEEK_API_KEY=你的 DeepSeek Key
```

当前自动化发文默认使用 `deepseek-chat`。后续可以继续适配 Qwen。

## 启动 auto 服务

```powershell
cd C:\program001\browser_use_demo4\browser-use
.\.venv\Scripts\Activate.ps1
python -m uvicorn auto.server:app --host 127.0.0.1 --port 19000
```

启动成功后访问：

- API 文档：[http://127.0.0.1:19000/docs](http://127.0.0.1:19000/docs)
- 健康检查：[http://127.0.0.1:19000/api/v1/auto/health](http://127.0.0.1:19000/api/v1/auto/health)

如果端口被占用，换一个端口：

```powershell
python -m uvicorn auto.server:app --host 127.0.0.1 --port 19001
```

## Docker 部署

第一版 Docker 部署采用单容器、单 worker、volume 持久化、Cloudflare quick tunnel。不要把 `uvicorn` worker 数量调大，因为当前 job 和远程登录 session 仍是内存态，多 worker 会导致任务查询或 `savecookie` 找不到对应 session。

### 1. 准备环境变量

复制并编辑 `.env`：

```bash
cp .env.example .env
```

至少配置：

```text
DEEPSEEK_API_KEY=你的 DeepSeek Key
```

### 2. 构建镜像

```bash
docker build -f Dockerfile.auto -t browser-use-auto:latest .
```

### 3. 启动服务

```bash
docker compose -f docker-compose.auto.yml up -d
```

服务端口：

```text
http://127.0.0.1:19000
```

健康检查：

```bash
curl http://127.0.0.1:19000/api/v1/auto/health
```

查看日志：

```bash
docker compose -f docker-compose.auto.yml logs -f auto
```

停止服务：

```bash
docker compose -f docker-compose.auto.yml down
```

### 4. 持久化目录

`docker-compose.auto.yml` 会将运行期数据挂载到本地：

```text
runtime/auto-data  -> /app/auto/data
runtime/auto-logs  -> /app/auto/logs
runtime/api-logs   -> /app/api/logs
```

其中：

- `runtime/auto-data/cookies/` 保存用户 Cookie
- `runtime/auto-data/remote_profiles/` 保存远程登录临时浏览器 profile
- `runtime/auto-logs/jobs/` 保存 auto job 日志
- `runtime/api-logs/requests/` 保存原发文服务请求日志

### 5. Docker 运行约束

- 镜像内使用 Playwright Python 基础镜像，并安装 `cloudflared`。
- 容器内优先使用 `BROWSER_EXECUTABLE_PATH`，未配置时会自动查找 Playwright 镜像内置 Chromium 或系统 Chrome/Chromium。
- 服务通过 `xvfb-run` 启动，支持远程登录时的浏览器画面。
- compose 已配置 `shm_size: "1gb"`，避免 Chromium 因 `/dev/shm` 太小不稳定。
- 生产公网部署前建议给接口加鉴权，或只允许内网访问。

## 自动化流程

```text
调用 POST /api/v1/auto/publish
        |
        v
创建 job，立即返回 job_id 和 query_url
        |
        v
检查 auto/data/cookies/{platform}/{user_id}.json
        |
        +-- Cookie 存在 --> 后台注入 Cookie 并执行自动化发文
        |
        +-- Cookie 缺失/失效 --> 启动本地浏览器 + 远程查看器 + Cloudflare Tunnel
                                |
                                v
                            返回 login_url
                                |
                                v
                            用户远程扫码登录头条
                                |
                                +-- 方式 1：用户关闭远程连接，系统自动提取并保存 Cookie
                                |
                                +-- 方式 2：调用 POST /api/v1/auto/savecookie/{job_id} 主动提取并保存 Cookie
                                                |
                                                v
                                      保存 Cookie 后继续原发文任务
```

关键规则：

- `user_id` 必传，用于区分不同用户 Cookie。
- `POST /api/v1/auto/publish` 是任务创建接口，只表示请求已被服务接收并创建 job。
- 真实任务进度和发布结果通过 `GET /api/v1/auto/jobs/{job_id}` 查询。
- 如果用户 Cookie 已存在，`publish` 返回 `task_status=running`，后台继续发文。
- 如果用户 Cookie 不存在，`publish` 返回 `task_status=login_required` 和 `login_url`。
- 远程登录完成后，关闭远程连接会自动提取 Cookie。
- 也可以调用 `POST /api/v1/auto/savecookie/{job_id}` 主动触发 Cookie 提取，不必等待用户关闭远程连接。
- `savecookie` 和关闭远程连接共用同一套保存逻辑，已做幂等保护，不会重复发文。

## API 使用

### 1. 创建发文任务

```powershell
$body = @{
  user_id = "user1"
  platform = "toutiao"
  title = "测试标题"
  content = "这里是文章正文内容。"
  cover_image_url = "https://example.com/cover.jpg"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:19000/api/v1/auto/publish" `
  -ContentType "application/json" `
  -Body $body
```

请求字段：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `user_id` | 是 | 用户 ID，用于隔离 Cookie |
| `platform` | 否 | 默认 `toutiao` |
| `title` | 是 | 文章标题 |
| `content` | 是 | 文章正文 |
| `cover_image_url` | 否 | 封面图片 URL |

Cookie 已存在时，返回示例：

```json
{
  "code": 200,
  "message": "任务创建成功，发布任务正在后台执行",
  "data": {
    "job_id": "xxxx",
    "task_status": "running",
    "query_url": "/api/v1/auto/jobs/xxxx",
    "login_url": "",
    "remote_session_id": "",
    "log_file_path": "C:\\program001\\browser_use_demo4\\browser-use\\auto\\logs\\jobs\\xxxx.log"
  }
}
```

需要用户登录时，返回示例：

```json
{
  "code": 200,
  "message": "任务创建成功，需要用户登录",
  "data": {
    "job_id": "xxxx",
    "task_status": "login_required",
    "query_url": "/api/v1/auto/jobs/xxxx",
    "login_url": "https://xxxxx.trycloudflare.com",
    "remote_session_id": "session-xxxx",
    "log_file_path": "C:\\program001\\browser_use_demo4\\browser-use\\auto\\logs\\jobs\\xxxx.log"
  }
}
```

创建失败时，`code=500`，并在 `data.reason` 中返回失败原因。

### 2. 查询任务状态

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://127.0.0.1:19000/api/v1/auto/jobs/{job_id}"
```

常见返回：

| code | task_status | 说明 |
|---:|---|---|
| `200` | `published` | 发布成功 |
| `202` | `running` | 后台执行中 |
| `401` | `login_required` | 需要用户登录，返回 `login_url` |
| `404` | `not_found` | job 不存在 |
| `408` | `expired` | 预留：远程登录 session 过期 |
| `410` | `closed` | 预留：远程登录 session 已关闭 |
| `500` | `failed` | 发布失败 |
| `503` | `query_failed` | 查询任务状态失败 |

发布成功时，`data` 会包含文章信息：

```json
{
  "code": 200,
  "task_status": "published",
  "message": "文章发布成功",
  "data": {
    "job_id": "xxxx",
    "user_id": "user1",
    "platform": "toutiao",
    "title": "测试标题",
    "cover_image_url": "https://example.com/cover.jpg",
    "article_url": "https://www.toutiao.com/item/...",
    "publish_result": {
      "success": true,
      "account_name": "",
      "platform_user_id": "",
      "article_title": "测试标题",
      "publish_signal": "",
      "operation_time": ""
    }
  }
}
```

### 3. 主动保存远程登录 Cookie

当 `/publish` 返回 `login_url` 后，用户完成扫码登录。如果不想等用户关闭远程连接，可以调用：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:19000/api/v1/auto/savecookie/{job_id}"
```

成功返回：

```json
{
  "code": 200,
  "job_id": "xxxx",
  "status": "succeeded",
  "message": "Cookie 保存成功，发布任务已继续执行",
  "login_url": "",
  "log_file_path": "C:\\program001\\browser_use_demo4\\browser-use\\auto\\logs\\jobs\\xxxx.log",
  "result": {
    "cookie_count": 8,
    "query_url": "/api/v1/auto/jobs/xxxx"
  }
}
```

重复调用或任务已经进入发布流程时，返回 `code=409`，不会重复保存或重复发文。

### 4. 手动提交 Cookie 回调

一般不需要手动调用。仅调试时使用：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:19000/api/v1/auto/jobs/{job_id}/cookies" `
  -ContentType "application/json" `
  -Body '{"cookies":[{"name":"sessionid","value":"xxx","domain":".toutiao.com","path":"/"}]}'
```

## 日志和数据

### auto 外层日志

```text
auto/logs/jobs/{job_id}.log
```

记录：

- 收到发文请求
- Cookie 是否存在
- 是否启动远程登录
- 远程登录链接
- 手动或自动保存 Cookie 的结果
- 是否继续进入发文流程
- 发文服务调用结果

### 原发文服务日志

```text
api/logs/requests/{job_id}.log
```

记录：

- browser-use Agent 每一步操作
- LLM 调用信息
- 点击发布确认
- 发布成功检测
- 作品管理页文章 URL 获取
- 候选文章标题和 URL

### Cookie 保存位置

```text
auto/data/cookies/{platform}/{user_id}.json
```

例如：

```text
auto/data/cookies/toutiao/user1.json
```

`auto/data/` 和 `auto/logs/` 是运行期目录，不会提交到 Git。

## 文章 URL 获取逻辑

发布成功后，`auto` 覆盖层会进入头条作品管理页：

```text
https://mp.toutiao.com/profile_v4/graphic/articles
```

然后执行多轮重试：

- 每轮重新进入作品管理页
- 等待 `.article-card` 或 `/item/` 链接出现
- 提取候选文章标题和 URL
- 每轮日志打印候选数量、前几条标题、URL
- 只有标题匹配时才打开详情页确认
- 标题不匹配时不会再误打开第一篇旧文章
- 返回前会规范化头条文章 URL

实现位置：

```text
auto/adapters/toutiao_publish_service.py
auto/url_utils.py
```

## 测试

运行 `auto` 相关测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\auto -q
```

当前预期：

```text
45 passed
```

## 常见问题

### 1. `/docs` 打不开

确认服务端口是否正确。例如服务启动在 `19000`：

```text
http://127.0.0.1:19000/docs
```

如果启动时报端口占用：

```text
WinError 10048
```

说明端口已有服务在使用，换端口启动：

```powershell
python -m uvicorn auto.server:app --host 127.0.0.1 --port 19001
```

### 2. 远程登录链接打不开

检查：

- `cloudflared --version` 是否可用
- 本地网络是否允许访问 Cloudflare Tunnel
- 日志中是否生成了 `https://*.trycloudflare.com`
- 本地 Chrome 或 Edge 是否已安装

### 3. 登录后没有继续发文

检查当前 job 日志：

```text
auto/logs/jobs/{job_id}.log
```

重点看：

- 是否检测到远程查看器关闭
- 是否调用了 `savecookie`
- 是否提取到 Cookie
- Cookie 是否通过头条域名和登录态校验
- 是否进入“开始调用自动化发文服务”

### 4. 调用 `savecookie` 后担心重复发文

当前已做幂等保护：

- 同一个远程登录 session 完成一次后，再次完成会被跳过。
- job 已进入 `publishing` 或 `succeeded` 后，再调用 `savecookie` 会返回 `409 cookie already saved`。
- 如果先调用 `savecookie`，随后用户又关闭远程连接，watcher 会检测到 session 已完成，不会再次提取 Cookie 或再次发文。

### 5. 发布成功但没有文章 URL

检查：

```text
api/logs/requests/{job_id}.log
```

重点搜索：

```text
article candidates extracted
selected title-matched article candidate
article detail verification
title-matched article URL not found after retries
```

如果候选列表里没有新文章，通常是作品管理页同步延迟、筛选页不对，或文章仍在审核。

### 6. 触发验证码或登录异常

头条建议使用 App 扫码登录。手机号验证码登录容易触发滑块，远程操作不稳定。

## 旧入口说明

项目仍保留 `auto` 依赖的原始发文和远程查看器模块：

- `api/publish/`：原发文核心服务，`auto` 会复用其中的 `PublishService`
- `tools/browser_test/`：远程浏览器查看器，`auto` 远程登录会复用 `viewer.py`
- `src/`：平台、浏览器和头条基础逻辑

当前推荐新开发和调试优先使用 `auto/`，因为它已经把远程登录、Cookie 保存、Cookie 注入和自动化发文串成统一流程。
