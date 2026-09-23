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
from .context import log
from .utils import _normalize_path, _compute_file_md5



def _migrate_cache_version(data: dict, old_version: str, new_version: str) -> Optional[dict]:
    """
    缓存版本自动迁移（v2.4 新增）。
    旧版本缓存自动转换新版字段，无需全量重新扫描。
    迁移规则：保留原有哈希字段，补全缺失的元数据字段，更新版本号。
    返回迁移后的 dict，无法迁移返回 None。
    """
    if not data or not isinstance(data, dict):
        return None

    # 支持的迁移路径：2.0/2.1/2.2/2.3 → 2.4
    migratable = {"2.0", "2.1", "2.2", "2.3", "2.4.0"}
    if old_version not in migratable:
        return None

    try:
        migrated = {"_version": new_version}
        migrated_count = 0
        for key, entry in data.items():
            if key.startswith("_"):
                continue
            if not isinstance(entry, dict):
                continue
            # 补全缺失字段（v2.4 新增字段默认值）
            entry.setdefault("width", entry.get("width", 0))
            entry.setdefault("height", entry.get("height", 0))
            entry.setdefault("fps", entry.get("fps", 0))
            entry.setdefault("audio", entry.get("audio"))
            # v2.4 新增字段：默认为空，不强制添加
            migrated[key] = entry
            migrated_count += 1
        log(f"  [缓存迁移] 成功迁移 {migrated_count} 条缓存记录")
        return migrated
    except Exception as e:
        log(f"  [缓存迁移] 迁移失败: {e}")
        return None


def _sqlite_cache_path(cache_path: str) -> str:
    """Return the sidecar SQLite cache path for a JSON cache path."""
    return os.path.splitext(cache_path)[0] + ".sqlite3"


def _load_sqlite_cache(cache_path: str) -> Optional[dict]:
    """Load cache entries from SQLite when the sidecar exists."""
    db_path = _sqlite_cache_path(cache_path)
    if not os.path.exists(db_path):
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS cache (path TEXT PRIMARY KEY, data TEXT NOT NULL)")
            rows = conn.execute("SELECT path, data FROM cache").fetchall()
        result = {"_version": CACHE_VERSION}
        for path, data in rows:
            result[path] = json.loads(data)
        return result
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return None


def _save_sqlite_cache(cache_path: str, cache: dict) -> bool:
    """Persist cache entries transactionally in a compact SQLite sidecar."""
    db_path = _sqlite_cache_path(cache_path)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS cache (path TEXT PRIMARY KEY, data TEXT NOT NULL)")
            conn.execute("BEGIN")
            conn.execute("DELETE FROM cache")
            rows = [(k, json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str))
                    for k, v in cache.items() if not k.startswith("_")]
            conn.executemany("INSERT INTO cache(path, data) VALUES (?, ?)", rows)
            conn.commit()
        return True
    except (sqlite3.Error, OSError):
        return False


