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



# ============================================================
# 模块 6：视频相似度比对（时长预筛 + LSH + double-check）
# ============================================================
def compute_similarity(hash_dict1: dict, hash_dict2: dict,
                       weights: dict = None) -> dict:
    """
    双哈希融合相似度计算（v2.4 修复：音频惩罚逻辑 + 自定义权重配置）。
    #【改造注释】音频惩罚仅当两段视频都存在音频哈希时才执行0.8折扣；
    任意一方无音频/静音不扣分，避免画面完全一致仅无音频判定不相似。
    权重支持配置文件自定义 phash_weight/dhash_weight/audio_weight。
    """
    if not hash_dict1 or not hash_dict2:
        return {"distance": 1.0, "similarity": 0.0}

    # 【改造】自定义权重，替代硬编码 0.7/0.3
    w = weights or {}
    phash_w = w.get("phash_weight", 0.7)
    dhash_w = w.get("dhash_weight", 0.3)
    audio_w = w.get("audio_weight", 0.8)  # 音频惩罚折扣系数

    # 时长预筛
    dur1 = hash_dict1.get("duration", 0)
    dur2 = hash_dict2.get("duration", 0)
    if dur1 > 0 and dur2 > 0:
        diff_ratio = abs(dur1 - dur2) / max(dur1, dur2)
        if diff_ratio > 0.2:
            return {"distance": 1.0, "similarity": 0.0}

    # pHash 相似度
    phash_list1 = hash_dict1.get("phash", [])
    phash_list2 = hash_dict2.get("phash", [])
    phash_sim = _hash_list_similarity(phash_list1, phash_list2)

    # dHash 相似度
    dhash_list1 = hash_dict1.get("dhash", [])
    dhash_list2 = hash_dict2.get("dhash", [])
    dhash_sim = _hash_list_similarity(dhash_list1, dhash_list2)

    # 加权融合（使用配置权重）
    total_w = phash_w + dhash_w
    if phash_list1 and dhash_list1 and total_w > 0:
        similarity = (phash_w * phash_sim + dhash_w * dhash_sim) / total_w
    elif phash_list1:
        similarity = phash_sim
    else:
        similarity = dhash_sim

    # 【改造修复】音频辅助：仅当两段视频都存在音频哈希时才执行惩罚
    audio1 = hash_dict1.get("audio")
    audio2 = hash_dict2.get("audio")
    if audio1 and audio2:
        audio_sim = _hash_list_similarity(audio1, audio2)
        # 音频相似度低于0.3时给予惩罚（仅双方都有音频时）
        if audio_sim < 0.3:
            similarity *= audio_w
    # 【改造】任意一方无音频/静音不扣分，保持画面相似度判定

    similarity = max(0.0, min(1.0, similarity))
    distance = 1.0 - similarity
    return {"distance": distance, "similarity": similarity}


def _hash_list_similarity(list1: list, list2: list) -> float:
    """计算两组哈希的归一化相似度"""
    if not list1 or not list2:
        return 0.0
    distances = []
    for h1 in list1:
        min_dist = min(h1 - h2 for h2 in list2)
        norm = min_dist / (HASH_SIZE ** 2)
        distances.append(norm)
    avg_dist = float(np.mean(distances))
    return max(0.0, min(1.0, 1.0 - avg_dist))


def _build_lsh_buckets(video_hashes: dict, num_buckets: int) -> dict:
    """构建保留召回率的 pHash 多 band 候选索引。

    旧实现把整数哈希对桶数取模，这与汉明距离无关：两个几乎相同的
    pHash 可能落入不同桶，导致真实重复被直接漏掉。这里把 64 bit pHash
    切成多个 band，每个 band 单独建桶；近似哈希只要有一个 band 相同就
    会成为候选。num_buckets 保留为兼容参数，仅用于限制每个 band 的桶数。
    """
    buckets = defaultdict(list)
    bands = 4
    bits_per_band = (HASH_SIZE ** 2) // bands
    bucket_count = max(2, int(num_buckets or 32))
    for idx, hash_dict in video_hashes.items():
        phash_list = hash_dict.get("phash", [])
        if not phash_list:
            buckets["__empty__"].append(idx)
            continue
        try:
            value = int(str(phash_list[0]), 16)
        except (ValueError, TypeError):
            buckets["__empty__"].append(idx)
            continue
        mask = (1 << bits_per_band) - 1
        for band in range(bands):
            band_value = (value >> (band * bits_per_band)) & mask
            buckets[(band, band_value % bucket_count)].append(idx)
    return buckets


