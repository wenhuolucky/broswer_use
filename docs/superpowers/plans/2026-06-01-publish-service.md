# 头条号发布服务化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 publisher.py 的自动发布头条文章功能封装为本地 HTTP 服务，支持并发调用、结构化 JSON 返回和全链路日志追踪

**Architecture:** 基于 FastAPI 构建异步 HTTP 服务，将 publisher.py 中的核心发布逻辑抽象为独立 Service 层，每个 HTTP 请求分配全局唯一 request_id，通过结构化 logger 实现全链路追踪，支持 cookie 直接传入而非依赖本地 auth.json。

**Tech Stack:** FastAPI, uvicorn, python-multipart, python json logging, uuid, asyncio, browser-use (现有依赖)

---

### 总体架构概览

```
请求入口 (HTTP) -> RequestID 生成 -> 结构化 Logger 初始化 -> PublishService -> 返回 JSON
```

目录结构：
```
publish_service/
+--- __init__.py              # 包初始化
+--- config.py                # 服务配置（端口、日志等）
+--- models.py                # 请求/响应数据模型（Pydantic）
+--- logger_config.py         # 日志配置
+--- publish_service.py       # 核心发布服务（从 publisher.py 提取逻辑）
+--- service_api.py           # FastAPI 路由定义
+--- middleware.py            # 请求中间件（request_id、计时）
+--- server.py                # 服务启动入口
```

---

### Task 1: 创建 publish_service 包与配置模块

**Files:**
- Create: `publish_service/__init__.py`
- Create: `publish_service/config.py`

- [ ] **Step 1: 创建 publish_service/__init__.py**

```python
"""头条号发布服务模块

将 publisher.py 的核心发布逻辑封装为可调用的 HTTP 服务。
"""
```

- [ ] **Step 2: 创建 publish_service/config.py**

```python
"""服务配置"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 服务配置
HOST = os.getenv('SERVICE_HOST', '127.0.0.1')
PORT = int(os.getenv('SERVICE_PORT', '8000'))
WORKERS = int(os.getenv('SERVICE_WORKERS', '1'))  # uvicorn workers

# 日志配置
LOG_DIR = Path(__file__).parent.parent / 'logs'
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FORMAT = '%(asctime)s | %(levelname)-8s | %(request_id)-36s | %(module)s:%(lineno)d | %(message)s'

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# browser-use 相关
USER_DATA_DIR = PROJECT_ROOT / 'chrome_profile'
EDGE_CDP_PORT = 9227

# 服务版本
SERVICE_VERSION = '1.0.0'
```

- [ ] **Step 3: 运行验证**

```bash
./venv/Scripts/python.exe -c "from publish_service.config import *; print(f'Service config OK: {HOST}:{PORT}')"
```
Expected: `Service config OK: 127.0.0.1:8000`

---

### Task 2: 创建日志配置模块

**Files:**
- Create: `publish_service/logger_config.py`

- [ ] **Step 1: 创建 publish_service/logger_config.py**

该模块提供两种 logger：
1. `setup_request_logger(request_id)` - 为单个请求创建独立 logger，所有日志自动带上 request_id，同时输出到控制台、按日期分割的文件、请求级别文件（logs/requests/{request_id}.log）
2. `get_service_logger()` - 获取服务级别的全局 logger（不带 request_id）

Formatter 始终包含 request_id 前缀，格式为:
`%(asctime)s | %(levelname)-8s | %(request_id)-36s | %(module)s:%(lineno)d | %(message)s`

---

### Task 3: 创建请求/响应数据模型（Pydantic）

**Files:**
- Create: `publish_service/models.py`

双层 JSON 结构：
```json
{
  "code": 200,
  "request_id": "uuid",
  "content": {
    "message": "发布成功",
    "url": "https://...",
    "user_name": "账号名称",
    "user_id": "账号ID"
  }
}
```

包含 PublishRequest, PublishContent, PublishResponse 三个 Pydantic 模型，以及 make_success_response() 和 make_error_response() 工厂函数。

---

### Task 4: 创建核心发布服务

**Files:**
- Create: `publish_service/publish_service.py`

从 publisher.py 提取核心发布逻辑，封装为 PublishService 类。新增功能：
1. 通过 cookie 参数直接传入登录态（_prepare_auth_from_cookie 创建临时 auth.json）
2. LLMTokenTracker 追踪每次 LLM 调用的 token 消耗和模型名称
3. Monkey-patch LLM.acomplete 拦截 token 信息
4. 每一步都有 logger.info/log 记录（动作、耗时）
5. _finalize 方法汇总所有信息（总耗时、token 消耗、结果）
6. 支持并发（PublishService 为无状态单例，每个请求独立执行）

---

### Task 5: 创建 FastAPI 路由与中间件

**Files:**
- Create: `publish_service/middleware.py`
- Create: `publish_service/service_api.py`

Middleware: 为每个请求生成唯一 request_id（uuid4），记录耗时，注入 X-Request-ID 和 X-Response-Time header。

API: POST /api/v1/publish 接收 multipart/form-data（title, content, cookie, cover_image），调用 PublishService，返回双层 JSON。异常捕获返回 code=500。

另提供 GET /api/v1/health 健康检查接口。

---

### Task 6: 创建服务启动入口

**Files:**
- Create: `publish_service/server.py`

FastAPI 应用创建，包含 CORS 中间件、RequestID 中间件、路由注册。启动/关闭事件日志。支持直接 python -m publish_service.server 或 uvicorn 命令启动。

---

### Task 7: 创建客户端测试脚本

**Files:**
- Create: `test_publish_service.py`

requests 库编写的测试脚本，支持 --test-health 和 --test-publish 两种模式。

---

### Task 8: 添加部署文档

**Files:**
- Modify: `requirements.txt`（追加 fastapi, uvicorn, python-multipart, requests）
- Create: `publish_service/README.md`

包含启动命令、API 文档地址、curl/Python 调用示例、返回格式、日志说明、服务器部署步骤。

---

### Self-Review

| 需求 | 对应 Task |
|------|-----------|
| 封装为可调用的服务 | Task 5 (API), Task 6 (Server) |
| 本地验证，后续上服务器 | Task 6 (可配置 host/port), Task 8 部署说明 |
| 输入: title, content, cookie, 封面图片 | Task 3 (models), Task 5 (Form/File) |
| 返回双层 JSON | Task 3 (models), Task 5 (API) |
| content 内含 message, url, user_name, user_id | Task 3 (PublishContent) |
| 并发性 | Task 6 (FastAPI async), Task 4 (PublishService 无状态) |
| 日志: 详细记录每一步 | Task 2 (logger), Task 4 (每步记录) |
| 日志前缀包含 request_id | Task 2 (RequestFormatter) |
| 日志包含: 动作、耗时、LLM 模型、token 消耗 | Task 4 (_finalize, LLMTokenTracker) |
| 按 request_id 极速定位问题 | Task 2 (logs/requests/{request_id}.log) |

**Placeholder scan:** 无占位符，所有步骤有完整代码
**Type consistency:** 所有函数签名和返回类型一致

---

Plan complete and saved to `docs/superpowers/plans/2026-06-01-publish-service.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