def load_cache(cache_path: str) -> dict:
    """
    加载缓存（v2.3 重写：支持分块自动合并读取）。
    读取主文件 _chunks 字段，自动加载所有 video_hash_cache_part*.json 分片并合并，
    解决分块后缓存读不全、命中失效问题。
    """
    sqlite_cache = _load_sqlite_cache(cache_path)
    if sqlite_cache is not None:
        return sqlite_cache
    if not os.path.exists(cache_path):
        # 【改造】自动识别 .gz 压缩缓存
        gz_path = cache_path + ".gz"
        if os.path.exists(gz_path):
            cache_path = gz_path
        else:
            return {"_version": CACHE_VERSION}
    try:
        # 【改造】支持 gzip 压缩缓存加载
        if cache_path.endswith(".gz"):
            import gzip
            with gzip.open(cache_path, "rt", encoding="utf-8") as f:
                data = json.load(f)
        else:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("缓存格式异常")
        # 版本校验 + 自动迁移
        stored_version = data.get("_version", "0")
        if stored_version != CACHE_VERSION:
            # 【改造 v2.4】缓存版本自动迁移：旧版本缓存自动转换新版字段，无需全量重新扫描
            migrated = _migrate_cache_version(data, stored_version, CACHE_VERSION)
            if migrated is not None:
                log(f"  [缓存] 自动迁移 v{stored_version} → v{CACHE_VERSION}，保留 {len([k for k in migrated if not k.startswith('_')])} 条缓存")
                # 保存迁移后的缓存
                try:
                    save_cache(cache_path, migrated)
                except Exception:
                    pass
                return migrated
            # 无法迁移则重建
            log(f"  [警告] 缓存版本不匹配 (当前v{CACHE_VERSION}, 缓存v{stored_version})，将重建缓存")
            backup_path = cache_path + ".bak"
            try:
                shutil.copy2(cache_path, backup_path)
                log(f"  [警告] 已备份旧缓存 → {backup_path}")
            except Exception:
                pass
            return {"_version": CACHE_VERSION}

        # v2.3 新增：分块缓存自动合并读取
        # 【改造修复】合并分片缓存时仅保留主文件 _version，不复制 _chunks 元字段
        num_chunks = data.get("_chunks", 0)
        if num_chunks and num_chunks > 0:
            base = os.path.splitext(cache_path)[0]
            cache_dir = os.path.dirname(cache_path) or "."
            # 【改造】仅保留 _version，不复制 _chunks 避免元字段污染
            merged = {"_version": CACHE_VERSION}
            loaded_chunks = 0
            for i in range(1, num_chunks + 1):
                part_path = f"{base}_part{i}.json"
                if not os.path.exists(part_path):
                    log(f"  [警告] 缓存分片缺失: {os.path.basename(part_path)}")
                    continue
                try:
                    with open(part_path, "r", encoding="utf-8") as pf:
                        part_data = json.load(pf)
                    if isinstance(part_data, dict):
                        # 【改造】分片仅覆盖视频条目，不覆盖全局缓存配置
                        for k, v in part_data.items():
                            if not k.startswith("_"):
                                merged[k] = v
                        loaded_chunks += 1
                except (json.JSONDecodeError, IOError):
                    log(f"  [警告] 缓存分片损坏: {os.path.basename(part_path)}")
                    continue
            if loaded_chunks > 0:
                log(f"  [缓存] 已合并 {loaded_chunks}/{num_chunks} 个分片，共 {len(merged) - len([k for k in merged if k.startswith('_')])} 条")
            return merged
        return data
    except (json.JSONDecodeError, IOError, ValueError) as e:
        log(f"  [警告] 缓存文件损坏: {e}")
        backup_path = cache_path + ".bak"
        try:
            shutil.copy2(cache_path, backup_path)
            log(f"  [警告] 已备份旧缓存 → {backup_path}")
        except Exception:
            pass
        return {"_version": CACHE_VERSION}


