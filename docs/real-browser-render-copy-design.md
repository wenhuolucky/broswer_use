# 真实浏览器渲染复制方案设计

> 版本：1.0  
> 日期：2026-06-26  
> 状态：待实施

---

## 一、问题背景

### 1.1 当前问题

头条富文本粘贴后，以下元素无法正确渲染：
- ❌ 有序列表：显示为 1,1,1（不是 1,2,3）
- ❌ 引用块：显示为纯文本
- ❌ 分隔线：不显示

### 1.2 失败方案回顾

| 方案 | 失败原因 |
|------|---------|
| `clipboard_api` | `navigator.clipboard.write()` 在 `page.evaluate()` 中无用户手势，Chromium 跳过浏览器序列化，剪贴板缺少 Fragment 标记 |
| `iframe + execCommand` | `execCommand('copy')` 在 `page.evaluate()` 中无用户手势，即使返回 `ok: true`，也只写入 `text/plain` |
| 手动 Fragment 标记 | 浏览器序列化时可能吞掉或改写这些注释 |

### 1.3 手动测试为什么成功

手动测试流程：
```
1. 浏览器打开 HTML 文件 → 真实渲染上下文
2. Ctrl+A 全选 → 真实键盘事件，有用户手势
3. Ctrl+C 复制 → 真实键盘事件，有用户手势
4. 粘贴到头条 → 完美渲染
```

**关键区别**：Ctrl+A、Ctrl+C 是真实的键盘事件，浏览器识别为用户手势，允许完整的剪贴板写入（包含 Fragment 标记、CF_HTML 头部）。

---

## 二、方案设计

### 2.1 核心思路

**不在 `page.evaluate()` 中操作剪贴板，而是：**
1. 让浏览器真正打开 HTML 文件（新标签页）
2. 通过 Playwright 的 `keyboard.press()` 模拟真实键盘事件（Ctrl+A, Ctrl+C）
3. 浏览器识别为用户手势，执行完整的剪贴板写入
4. 关闭临时标签页，回到头条页面粘贴（Ctrl+V）

### 2.2 方案对比

| 方案 | 与手动测试一致性 | 实现复杂度 | 稳定性 | 推荐度 |
|------|-----------------|-----------|--------|--------|
| 新标签页 file:// 渲染 | ★★★★★ | 低 | 高 | ✅ 推荐 |
| 注入 contenteditable | ★★★☆☆ | 高 | 中 | ⭐⭐ |
| clipboard_api（当前） | ★☆☆☆☆ | 低 | 低 | ❌ |
| iframe + execCommand（当前） | ★★☆☆☆ | 中 | 低 | ❌ |

### 2.3 选择理由

**新标签页方案完全复刻手动测试流程**：
- ✅ 真实的渲染上下文（浏览器打开真实 HTML 文件）
- ✅ 真实的用户手势（Playwright `keyboard.press()` 模拟键盘事件）
- ✅ 浏览器自然生成标准 CF_HTML（包含 Fragment 标记）
- ✅ 头条编辑器应该能完美识别

---

## 三、技术实现

### 3.1 实现流程

```
┌─────────────────────────────────────────────────────────┐
│ 1. 准备 HTML 文件                                         │
│    - 生成临时 HTML 文件（包含 <!DOCTYPE> 和 <meta charset>）│
│    - 写入 rich_html 内容                                  │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ 2. 打开新标签页渲染                                       │
│    - context.newPage()                                   │
│    - new_page.goto('file:///tmp/xxx.html')               │
│    - 等待渲染完成（networkidle + 额外延迟）               │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ 3. 模拟真实键盘复制                                       │
│    - new_page.click('body') 聚焦                         │
│    - new_page.keyboard.press('Control+a') 全选           │
│    - new_page.keyboard.press('Control+c') 复制           │
│    - 等待剪贴板写入完成                                   │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ 4. 清理临时资源                                           │
│    - new_page.close() 关闭临时标签页                     │
│    - os.unlink(temp_file) 删除临时文件                   │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ 5. 回到头条页面粘贴                                       │
│    - 确保头条编辑器有焦点                                 │
│    - page.keyboard.press('Control+v') 粘贴               │
│    - 验证粘贴结果                                         │
└─────────────────────────────────────────────────────────┘
```

### 3.2 代码结构

