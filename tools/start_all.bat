@echo off

echo =============================================
echo   noVNC Remote Browser - Start All
echo =============================================
echo.

cd /d "%~dp0"

echo [1/3] Starting container...
cd test_remote_02
docker-compose up -d
if %errorlevel% neq 0 (
    echo [ERROR] docker-compose failed, trying build...
    docker-compose up -d --build
    if %errorlevel% neq 0 (
        echo [FATAL] Docker failed completely.
        pause
        exit /b 1
    )
)

echo [1/3] Waiting for container...
timeout /t 5 /nobreak >nul

echo [1/3] Waiting for noVNC...
:wait_novnc
curl -s --max-time 2 http://localhost:6080/vnc.html >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 2 /nobreak >nul
    goto wait_novnc
)
echo [1/3] OK - container ready

echo [2/3] Starting Cloudflare Tunnel...
start "CloudflareTunnel" cmd /c "cloudflared tunnel --url http://localhost:6080"
echo [2/3] OK - check the new window for public URL

echo [3/3] Starting login monitor (runs inside Docker)...
echo.
echo =============================================
echo   All services started!
echo   Local: http://localhost:6080/vnc.html
echo   Public: see Cloudflare Tunnel window
echo   Input: Ctrl+Space for Chinese
echo =============================================
echo.

docker exec novnc-browser python3 /output/monitor.py

echo.
echo Press any key to stop...
pause >nul

cd test_remote_02
docker-compose down
taskkill /FI "WINDOWTITLE eq CloudflareTunnel" /F >nul 2>&1
echo Done.
pause