def save_cache(cache_path: str, cache: dict, chunk_size: int = 500,
               compress: bool = False):
    """
    保存缓存（v2.4 修复：原子写入 tmp+rename 防 Ctrl+C 损坏 + gzip 压缩支持）。
    #【改造注释】先写入 xxx.tmp 临时文件，完整无报错后原子替换原缓存文件，
    避免 Ctrl+C 中断导致 JSON 截断损坏。支持 --compress-cache 输出 .gz 压缩包。
    """
    try:
        if _save_sqlite_cache(cache_path, cache):
            # SQLite 是快速读取的 sidecar；同时保留 JSON 以兼容旧工具。
            pass
        items = [(k, v) for k, v in cache.items() if not k.startswith("_")]
        # 【改造】过滤掉 frames_pil 等不可序列化的大对象
        for k, v in items:
            if isinstance(v, dict) and "frames_pil" in v:
                v.pop("frames_pil", None)
        meta = {k: v for k, v in cache.items() if k.startswith("_")}
        meta["_version"] = CACHE_VERSION

        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)

        if len(items) <= chunk_size:
            full = dict(meta)
            full.update(dict(items))
            # 【改造】原子写入：先写 .tmp 再 rename
            tmp_path = cache_path + ".tmp"
            json_data = json.dumps(full, ensure_ascii=False, indent=2, default=str)
            if compress:
                import gzip
                gz_path = cache_path + ".gz"
                tmp_gz = gz_path + ".tmp"
                with gzip.open(tmp_gz, "wt", encoding="utf-8") as f:
                    f.write(json_data)
                os.replace(tmp_gz, gz_path)
                # 清理未压缩版本
                if os.path.exists(cache_path):
                    os.remove(cache_path)
            else:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(json_data)
                os.replace(tmp_path, cache_path)
            return

        # 分块存储
        base = os.path.splitext(cache_path)[0]
        for old in Path(cache_path).parent.glob(os.path.basename(base) + "_part*.json"):
            try:
                old.unlink()
            except OSError:
                pass

        for i in range(0, len(items), chunk_size):
            chunk = dict(items[i:i + chunk_size])
            part_path = f"{base}_part{i // chunk_size + 1}.json"
            part_data = dict(meta)
            part_data.update(chunk)
            # 【改造】每个分片也用原子写入
            tmp_part = part_path + ".tmp"
            with open(tmp_part, "w", encoding="utf-8") as f:
                json.dump(part_data, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp_part, part_path)

        # 写主索引（原子写入）
        meta["_chunks"] = (len(items) + chunk_size - 1) // chunk_size
        tmp_main = cache_path + ".tmp"
        with open(tmp_main, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_main, cache_path)

    except (IOError, OSError) as e:
        log(f"  [警告] 缓存保存失败: {e}")


def is_cache_valid(cache_entry: dict, file_info: dict, use_md5: bool = False) -> bool:
    """
    检查缓存条目有效性（v2.6 增强：可选 MD5 校验）。
    v2.6 新增：当 use_md5=True 且缓存中有 md5 字段时，执行 MD5 校验，
    避免"相同大小+相同时间戳但内容已修改"的漏检。

    Args:
        cache_entry: 缓存条目
        file_info: 文件信息
        use_md5: 是否启用 MD5 校验（默认 False，保持向后兼容）
    """
    # 基础校验：大小和时间戳
    basic_ok = (
        cache_entry.get("size") == file_info["size"]
        and abs(cache_entry.get("mtime", 0) - file_info["mtime"]) < 1.0
    )
    if not basic_ok:
        return False
    # 开启 MD5 时，旧缓存没有指纹必须重算，不能把旧条目当作已校验。
    if use_md5 and not cache_entry.get("md5"):
        return False
    # v2.6 增强：MD5 校验
    if use_md5 and cache_entry.get("md5"):
        cached_md5 = cache_entry.get("md5")
        current_md5 = _compute_file_md5(file_info["path"])
        if current_md5 and cached_md5 != current_md5:
            return False
    return True


# v2.3 新增：断点续扫进度缓存
SCAN_PROGRESS_FILE = "scan_progress.json"

def save_scan_progress(scanned_dirs: set, output_dir: str):
    """保存扫描进度（v2.3 新增：断点续扫机制）"""
    progress_path = os.path.join(output_dir, SCAN_PROGRESS_FILE)
    try:
        with open(progress_path, "w", encoding="utf-8") as f:
            json.dump({"scanned_dirs": list(scanned_dirs),
                       "timestamp": time.time()}, f, ensure_ascii=False)
    except (IOError, OSError):
        pass

def load_scan_progress(output_dir: str) -> set:
    """加载扫描进度（v2.3 新增：断点续扫机制，v2.4 新增：30天过期自动清理）"""
    progress_path = os.path.join(output_dir, SCAN_PROGRESS_FILE)
    if not os.path.exists(progress_path):
        return set()
    try:
        # 【改造 v2.4】超过30天未使用的进度文件自动删除，避免目录堆积
        file_age = time.time() - os.path.getmtime(progress_path)
        if file_age > 30 * 86400:
            try:
                os.remove(progress_path)
                log(f"  [进度] 清理过期进度文件（{file_age/86400:.0f}天未使用）")
            except OSError:
                pass
            return set()
        with open(progress_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("scanned_dirs", []))
    except (json.JSONDecodeError, IOError, OSError):
        return set()

