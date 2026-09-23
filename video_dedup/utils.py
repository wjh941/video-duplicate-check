# -*- coding: utf-8 -*-
"""video_dedup — 由 find_mp4.py v2.8 按职责拆分生成的模块。"""
from __future__ import annotations

import argparse
import base64
import configparser
import csv
import fnmatch
import glob as _glob
import io
import json
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from collections import defaultdict
from concurrent.futures import (ThreadPoolExecutor, as_completed, wait,
                                FIRST_COMPLETED,
                                TimeoutError as FutureTimeout)
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
from PIL import Image
import imagehash

try:  # tqdm 可选
    from tqdm import tqdm as _tqdm
    TQDM_AVAILABLE = True
except ImportError:  # pragma: no cover
    TQDM_AVAILABLE = False

try:  # psutil 可选
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    PSUTIL_AVAILABLE = False

from .constants import *  # noqa: F401,F403



# ============================================================
# 模块 3：文件扫描与过滤
# ============================================================
def _resolve_path(path_str: str) -> str:
    """路径转绝对路径，支持 Windows 长路径（v2.3 统一使用 _normalize_path）"""
    return _normalize_path(path_str)


def _parse_size_str(size_str: str) -> Optional[int]:
    """解析 '100MB', '50GB' 等格式为字节数"""
    if not size_str:
        return None
    size_str = size_str.strip().upper()
    units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    for unit, factor in units.items():
        if size_str.endswith(unit):
            try:
                return int(float(size_str[:-len(unit)]) * factor)
            except ValueError:
                return None
    try:
        return int(size_str)
    except ValueError:
        return None




def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


# ============================================================
# 模块 4：哈希缓存管理（版本化 + 分块 + 合并/校验）
# ============================================================
def _normalize_path(path_str: str) -> str:
    """统一路径存储规则：转绝对路径 + Windows 长路径 \\?\ 前缀（v2.3 修复路径匹配失效）"""
    try:
        resolved = str(Path(path_str).resolve())
    except Exception:
        resolved = os.path.abspath(path_str)
    # Windows 长路径兼容（>248 字符时添加 \\?\ 前缀）
    # 【v2.6 修复】支持 UNC 网络路径（\\server\share → \\?\UNC\server\share）
    if sys.platform == "win32" and len(resolved) > 248:
        if resolved.startswith("\\\\?\\") or resolved.startswith("\\\\?\\UNC\\"):
            pass  # 已有前缀，不重复添加
        elif resolved.startswith("\\\\"):
            # UNC 网络路径：\\server\share → \\?\UNC\server\share
            resolved = "\\\\?\\UNC\\" + resolved[2:]
        else:
            resolved = "\\\\?\\" + resolved
    return resolved




# ============================================================
# 模块 8：结果导出
# ============================================================
def _atomic_write_text(file_path: str, content: str, encoding: str = "utf-8"):
    """
    原子写入文本文件（v2.4 新增）。
    #【改造注释】所有导出文件先写入 xxx.tmp，完整无报错后 os.replace 原子替换，
    防止中途断电/中断生成损坏半截文件。
    """
    tmp_path = file_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp_path, file_path)
    except (IOError, OSError) as e:
        # 清理残留 tmp
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise e


def _atomic_write_lines(file_path: str, lines: list, encoding: str = "utf-8",
                        newline: str = ""):
    """
    原子写入多行文本（v2.4 新增）。
    适配 open(..., "w") 的 writelines 调用风格。
    """
    tmp_path = file_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding=encoding, newline=newline) as f:
            f.writelines(lines)
        os.replace(tmp_path, file_path)
    except (IOError, OSError) as e:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise e


def _prefixed_path(output_dir: str, filename: str, prefix: str = "") -> str:
    """
    构造带前缀的输出文件路径（v2.4 新增 --output-prefix）。
    多批次扫描不覆盖报告，prefix 为空时返回原路径。
    """
    if not prefix:
        return os.path.join(output_dir, filename)
    # 在文件名主名前加前缀，保留扩展名
    base, ext = os.path.splitext(filename)
    return os.path.join(output_dir, f"{prefix}{base}{ext}")




def _compute_file_sha256(file_path: str, chunk_size: int = 1024 * 1024) -> str:
    """Calculate a full-file SHA-256 fingerprint for operation verification."""
    import hashlib
    digest = hashlib.sha256()
    try:
        with open(file_path, "rb") as stream:
            for chunk in iter(lambda: stream.read(chunk_size), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (IOError, OSError, PermissionError):
        return ""


def _compute_file_md5(file_path: str, chunk_size: int = 1024 * 1024) -> str:
    """
    计算文件 MD5 指纹（v2.6 新增）。
    用于增量扫描时判断文件内容是否真正变更，避免仅靠 mtime 漏检。

    Args:
        file_path: 文件路径
        chunk_size: 分块读取大小（默认 8KB）

    Returns:
        MD5 十六进制字符串，失败返回空字符串
    """
    import hashlib
    try:
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                md5.update(chunk)
        return md5.hexdigest()
    except (IOError, OSError, PermissionError):
        return ""