def find_similar_pairs(
    video_hashes: dict,
    mp4_files: list[dict],
    threshold: float,
    skip_pairs: Optional[set] = None,
    lsh_buckets: int = 0,
    weights: dict = None,
) -> list[dict]:
    """
    两两比对。支持 LSH 加速和预筛跳过。
    v2.4 优化：LSH空桶直接跳过比对；时长差异0.2阈值视频提前剔除，不进入双重循环；
    支持 config.ini 自定义权重传入。
    """
    keys = sorted(video_hashes.keys())
    n = len(keys)
    total_pairs = n * (n - 1) // 2
    compared = 0
    similar_pairs = []
    skipped_count = 0

    # 【改造 v2.4】时长预筛：构建时长索引，差异>0.2的视频对直接剔除
    duration_skip_count = 0

    def _duration_too_different(idxa: int, idxb: int) -> bool:
        """时长差异>20%直接判定不相似，跳过哈希计算"""
        ha = video_hashes.get(idxa, {})
        hb = video_hashes.get(idxb, {})
        d1 = ha.get("duration", 0)
        d2 = hb.get("duration", 0)
        if d1 > 0 and d2 > 0:
            diff = abs(d1 - d2) / max(d1, d2)
            if diff > 0.2:
                return True
        return False

    log(f"开始两两比对，共 {total_pairs} 对视频...")
    if skip_pairs:
        log(f"  预筛跳过 {len(skip_pairs)} 对（元数据完全一致）")

    # LSH 加速
    use_lsh = lsh_buckets > 0 and n > 50
    if use_lsh:
        buckets = _build_lsh_buckets(video_hashes, lsh_buckets)
        # 【改造 v2.4】统计空桶数量并跳过
        empty_buckets = sum(1 for v in buckets.values() if not v)
        valid_buckets = sum(1 for v in buckets.values() if v)
        log(f"  LSH 分桶: {valid_buckets} 个有效桶，{empty_buckets} 个空桶（已跳过）")
        # 计算实际需要比对的对数（仅桶内两两）
        actual_pairs = sum(len(v) * (len(v) - 1) // 2 for v in buckets.values() if len(v) > 1)
        log(f"  LSH 优化: 实际比对 {actual_pairs} 对（原 {total_pairs} 对，节省 {max(0, total_pairs-actual_pairs)} 对）")
        # 多 band 可能让同一对视频进入多个桶，按唯一候选对计数。
        # 多 band 下同一对可能进入多个桶，去重后只比较一次。
        candidate_pairs = set()
        for bucket_indices in buckets.values():
            if len(bucket_indices) < 2:
                continue
            for pos, left in enumerate(bucket_indices):
                for right in bucket_indices[pos + 1:]:
                    candidate_pairs.add((min(left, right), max(left, right)))
        total_pairs = len(candidate_pairs)

    if TQDM_AVAILABLE:
        # 【改造 v2.4】mininterval=1.0 节流刷新，降低机械硬盘IO阻塞
        pbar = _tqdm(total=total_pairs, desc="比对相似度", unit="对", ncols=80, mininterval=1.0)

    # 【改造 v2.4】LSH 模式下仅桶内两两比对；非 LSH 模式全量两两
    if use_lsh:
        for idx_a, idx_b in sorted(candidate_pairs):
            pair_key = (min(idx_a, idx_b), max(idx_a, idx_b))
            if skip_pairs and pair_key in skip_pairs:
                compared += 1
                skipped_count += 1
                if TQDM_AVAILABLE:
                    pbar.update(1)
                continue
            # 时长预筛
            if _duration_too_different(idx_a, idx_b):
                duration_skip_count += 1
                compared += 1
                if TQDM_AVAILABLE:
                    pbar.update(1)
                continue
            hashes_a = video_hashes[idx_a]
            hashes_b = video_hashes[idx_b]
            if not hashes_a or not hashes_b:
                compared += 1
                if TQDM_AVAILABLE:
                    pbar.update(1)
                continue
            result = compute_similarity(hashes_a, hashes_b, weights=weights)
            compared += 1
            if TQDM_AVAILABLE:
                pbar.update(1)
            if result["similarity"] >= threshold:
                similar_pairs.append({
                    "idx_a": idx_a, "idx_b": idx_b,
                    "name_a": mp4_files[idx_a]["name"],
                    "name_b": mp4_files[idx_b]["name"],
                    "path_a": mp4_files[idx_a]["path"],
                    "path_b": mp4_files[idx_b]["path"],
                    "distance": result["distance"],
                    "similarity": result["similarity"],
                })
    else:
        for i in range(n):
            for j in range(i + 1, n):
                idx_a, idx_b = keys[i], keys[j]

                # 预筛跳过
                pair_key = (min(idx_a, idx_b), max(idx_a, idx_b))
                if skip_pairs and pair_key in skip_pairs:
                    compared += 1
                    skipped_count += 1
                    if TQDM_AVAILABLE:
                        pbar.update(1)
                    continue

                # 【改造 v2.4】时长预筛
                if _duration_too_different(idx_a, idx_b):
                    duration_skip_count += 1
                    compared += 1
                    if TQDM_AVAILABLE:
                        pbar.update(1)
                    continue

                hashes_a = video_hashes[idx_a]
                hashes_b = video_hashes[idx_b]

                if not hashes_a or not hashes_b:
                    compared += 1
                    if TQDM_AVAILABLE:
                        pbar.update(1)
                    continue

                result = compute_similarity(hashes_a, hashes_b, weights=weights)
                compared += 1

                if TQDM_AVAILABLE:
                    pbar.update(1)
                else:
                    pct = compared / total_pairs * 100
                    log(f"  比对进度: {compared}/{total_pairs} ({pct:.1f}%)", "\r")

                if result["similarity"] >= threshold:
                    similar_pairs.append({
                        "idx_a": idx_a,
                        "idx_b": idx_b,
                        "name_a": mp4_files[idx_a]["name"],
                        "name_b": mp4_files[idx_b]["name"],
                        "path_a": mp4_files[idx_a]["path"],
                        "path_b": mp4_files[idx_b]["path"],
                        "distance": result["distance"],
                        "similarity": result["similarity"],
                    })

    if TQDM_AVAILABLE:
        pbar.close()
    log("")
    if skipped_count > 0:
        log(f"  已跳过 {skipped_count} 对预筛匹配")
    if duration_skip_count > 0:
        log(f"  时长预筛剔除 {duration_skip_count} 对（差异>20%）")

    return similar_pairs


def pre_filter_by_metadata(mp4_files: list[dict]) -> tuple:
    """快速预筛：按 size+mtime 分组"""
    groups = {}
    for i, f in enumerate(mp4_files):
        key = (f["size"], round(f["mtime"], 0))
        groups.setdefault(key, []).append(i)

    # 大小+mtime 只能作为候选提示，不能证明文件内容相同。
    # 过去这里把这些 pair 跳过哈希并直接判定重复，会误报“同大小同秒修改”的视频。
    meta_candidates = []
    for key, indices in groups.items():
        if len(indices) > 1:
            indices.sort()
            for a in range(len(indices)):
                for b in range(a + 1, len(indices)):
                    meta_candidates.append((indices[a], indices[b]))

    # 保留返回结构兼容调用方，但不再跳过真实内容比对。
    return meta_candidates, set()


# ============================================================
# 模块 7：连通图分组（多维度保留策略）
# ============================================================