def clear_scan_progress(output_dir: str):
    """清除扫描进度（v2.3 新增）"""
    progress_path = os.path.join(output_dir, SCAN_PROGRESS_FILE)
    try:
        if os.path.exists(progress_path):
            os.remove(progress_path)
    except OSError:
        pass


# v2.3 新增：缓存过期清理
def clean_expired_cache(cache_path: str, expire_days: int) -> int:
    """清理超过指定天数未修改的缓存条目（v2.3 新增）"""
    if expire_days <= 0:
        return 0
    cache = load_cache(cache_path)
    removed = 0
    kept = {"_version": CACHE_VERSION}
    expire_seconds = expire_days * 86400
    current_time = time.time()

    for path, entry in cache.items():
        if path.startswith("_"):
            continue
        try:
            stat = os.stat(path)
            # 文件修改时间超过指定天数则清理
            if current_time - stat.st_mtime > expire_seconds:
                removed += 1
                continue
        except OSError:
            removed += 1
            continue
        kept[path] = entry

    if removed > 0:
        save_cache(cache_path, kept)
        log(f"  [缓存] 清理 {removed} 条超过 {expire_days} 天的缓存")
    return removed


def clean_invalid_cache(cache_path: str) -> tuple[int, int]:
    """
    清理无效缓存（v2.3 增强：同步清除 AI 语义缓存字段）。
    删除失效文件时同步清除该视频 scene_tags、semantic_emb、dataset_purpose 等 AI 数据，不残留无效 AI 数据。
    """
    cache = load_cache(cache_path)
    removed = 0
    kept = {"_version": CACHE_VERSION}
    # v2.3 新增：AI 语义字段清单，文件失效时一并清除
    _AI_CACHE_FIELDS = [
        "scene_tags", "object_tags", "action_tags",
        "dataset_purpose", "semantic_emb", "semantic_conf",
        "quality_score", "is_training_ready",
    ]

    for path, entry in cache.items():
        if path.startswith("_"):
            continue
        if not os.path.exists(path):
            removed += 1
            continue
        try:
            stat = os.stat(path)
            if (entry.get("size") != stat.st_size or
                    abs(entry.get("mtime", 0) - stat.st_mtime) >= 1.0):
                removed += 1
                continue
        except OSError:
            removed += 1
            continue
        # v2.3 新增：文件有效但检查是否有过期 AI 字段需清理
        # （文件存在但 AI 标签为空且曾有 emb 的，保留但不强制清理）
        kept[path] = entry

    save_cache(cache_path, kept)
    removed_ai = 0
    # v2.3 新增：统计被清理的 AI 字段数量
    for path in cache:
        if path.startswith("_"):
            continue
        if path not in kept:
            removed_ai += sum(1 for f in _AI_CACHE_FIELDS if cache[path].get(f) is not None)
    if removed_ai > 0:
        log(f"  [缓存] 同步清理 {removed_ai} 个 AI 语义字段")
    return removed, len([k for k in kept if not k.startswith("_")])


def merge_caches(cache_paths: list[str], output_path: str) -> tuple[int, int]:
    """合并多个缓存文件"""
    merged = {"_version": CACHE_VERSION}
    total_merged = 0

    for cp in cache_paths:
        if not os.path.exists(cp):
            log(f"  [警告] 缓存文件不存在: {cp}")
            continue
        data = load_cache(cp)
        count = 0
        for k, v in data.items():
            if not k.startswith("_") and k not in merged:
                merged[k] = v
                count += 1
        total_merged += count
        log(f"  合并 {cp}: +{count} 条新条目")

    save_cache(output_path, merged)
    return total_merged, len([k for k in merged if not k.startswith("_")])


# ============================================================
# 模块 5：哈希提取（双哈希融合 + 预处理 + FFmpeg兜底 + 音频）
# ============================================================
