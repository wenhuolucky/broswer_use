@echo off
chcp 65001 >nul 2>&1
echo 正在停止所有服务...

cd /d "%~dp0"

echo [1/3] 停止 FRP 客户端...
taskkill /FI "WINDOWTITLE eq FRP-Client*" /F >nul 2>&1
echo [1/3] OK

echo [2/3] 停止 Docker 容器...
cd docker
docker-compose down 2>nul
cd ..
echo [2/3] OK

echo [3/3] 清理...
echo [3/3] OK

echo.
echo 所有服务已停止。
pause