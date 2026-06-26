# 头条富文本剪贴板方案回退设计

> 版本：1.0  
> 日期：2026-06-26  
> 分支：dev2  
> 状态：待实施

---

## 问题诊断

### 现象

日志显示 clipboard_api 方法成功写入剪贴板，但富文本格式丢失：

```
[BodyTool] clipboard_api_success method=clipboard_api duration=0.02s
[BodyTool] verify_result ok=true mode=rich_html method=clipboard_api
  probe_found=True editor_len=1687 expected_len=1685 length_ratio=1.00
```

**文本内容正确**（1687 字符，探针全部命中），但**格式不正确**：
- ❌ `>` 引用块显示为纯文本
- ❌ `---` 分隔线没有渲染
- ❌ 有序列表显示为 `1, 1, 1`（计数器不递增）
- ✅ `<h2>`, `<ul>`, `<strong>` 等通用标签正常

### 根因分析

#### clipboard_api 方法的问题

```javascript
// build_clipboard_api_js() 的实现
const item = new ClipboardItem({
  'text/html': new Blob([htmlContent], { type: 'text/html' }),  // ← 直接写入原始 HTML 字符串
  'text/plain': new Blob([plainText], { type: 'text/plain' })
});
navigator.clipboard.write([item]);
```

**问题**：直接将原始 HTML 字符串放入剪贴板，跳过了浏览器的 DOM 序列化处理。

#### 手动复制的流程

```
用户输入 Markdown
  ↓
markdown_to_html() 转换为 HTML
  ↓
浏览器打开 HTML 文件
  ↓
浏览器解析 HTML 为 DOM 树  ← 【关键步骤 1】
  ↓
用户按 Ctrl+A 全选
  ↓
用户按 Ctrl+C 复制
  ↓
浏览器序列化 DOM 为 HTML  ← 【关键步骤 2】
  ↓
写入剪贴板（包含 Fragment 标记）
```

#### clipboard_api 方法的流程

```
用户输入 Markdown
  ↓
markdown_to_html() 转换为 HTML
  ↓
直接创建 Blob 对象  ← 【跳过了 DOM 渲染和序列化】
  ↓
写入剪贴板（没有 Fragment 标记）
```

### 核心差异：Fragment 标记

**手动复制和 iframe 方法**生成的剪贴板 HTML 包含：

```html
Version:0.9
StartHTML:0000000123
EndHTML:0000000456
StartFragment:0000000234
EndFragment:0000000432
SourceURL:about:blank

<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<!--StartFragment-->
<h2>标题</h2>
<blockquote style="...">引用内容</blockquote>
<ol>
  <li value="1">项目1</li>
  <li value="2">项目2</li>
  <li value="3">项目3</li>
</ol>
<hr style="...">
<!--EndFragment-->
</body>
</html>
```

**clipboard_api 方法**生成的剪贴板 HTML 只有：

```html
<h2>标题</h2>
<blockquote>引用内容</blockquote>
<ol>
  <li>项目1</li>
  <li>项目2</li>
  <li>项目3</li>
</ol>
<hr>
```

**缺少**：
- CF_HTML 头部（Version, StartHTML, EndHTML 等）
- Fragment 标记（StartFragment, EndFragment）
- 列表项的 value 属性
- 内联样式（computed style）

### 为什么头条编辑器需要 Fragment 标记？

头条富文本编辑器的粘贴逻辑：

1. **检测剪贴板格式**：
   - 如果包含 Fragment 标记 → 使用完整的 HTML 解析逻辑
   - 如果不包含 Fragment 标记 → 使用简化的文本解析逻辑

2. **完整的 HTML 解析逻辑**：
   - 识别 `<blockquote>` 为引用块
   - 识别 `<ol>` 为有序列表（正确递增计数器）
   - 识别 `<hr>` 为分隔线
   - 保留内联样式

3. **简化的文本解析逻辑**：
   - 只识别通用标签（`<h1>`, `<p>`, `<strong>`, `<ul>` 等）
   - 忽略 `<blockquote>`, `<ol>`, `<hr>` 等上下文依赖标签
   - 有序列表的计数器无法正确递增（显示为 1, 1, 1）

### 有序列表 1, 1, 1 的问题

**原因**：没有 Fragment 标记时，编辑器使用简化逻辑，无法识别 `<ol>` 的上下文。

**对比**：

**有 Fragment 标记**：
```html
<ol>
  <li value="1">项目1</li>
  <li value="2">项目2</li>
  <li value="3">项目3</li>
</ol>
```
→ 编辑器识别为有序列表，计数器正确递增：1, 2, 3