```python
async def _paste_once(self, *, mode: str, page, text: str, html: str, ...) -> dict:
    """粘贴正文到编辑器"""
    
    # 1. 聚焦编辑器（原有逻辑）
    focus_result = await self._focus_editor(page)
    if not focus_result['ok']:
        return make_body_write_failure(...)
    
    # 2. 富文本模式：使用真实浏览器渲染复制
    if mode == "rich_html" and html:
        clipboard_result = await self._paste_via_real_browser(page, html, text)
    else:
        # 纯文本模式：使用原有 clipboard_api
        clipboard_result = await self._paste_via_clipboard_api(page, text)
    
    if not clipboard_result['ok']:
        return make_body_write_failure(...)
    
    # 3. 粘贴到编辑器（原有逻辑）
    await page.keyboard.press('Control+v')
    await asyncio.sleep(1.0)
    
    # 4. 验证粘贴结果（原有逻辑）
    return await self._verify_paste_result(page, ...)
```

### 3.3 核心方法：`_paste_via_real_browser()`

```python
async def _paste_via_real_browser(self, page: Page, html: str, text: str) -> dict:
    """通过真实浏览器渲染复制富文本"""
    
    temp_file = None
    new_page = None
    
    try:
        # 1. 生成临时 HTML 文件
        html_doc = self._build_full_html(html)
        temp_file = self._create_temp_html_file(html_doc)
        
        # 2. 打开新标签页渲染
        context = page.context
        new_page = await context.new_page()
        await new_page.goto(f'file://{temp_file.name}')
        await new_page.wait_for_load_state('networkidle')
        await asyncio.sleep(0.5)  # 额外等待渲染完成
        
        # 3. 授予剪贴板权限（file:// 协议）
        await context.grant_permissions(
            ['clipboard-write'],
            origin=f'file://{temp_file.name}'
        )
        
        # 4. 模拟真实键盘复制
        await new_page.click('body')
        await new_page.keyboard.press('Control+a')
        await asyncio.sleep(0.2)
        await new_page.keyboard.press('Control+c')
        await asyncio.sleep(0.5)  # 等待剪贴板写入完成
        
        return {
            'ok': True,
            'method': 'real_browser_copy',
            'html_length': len(html),
            'temp_file': temp_file.name
        }
        
    except Exception as e:
        self._warning(f'[BodyTool] real_browser_copy failed: {e}')
        return {
            'ok': False,
            'method': 'real_browser_copy',
            'error': str(e)
        }
        
    finally:
        # 5. 清理资源
        if new_page:
            await new_page.close()
        if temp_file and os.path.exists(temp_file.name):
            os.unlink(temp_file.name)


def _build_full_html(self, html_content: str) -> str:
    """构建完整的 HTML 文档"""
    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>富文本预览</title>
</head>
<body>
{html_content}
</body>
</html>'''


def _create_temp_html_file(self, html_doc: str) -> tempfile.NamedTemporaryFile:
    """创建临时 HTML 文件"""
    temp_file = tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.html',
        encoding='utf-8',
        delete=False
    )
    temp_file.write(html_doc)
    temp_file.close()
    return temp_file
```

### 3.4 保留降级方案

```python
async def _paste_via_clipboard_api(self, page: Page, text: str) -> dict:
    """降级方案：使用 clipboard_api 写入纯文本"""
    # 原有的 clipboard_api 逻辑
    ...
```

**注意**：clipboard_api 只用于纯文本模式，富文本模式统一使用真实浏览器渲染复制。

---

## 四、关键技术点

### 4.1 剪贴板权限

**问题**：新标签页是 `file://` 协议，`clipboard-write` 权限是在 `https://mp.toutiao.com` 上授予的。

**解决方案**：
```python
await context.grant_permissions(
    ['clipboard-write'],
    origin=f'file://{temp_file.name}'
)
```

**备选方案**：如果 `file://` 权限授予失败，可以在打开新标签页前先授予权限：
```python
await context.grant_permissions(['clipboard-write'], origin='file://')
```

### 4.2 跨标签页焦点

**问题**：复制完成后需要确保头条页面的编辑器仍然有焦点，否则 Ctrl+V 会粘贴到错误的地方。

**解决方案**：
```python
# 关闭临时标签页后，显式聚焦头条编辑器
await new_page.close()
await page.bring_to_front()  # 确保头条页面在前台
await page.click(editor_selector)  # 聚焦编辑器
```

### 4.3 图片可访问性

**问题**：HTML 中的图片 URL 必须是新标签页可以访问的（网络 URL）。

**检查**：当前 `markdown.py` 生成的 HTML 中图片都是网络 URL（`http://...` 或 `https://...`），应该没问题。

