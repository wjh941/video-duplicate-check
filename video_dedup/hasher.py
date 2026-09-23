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
from .context import log, _APP_CTX, _get_memory_usage
from .utils import _compute_file_sha256, _compute_file_md5
from .cache import is_cache_valid, load_cache, save_cache



# ============================================================
# 模块 5：哈希提取（双哈希融合 + 预处理 + FFmpeg兜底 + 音频）
# ============================================================
def _compute_frame_hashes(gray_frame: np.ndarray) -> tuple:
    """对灰度帧计算 pHash + dHash"""
    pil_img = Image.fromarray(gray_frame)
    phash = imagehash.phash(pil_img, hash_size=HASH_SIZE)
    dhash = imagehash.dhash(pil_img, hash_size=HASH_SIZE)
    return phash, dhash




def _detect_keyframes(cap, total_frames: int, threshold: float = 30.0,
                      max_keys: int = 5) -> list:
    """
    检测视频关键帧（场景切换点）（v2.6 新增）。
    基于帧间直方图差异检测场景切换，优先抽取关键帧进行哈希比对。

    Args:
        cap: cv2.VideoCapture 对象
        total_frames: 总帧数
        threshold: 直方图差异阈值（默认30.0）
        max_keys: 最大关键帧数量（默认5）

    Returns:
        关键帧索引列表（含首帧），检测失败返回空列表
    """
    try:
        keyframes = [0]  # 首帧总是关键帧
        prev_hist = None
        sample_step = max(1, total_frames // 100)  # 最多采样100帧

        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        for idx in range(0, min(total_frames, 100 * sample_step), sample_step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
            hist = cv2.normalize(hist, hist).flatten()

            if prev_hist is not None:
                diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA) * 100
                if diff > threshold and len(keyframes) < max_keys:
                    keyframes.append(idx)

            prev_hist = hist

        return keyframes if len(keyframes) > 1 else []
    except Exception:
        return []


def _extract_audio_hash(video_path: str, num_samples: int = 5) -> Optional[list]:
    """
    使用 FFmpeg 提取音频频谱哈希。
    返回音频哈希列表，失败返回 None。
    """
    if not FFMPEG_AVAILABLE:
        return None
    try:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            "-f", "wav", "-"
        ]
        proc = subprocess.run(
            cmd, capture_output=True, timeout=10,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            return None
        audio_data = proc.stdout
        if len(audio_data) < 65536:
            return None

        # 简单频谱特征：分段计算均值
        chunk_size = len(audio_data) // (2 * num_samples)
        if chunk_size <= 0:
            return None
        hashes = []
        for i in range(min(num_samples, len(audio_data) // (2 * chunk_size))):
            chunk = audio_data[i * chunk_size * 2: (i + 1) * chunk_size * 2]
            samples = np.frombuffer(chunk, dtype=np.int16)
            if len(samples) > 0:
                # 简单哈希：基于采样统计
                stats = [int(np.mean(np.abs(samples))),
                         int(np.std(samples)),
                         int(np.max(np.abs(samples)))]
                hash_val = imagehash.hex_to_hash(
                    format(stats[0] % (16**16), "016x")
                )
                hashes.append(hash_val)
        return hashes if hashes else None
    except Exception:
        return None


def _extract_hashes_single(
    video_path: str, num_frames: int, double_check: bool = False,
    use_audio: bool = False, store_frames: bool = True,
) -> tuple[Optional[dict], Optional[str]]:
    """
    单视频哈希提取（v2.3 增强：超大视频分段抽帧防内存溢出）。
    v2.4 新增 store_frames 参数：--no-store-frames 时不保留 PIL 帧降低内存占用。
    返回 (hash_dict或None, 错误类型或None)
    hash_dict = {"phash": [hash_obj,...], "dhash": [hash_obj,...],
                 "duration": float, "width": int, "height": int,
                 "fps": float, "audio": [hash_obj,...]或None,
                 "frames_pil": [PIL Image,...] 或 None}
    """
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None, ERR_READ_FAILED

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = total_frames / fps if fps > 0 else 0

        if total_frames <= 0:
            return None, ERR_ZERO_FRAMES

        # v2.3 新增：超大视频分段读取阈值（>2GB 或 >30分钟 按时间切片）
        file_size = 0
        try:
            file_size = os.path.getsize(video_path)
        except OSError:
            pass
        is_large_video = file_size > 2 * 1024**3 or duration > 1800

        # 预处理：缩放到 32x32 灰度
        start_ratio, end_ratio = FRAME_SAMPLE_RANGE
        start_frame = max(0, int(total_frames * start_ratio))
        end_frame = min(total_frames, int(total_frames * end_ratio))
        span = max(1, end_frame - start_frame)

        actual_frames = min(num_frames, span)
        if actual_frames <= 0:
            actual_frames = 1

        frame_indices = [
            start_frame + int(span * i / actual_frames)
            for i in range(actual_frames)
        ]

        phashes = []
        dhashes = []
        frames_pil = []  # v2.3 新增：保留 PIL 帧供 AI 复用

        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            # v2.3 新增：超大视频读取后立即缩小，释放原始帧内存
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
            ph, dh = _compute_frame_hashes(small)
            phashes.append(ph)
            dhashes.append(dh)
            # 保留缩小后的 PIL 帧供 AI 复用（不保留原始大帧）
            if is_large_video:
                # 超大视频仅保留低分辨率帧，避免内存堆积
                pil_small = Image.fromarray(
                    cv2.resize(frame, (224, 224), interpolation=cv2.INTER_AREA)
                )
                frames_pil.append(pil_small)
                del frame  # 主动释放
            else:
                frames_pil.append(Image.fromarray(
                    cv2.resize(frame, (224, 224), interpolation=cv2.INTER_AREA)
                ))

        if not phashes:
            return None, ERR_DECODE_ERROR

        # 二次校验：额外抽取中段1秒连续帧
        if double_check and duration > 2:
            mid_point = total_frames // 2
            check_indices = [
                mid_point - fps // 2 + int(fps * i / 5)
                for i in range(5)
            ]
            for idx in check_indices:
                if 0 <= idx < total_frames:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                    ret, frame = cap.read()
                    if ret:
                        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                        small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
                        ph, dh = _compute_frame_hashes(small)
                        phashes.append(ph)
                        dhashes.append(dh)

        # 音频哈希
        audio_hashes = None
        if use_audio:
            audio_hashes = _extract_audio_hash(video_path)

        return {
            "phash": phashes,
            "dhash": dhashes,
            "duration": duration,
            "width": width,
            "height": height,
            "fps": fps,
            "audio": audio_hashes,
            # 【改造 v2.4】--no-store-frames 时不保留 PIL 帧，降低内存占用
            "frames_pil": (frames_pil if frames_pil else None) if store_frames else None,
        }, None

    except PermissionError:
        return None, ERR_PERMISSION
    except OSError:
        return None, ERR_DISK_ERROR
    except Exception:
        return None, ERR_DECODE_ERROR
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def extract_hashes_with_cache(
    mp4_files: list[dict],
    cache_path: str,
    num_frames: int,
    max_workers: int,
    args,
    use_cache: bool = True,
) -> tuple[dict, list[dict], dict]:
    """批量提取哈希（多线程 + 缓存 + 超时 + 错误分类）"""

    cache = load_cache(cache_path) if use_cache else {"_version": CACHE_VERSION}
    _APP_CTX.global_cache = cache
    video_hashes = {}
    bad_videos = []
    to_compute = {}

    double_check = args.double_check
    use_audio = getattr(args, "audio_check", False)
    incremental = args.incremental
    # 【改造 v2.4】--no-store-frames 不将预览帧持久化存入缓存，仅运行时临时复用降低内存占用
    store_frames = not getattr(args, "no_store_frames", False)
    use_md5 = bool(getattr(args, "use_md5", False))

    # 缓存判定
    for i, file_info in enumerate(mp4_files):
        path = file_info["path"]
        if use_cache and path in cache and is_cache_valid(cache[path], file_info, use_md5=use_md5):
            try:
                entry = cache[path]
                video_hashes[i] = {
                    "phash": [imagehash.hex_to_hash(h) for h in entry["phash"]],
                    "dhash": [imagehash.hex_to_hash(h) for h in entry["dhash"]],
                    "duration": entry.get("duration", 0),
                    "width": entry.get("width", 0),
                    "height": entry.get("height", 0),
                    "fps": entry.get("fps", 0),
                    "audio": [imagehash.hex_to_hash(h) for h in entry["audio"]] if entry.get("audio") else None,
                }
                # 增量模式下跳过有效文件
                if incremental and not entry.get("_modified", False):
                    continue
            except Exception:
                to_compute[i] = file_info
        else:
            to_compute[i] = file_info

    log(f"哈希提取：{len(video_hashes)} 个缓存命中，{len(to_compute)} 个待计算")
    if incremental:
        log(f"  增量模式：仅处理新增/修改视频")

    if not to_compute:
        return video_hashes, bad_videos, cache

    total = len(to_compute)
    completed = 0
    mem_limit = getattr(args, "mem_limit", 0)

    if TQDM_AVAILABLE:
        # 【改造 v2.4】mininterval=1.0 节流刷新，降低机械硬盘IO阻塞
        pbar = _tqdm(total=total, desc="提取哈希", unit="视频", ncols=80, mininterval=1.0)

    max_workers = max(1, int(max_workers or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 有界提交：避免数万视频一次性创建 Future 导致内存峰值。
        pending = {}
        work_items = iter(to_compute.items())
        for _ in range(min(max_workers, total)):
            idx, file_info = next(work_items)
            future = executor.submit(
                _extract_hashes_single, file_info["path"],
                num_frames, double_check, use_audio, store_frames,
            )
            pending[future] = (idx, file_info)

        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                idx, file_info = pending.pop(future)
                path = file_info["path"]

                try:
                    result_dict, err_type = future.result(timeout=FRAME_TIMEOUT)
                except FutureTimeout:
                    result_dict, err_type = None, ERR_TIMEOUT
                except Exception:
                    result_dict, err_type = None, ERR_DECODE_ERROR

                if result_dict is not None:
                    video_hashes[idx] = result_dict
                    cache[path] = {
                        "phash": [str(h) for h in result_dict["phash"]],
                        "dhash": [str(h) for h in result_dict["dhash"]],
                        "duration": result_dict["duration"],
                        "width": result_dict["width"],
                        "height": result_dict["height"],
                        "fps": result_dict["fps"],
                        "audio": [str(h) for h in result_dict["audio"]] if result_dict.get("audio") else None,
                        "size": file_info["size"],
                        "mtime": file_info["mtime"],
                        "md5": _compute_file_md5(path) if use_md5 else None,
                    }
                    _APP_CTX.global_cache = cache
                else:
                    bad_videos.append({"path": path, "error_type": err_type or ERR_DECODE_ERROR})

                completed += 1
                mem_info = _get_memory_usage()
                extra = f" 内存:{mem_info}" if mem_info else ""
                if TQDM_AVAILABLE:
                    pbar.set_postfix_str(f"{extra}")
                    pbar.update(1)
                else:
                    pct = completed / total * 100
                    log(f"  哈希进度: {completed}/{total} ({pct:.0f}%){extra}", "\r")

                current_mem_mb = 0.0
                if mem_info:
                    try:
                        current_mem_mb = float(mem_info.replace(" MB", ""))
                    except ValueError:
                        pass
                if mem_limit > 0 and current_mem_mb > mem_limit:
                    save_cache(cache_path, cache)
                    log(f"  [内存管控] 已达 {mem_info}，强制落地缓存")
                elif use_cache and completed % 50 == 0:
                    save_cache(cache_path, cache)

                # 完成一个任务后才补充一个任务，保持队列有界。
                try:
                    next_idx, next_info = next(work_items)
                except StopIteration:
                    continue
                next_future = executor.submit(
                    _extract_hashes_single, next_info["path"],
                    num_frames, double_check, use_audio, store_frames,
                )
                pending[next_future] = (next_idx, next_info)

    if TQDM_AVAILABLE:
        pbar.close()
    log("")

    if use_cache:
        save_cache(cache_path, cache)

    return video_hashes, bad_videos, cache


# ============================================================
# 模块 6：视频相似度比对（时长预筛 + LSH + double-check）
# ============================================================




def _video_quality_score(info: dict, hash_dict: dict = None) -> float:
    """根据可用元数据估算保留建议分（0-100），不解码额外帧。"""
    info = info or {}
    hash_dict = hash_dict or {}
    width = float(hash_dict.get("width", 0) or 0)
    height = float(hash_dict.get("height", 0) or 0)
    duration = float(hash_dict.get("duration", 0) or 0)
    size = float(info.get("size", 0) or 0)
    pixels = width * height
    resolution_score = min(1.0, pixels / (1920 * 1080)) if pixels else 0.0
    bitrate = size / duration if duration > 0 else 0.0
    bitrate_score = min(1.0, bitrate / (8 * 1024 * 1024)) if bitrate else 0.0
    # 分辨率优先，码率辅助；没有元数据时返回中性分而非误导性高分。
    if resolution_score == 0 and bitrate_score == 0:
        return 50.0
    return round((resolution_score * 0.65 + bitrate_score * 0.35) * 100, 2)
