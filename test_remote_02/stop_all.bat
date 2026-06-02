@echo off
echo Stopping all services...
cd /d "%~dp0"
docker-compose down
taskkill /FI "WINDOWTITLE eq CloudflareTunnel" /F >nul 2>&1
echo All services stopped.
pause
