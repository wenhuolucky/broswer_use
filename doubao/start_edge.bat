@echo off
chcp 65001 >nul
setlocal

set "EDGE_PATH=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE_PATH%" set "EDGE_PATH=C:\Program Files\Microsoft\Edge\Application\msedge.exe"

REM 使用独立的用户数据目录（不影响日常 Edge，可同时运行两个 Edge 实例）
set "USER_DATA_DIR=%~dp0..\chrome_profile_doubao"
set "CDP_PORT=9228"

echo ============================================================
echo  启动 Edge（CDP 附加模式）
echo ============================================================
echo  Edge 路径: %EDGE_PATH%
echo  用户数据目录: %USER_DATA_DIR%
echo  CDP 端口: %CDP_PORT%
echo ============================================================
echo.

if not exist "%EDGE_PATH%" (
    echo [错误] 找不到 Edge 浏览器
    pause
    exit /b 1
)

echo 正在启动 Edge ...
echo.
start "" "%EDGE_PATH%" --remote-debugging-port=%CDP_PORT% --user-data-dir="%USER_DATA_DIR%" https://www.doubao.com

echo Edge 已启动。
echo.
echo 请在 Edge 中登录豆包后保持窗口打开，再运行：
echo   cd /d %~dp0..
echo   venv\Scripts\python.exe doubao\doubao_tester.py --loop --interval 120 --max-iterations 100
echo.
pause
