@echo off
chcp 65001 >nul
echo ============================================
echo   MP4 视频查重工具 v2.3 - AI 依赖安装脚本
echo ============================================
echo.
echo  本脚本将安装：
echo    - 基础查重依赖（opencv, numpy, Pillow, imagehash 等）
echo    - AI 语义分析依赖（torch, open-clip, scikit-learn 等）
echo.
echo  注意事项：
echo    - torch 包体积较大（CPU版约 200MB，GPU版约 2GB+）
echo    - 如无 NVIDIA GPU，将安装 CPU 版本（速度较慢但功能完整）
echo    - 已配置国内镜像源加速下载
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 检查 pip
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [错误] pip 不可用，请升级 Python 或手动安装 pip
    pause
    exit /b 1
)

REM v2.3 新增：国内镜像源配置
echo [0/4] 配置国内 pip 镜像源...
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
pip config set install.trusted-host pypi.tuna.tsinghua.edu.cn

echo [1/4] 升级 pip...
python -m pip install --upgrade pip -q

echo.
echo [2/4] 安装基础查重依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 基础依赖安装失败
    pause
    exit /b 1
)

echo.
echo [3/4] 安装 AI 扩展依赖...
echo       正在下载 torch（首次安装可能需要几分钟）...
pip install -r requirements_ai.txt
if errorlevel 1 (
    echo.
    echo [警告] 部分依赖安装失败
    echo        可尝试手动安装：
    echo          pip install torch --index-url https://download.pytorch.org/whl/cpu
    echo          pip install open-clip-torch scikit-learn
    echo.
    echo        如无 GPU，请使用 CPU 版本：
    echo          pip install torch==2.0.0 --index-url https://download.pytorch.org/whl/cpu
    pause
)

echo.
echo [4/4] 验证安装...
echo.

python -c "import torch; print(f'  PyTorch: {torch.__version__} | CUDA: {\"可用\" if torch.cuda.is_available() else \"不可用(CPU模式)\"}')" 2>nul
if errorlevel 1 (
    echo  PyTorch: 未安装
)

python -c "import open_clip; print(f'  open_clip: 已安装')" 2>nul
if errorlevel 1 (
    echo  open_clip: 未安装
)

python -c "import sklearn; print(f'  scikit-learn: {sklearn.__version__}')" 2>nul
if errorlevel 1 (
    echo  scikit-learn: 未安装
)

python -c "import cv2; print(f'  OpenCV: {cv2.__version__}')" 2>nul
if errorlevel 1 (
    echo  OpenCV: 未安装
)

echo.
echo ============================================
echo   安装完成！v2.3 新功能：
echo ============================================
echo.
echo  快速开始：
echo    python find_mp4.py --dir D:\Videos              # 基础查重
echo    python find_mp4.py --dir D:\Videos --semantic   # 开启 AI 分析
echo    python find_mp4.py semantic-analyze --dir D:\Videos    # 纯 AI 分析
echo    python find_mp4.py dataset-filter --purpose 监控 --dir D:\Videos  # 筛选数据集
echo    python find_mp4.py cluster-scene --dir D:\Videos  # 场景聚类
echo    python find_mp4.py dataset-split --dir D:\Videos   # 数据集分类拆分(v2.3)
echo    python find_mp4.py clear-semantic-cache           # 清理AI缓存(v2.3)
echo.
echo  查看帮助：
echo    python find_mp4.py --help
echo    python find_mp4.py version
echo ============================================
pause