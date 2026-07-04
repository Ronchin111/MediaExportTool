@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   MediaExport 控制面板
echo ========================================
echo.

:: 检测 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    echo 安装时请务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)

:: 显示版本
for /f "tokens=*" %%i in ('python --version 2^>^&1') do echo Python: %%i

:: 检测依赖
python -c "import playwright" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [提示] 未安装依赖，正在自动安装...
    echo.
    pip install playwright openpyxl
    echo.
    echo 正在安装 Chromium 浏览器内核（约 200MB）...
    python -m playwright install chromium
    echo.
)

echo.
echo 正在启动面板...
echo 浏览器将自动打开，如未打开请手动访问: http://127.0.0.1:8766
echo 按 Ctrl+C 或关闭此窗口可停止服务
echo.

:: 优先使用 pythonw.exe（无窗口），不存在则用 python.exe
where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pythonw launcher.py
) else (
    start "" python launcher.py
)

exit