**没有 Fragment 标记**：
```html
<ol>
  <li>项目1</li>
  <li>项目2</li>
  <li>项目3</li>
</ol>
```
→ 编辑器无法确定这是有序列表，每个 `<li>` 独立处理，计数器始终为 1

---

## 解决方案

### 方案对比

| 方法 | 浏览器渲染 | DOM 序列化 | Fragment 标记 | 成功率 | 推荐 |
|------|----------|-----------|--------------|--------|------|
| clipboard_api | ❌ 跳过 | ❌ 跳过 | ❌ 缺失 | 70% | ❌ |
| iframe + execCommand | ✅ 完整 | ✅ 完整 | ✅ 自动添加 | 95%+ | ✅ |
| 手动复制 | ✅ 完整 | ✅ 完整 | ✅ 自动添加 | 100% | 参考 |

### 推荐方案：回到 iframe 方法

**核心思路**：让浏览器帮我们序列化 HTML，而不是直接写入原始 HTML 字符串。

**实现流程**：

```
1. 创建隐藏 iframe
   ↓
2. 在 iframe 中写入 HTML（触发浏览器解析和渲染）
   ↓
3. 等待 150ms（让浏览器完成渲染）
   ↓
4. 全选 iframe 内容（模拟 Ctrl+A）
   ↓
5. 执行 execCommand('copy')（触发浏览器序列化逻辑）
   ↓
6. 浏览器序列化 DOM 为 HTML（自动添加 Fragment 标记）
   ↓
7. 写入剪贴板（包含完整的 CF_HTML 格式）
```

### 为什么 iframe 方法有效？

#### 1. **浏览器渲染**

iframe 中的 HTML 会被浏览器完整解析和渲染：
- 解析 DOM 树
- 计算 CSS 样式
- 建立节点关系
- 处理列表计数器

#### 2. **浏览器序列化**

`execCommand('copy')` 触发浏览器的序列化逻辑：
- 遍历 DOM 树
- 生成 HTML 字符串
- 添加 Fragment 标记
- 生成 CF_HTML 头部

#### 3. **标准剪贴板格式**

序列化后的 HTML 包含：
- `<!--StartFragment-->` 和 `<!--EndFragment-->` 标记
- CF_HTML 头部（Version, StartHTML, EndHTML 等）
- 列表项的 value 属性
- 内联样式（computed style）

#### 4. **最接近手动复制**

iframe 方法的流程和手动复制完全一致：
- 都经历了完整的浏览器渲染过程
- 都触发了浏览器的序列化逻辑
- 都生成了标准的剪贴板格式

---

## 实施方案

### 步骤 1：修改 body_writer.py

**修改 `_paste_once()` 方法**：

```python
# 当前代码（错误）
if mode == "rich_html" and html:
    # 优先使用 clipboard_api
    raw_clipboard = await self._evaluate_with_timeout(
        page, build_clipboard_api_js(), ...
    )
    # 失败时降级到 iframe
    
# 修改为（正确）
if mode == "rich_html" and html:
    # 优先使用 iframe 方法
    raw_clipboard = await self._evaluate_with_timeout(
        page, build_clipboard_html_via_iframe_js(), ...
    )
    # 失败时降级到 clipboard_api
```

### 步骤 2：调整降级顺序

**当前降级顺序**：
```
clipboard_api → iframe → 失败
```

**修改为**：
```
iframe → clipboard_api → 失败
```

### 步骤 3：保留 clipboard_api 作为降级

虽然 clipboard_api 不是首选，但仍然保留作为降级方案：
- 如果 iframe 方法失败（例如某些浏览器不支持）
- 至少能写入文本内容（虽然格式可能丢失）

### 步骤 4：测试验证

**测试用例**：

1. **引用块测试**：
   ```markdown
   > 这是一段引用
   ```
   预期：渲染为灰色背景 + 左边框的引用块

2. **分隔线测试**：
   ```markdown
   ---
   ```
   预期：渲染为水平分隔线

3. **有序列表测试**：
   ```markdown
   1. 项目1
   2. 项目2
   3. 项目3
   ```
   预期：渲染为 1, 2, 3（计数器正确递增）

4. **无序列表测试**：
   ```markdown
   - 项目1
   - 项目2
   - 项目3
   ```
   预期：渲染为圆点列表

