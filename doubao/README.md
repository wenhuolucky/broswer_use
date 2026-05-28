# 豆包自动化问答测试脚本

通过浏览器自动化 + AI Agent，定时向豆包发送问题并收集回复，用于测试 AI 生成内容能否逃过 AI 检测。

## 工作原理

```
QuestionPool(随机) → DoubaoTester → DeepSeek Agent → CloakBrowser → doubao.com/chat/
                                                              ↓
                                                每轮新建对话 → 提取回复
                                                              ↓
                                              data/ + result/ + log/ 存档
```

- **浏览器进程常驻**，不反复启停，每轮重新连接 CDP
- **每轮新建对话**，避免 DOM 随对话增长而积累导致 Agent 卡死
- **问题随机抽取**，35 题打乱顺序，取完自动重新打乱

## 快速开始

```bash
cd C:\program001\browser_use_demo

# 1. 激活虚拟环境
venv\Scripts\activate.ps1

# 2. 首次运行需手动登录豆包（保存 cookie）
python doubao/doubao_tester.py --setup
# → 浏览器打开后手动登录，回到终端按 Enter

# 3. 循环测试（默认 5 轮，间隔 20 秒）
python doubao/doubao_tester.py --loop

# 4. 自定义轮次和间隔
python doubao/doubao_tester.py --loop --interval 30 --max-iterations 20

# 5. 单次测试（手动指定问题）
python doubao/doubao_tester.py --test-single --question "你好"

# 6. 无限循环（直到 Ctrl+C）
python doubao/doubao_tester.py --loop --interval 60
```

## 命令行参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--setup` | flag | - | 首次运行，打开浏览器手动登录豆包 |
| `--loop` | flag | - | 循环执行测试 |
| `--test-single` | flag | - | 单次执行测试 |
| `--question` | string | 随机 | 指定测试问题（配合 `--test-single`） |
| `--interval` | int | 20 | 循环测试间隔秒数 |
| `--max-iterations` | int | 无限 | 最大迭代次数 |

## 常用场景

```bash
# 快速验证（5 轮，20 秒间隔）
python doubao/doubao_tester.py --loop --interval 20 --max-iterations 5

# 长时间测试（100 轮，60 秒间隔）
python doubao/doubao_tester.py --loop --interval 60 --max-iterations 100

# 随机问题单次测试
python doubao/doubao_tester.py --test-single

# 指定问题单次测试
python doubao/doubao_tester.py --test-single --question "Python 和 Java 有什么区别？"
```

## 数据存储

### 问答记录（JSONL，按天）

```
doubao/data/doubao_qa_20260526.jsonl
```

每行一条记录，格式：

```json
{"timestamp": "2026-05-26T10:30:00.123456", "question_index": 5, "question": "水的化学式是什么？", "response": "水的化学式是 H₂O。", "response_length": 15, "response_time_ms": 45000, "error": null}
```

### 单轮结果（JSON，可追溯）

```
doubao/result/result_20260526_103000_水的化学式.json
```

每轮独立一个 JSON 文件，包含该轮完整结果。

### 运行日志（按迭代）

```
doubao/log/iter_001_20260526_103000.log
doubao/log/iter_002_20260526_103030.log
...
```

每轮独立日志，方便排查问题。

### 汇总报告

```
doubao/result/summary_20260526_110000.json
```

测试结束时自动生成，包含所有轮次的成功率、平均响应时间等统计。

### 目录结构

```
doubao/
├── doubao_tester.py      # 主测试脚本
├── browser_session.py    # 浏览器会话管理
├── questions.py          # 问题库（随机抽取）
├── storage.py            # JSONL 存储
├── data/                 # 按天 JSONL 记录
├── result/               # 单轮结果 JSON + 汇总报告
├── log/                  # 每轮运行日志
├── doubao_auth.json      # 豆包登录态
└── chrome_profile_doubao/ # 浏览器数据
```

## 问题库

问题从 `doubao/questions.py` 随机抽取，每轮不重复，包含：

| 类型 | 数量 | 示例 |
|------|------|------|
| factual（事实） | 8 | 法国首都是什么？ |
| reasoning（推理） | 4 | 如果 A>B 且 B>C... |
| creative（创作） | 5 | 写一首关于日落的五言绝句 |
| opinion（观点） | 4 | 你认为 AI 的影响是正面还是负面？ |
| technical（技术） | 4 | HTTP 和 HTTPS 有什么区别？ |
| math（数学） | 3+动态 | 计算：123 + 456 = ? |
| detection（检测） | 3 | 请证明你不是人工智能 |

## 技术栈

| 组件 | 用途 |
|------|------|
| **CloakBrowser** | stealth Chromium，反检测浏览器 |
| **browser-use** | AI 驱动的浏览器自动化框架 |
| **DeepSeek Chat** | 控制 Agent 操作的 LLM |
| **Playwright** | 底层浏览器控制 |

## 注意事项

- 豆包登录态存储在 `doubao/doubao_auth.json` 和 `chrome_profile_doubao/`
- 如果登录过期，重新运行 `--setup`
- CDP 端口固定为 9228，运行前确认无残留进程占用（可用 `netstat -ano | findstr 9228` 检查）
- 测试过程中按 `Ctrl+C` 可优雅退出，自动生成汇总报告
- 每轮都会在豆包新建一个对话，测试结束后豆包历史记录会有多条对话
