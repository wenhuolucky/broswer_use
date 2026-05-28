@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM 启动浏览器（带 CDP 调试端口，供 doubao 脚本附加）
REM 自动检测：Edge > Chrome
REM 也可在 .env 中设置 BROWSER_TYPE=chrome 指定浏览器类型
REM ============================================================

REM 优先尝试 Edge
set "EDGE_PATH=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE_PATH%" set "EDGE_PATH=C:\Program Files\Microsoft\Edge\Application\msedge.exe"

REM 尝试 Chrome
set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_PATH%" set "CHROME_PATH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_PATH%" set "CHROME_PATH=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

REM 用户数据目录（独立 profile，不影响日常浏览器）
set "USER_DATA_DIR=%~dp0..\chrome_profile_doubao"
set "CDP_PORT=9228"

echo ============================================================
echo  启动浏览器（CDP 附加模式）
echo ============================================================
echo  用户数据目录: %USER_DATA_DIR%
echo  CDP 端口: %CDP_PORT%
echo ============================================================
echo.

REM 自动检测：优先 Chrome，其次 Edge
set "BROWSER_PATH="
set "BROWSER_NAME="

if exist "%CHROME_PATH%" (
    set "BROWSER_PATH=%CHROME_PATH%"
    set "BROWSER_NAME=Chrome"
    goto :launch
)
if exist "%EDGE_PATH%" (
    set "BROWSER_PATH=%EDGE_PATH%"
    set "BROWSER_NAME=Edge"
    goto :launch
)

echo [错误] 找不到 Chrome 或 Edge 浏览器！
echo 请在 .env 中设置 BROWSER_EXECUTABLE_PATH=你的浏览器路径
pause
exit /b 1

:launch
echo 正在启动 %BROWSER_NAME% ...
echo  浏览器路径: %BROWSER_PATH%
echo.
start "" "%BROWSER_PATH%" --remote-debugging-port=%CDP_PORT% --user-data-dir="%USER_DATA_DIR%" https://www.doubao.com

echo %BROWSER_NAME% 已启动。
echo.
echo 请在浏览器中登录豆包后保持窗口打开，再运行：
echo   cd /d %~dp0..
echo   venv\Scripts\python.exe doubao\doubao_tester.py --skip-nav --loop --interval 120 --max-iterations 100
echo.
pause
