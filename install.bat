@echo off
chcp 65001 >nul
echo ============================================
echo   MP4 视频查重工具 - 一键安装脚本
echo ============================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/2] 升级 pip...
python -m pip install --upgrade pip -q

echo [2/2] 安装依赖包...
pip install opencv-python numpy Pillow imagehash tqdm psutil

echo.
echo ============================================
echo   安装完成！
echo   使用方法: python find_mp4.py --dir D:\Videos
echo ============================================
pause
