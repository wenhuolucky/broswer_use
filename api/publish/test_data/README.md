# 测试数据目录

这里存放用于 `publish_service` 联调的基础测试数据。

## 文件说明

| 文件 | 说明 |
|------|------|
| `article.md` | 测试文章，Markdown 格式 |
| `title.txt` | 测试标题 |
| `cookie_user2.json` | 登录 cookie，完整 `auth.json` 格式 |
| `cover.jpg` | 测试封面图 |

## 正文图片规则

- 如果正文需要图片，请直接在 `article.md` 中写 Markdown 网络图片：
  `![alt](https://example.com/image.png)`
- 正文图片只支持网络 URL。
- 不再支持单独上传正文图片文件。

## 使用方式

### 健康检查

```bash
curl http://127.0.0.1:8000/api/v1/health
```

### `curl` 发布测试

```bash
curl -X POST http://127.0.0.1:8000/api/v1/publish \
  -F "title=@publish_service/test_data/title.txt" \
  -F "content=@publish_service/test_data/article.md" \
  -F "cookie=<publish_service/test_data/cookie_user2.json" \
  -F "cover_image=@publish_service/test_data/cover.jpg"
```

### Python 脚本发布测试

```bash
./venv/Scripts/python.exe publish_service/test_data/run_test.py
./venv/Scripts/python.exe publish_service/test_data/run_test.py --health
./venv/Scripts/python.exe publish_service/test_data/run_test.py --publish --no-cover
```

### 旧测试脚本

```bash
./venv/Scripts/python.exe test_publish_service.py --test-publish \
  --cookie-file publish_service/test_data/cookie_user2.json \
  --cover publish_service/test_data/cover.jpg
```
