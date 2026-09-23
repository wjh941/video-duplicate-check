# -*- coding: utf-8 -*-
"""video_dedup.context — AppContext 全局状态单例 + 双输出日志系统。

所有可变全局状态（缓存、退出码、quiet 模式、CLIP 模型句柄）统一通过
_APP_CTX 单例管理，避免拆分后的模块间 `global` 失效问题。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    PSUTIL_AVAILABLE = False

from .constants import EXIT_OK, LOG_FILE


def repo_dir() -> str:
    """仓库根目录（原 find_mp4.py 所在目录）。

    拆分后包内模块的 ``__file__`` 指向 video_dedup/，
    所有原「脚本目录」语义（config.ini、缓存、全局忽略规则、委托脚本定位）
    必须解析到仓库根目录。刻意不叫 script_dir：原代码多处存在
    ``script_dir = ...`` 局部变量，同名辅助函数会被遮蔽。
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AppContext:
    """应用全局状态容器（单例模式）"""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        # 原 find_mp4 模块级全局变量迁移到此处
        self.global_cache: dict = {}
        self.global_exit_code: int = EXIT_OK
        self.global_results_saved: bool = False
        self.quiet_mode: bool = False
        self.semantic_stats: dict = {}
        self.log_file_handle: Optional[object] = None

        # CLIP 模型状态（默认 cpu；cli 模块加载 AI 依赖后改写）
        self.semantic_clip_model: Optional[object] = None
        self.semantic_clip_preprocess: Optional[object] = None
        self.semantic_clip_device: str = "cpu"

        # 分类相关状态（v2.5 新增）
        self.classify_results: dict = {}
        self.classify_mapping: dict = {}


# 全局上下文实例
_APP_CTX = AppContext()


def log_init(log_dir: str):
    """初始化日志系统"""
    if _APP_CTX.quiet_mode:
        return
    log_path = os.path.join(log_dir, LOG_FILE)
    try:
        _APP_CTX.log_file_handle = open(log_path, "a", encoding="utf-8")
    except (IOError, OSError):
        _APP_CTX.log_file_handle = None


def log(msg: str = "", end: str = "\n", force: bool = False):
    """双输出：控制台 + 日志文件。force=True 时忽略 quiet 模式"""
    if _APP_CTX.quiet_mode and not force:
        return
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}" if msg else ""
    print(line, end=end)
    if _APP_CTX.log_file_handle:
        _APP_CTX.log_file_handle.write(line + end)
        _APP_CTX.log_file_handle.flush()


def log_close():
    """关闭日志文件"""
    if _APP_CTX.log_file_handle:
        try:
            _APP_CTX.log_file_handle.close()
        except Exception:
            pass
        _APP_CTX.log_file_handle = None


def _get_memory_usage() -> Optional[str]:
    """获取当前进程内存占用"""
    if not PSUTIL_AVAILABLE:
        return None
    try:
        process = psutil.Process(os.getpid())
        mem = process.memory_info().rss / (1024 * 1024)
        return f"{mem:.1f} MB"
    except Exception:
        return None
