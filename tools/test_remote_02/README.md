# noVNC 远程浏览器 Cookie 采集方案

基于 Docker + Xvfb + Chrome + x11vnc + noVNC 的远程浏览器方案，支持中文输入。

## 架构

```
┌───────────────────────────────────┐
│  Docker 容器                       │
│                                   │
│  Chrome (CDP:9222) ──→ Xvfb (:99) │
│                              │    │
│  fcitx (中文输入)        x11vnc     │
│                          │        │
│                    websockify     │
│                          │        │
│                      noVNC :6080  │
└──────────────────────────┼────────┘
                           │
              Cloudflare Tunnel
                           │
              ┌────────────▼────────────┐
              │  用户浏览器              │
              │  noVNC Web 客户端        │
              │  (点击链接直接操作)       │
              └─────────────────────────┘
```

## 前置要求

- Docker Desktop for Windows（需启用 WSL2 后端）
- cloudflared（用于暴露到公网）

## 一键启动

```bash
# 双击或命令行运行
test_remote_02\start_all.bat
```

会自动完成：
1. 构建并启动 Docker 容器
2. 启动 Cloudflare Tunnel（公网链接显示在新窗口）
3. 启动登录监控（等待用户登录后自动提取 cookies）

## 手动启动

### 1. 构建并启动

```bash
cd test_remote_02
docker-compose up -d --build
```

### 2. 启动 Cloudflare Tunnel

```bash
cloudflared tunnel --url http://localhost:6080
```

### 3. 运行登录监控

```bash
cd C:/program001/browser_use_demo
venv/Scripts/python.exe test_remote_02/python_bridge/monitor.py
```

### 4. 停止

```bash
test_remote_02\stop_all.bat
# 或手动
cd test_remote_02 && docker-compose down
```

## 中文输入

远程桌面中按 **Ctrl+Space** 切换中英文输入法（Google 拼音）。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RESOLUTION` | `1280x800x24` | 虚拟显示器分辨率 |
| `START_URL` | `https://mp.toutiao.com/auth/page/login` | Chrome 启动时打开的 URL |

## 端口说明

| 端口 | 用途 |
|------|------|
| 6080 | noVNC Web 界面 |
| 9222 | Chrome CDP 调试端口 |
| 5900 | VNC 原始端口（通常不需要直接访问） |