**验证**：
```python
# 检查 HTML 中的图片 URL
import re
img_urls = re.findall(r'<img[^>]+src="([^"]+)"', html_content)
for url in img_urls:
    if not url.startswith(('http://', 'https://', '//')):
        self._warning(f'Non-network image URL: {url}')
```

### 4.4 临时文件清理

**问题**：测试完成后需要删除临时 HTML 文件。

**解决方案**：使用 `try/finally` 确保清理：
```python
try:
    # 打开、复制、粘贴
    ...
finally:
    if temp_file and os.path.exists(temp_file.name):
        os.unlink(temp_file.name)
```

### 4.5 并发场景

**问题**：如果多个发布任务并发，剪贴板会互相覆盖。

**风险**：剪贴板是全局的，**不能并发执行富文本写入**。

**解决方案**：
- 方案1：在发布队列中串行化富文本粘贴步骤
- 方案2：使用文件锁或进程锁
- 方案3：每次粘贴之间加随机延迟，降低冲突概率

**推荐**：方案1（串行化）最安全，但会降低吞吐量。当前项目并发量不大，可以先不处理。

---

## 五、边界情况和异常处理

### 5.1 临时文件创建失败

```python
try:
    temp_file = self._create_temp_html_file(html_doc)
except Exception as e:
    return {'ok': False, 'error': f'Failed to create temp file: {e}'}
```

### 5.2 新标签页打开失败

```python
try:
    new_page = await context.new_page()
    await new_page.goto(f'file://{temp_file.name}')
except Exception as e:
    return {'ok': False, 'error': f'Failed to open new page: {e}'}
```

### 5.3 渲染超时

```python
try:
    await new_page.wait_for_load_state('networkidle', timeout=10000)
except TimeoutError:
    self._warning('Page load timeout, continuing anyway')
```

### 5.4 剪贴板写入失败

**检测**：通过读取剪贴板内容验证：
```python
# 读取剪贴板内容（需要额外权限）
clipboard_content = await new_page.evaluate('''
    async () => {
        return await navigator.clipboard.readText();
    }
''')
if not clipboard_content:
    self._warning('Clipboard write may have failed')
```

**注意**：读取剪贴板需要 `clipboard-read` 权限，且只能读取 `text/plain`。对于 `text/html`，无法直接验证。

### 5.5 粘贴失败

**验证**：通过探针检测粘贴是否成功：
```python
editor_text = await self._get_editor_text(page)
if probe_text not in editor_text:
    return {'ok': False, 'error': 'Paste failed, probe not found'}
```

---

## 六、性能影响

### 6.1 时间开销

| 步骤 | 耗时 | 说明 |
|------|------|------|
| 生成临时文件 | ~10ms | 写入 HTML 到磁盘 |
| 打开新标签页 | ~200-500ms | 浏览器加载文件 |
| 等待渲染 | ~500ms | networkidle + 额外延迟 |
| 模拟键盘事件 | ~700ms | Ctrl+A + Ctrl+C + 延迟 |
| 关闭标签页 | ~100ms | 释放资源 |
| **总计** | **~1.5s** | 比原方案慢约 1s |

### 6.2 资源开销

- **内存**：临时标签页占用额外内存（~10-50MB）
- **磁盘**：临时 HTML 文件（~10-100KB）
- **CPU**：渲染 HTML（短暂增加）

### 6.3 优化空间

- 减少渲染等待时间：从 500ms 降到 300ms（需要测试验证）
- 复用临时文件：使用固定文件名，避免每次创建新文件
- 异步清理：关闭标签页和删除文件在后台执行

---

## 七、实施步骤

### 步骤 1：修改 `_paste_once()` 方法（30 分钟）

- 添加 `_paste_via_real_browser()` 方法
- 修改 `_paste_once()` 的分支逻辑
- 保留原有的 `clipboard_api` 作为降级方案

### 步骤 2：添加权限授予逻辑（15 分钟）

- 在打开新标签页前授予 `file://` 协议的剪贴板权限
- 测试权限授予是否成功

### 步骤 3：实现真实浏览器复制（1 小时）

- 实现 `_paste_via_real_browser()` 方法
- 处理临时文件创建、清理
- 模拟键盘事件（Ctrl+A, Ctrl+C）
- 添加详细日志记录

### 步骤 4：焦点管理（30 分钟）

- 确保关闭临时标签页后，头条编辑器仍然有焦点
- 测试 Ctrl+V 粘贴是否成功

### 步骤 5：测试验证（1 小时）

