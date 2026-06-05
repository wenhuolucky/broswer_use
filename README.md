# browser-use 自动化发文服务

本项目基于 `browser-use`、`Playwright`、`FastAPI` 和 LLM，实现头条号自动化发文。当前重点入口是 `auto/` 服务：调用方只需要提交一次发文请求，系统会自动判断用户 Cookie 是否存在；如果没有 Cookie，会启动远程登录服务获取 Cookie，然后继续完成自动化发文。

## 核心能力

- `auto` 一体化发文接口：`POST /api/v1/auto/publish`
- `user_id` 隔离 Cookie：每个用户单独保存登录态
- Cookie 存在时：直接注入 Cookie 并执行自动发文
- Cookie 不存在时：自动启动远程登录链接，用户登录后保存 Cookie，再继续发文
- 每个任务独立日志：便于定位登录、Cookie、发文、文章 URL 获取问题
- 复用原有发文能力，但 `auto/` 内部做隔离封装，尽量不影响原代码

## 目录结构

```text
browser-use/
├── auto/                         # 当前重点：登录 + Cookie + 发文一体化服务
│   ├── api.py                    # /api/v1/auto 路由
│   ├── server.py                 # FastAPI app 入口
│   ├── publish_agent.py          # 自动判断 Cookie、远程登录、发文编排
│   ├── cookie_store.py           # 按 platform/user_id 保存 Cookie
│   ├── job_store.py              # 任务状态管理
│   ├── logging_config.py         # 每个 job 单独日志
│   ├── adapters/                 # 对原发文服务的适配与覆盖
│   ├── remote_cookie/            # 远程登录和 Cookie 获取
│   ├── data/                     # 运行期 Cookie/Profile，已 gitignore
│   └── logs/                     # 运行期日志，已 gitignore
├── api/publish/                  # 原自动发文服务
├── tools/browser_test/           # 原远程浏览器查看和 Cookie 获取工具
├── src/                          # 平台、浏览器、发布相关基础代码
├── tests/auto/                   # auto 服务测试
├── requirements.txt
└── .env.example
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

`auto` 远程获取 Cookie 依赖 Cloudflare Tunnel。Windows 可使用：

```powershell
winget install Cloudflare.cloudflared
```

安装后确认可用：

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

## auto 自动化流程

### 总流程

```text
调用 /api/v1/auto/publish
        |
        v
检查 auto/data/cookies/{platform}/{user_id}.json
        |
        +-- Cookie 有效 --> 注入 Cookie --> 调用自动化发文 --> 返回发布结果
        |
        +-- Cookie 缺失/无效 --> 启动本地浏览器 + 远程查看器 + Cloudflare Tunnel
                                |
                                v
                            返回 login_url
                                |
                                v
                            用户远程扫码登录头条
                                |
                                v
                            用户关闭远程浏览器/查看器
                                |
                                v
                            系统提取 Cookie 并保存
                                |
                                v
                            自动继续发文
```

### 关键规则

- `user_id` 必传，用于区分不同用户 Cookie。
- 默认平台是 `toutiao`。
- 如果用户 Cookie 已存在且有效，请求会同步进入发文流程。
- 如果用户 Cookie 不存在，请求会返回 `waiting_cookie` 和 `login_url`。
- 远程登录完成后，关闭远程浏览器/查看器会触发 Cookie 提取。
- Cookie 保存成功后，系统自动继续执行原来的发文任务。

## API 使用

### 1. 发起自动发文

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

### 2. Cookie 已存在时的返回

```json
{
  "code": 200,
  "job_id": "xxxx",
  "status": "succeeded",
  "message": "",
  "login_url": "",
  "log_file_path": "C:\\program001\\browser_use_demo4\\browser-use\\auto\\logs\\jobs\\xxxx.log",
  "result": {
    "success": true,
    "article_url": "https://www.toutiao.com/item/...",
    "article_title": "测试标题"
  }
}
```

### 3. Cookie 不存在时的返回

```json
{
  "code": 202,
  "job_id": "xxxx",
  "status": "waiting_cookie",
  "message": "请打开 login_url 完成登录，登录成功后系统可继续发文",
  "login_url": "https://xxxxx.trycloudflare.com",
  "log_file_path": "C:\\program001\\browser_use_demo4\\browser-use\\auto\\logs\\jobs\\xxxx.log",
  "result": {}
}
```

此时把 `login_url` 发给登录用户。用户打开链接后可以看到本地浏览器画面，在头条后台扫码登录。登录完成后关闭远程窗口，服务会提取 Cookie、保存 Cookie，并继续执行发文。

### 4. 查询任务状态

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://127.0.0.1:19000/api/v1/auto/jobs/{job_id}"
```

返回中重点看：

- `status`：任务状态
- `login_url`：远程登录入口
- `log_file_path`：当前任务日志
- `result.article_url`：发布成功后的文章 URL
- `error`：失败原因

### 5. 手动提交 Cookie 回调

一般不需要手动调用。只有调试时才使用：

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
- Cookie 保存结果
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
- 标题不匹配时不会再打开第一个旧文章

这部分实现位于：

```text
auto/adapters/toutiao_publish_service.py
```

## 测试

运行 `auto` 相关测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\auto -q
```

当前预期：

```text
25 passed
```

也可以只测试文章 URL 获取逻辑：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\auto\test_toutiao_publish_service.py -q
```

## 常见问题

### 1. `/docs` 打不开

确认服务端口是否正确。例如服务启动在 `19000`，访问：

```text
http://127.0.0.1:19000/docs
```

如果启动时报端口占用：

```text
WinError 10048
```

说明端口已有服务在使用。换端口启动：

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
- 是否提取到 Cookie
- Cookie 是否通过头条域名和登录态校验
- 是否进入 `开始调用自动化发文服务`

### 4. 发布成功但没有文章 URL

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

### 5. 触发验证码或登录异常

头条建议用 App 扫码登录。手机号验证码登录容易触发滑块，远程操作不稳定。

## 旧入口说明

项目仍保留 `auto` 依赖的原始发文和远程查看器模块：

- `api/publish/`：原发文核心服务，`auto` 会复用其中的 `PublishService`
- `tools/browser_test/`：远程浏览器查看器，`auto` 远程登录会复用 `viewer.py`
- `src/`：平台、浏览器和头条基础逻辑

当前推荐新开发和调试优先使用 `auto/`，因为它已经把远程登录、Cookie 保存、Cookie 注入和自动化发文串成一个统一流程。
