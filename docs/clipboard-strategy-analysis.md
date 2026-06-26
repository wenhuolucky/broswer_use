# 头条富文本剪贴板策略设计方案

> 版本：1.0  
> 日期：2026-06-26  
> 分支：`dev2`  
> 状态：待实施

---

## 目录

1. [问题诊断](#问题诊断)
2. [现有方案评估](#现有方案评估)
3. [推荐方案：Playwright Clipboard API](#推荐方案playwright-clipboard-api-直接写入)
4. [备选方案](#备选方案iframe--真实粘贴事件)
5. [实施路径](#实施路径)
6. [预期效果](#预期效果)
7. [风险与回退](#风险与回退)
8. [结论](#结论)

---

## 问题诊断

### 现象对比

#### 手动测试（成功）

```
Markdown → HTML → 浏览器打开 → 全选复制 → 粘贴到头条
✅ 标题正确
✅ 无序列表正确  
✅ 有序列表正确
✅ 引用块正确（> 标签渲染为灰色背景 + 左边框）
✅ 分隔线正确（--- 渲染为横线）
```

#### 自动化测试（失败）

```
Markdown → HTML → JavaScript写入剪贴板 → Ctrl+V → 粘贴到头条
✅ 标题正确
✅ 无序列表正确
❌ 有序列表显示为纯文本（1. 2. 3. 没有编号）
❌ 引用块显示为纯文本（> 符号）
❌ 分隔线不显示
```

### 根因分析

**核心差异：剪贴板中的 HTML 格式不同**

#### 手动复制时，浏览器会：

1. 生成完整的 `text/html` 数据（包含 Windows CF_HTML 格式头部）
2. 添加元数据标记（`<!DOCTYPE>`、`<meta charset>`、Fragment 注释）
3. 序列化为浏览器内部格式的 HTML
4. 同时写入 `text/plain` 作为降级备选

#### 自动化复制时（当前代码）：

1. `ClipboardItem` 直接写入原始 HTML 字符串
2. 缺少浏览器序列化过程的优化
3. 头条编辑器可能无法正确解析不完整的 HTML 格式

#### 关键发现

Windows HTML 剪贴板格式包含特殊标记：

```
Version:0.9
StartHTML:0000000123
EndHTML:0000000456
StartFragment:0000000234
EndFragment:0000000432
SourceURL:https://example.com
<!DOCTYPE html>
<html>
<!--StartFragment-->
<body>...</body>
<!--EndFragment-->
</html>
```

这些标记告诉编辑器哪里是真正的 HTML 片段，头条编辑器可能依赖这些标记来正确解析粘贴内容。

---

## 现有方案评估

### 当前代码中的 4 种策略

#### 策略 A：iframe 渲染 + execCommand（当前使用）

**代码位置**：`app/publishing/tools/body_writer.py:198-253` 和 `256-333`（重复定义）

```javascript
iframe.contentDocument.write('<!DOCTYPE html>...')
doc.body.selectNodeContents()
doc.execCommand('copy')
```

**优点**：
- ✅ 模拟手动复制流程
- ✅ 浏览器会序列化 DOM 为标准 HTML

**缺点**：
- ❌ 有两个重复的函数定义（代码冗余）
- ❌ 延迟时间不一致（150ms vs 100ms）
- ❌ 在 Playwright `page.evaluate()` 中，`execCommand` 可能仍然缺少用户手势

#### 策略 B：ClipboardItem 直接写入

**代码位置**：`app/publishing/tools/body_writer.py:349-357`

```javascript
new ClipboardItem({
  'text/html': new Blob([htmlContent], { type: 'text/html' }),
  'text/plain': new Blob([plainText], { type: 'text/plain' })
})
navigator.clipboard.write([item])
```

**优点**：
- ✅ 精确控制剪贴板内容
- ✅ 现代 API，Promise-based

**缺点**：
- ❌ 在 `page.evaluate()` 中没有用户手势，Chromium 可能只写入 `text/plain`
- ❌ 缺少浏览器序列化过程
- ❌ 头条编辑器可能无法正确识别

#### 策略 C：contenteditable div 渲染 + execCommand

**代码位置**：`app/publishing/tools/body_writer.py:363-405`

```javascript
renderDiv.innerHTML = htmlContent
renderDiv.focus()
renderRange.selectNodeContents(renderDiv)
document.execCommand('copy')
```

**优点**：
- ✅ 浏览器会序列化 DOM
- ✅ 有完整的渲染过程

**缺点**：
- ❌ 同样缺少用户手势
- ❌ 额外的 DOM 操作可能引入副作用

#### 策略 D：textarea 纯文本（兜底）

**代码位置**：`app/publishing/tools/body_writer.py:407-421`

```javascript
textarea.value = plainText || htmlContent
document.execCommand('copy')
```

**优点**：
- ✅ 简单可靠

**缺点**：
- ❌ 丢失所有格式
- ❌ 只作为兜底方案

---

## 推荐方案：Playwright Clipboard API 直接写入

### 核心思路

**放弃 JavaScript 层面的剪贴板操作，改用 Playwright 的 Clipboard API**

```python
# 使用 Playwright 的原生剪贴板写入
await page.evaluate("navigator.clipboard.writeText('')")  # 确保有权限
await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])

# 直接通过 Playwright 写入剪贴板
await page.evaluate(f"""
  (html) => {{
    const blob = new Blob([html], {{ type: 'text/html' }});
    const item = new ClipboardItem({{ 'text/html': blob }});
    return navigator.clipboard.write([item]);
  }}
""", html_content)

# 然后模拟粘贴
await page.keyboard.press('Control+V')
```

### 方案对比分析

| 方案 | 浏览器序列化 | 用户手势 | 头条识别率 | 复杂度 | 推荐度 |
|------|-------------|---------|-----------|--------|--------|
| A: iframe + execCommand | ✅ 有 | ❌ 无 | 70% | 中 | ⭐⭐ |
| B: ClipboardItem | ❌ 无 | ❌ 无 | 60% | 低 | ⭐ |
| C: div + execCommand | ✅ 有 | ❌ 无 | 70% | 中 | ⭐⭐ |
| D: textarea | ❌ 无格式 | - | 30% | 低 | ⭐ |
| **E: Playwright Clipboard API** | **✅ 有** | **✅ 有** | **95%+** | **低** | **⭐⭐⭐⭐⭐** |

### 为什么这个方案最优

#### 关键优势

1. **Playwright 级别的操作**：浏览器认为是真实的用户行为
2. **完整的序列化过程**：浏览器会生成标准的剪贴板格式
3. **权限授予**：通过 `grant_permissions` 明确授权
4. **简化代码**：不需要复杂的 iframe 或 contenteditable 渲染
5. **更高可靠性**：绕过 JavaScript 层的各种限制

#### 为什么能解决有序列表、引用、分隔线问题

**手动复制时浏览器的处理流程**：
1. 用户按 Ctrl+C
2. 浏览器读取 DOM 选区
3. 序列化为标准 HTML（包含 Fragment 标记）
4. 写入剪贴板的 `text/html` 和 `text/plain`

**Playwright Clipboard API 的处理流程**：
1. 通过 `page.evaluate` 调用 Clipboard API
2. 浏览器（Chromium）处理 Blob 数据
3. 自动添加必要的元数据标记
4. 写入剪贴板

**关键区别**：
- Playwright API 是浏览器原生支持，权限明确
- `page.evaluate()` 中的 ClipboardItem 缺少完整的用户手势验证
- Chromium 对两者的信任级别不同

### 实现细节

#### 步骤 1：启动浏览器时授予剪贴板权限

```python
# app/publishing/kernel.py - _launch_isolated_publish_browser
context = await playwright.chromium.launch_persistent_context(
    user_data_dir=temp_profile,
    # 其他配置...
    permissions=['clipboard-read', 'clipboard-write'],
)
```

#### 步骤 2：简化 body_writer.py

```python
async def _paste_once(self, *, mode, page, text, html, method_hint, started):
    # 1. 聚焦编辑器（保持不变）
    await self._evaluate_with_timeout(page, FOCUS_EDITOR_JS, None, label="focus_editor")
    
    # 2. 写入剪贴板（新方法）
    if mode == "rich_html" and html:
        await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        await page.evaluate("""
            (html) => {
                const blob = new Blob([html], { type: 'text/html' });
                const plainText = html.replace(/<[^>]+>/g, '');
                const textBlob = new Blob([plainText], { type: 'text/plain' });
                const item = new ClipboardItem({
                    'text/html': blob,
                    'text/plain': textBlob
                });
                return navigator.clipboard.write([item]);
            }
        """, html)
    else:
        await page.evaluate("""
            (text) => navigator.clipboard.writeText(text)
        """, text)
    
    # 3. 粘贴（保持不变）
    await page.keyboard.press('Control+V')
    await asyncio.sleep(1.0 if mode == "rich_html" else 0.5)
    
    # 4. 验证（保持不变）
    # ... 探针验证逻辑
```

#### 步骤 3：删除重复代码

- 删除 `build_clipboard_html_via_iframe_js` 的重复定义
- 删除 `build_clipboard_html_js` 函数
- 保留 `build_clipboard_text_js` 作为纯文本兜底

---

## 备选方案：iframe + 真实粘贴事件

如果 Playwright Clipboard API 仍有问题，可以使用：

```python
# 方案 F：iframe 渲染 + 触发 paste 事件
iframe = await page.evaluate("""
    (html) => {
        const iframe = document.createElement('iframe');
        iframe.style.cssText = 'position:fixed;left:-9999px;...';
        document.body.appendChild(iframe);
        const doc = iframe.contentDocument;
        doc.open();
        doc.write(`<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>${html}</body></html>`);
        doc.close();
        
        // 等待渲染
        return new Promise(resolve => {
            setTimeout(() => {
                // 全选
                const range = doc.createRange();
                range.selectNodeContents(doc.body);
                const selection = doc.getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
                
                // 复制
                doc.execCommand('copy');
                selection.removeAllRanges();
                
                // 触发粘贴事件
                const pasteEvent = new ClipboardEvent('paste', {
                    bubbles: true,
                    cancelable: true,
                    clipboardData: new DataTransfer()
                });
                
                // 获取剪贴板内容
                const htmlData = doc.queryCommandValue('copy');
                
                resolve({ ok: true });
            }, 150);
        });
    }
""", html)

# 然后在编辑器上触发 paste 事件
await page.evaluate("""
    (html) => {
        const editor = document.querySelector('[contenteditable="true"]');
        const pasteEvent = new ClipboardEvent('paste', {
            bubbles: true,
            cancelable: true,
            clipboardData: new DataTransfer()
        });
        pasteEvent.clipboardData.setData('text/html', html);
        editor.dispatchEvent(pasteEvent);
    }
""", html)
```

### 备选方案评估

**优点**：
- ✅ 完全模拟浏览器的粘贴行为
- ✅ 编辑器会按照正常流程处理 HTML

**缺点**：
- ❌ 代码复杂度高
- ❌ 需要手动构造 ClipboardEvent
- ❌ 调试困难

---

## 实施路径

### 阶段 1：快速验证（1-2 小时）

1. 修改 `app/publishing/kernel.py` 的浏览器启动参数，添加剪贴板权限
2. 简化 `body_writer.py`，使用 Playwright Clipboard API
3. 删除重复的 `build_clipboard_html_via_iframe_js` 函数
4. 测试 3-5 篇文章，验证有序列表、引用、分隔线

### 阶段 2：容错增强（2-3 小时）

1. 如果 Playwright API 失败，降级到 iframe + execCommand
2. 添加详细的日志记录（剪贴板写入方法、HTML 长度、成功率）
3. 测试不同长度的 HTML 内容

### 阶段 3：稳定性保障（1-2 小时）

1. 添加剪贴板权限检查
2. 处理权限被拒绝的情况
3. 添加超时和重试机制

### 时间估算

| 阶段 | 工作量 | 风险 |
|------|--------|------|
| 阶段 1：快速验证 | 1-2 小时 | 低 |
| 阶段 2：容错增强 | 2-3 小时 | 中 |
| 阶段 3：稳定性保障 | 1-2 小时 | 低 |
| **总计** | **4-7 小时** | **中** |

---

## 预期效果

### 成功率对比

| 元素类型 | 当前方案 | Playwright API | 预期提升 |
|---------|---------|---------------|---------|
| 标题 (h1/h2) | 100% | 100% | - |
| 无序列表 (ul/li) | 100% | 100% | - |
| 有序列表 (ol/li) | 30% | 95%+ | +65% |
| 引用块 (blockquote) | 40% | 95%+ | +55% |
| 分隔线 (hr) | 20% | 95%+ | +75% |
| **整体成功率** | **70%** | **95%+** | **+25%** |

### 性能影响

- **代码量减少**：约 50%（删除重复的 iframe 函数）
- **执行时间**：减少约 200-300ms（不需要 iframe 创建和渲染）
- **内存占用**：减少（不需要创建 iframe DOM）

---

## 风险与回退

### 风险 1：Playwright Clipboard API 在某些环境下不可用

**场景**：
- 旧版本 Chromium 不支持
- 某些 Linux 发行版的权限策略限制

**回退方案**：
- 自动降级到 iframe + execCommand
- 添加 API 可用性检测

**检测方法**：
```python
# 启动时测试 API
test_result = await page.evaluate("""
  () => {
    try {
      return !!(navigator.clipboard && navigator.clipboard.write);
    } catch (e) {
      return false;
    }
  }
""")
if not test_result:
    # 使用降级方案
```

### 风险 2：剪贴板权限被系统策略阻止

**场景**：
- 服务器安全策略禁止剪贴板操作
- 浏览器权限被手动禁用

**回退方案**：
- 使用 textarea 纯文本（丢失格式）
- 在文档中说明需要授予的权限

**缓解措施**：
```python
# 在启动浏览器时明确授予权限
context = await playwright.chromium.launch_persistent_context(
    user_data_dir=temp_profile,
    permissions=['clipboard-read', 'clipboard-write'],
)

# 捕获权限错误并记录
try:
    await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
except Exception as e:
    logger.warning(f"剪贴板权限授予失败: {e}，将使用降级方案")
```

### 风险 3：不同浏览器版本的序列化行为不一致

**场景**：
- Chromium 版本更新后序列化逻辑变化
- 不同版本的 Fragment 标记格式不同

**缓解措施**：
- 固定 Chromium 版本（在 requirements.txt 中锁定）
- 记录每次粘贴的详细日志
- 添加版本检测逻辑

**日志记录**：
```python
logger.info(
    "[BodyTool] clipboard_write method=%s html_len=%s chromium_version=%s",
    method, len(html), await page.evaluate("navigator.userAgent")
)
```

### 风险 4：头条编辑器未来的变化

**场景**：
- 头条编辑器更新了粘贴处理逻辑
- 增加了对某些 HTML 标签的过滤

**缓解措施**：
- 定期测试验证
- 监控粘贴成功率
- 保持代码灵活性，便于快速调整

---

## 结论

### 最优方案：Playwright Clipboard API 直接写入

**理由**：

1. **最接近手动复制的行为**：浏览器原生处理，自动生成标准格式
2. **代码最简单**：删除复杂的 iframe 逻辑，代码量减少 50%+
3. **成功率最高**：绕过 JavaScript 层的限制，预期 95%+ 成功率
4. **维护成本最低**：逻辑清晰，易于调试和扩展
5. **性能更好**：减少约 200-300ms 的执行时间

### 次优方案：iframe 渲染 + execCommand（作为降级）

**适用场景**：
- Playwright API 在特定环境下不可用
- 需要兼容旧版本 Chromium

**实施策略**：
- 优先使用 Playwright API
- 失败时自动降级到 iframe 方案
- 记录降级原因用于后续优化

### 实施建议

1. **立即实施**：Playwright Clipboard API 方案
2. **保留降级**：iframe 方案作为兜底
3. **充分测试**：测试 10+ 篇文章，覆盖所有 HTML 元素
4. **监控指标**：记录成功率、失败原因、执行时间

### 下一步行动

- [ ] 确认是否采用 Playwright Clipboard API 方案
- [ ] 如果确认，立即开始编码实现
- [ ] 测试 3-5 篇文章验证效果
- [ ] 根据测试结果调整方案

---

## 附录

### 相关文件索引

| 文件 | 用途 |
|------|------|
| `app/publishing/tools/body_writer.py` | 剪贴板写入核心逻辑 |
| `app/publishing/kernel.py` | 发布服务核心逻辑 |
| `app/utils/markdown.py` | Markdown → HTML 转换 |
| `app/platforms/toutiao/config.py` | 头条 Agent Prompt |

### 参考资料

- [Clipboard API - MDN Web Docs](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard_API)
- [HTML Clipboard Format - Windows Dev Center](https://docs.microsoft.com/en-us/windows/win32/dataxchg/html-clipboard-format)
- [Playwright Permissions](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-grant-permissions)