- 测试 1-2 篇文章
- 验证有序列表、引用块、分隔线是否正确渲染
- 检查日志中是否使用 `real_browser_copy` 方法

### 步骤 6：提交代码（15 分钟）

- 提交到 dev2 分支
- 推送到远程仓库

**总耗时**：约 4 小时

---

## 八、预期效果

### 8.1 功能验证

修改后，头条编辑器应该能正确渲染：
- ✅ 有序列表：1, 2, 3（不是 1, 1, 1）
- ✅ 引用块：显示为灰色背景 + 左边框
- ✅ 分隔线：正确渲染为水平线
- ✅ 所有其他富文本元素

### 8.2 日志验证

```
[BodyTool] real_browser_copy_start html_length=5614
[BodyTool] temp_file_created path=/tmp/xxx.html
[BodyTool] new_page_opened url=file:///tmp/xxx.html
[BodyTool] page_rendered duration=0.5s
[BodyTool] clipboard_permission_granted origin=file:///tmp/xxx.html
[BodyTool] keyboard_copy_simulated keys=Control+a,Control+c
[BodyTool] real_browser_copy_success duration=1.5s
[BodyTool] paste_simulated key=Control+v
[BodyTool] verify_result ok=true method=real_browser_copy probe_found=True
```

### 8.3 成功率预期

| 元素类型 | 当前成功率 | 预期成功率 | 提升 |
|---------|-----------|-----------|------|
| 有序列表 | 0% | 100% | +100% |
| 引用块 | 0% | 100% | +100% |
| 分隔线 | 0% | 100% | +100% |
| **整体** | **0%** | **100%** | **+100%** |

---

## 九、风险与回退

### 9.1 风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| `file://` 权限授予失败 | 中 | 无法复制 | 尝试不同的权限授予方式 |
| 新标签页打开失败 | 低 | 无法复制 | 添加重试逻辑 |
| 渲染超时 | 低 | 复制不完整 | 增加超时时间 |
| 剪贴板写入失败 | 中 | 粘贴失败 | 添加剪贴板内容验证 |
| 焦点丢失 | 中 | 粘贴到错误位置 | 显式聚焦编辑器 |
| 并发冲突 | 低 | 剪贴板覆盖 | 串行化富文本粘贴 |

### 9.2 回退方案

如果新方案失败，可以回退到：
1. **降级到 clipboard_api**：虽然富文本格式丢失，但至少能粘贴纯文本
2. **回退到 iframe 方法**：虽然可能仍然失败，但代码已经实现

---

## 十、总结

### 10.1 核心优势

**真实浏览器渲染复制方案完全复刻手动测试流程**：
- ✅ 真实的渲染上下文
- ✅ 真实的用户手势
- ✅ 浏览器自然生成标准 CF_HTML
- ✅ 头条编辑器应该能完美识别

### 10.2 下一步行动

1. **立即实施**：按照上述步骤实施新方案
2. **充分测试**：测试 3-5 篇文章，验证所有富文本元素
3. **监控日志**：记录每次复制的详细信息
4. **根据测试结果调整**：如果仍然失败，分析日志并调整参数

### 10.3 关键成功因素

- **真实的用户手势**：Playwright `keyboard.press()` 必须被浏览器识别为用户手势
- **正确的权限授予**：`file://` 协议必须有 `clipboard-write` 权限
- **焦点管理**：关闭临时标签页后，头条编辑器必须有焦点
- **充分等待**：渲染和剪贴板写入需要足够的等待时间

---

## 附录

### A. 相关文件

- `app/publishing/tools/body_writer.py` - 剪贴板写入逻辑
- `app/utils/markdown.py` - Markdown 转 HTML
- `docs/clipboard-strategy-analysis.md` - 之前的设计方案

### B. 参考资料

- [Playwright Keyboard API](https://playwright.dev/python/docs/api/class-keyboard)
- [Playwright Page API](https://playwright.dev/python/docs/api/class-page)
- [Clipboard API - MDN](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard_API)
- [HTML Clipboard Format - Windows](https://docs.microsoft.com/en-us/windows/win32/dataxchg/html-clipboard-format)

### C. 测试用例

**测试文章**：包含有序列表、引用块、分隔线的 Markdown 文章

**预期结果**：
- 有序列表显示为 1, 2, 3
- 引用块显示为灰色背景 + 左边框
- 分隔线正确渲染为水平线

**验证方法**：
- 查看日志中的 `method=real_browser_copy`
- 检查头条页面的实际渲染效果
- 对比手动测试的结果