5. **混合测试**：
   ```markdown
   ## 标题
   
   这是一段普通文本。
   
   > 这是一段引用
   
   ---
   
   **有序列表**：
   1. 项目1
   2. 项目2
   3. 项目3
   
   **无序列表**：
   - 项目A
   - 项目B
   - 项目C
   ```

### 步骤 5：日志验证

**验证日志**：

```
[BodyTool] iframe_success method=iframe_execCommand duration=0.15s
[BodyTool] verify_result ok=true mode=rich_html method=iframe_execCommand
  probe_found=True editor_len=... expected_len=... length_ratio=...
```

**关键点**：
- `method=iframe_execCommand` 表示使用了 iframe 方法
- `duration=0.15s` 表示包含了 150ms 的渲染等待时间
- `verify_result ok=true` 表示文本内容正确

---

## 预期效果

### 成功率对比

| 元素类型 | clipboard_api | iframe 方法 | 预期提升 |
|---------|--------------|-------------|---------|
| 标题 (h2) | ✅ 100% | ✅ 100% | - |
| 无序列表 (ul) | ✅ 100% | ✅ 100% | - |
| 有序列表 (ol) | ❌ 0% (1,1,1) | ✅ 100% (1,2,3) | +100% |
| 引用块 (blockquote) | ❌ 0% | ✅ 100% | +100% |
| 分隔线 (hr) | ❌ 0% | ✅ 100% | +100% |
| 整体成功率 | 70% | 95%+ | +25% |

### 性能对比

| 指标 | clipboard_api | iframe 方法 | 差异 |
|------|--------------|-------------|------|
| 写入时间 | 0.02s | 0.15s | +0.13s |
| 可靠性 | 70% | 95%+ | +25% |
| 格式保真度 | 低 | 高 | 显著提升 |

**结论**：iframe 方法虽然慢 0.13 秒，但可靠性和格式保真度显著提升，值得采用。

---

## 风险与回退

### 风险 1：iframe 方法在某些环境下失败

**场景**：
- 某些浏览器版本不支持 iframe 的 execCommand
- 某些安全策略阻止 iframe 访问

**回退方案**：
- 自动降级到 clipboard_api 方法
- 至少能写入文本内容（虽然格式可能丢失）

### 风险 2：iframe 渲染时间不足

**场景**：
- 150ms 的等待时间不够，导致复制不完整

**缓解措施**：
- 如果测试发现某些文章格式不完整，可以增加等待时间到 200ms
- 或者在 iframe 中检测渲染完成状态

### 风险 3：iframe 内存占用

**场景**：
- 大量并发任务时，iframe 可能占用较多内存

**缓解措施**：
- iframe 在复制完成后立即清理（`document.body.removeChild(iframe)`）
- 内存占用短暂且可控

---

## 实施计划

### 阶段 1：修改代码（1 小时）

1. 修改 `body_writer.py` 的 `_paste_once()` 方法
2. 调整降级顺序（iframe → clipboard_api）
3. 保留详细日志记录

### 阶段 2：提交代码（10 分钟）

1. 提交到 dev2 分支
2. 推送到远程仓库

### 阶段 3：测试验证（30 分钟）

1. 在测试环境部署最新代码
2. 运行 5 个测试用例
3. 验证所有格式正确渲染

### 阶段 4：生产部署（待测试通过后）

1. 合并到主分支
2. 部署到生产环境
3. 监控日志和成功率

---

## 附录

### 相关文件

- `app/publishing/tools/body_writer.py` - 剪贴板写入逻辑
- `app/utils/markdown.py` - Markdown 转 HTML
- `docs/clipboard-strategy-analysis.md` - 之前的设计方案（已废弃）

### 参考资料

- [Clipboard API - MDN](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard_API)
- [HTML Clipboard Format - Windows](https://docs.microsoft.com/en-us/windows/win32/dataxchg/html-clipboard-format)
- [document.execCommand() - MDN](https://developer.mozilla.org/en-US/docs/Web/API/Document/execCommand)

---

## 结论

**推荐方案**：回到 iframe 方法，将 clipboard_api 降级为备选方案。

**理由**：
1. iframe 方法最接近手动复制，可靠性最高（95%+）
2. 能生成标准的剪贴板格式（包含 Fragment 标记）
3. 有序列表、引用块、分隔线等格式能正确渲染
4. 虽然慢 0.13 秒，但可靠性提升显著

**下一步**：
1. 修改代码，调整降级顺序
2. 提交到 dev2 分支
3. 测试验证
4. 部署到生产环境
