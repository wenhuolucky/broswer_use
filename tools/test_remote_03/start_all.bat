@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo =============================================
echo   FRP 远程浏览器 - 一键启动
echo =============================================
echo.

cd /d "%~dp0"

:: ---- 检查 frpc ----
where frpc >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 frpc，请先下载:
    echo   https://github.com/fatedier/frp/releases
    echo   下载后放到 PATH 中或当前目录
    pause
    exit /b 1
)

:: ---- 选择用户 ----
echo 请选择要启动的用户:
echo   [1] user1  (noVNC:6081, CDP:9227)
echo   [2] user2  (noVNC:6082, CDP:9237)
echo   [3] user3  (noVNC:6083, CDP:9247)
echo   [A] 全部用户
echo   [Q] 退出
echo.
set /p CHOICE=请输入选择 (1/2/3/A/Q): 

if /i "%CHOICE%"=="Q" exit /b 0
if /i "%CHOICE%"=="A" (
    set "FRPC_CONFIG=client\frpc_all.toml"
    set "COMPOSE_PROFILES=user1,user2,user3"
    set "START_USER=all"
)
if "%CHOICE%"=="1" (
    set "FRPC_CONFIG=client\frpc_user1.toml"
    set "START_USER=user1"
)
if "%CHOICE%"=="2" (
    set "FRPC_CONFIG=client\frpc_user2.toml"
    set "START_USER=user2"
)
if "%CHOICE%"=="3" (
    set "FRPC_CONFIG=client\frpc_user3.toml"
    set "START_USER=user3"
)

if not defined FRPC_CONFIG (
    echo [错误] 无效选择
    pause
    exit /b 1
)

:: ---- Step 1: 启动 Docker 容器 ----
echo.
echo [1/3] 启动 Docker 容器...
cd docker

if "%START_USER%"=="all" (
    docker-compose up -d --build
) else (
    docker-compose up -d --build %START_USER%
)

if %errorlevel% neq 0 (
    echo [错误] Docker 启动失败
    cd ..
    pause
    exit /b 1
)

echo [1/3] 等待容器就绪...
timeout /t 8 /nobreak >nul
cd ..

:: ---- Step 2: 启动 frpc ----
echo [2/3] 启动 FRP 客户端...
start "FRP-Client" cmd /c "frpc -c %~dp0%FRPC_CONFIG%"
timeout /t 3 /nobreak >nul
echo [2/3] FRP 客户端已启动

:: ---- Step 3: 启动登录监控 ----
echo [3/3] 启动登录监控...
echo.
echo =============================================
echo   所有服务已启动!
echo.
if "%START_USER%"=="all" (
    echo   user1: http://VPS_IP:6801/vnc.html
    echo   user2: http://VPS_IP:6802/vnc.html
    echo   user3: http://VPS_IP:6803/vnc.html
) else (
    echo   远程访问: http://VPS_IP:68%CHOICE%1/vnc.html
    echo   本地访问: http://localhost:608%CHOICE%/vnc.html
)
echo.
echo   Ctrl+Space 切换中文输入法
echo =============================================
echo.

:: 运行登录监控
if "%START_USER%"=="all" (
    echo [提示] 多用户模式，请手动运行监控:
    echo   venv\Scripts\python.exe test_remote_03\python_bridge\monitor.py --user user1
    echo   venv\Scripts\python.exe test_remote_03\python_bridge\monitor.py --user user2
    echo   venv\Scripts\python.exe test_remote_03\python_bridge\monitor.py --user user3
    echo.
    echo 按任意键退出（Docker 和 FRP 继续运行）...
    pause >nul
) else (
    cd /d "%~dp0\..\.."
    venv\Scripts\python.exe test_remote_03\python_bridge\monitor.py --user %START_USER%
)

echo.
echo 按任意键停止所有服务...
pause >nul
call "%~dp0stop_all.bat"