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
from .context import log, repo_dir
from .utils import (_resolve_path, _parse_size_str, _format_size,
                    _normalize_path)
from .cache import load_cache



def _load_duplicateignore(folder_path: str) -> list[str]:
    """读取 .duplicateignore 文件返回排除规则列表"""
    ignore_path = os.path.join(folder_path, IGNORE_FILE)
    if not os.path.exists(ignore_path):
        return []
    rules = []
    try:
        with open(ignore_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    rules.append(line)
    except (IOError, OSError):
        pass
    return rules


def _load_globalignore() -> list[str]:
    """
    读取程序同目录的 .globalignore 全局过滤规则（v2.4 新增）。
    规则对所有扫描目录生效，与 .duplicateignore 合并使用。
    """
    script_dir = repo_dir()
    global_ignore_path = os.path.join(script_dir, GLOBAL_IGNORE_FILE)
    if not os.path.exists(global_ignore_path):
        return []
    rules = []
    try:
        with open(global_ignore_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    rules.append(line)
        if rules:
            log(f"  [过滤] 已加载 .globalignore 全局规则 ({len(rules)} 条)")
    except (IOError, OSError):
        pass
    return rules


def _match_ignore_rule(file_path: Path, rules: list[str]) -> bool:
    """检查文件是否匹配任意排除规则"""
    name = file_path.name
    rel = str(file_path)
    for rule in rules:
        if fnmatch.fnmatch(name, rule) or fnmatch.fnmatch(rel, rule):
            return True
    return False




# ============================================================
# v2.4 新增：时长筛选统计模块
# ============================================================
def parse_duration_filter(filter_str: str) -> list[tuple[str, float]]:
    """
    解析时长筛选条件字符串（v2.4 新增）。
    支持格式：>=60、<30、>120&<=360
    返回 [(运算符, 数值), ...]
    """
    if not filter_str:
        return []
    conditions = []
    # 按 & 分割多条件
    parts = filter_str.split("&")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 匹配运算符 + 数值
        for op in [">=", "<=", "!=", ">", "<", "=="]:
            if part.startswith(op):
                num_str = part[len(op):].strip()
                try:
                    num = float(num_str)
                    conditions.append((op, num))
                except ValueError:
                    pass
                break
    return conditions


def match_duration(duration: float, conditions: list[tuple[str, float]]) -> bool:
    """检查视频时长是否满足所有筛选条件（v2.4 新增）"""
    if not conditions:
        return True
    for op, val in conditions:
        if op == ">=" and not (duration >= val):
            return False
        elif op == "<=" and not (duration <= val):
            return False
        elif op == ">" and not (duration > val):
            return False
        elif op == "<" and not (duration < val):
            return False
        elif op == "==" and not (abs(duration - val) < 0.01):
            return False
        elif op == "!=" and not (abs(duration - val) >= 0.01):
            return False
    return True




def apply_duration_resolution_filter(
    mp4_files: list[dict], args, cache_path: str = "",
) -> tuple[list[dict], dict]:
    """
    应用时长筛选 + 分辨率筛选（v2.4 新增）。
    联动逻辑：筛选后全局过滤，哈希提取、AI分析、相似度比对仅处理匹配条件的视频。
    返回 (过滤后的 mp4_files, 统计信息 dict)。
    #【改造注释】时长筛选读取视频元数据（优先使用缓存），分辨率筛选读取缓存 width/height。
    """
    stats = {
        "total_before": len(mp4_files),
        "duration_filtered": 0,
        "res_filtered": 0,
        "total_after": len(mp4_files),
    }

    duration_filter = getattr(args, "duration_filter", "")
    min_res = getattr(args, "min_res", 0)
    max_res = getattr(args, "max_res", 0)
    conditions = parse_duration_filter(duration_filter) if duration_filter else []

    if not conditions and min_res <= 0 and max_res <= 0:
        return mp4_files, stats

    # 加载缓存用于时长/分辨率查询，避免筛选阶段重复打开视频。
    cache = load_cache(cache_path) if cache_path else {}

    filtered = []
    for fi in mp4_files:
        path = fi["path"]
        keep = True
        # 统一写回筛选阶段解析出的元数据，后续流程和摘要可直接复用。
        cached_entry = cache.get(path) if isinstance(cache.get(path), dict) else None
        if cached_entry:
            for field in ("duration", "width", "height", "fps"):
                if not fi.get(field) and cached_entry.get(field):
                    fi[field] = cached_entry[field]

        # 时长筛选
        if conditions:
            duration = 0.0
            cached_entry = cache.get(path) if isinstance(cache.get(path), dict) else None
            if cached_entry:
                duration = float(cached_entry.get("duration", 0) or 0)
            if duration <= 0:
                try:
                    cap = cv2.VideoCapture(path)
                    if cap.isOpened():
                        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                        fps = cap.get(cv2.CAP_PROP_FPS)
                        duration = total_frames / fps if fps > 0 else 0
                    cap.release()
                except Exception:
                    duration = 0.0
            if not match_duration(duration, conditions):
                stats["duration_filtered"] += 1
                keep = False

        # 分辨率筛选
        if keep and (min_res > 0 or max_res > 0):
            width = 0
            # 优先从缓存读取
            if path in cache and isinstance(cache[path], dict):
                width = cache[path].get("width", 0)
            if width <= 0:
                try:
                    cap = cv2.VideoCapture(path)
                    if cap.isOpened():
                        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        cap.release()
                except Exception:
                    width = 0
            if min_res > 0 and width < min_res:
                stats["res_filtered"] += 1
                keep = False
            elif max_res > 0 and width > max_res:
                stats["res_filtered"] += 1
                keep = False

        if keep:
            filtered.append(fi)

    stats["total_after"] = len(filtered)
    if stats["duration_filtered"] > 0 or stats["res_filtered"] > 0:
        log(f"  [筛选] 时长筛除 {stats['duration_filtered']} 个，"
            f"分辨率筛除 {stats['res_filtered']} 个，"
            f"剩余 {stats['total_after']}/{stats['total_before']}")
    return filtered, stats


def apply_skip_low_quality(
    mp4_files: list[dict], semantic_results: dict,
) -> tuple[list[dict], dict, int]:
    """
    应用 --skip-low-quality 过滤（v2.4 新增）。
    扫描阶段直接过滤 AI 判定低质量模糊暗光视频，不参与后续查重。
    返回 (过滤后的 mp4_files, 过滤后的 semantic_results, 移除数量)。
    """
    # 质量阈值：清晰度<0.3 或 亮度<0.2 视为低质量
    MIN_CLARITY = 0.3
    MIN_BRIGHTNESS = 0.2
    removed = 0
    keep_indices = set()
    for i, sd in semantic_results.items():
        qs = sd.get("quality_score", 1.0)
        conf = sd.get("semantic_conf", {})
        # 质量分过低或明确标记不适合训练
        if qs < MIN_CLARITY:
            removed += 1
            continue
        keep_indices.add(i)

    if removed == 0:
        return mp4_files, semantic_results, 0

    # 同步过滤文件列表并重建连续索引，避免后续 video_hashes 使用旧索引。
    filtered_files = []
    index_map = {}
    for old_idx, file_info in enumerate(mp4_files):
        if old_idx in keep_indices or old_idx not in semantic_results:
            index_map[old_idx] = len(filtered_files)
            filtered_files.append(file_info)
    filtered_semantic = {
        index_map[i]: sd for i, sd in semantic_results.items()
        if i in keep_indices and i in index_map
    }
    log(f"  [skip-low-quality] 过滤 {removed} 个低质量视频")
    return filtered_files, filtered_semantic, removed


def scan_mp4_files(args) -> list[dict]:
    """
    扫描 MP4 文件。
    支持：多后缀、排除文件夹、大小限制、.duplicateignore 规则。
    v2.3 增强：权限拒绝自动跳过 + 统一路径存储 + 中文/空格/特殊字符兼容。
    """
    folder_path = _resolve_path(args.dir)
    recursive = not args.no_recursive
    ext_str = args.ext or "mp4,mov,mkv,avi,webm,m4v,flv"
    extensions = set(f".{e.lower().lstrip('.')}" for e in ext_str.split(","))
    exclude_folders = set(f.strip().lower() for f in (args.exclude_folder or "").split(",") if f.strip())
    min_size = _parse_size_str(args.exclude_size_lt)
    max_size = _parse_size_str(args.exclude_size_gt)
    # 【改造 v2.4】合并目录级 .duplicateignore + 程序同目录 .globalignore 全局规则
    ignore_rules = _load_duplicateignore(folder_path)
    ignore_rules.extend(_load_globalignore())

    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"路径不存在: {folder_path}")
    if not folder.is_dir():
        raise NotADirectoryError(f"路径不是文件夹: {folder_path}")

    mp4_files = []
    skip_dirs = {"__pycache__"}
    skip_dirs.update(exclude_folders)
    skipped_empty = 0
    skipped_size = 0
    skipped_ignore = 0
    skipped_perm = 0  # v2.3 新增：权限拒绝计数

    # v2.3 新增：FFmpeg 缺失警告
    if getattr(args, "audio_check", False) and not FFMPEG_AVAILABLE:
        log("  [警告] FFmpeg 未安装，音频辅助比对功能将失效！")
        log("  [警告] 下载地址: https://ffmpeg.org/download.html")

    try:
        iterator = folder.rglob("*") if recursive else folder.iterdir()
    except PermissionError as e:
        log(f"  [警告] 目录访问被拒绝: {e}")
        return mp4_files

    for file_path in iterator:
        try:
            if not file_path.is_file():
                continue
        except PermissionError:
            skipped_perm += 1
            continue
        if any(part in skip_dirs for part in file_path.parts):
            continue
        if file_path.name.startswith(".") or file_path.name.startswith("~$"):
            continue
        if file_path.suffix.lower() not in extensions:
            continue
        if _match_ignore_rule(file_path, ignore_rules):
            skipped_ignore += 1
            continue

        try:
            stat = file_path.stat()
            if stat.st_size == 0:
                skipped_empty += 1
                continue
            if min_size and stat.st_size < min_size:
                skipped_size += 1
                continue
            if max_size and stat.st_size > max_size:
                skipped_size += 1
                continue

            # v2.3 修复：统一使用 _normalize_path 存储路径，确保缓存匹配
            mp4_files.append({
                "name": file_path.name,
                "path": _normalize_path(str(file_path)),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "size_readable": _format_size(stat.st_size),
            })
        except PermissionError:
            skipped_perm += 1
            continue
        except OSError:
            continue

    if skipped_empty > 0:
        log(f"  [提示] 跳过 {skipped_empty} 个0字节空文件")
    if skipped_size > 0:
        log(f"  [提示] 跳过 {skipped_size} 个超出大小限制的文件")
    if skipped_ignore > 0:
        log(f"  [提示] 跳过 {skipped_ignore} 个匹配 .duplicateignore 规则的文件")
    if skipped_perm > 0:
        log(f"  [提示] 跳过 {skipped_perm} 个权限拒绝的文件/目录")

    return mp4_files
