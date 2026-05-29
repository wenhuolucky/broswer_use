# 头条号自动发文 + 豆包自动化测试 (browser-use + DeepSeek)

基于 [browser-use](https://github.com/browser-use/browser-use) 和 [DeepSeek](https://platform.deepseek.com) 实现的 AI 驱动的浏览器自动化项目。包含两个独立模块：

- **头条号自动发布** — AI Agent 操作本地 Edge 浏览器，完成从登录到发布的全流程
- **豆包循环问答** — 每分钟自动向豆包发送问题，收集回复用于 AI 检测分析
- **热点文章生成** — 自动检测头条热点，LLM 两轮生成（草稿+人性化），可直接发布

## 功能

### 头条号自动发布

- **自动登录**：输入手机号后自动填写、自动完成滑块验证、短信验证码登录，登录态保存到 `toutiao_auth.json`
- **手动登录保存**：首次运行通过 Edge 浏览器手动登录头条号，自动保存 cookie 到 `auth.json`
- **AI 自动发文**：基于 DeepSeek 大模型，AI Agent 自主导航、填写标题、输入正文、处理弹窗
- **封面图片设置**：Agent 通过头条号封面设置对话框的"本地上传"功能直接上传封面图片
- **正文图片插入**：正文图片预上传到头条号 CDN，Agent 自动在正文中插入图片
- **内容文件读取**：支持从文本/Markdown 文件读取长篇文章内容
- **多用户支持**：每个用户独立浏览器配置、auth 文件和 CDP 端口

### 豆包循环问答

- **手动登录保存**：首次运行通过 Edge 浏览器手动登录豆包
- **循环问答**：自动轮换不同类型问题（事实/推理/创造/观点/技术/数学/科学/历史/生活/语言）
- **JSONL 存储**：每条问答记录按日期持久化
- **五层反检测**：启动参数去自动化标记 + 启动时 Stealth 脚本注入 + CDP 连接后二次注入 + 随机人类行为（滚动/鼠标轨迹/停留）+ 手动启动 Edge（CDP 附加模式）

### 热点文章生成

- **自动检测热点**：实时抓取头条热榜，按热度排序
- **两轮 LLM 生成**：第一轮写草稿，第二轮人性化润色（去 AI 痕迹、口语化、长短句交替）
- **反 AI 检测**：禁止模板化连接词、AI 套话，加入口语表达和设问反问
- **两种使用方式**：独立脚本生成，或通过 publisher.py 一键生成并发布

## 快速开始

### 1. 安装依赖

```bash
cd browser_use_demo
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入 DeepSeek 和 MiniMax API Key
```

前往 [DeepSeek 平台](https://platform.deepseek.com) 和 [MiniMax 平台](https://platform.minimaxi.com) 注册获取 API Key。

### 3. 头条号首次登录

```bash
python publisher.py --setup
```

打开 Edge 浏览器并跳转到头条号后台，手动完成登录后回到终端按 Enter，登录状态将保存到 `auth.json`。

### 4. 头条号自动发文

```bash
# 纯文本发布
python publisher.py --publish --title "标题" --content "正文"

# 从文件读取
python publisher.py --publish --title "标题" --content-file article.txt

# 带图片发布（第一张为封面，其余插入正文）
python publisher.py --publish --title "标题" --content-file article.txt --images cover.jpg img1.jpg img2.jpg
```

### 5. 热点文章生成

```bash
# 列出当前热点
python hot_topic_generator.py --list-topics --topic-count 10

# 生成文章（自动选 TOP1 热点）
python hot_topic_generator.py --generate --output article.json

# 指定话题索引
python hot_topic_generator.py --generate --topic-index 3

# 一键生成并发布到头条号
python publisher.py --generate-from-hot
python publisher.py --generate-from-hot --topic-index 3
```

### 6. 头条号自动登录

```bash
# 全自动登录（手机号 + 滑块验证 + 短信验证码）
python toutiao_login.py --phone 13800138000

# 交互式输入手机号
python toutiao_login.py
```

首次运行后登录态保存到 `toutiao_auth.json`，后续发文可复用此登录态。

### 7. 豆包首次登录（方案 A，脚本自动启动 Edge）

#### 方案 B：手动启动 Edge（推荐，反检测最佳）

```bash
# 1. 双击启动 Edge 浏览器
doubao\start_edge.bat

# 2. 在 Edge 中登录豆包（如未登录）

# 3. 保持 Edge 开着，运行测试
python doubao/doubao_tester.py --loop --interval 120 --max-iterations 50
```

#### 方案 A：脚本自动启动（不推荐）

```bash
# 单次测试
python doubao/doubao_tester.py --test-single --question "你好"

# 循环测试
python doubao/doubao_tester.py --loop --interval 120
```

## 目录结构

```
browser_use_demo/
├── toutiao_login.py          # 头条号自动登录脚本（手机号 + 滑块 + 短信验证码）
├── captcha_solver.py         # 滑块验证码求解模块（LLM 视觉识别 + 人类拖拽轨迹）
├── publisher.py              # 头条号主程序，发文流程控制
├── concurrent_publisher.py   # 多用户并发发布管理器
├── user_manager.py           # 多用户配置管理
├── browser_utils.py          # 共享工具（浏览器路径检测 + CDP URL 获取）
├── toutiao_uploader.py       # 头条号图片上传模块
├── hot_topic_generator.py    # 热点文章自动生成（头条热榜 + LLM 两轮生成）
├── test_concurrent_publish.py# 并发发布功能测试
├── users.yaml                # 多用户配置文件
├── requirements.txt          # Python 依赖
├── .env                      # 环境变量（API Key，勿提交）
├── .env.example              # 环境变量模板
├── .gitignore                # Git 忽略规则
│
├── browser_test/             # 远程浏览器 Cookie 采集模块
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py             # 平台配置（头条号/搜狐号等）
│   ├── cookie_store.py       # Cookie 持久化与验证
│   ├── remote_login.py       # 远程登录 CLI（本地 Chrome + Cloudflare Tunnel）
│   └── viewer.py             # CDP screencast 实时查看器
│
├── platforms/                # 平台配置模块
│   ├── __init__.py
│   ├── base.py               # 平台配置基类
│   ├── toutiao.py            # 头条号平台配置
│   └── sohu.py               # 搜狐号平台配置
│
├── users/                    # 多用户数据目录
│   ├── user1/auth.json       # 用户1 登录态
│   ├── user2/auth.json       # 用户2 登录态
│   └── user3/auth.json       # 用户3 登录态
│
├── doubao/                   # 豆包自动化测试模块
│   ├── doubao_tester.py      # 豆包循环问答主程序
│   ├── browser_session.py    # 豆包浏览器会话管理
│   ├── questions.py          # 测试问题库
│   ├── storage.py            # JSONL 数据存储
│   ├── human_behavior.py     # 人类行为模拟
│   └── start_edge.bat        # 手动启动 Edge 脚本
│
├── docs/                     # 项目文档
│   ├── 技术方案.md
│   └── 项目原理.md
│
├── test_perper/              # 文章素材目录（gitignore）
└── result/                   # 发布结果记录（gitignore）
```

## 命令行参数

### 头条号发布

| 参数 | 类型 | 说明 |
|------|------|------|
| `--setup` | flag | 打开 Edge 浏览器手动登录头条号 |
| `--publish` | flag | 使用已保存的登录状态自动发文 |
| `--title` | string | 文章标题 |
| `--content` | string | 文章正文内容 |
| `--content-file` | string | 从文件读取文章正文 |
| `--images` | string[] | 图片文件路径列表（第一张作为封面，其余插入正文） |
| `--user-id` | string | 指定用户 ID（多用户模式） |
| `--list-users` | flag | 列出所有已配置用户 |

### 豆包问答

| 参数 | 类型 | 说明 |
|------|------|------|
| `--setup` | flag | 打开 Edge 浏览器手动登录豆包 |
| `--test-single` | flag | 单次执行测试 |
| `--loop` | flag | 循环执行测试（默认间隔60秒） |
| `--question` | string | 指定测试问题 |
| `--interval` | int | 循环间隔秒数（默认60） |
| `--max-iterations` | int | 最大迭代次数（默认无限） |

## 注意事项

- **头条号要求**：需完成实名认证才能发布文章
- **浏览器**：系统需安装 Microsoft Edge 浏览器
- **auth.json 有效期**：登录 cookie 会过期，过期后需重新运行 `--setup`
- **图片格式**：支持 JPG、PNG 格式，单张不超过 20MB
- **API 费用**：每次发文会消耗 DeepSeek API token，请注意用量
- **封面上传**：browser-use 的安全机制要求封面图片路径必须在 `available_file_paths` 中注册，代码已自动处理
- **豆包反检测**：建议使用方案 B（`start_edge.bat` 手动启动 Edge），`--interval` ≥ 120 秒，避免同账号高频提问
- **验证码问题**：如触发验证码，先手动处理后保持 Edge 开着继续跑，或增加间隔时间

## 技术栈

- **browser-use** — AI 浏览器自动化框架（核心框架）
- **Playwright** — 本地 Edge 浏览器控制
- **DeepSeek** — LLM 驱动 AI Agent 操作浏览器（结构化输出）
- **MiniMax** — LLM 备用，用于非浏览器任务
- **Python** — 运行环境
- **反检测** — Stealth JS 注入（启动时 + CDP 运行时）+ 启动参数去自动化标记 + 手动启动 Edge（CDP 附加）+ 随机人类行为模拟

## License

MIT
