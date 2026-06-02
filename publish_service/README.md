# 头条号发布服务

把 `publisher.py` 的自动发布能力封装成 HTTP 服务，支持调用发布接口并记录全链路日志。

## 快速启动

```bash
./venv/Scripts/python.exe -m publish_service.server --reload
```

生产模式：

```bash
./venv/Scripts/python.exe -m uvicorn publish_service.server:app --host 127.0.0.1 --port 8000
```

启动后访问 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

## 接口

### `POST /api/v1/publish`

发布文章到头条号。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | form string | 是 | 文章标题 |
| `content` | form string | 是 | 文章正文，支持普通文本或 Markdown |
| `cookie` | form string | 是 | 登录 cookie，支持 JSON 或原始 cookie 字符串 |
| `cover_image` | file | 否 | 封面图片文件 |

### 正文图片规则

- 正文图片只支持 Markdown 中的网络 URL，例如 `![alt](https://example.com/a.png)`。
- 服务会把 Markdown 直接转换成 HTML 富文本，然后通过剪贴板粘贴到头条编辑器。
- 不再支持通过 `body_images` 或其它单独上传字段添加正文图片。

### `GET /api/v1/health`

健康检查接口。

## 返回格式

```json
{
  "code": 200,
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "content": {
    "message": "Publish successful",
    "url": "https://www.toutiao.com/article/xxx",
    "user_name": "账号名称",
    "user_id": "账号ID",
    "operation_time": "2026-06-01 10:00:00",
    "article_title": "文章标题"
  }
}
```

## 调用示例

### `curl`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/publish \
  -F "title=Test" \
  -F "content=# Title\n\n![Alt](https://example.com/image.png)" \
  -F 'cookie={"cookies":[...]}' \
  -F "cover_image=@cover.jpg"
```

### Python

```python
import requests

resp = requests.post(
    "http://127.0.0.1:8000/api/v1/publish",
    data={
        "title": "Article Title",
        "content": "# Title\n\n![Alt](https://example.com/image.png)",
        "cookie": '{"cookies": [...]}',
    },
    files={"cover_image": open("cover.jpg", "rb")},
)
print(resp.json())
```

## 日志

日志目录：`logs/`

- `logs/publish_service_YYYY-MM-DD.log`：按日期滚动的全量日志
- `logs/requests/{request_id}.log`：单个请求的完整链路日志

## 部署说明

1. 安装依赖：`pip install -r requirements.txt`
2. 配置 `.env` 文件，例如 `DEEPSEEK_API_KEY`
3. 启动服务：`uvicorn publish_service.server:app --host 0.0.0.0 --port 8000`

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SERVICE_HOST` | `127.0.0.1` | 监听地址 |
| `SERVICE_PORT` | `8000` | 监听端口 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
