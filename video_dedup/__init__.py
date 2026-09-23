# -*- coding: utf-8 -*-
"""video_dedup — MP4 视频相似度查重核心包。

自 find_mp4.py v2.8 按职责拆分：

    constants   全局常量 / 输出文件名 / 错误码 / 退出码
    context     AppContext 全局状态单例 + 日志系统
    utils       路径/格式化/原子写入等小工具
    scanner     目录扫描 + 文件发现 + ignore 规则 + 时长/分辨率/低质过滤
    cache       哈希缓存（JSON/SQLite、合并、校验、过期清理、增量进度）
    hasher      pHash/dHash 计算 + MD5/SHA-256 + 音频哈希 + 带缓存提取
    compare     LSH 分桶 + 相似度比对 + 候选对生成
    grouper     连通图分组 + 最低组内相似度拆分 + 保留策略
    reporter    CSV/TXT/MD/HTML/XLSX 导出 + 扫描摘要 + 终端汇总
    cleanup     安全清理：计划生成/校验/执行/恢复/过期清理
    cli         argparse 入口 + 子命令分发 + 主扫描流程

兼容入口仍是仓库根目录的 find_mp4.py（瘦壳，聚合导出全部公开名字）。
"""
import os as _os
import sys as _sys

# 控制台编码加固：英文 Windows 默认 cp1252，直接 print 中文会 UnicodeEncodeError。
# 统一把 stdout/stderr 换成 UTF-8 输出（无控制台句柄或不可写时静默跳过）。
for _stream in (_sys.stdout, _sys.stderr):
    try:
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

# 保证仓库根目录在 sys.path 中：兄弟模块 ai_semantic.py / label_verify.py
# 仍是可独立运行的顶层脚本，包内以顶层模块名导入它们。
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

from .constants import __version__  # noqa: E402,F401

__all__ = ["__version__"]
