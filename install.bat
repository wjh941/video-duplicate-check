@echo off
chcp 65001 >nul
echo ============================================
echo   MP4 视频查重工具 v2.2 - 一键安装脚本
echo ============================================
echo.
echo [基础版] 仅安装查重所需依赖（约 200MB）
echo [AI增强] 额外安装 torch/CLIP/sklearn（约 2GB+）
echo.
set /p choice="请选择 [1]基础版 [2]AI增强版: "

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo.
echo [1/2] 升级 pip...
python -m pip install --upgrade pip -q

echo [2/2] 安装依赖包...
if "%choice%"=="2" (
    echo 安装基础依赖 + AI 扩展依赖...
    pip install -r requirements.txt
    pip install -r requirements_ai.txt
) else (
    echo 仅安装基础依赖...
    pip install opencv-python numpy Pillow imagehash tqdm psutil
)

echo.
echo ============================================
echo   安装完成！
echo   基础使用: python find_mp4.py --dir D:\Videos
echo   AI分析:   python find_mp4.py --dir D:\Videos --semantic
echo ============================================
pause
