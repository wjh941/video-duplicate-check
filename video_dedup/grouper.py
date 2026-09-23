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
# 模块 7：连通图分组（多维度保留策略）
# ============================================================
def _split_group_min_sim(
    members: list,
    sim_lookup: dict,
    floor: float,
) -> list:
    """
    【v2.7 新增】complete-linkage 式分组约束：
    1) 先剔除低于 floor 的"弱桥"边，对组内重新求连通分量（切断传递链）；
    2) 再逐个剔除组内平均相似度最低的成员，直到组内所有点对相似度 >= floor；
    3) 被剔除后落单的成员不再成组（视为非重复）。
    返回拆分后的成员列表（每个元素是一组成员索引列表）。
    """
    parent = {m: m for m in members}

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (i, j), sim in sim_lookup.items():
        if i in parent and j in parent and sim >= floor:
            ri, rj = _find(i), _find(j)
            if ri != rj:
                parent[ri] = rj

    comps_map = {}
    for m in members:
        comps_map.setdefault(_find(m), []).append(m)

    final = []
    for comp in comps_map.values():
        comp = list(comp)
        ejected = []
        guard = 0
        while len(comp) >= 2 and guard < 4 * len(members) + 16:
            guard += 1
            pair_sims = []
            for ai, a in enumerate(comp):
                for b in comp[ai + 1:]:
                    pair_sims.append((sim_lookup.get((min(a, b), max(a, b)), 0.0), a, b))
            min_sim = min(s for s, _a, _b in pair_sims)
            if min_sim >= floor:
                break
            avg = {}
            for m in comp:
                vals = [s for s, a, b in pair_sims if a == m or b == m]
                avg[m] = (sum(vals) / len(vals)) if vals else 0.0
            weakest = min(comp, key=lambda m: avg[m])
            comp.remove(weakest)
            ejected.append(weakest)
        if len(comp) >= 2:
            final.append(comp)
        else:
            ejected.extend(comp)
    return final


def build_groups(
    similar_pairs: list[dict],
    mp4_files: list[dict],
    video_hashes: dict,
    keep_strategy: str = "max-size",
    min_group_sim: float = 0.0,
) -> list[dict]:
    """
    连通图分组，支持多维度保留策略。
    keep_strategy: max-size / latest / max-res / max-bitrate
    min_group_sim: 【v2.7 新增】组内最低相似度约束（0=关闭）。>0 时对每个连通分量
        调用 _split_group_min_sim，保证组内任意点对相似度不低于该值，
        遏制 A≈B≈C 却 A≉C 的传递性误差（固定机位场景滚雪球分组的主要来源）。
    """
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for pair in similar_pairs:
        a, b = pair["idx_a"], pair["idx_b"]
        if a not in parent:
            parent[a] = a
        if b not in parent:
            parent[b] = b
        union(a, b)

    group_map = {}
    for idx in parent:
        root = find(idx)
        group_map.setdefault(root, []).append(idx)

    sim_lookup = {}
    for pair in similar_pairs:
        key = (min(pair["idx_a"], pair["idx_b"]), max(pair["idx_a"], pair["idx_b"]))
        sim_lookup[key] = pair["similarity"]

    # 【v2.7 新增】组内最低相似度约束
    if min_group_sim and min_group_sim > 0.0:
        member_lists = []
        for members in group_map.values():
            if len(members) >= 2:
                member_lists.extend(_split_group_min_sim(members, sim_lookup, min_group_sim))
        member_lists = [m for m in member_lists if len(m) >= 2]
        dropped = sum(len(v) for v in group_map.values()) - sum(len(m) for m in member_lists)
        if dropped > 0:
            log(f"  [分组约束] --group-min-sim={min_group_sim}: 拆分后 {len(member_lists)} 组，"
                f"{dropped} 个成员不再成组（组内相似度不达标）")
    else:
        member_lists = list(group_map.values())

    groups = []
    for members in member_lists:
        member_files = [(idx, mp4_files[idx]) for idx in members]

        # 选择保留视频
        retain_idx = _select_retain(member_files, video_hashes, keep_strategy)

        # 排序：保留的放第一个，其他按大小降序
        member_files.sort(
            key=lambda x: (x[0] != retain_idx, -x[1]["size"])
        )

        group_similarities = {}
        for i_idx, _ in member_files:
            for j_idx, _ in member_files:
                if i_idx < j_idx:
                    key = (min(i_idx, j_idx), max(i_idx, j_idx))
                    if key in sim_lookup:
                        group_similarities[key] = sim_lookup[key]

        groups.append({
            "members": member_files,
            "similarities": group_similarities,
            "retain_idx": retain_idx,
        })

    groups.sort(key=lambda g: len(g["members"]), reverse=True)
    return groups


def _select_retain(
    member_files: list[tuple],
    video_hashes: dict,
    strategy: str,
) -> int:
    """根据策略选择要保留的视频索引"""
    if strategy == "latest":
        return max(member_files, key=lambda x: x[1]["mtime"])[0]
    elif strategy == "max-res":
        def resolution(idx_info):
            h = video_hashes.get(idx_info[0], {})
            return h.get("width", 0) * h.get("height", 0)
        return max(member_files, key=resolution)[0]
    elif strategy == "max-bitrate":
        def bitrate(idx_info):
            info = idx_info[1]
            h = video_hashes.get(idx_info[0], {})
            dur = h.get("duration", 0)
            if dur > 0:
                return info["size"] / dur
            return 0
        return max(member_files, key=bitrate)[0]
    else:  # max-size
        return max(member_files, key=lambda x: x[1]["size"])[0]


# ============================================================
# 模块 8：结果导出
# ============================================================




def _get_keep_strategy(args) -> str:
    """从参数确定保留策略"""
    if getattr(args, "keep_latest", False):
        return "latest"
    elif getattr(args, "keep_max_res", False):
        return "max-res"
    elif getattr(args, "keep_max_bitrate", False):
        return "max-bitrate"
    return "max-size"
