#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MP4 视频相似度查重工具 v2.0
==============================
功能：扫描指定目录下的 MP4 视频，基于感知哈希（pHash）检测内容相似/重复的视频。
支持快速预筛、多线程哈希提取、缓存机制、连通图分组、多格式导出。

模块结构：
    0. 全局配置常量
    1. 命令行参数解析
    2. 日志系统（控制台 + 文件双输出）
    3. 文件扫描与过滤
    4. 哈希缓存管理（备份/恢复/清理）
    5. 哈希提取（多线程 + 超时 + 容错 + 错误分类）
    6. 视频相似度比对（快速预筛 + 进度条）
    7. 连通图分组算法
    8. 结果导出（CSV / TXT / Markdown / 损坏清单 / 清理脚本）
    9. 全局异常处理与主入口

使用示例：
    python find_mp4.py --dir D:\\Videos
    python find_mp4.py --dir D:\\Videos --threshold 0.75 --fast
    python find_mp4.py --dir D:\\Videos --output-dir D:\\Reports
    python find_mp4.py --dir D:\\Videos --format md
    python find_mp4.py --version
    python find_mp4.py --check-only --dir D:\\Videos
    python find_mp4.py --clean-cache
"""

import argparse
import csv
import json
import os
import shutil
import signal
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image
import imagehash

# tqdm 可选依赖
try:
    from tqdm import tqdm as _tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# psutil 可选依赖（内存监控）
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


# ============================================================
# 模块 0：全局配置常量
# ============================================================
__version__ = "2.0.0"

HASH_SIZE = 8
CACHE_FILE = "video_hash_cache.json"
CACHE_BACKUP = "video_hash_cache.json.bak"
RESULT_CSV = "similar_result.csv"
RESULT_GROUPS = "duplicate_groups.txt"
RESULT_GROUPS_MD = "duplicate_groups.md"
BAD_VIDEO_LIST = "bad_video_list.txt"
HASH_EXPORT = "hash_export.json"
CLEANUP_SCRIPT_WIN = "cleanup_duplicates.bat"
CLEANUP_SCRIPT_LINUX = "cleanup_duplicates.sh"
LOG_FILE = "run_log.txt"
FRAME_TIMEOUT = 30
FRAME_SAMPLE_RANGE = (0.1, 0.9)
FAST_FRAMES = 5         # --fast 模式下的帧数
FAST_THRESHOLD = 0.85   # --fast 模式下的预筛阈值

# 全局状态：用于异常退出时保存数据
_global_cache = {}
_global_results_saved = False


# ============================================================
# 模块 1：日志系统
# ============================================================
_log_file_handle = None


def log_init(log_dir: str):
    """初始化日志系统"""
    global _log_file_handle
    log_path = os.path.join(log_dir, LOG_FILE)
    try:
        _log_file_handle = open(log_path, "a", encoding="utf-8")
    except (IOError, OSError):
        _log_file_handle = None


def log(msg: str = "", end: str = "\n"):
    """双输出：控制台 + 日志文件"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}" if msg else ""
    print(line, end=end)
    if _log_file_handle:
        _log_file_handle.write(line + end)
        _log_file_handle.flush()


def log_close():
    """关闭日志文件"""
    global _log_file_handle
    if _log_file_handle:
        try:
            _log_file_handle.close()
        except Exception:
            pass
        _log_file_handle = None


# ============================================================
# 模块 2：命令行参数解析
# ============================================================
def parse_args():
    parser = argparse.ArgumentParser(
        description="MP4 视频相似度查重工具 v2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python find_mp4.py --dir D:\\Videos
  python find_mp4.py --dir D:\\Videos --threshold 0.75 --frames 15
  python find_mp4.py --dir D:\\Videos --output-dir D:\\Reports --format md
  python find_mp4.py --dir D:\\Videos --fast              # 快速粗筛
  python find_mp4.py --dir D:\\Videos --check-only        # 仅校验不比对
  python find_mp4.py --version                            # 查看版本
  python find_mp4.py --clean-cache                        # 清理无效缓存

输出文件:
  similar_result.csv       两两比对明细
  duplicate_groups.txt     分组报告（TXT）
  duplicate_groups.md      分组报告（Markdown，可选）
  bad_video_list.txt       损坏视频清单（带故障分类）
  video_hash_cache.json    哈希缓存
  cleanup_duplicates.bat   批量清理脚本（可选）
  hash_export.json         全量哈希备份（可选）
  run_log.txt              运行日志
        """,
    )

    parser.add_argument(
        "--dir", type=str, default=None,
        help="视频扫描文件夹路径（--clean-cache/--version 时可省略）",
    )
    parser.add_argument(
        "--no-recursive", action="store_true", default=False,
        help="不递归子文件夹",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.7,
        help="相似度判定阈值 0~1，默认 0.7（越高越严格）",
    )
    parser.add_argument(
        "--frames", type=int, default=10,
        help="单视频抽取帧数，默认 10",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="哈希提取线程数，默认 4",
    )
    parser.add_argument(
        "--no-cache", action="store_true", default=False,
        help="禁用哈希缓存",
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="自定义输出目录（默认脚本同级）",
    )
    parser.add_argument(
        "--format", choices=["txt", "md"], default="txt",
        help="分组报告格式：txt 或 md（默认 txt）",
    )
    parser.add_argument(
        "--fast", action="store_true", default=False,
        help="快速粗筛模式（减少帧数 + 提高阈值）",
    )
    parser.add_argument(
        "--check-only", action="store_true", default=False,
        help="仅扫描+提取哈希，不执行相似度比对",
    )
    parser.add_argument(
        "--version", action="store_true", default=False,
        help="打印版本信息后退出",
    )
    parser.add_argument(
        "--clean-cache", action="store_true", default=False,
        help="清理无效缓存条目后退出",
    )
    parser.add_argument(
        "--export-hash", action="store_true", default=False,
        help="导出全量哈希 JSON 备份",
    )
    parser.add_argument(
        "--gen-cleanup", action="store_true", default=False,
        help="生成批量清理重复视频脚本",
    )

    return parser.parse_args()


def validate_args(args):
    """参数二次校验：边界限制 + 条件性必填"""
    # --dir 在以下模式下可省略
    if not args.version and not args.clean_cache and not args.dir:
        print("错误: 必须指定 --dir 文件夹路径，或使用 --version / --clean-cache")
        sys.exit(1)

    # 阈值边界
    if args.threshold < 0.0 or args.threshold > 1.0:
        print("错误: --threshold 必须在 0.0 ~ 1.0 之间")
        sys.exit(1)

    # 帧数边界
    if args.frames < 1:
        print("错误: --frames 必须 >= 1")
        sys.exit(1)

    # 线程数边界
    if args.workers < 1:
        print("错误: --workers 必须 >= 1")
        sys.exit(1)


# ============================================================
# 模块 3：文件扫描与过滤
# ============================================================
def _resolve_path(path_str: str) -> str:
    """将相对路径转为绝对路径，支持 Windows 长路径"""
    resolved = str(Path(path_str).resolve())
    # Windows 长路径支持（> 260 字符时添加 \\?\ 前缀）
    if len(resolved) > 248 and sys.platform == "win32" and not resolved.startswith("\\\\"):
        resolved = "\\\\?\\" + resolved
    return resolved


def scan_mp4_files(folder_path: str, recursive: bool = True) -> list[dict]:
    """
    扫描指定目录，返回合法的 mp4 文件列表。
    过滤：__pycache__、临时文件、非 mp4、0 字节空文件。
    """
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"路径不存在: {folder_path}")
    if not folder.is_dir():
        raise NotADirectoryError(f"路径不是文件夹: {folder_path}")

    mp4_files = []
    skip_dirs = {"__pycache__"}
    skipped_empty = 0

    iterator = folder.rglob("*") if recursive else folder.iterdir()

    for file_path in iterator:
        if not file_path.is_file():
            continue
        if any(part in skip_dirs for part in file_path.parts):
            continue
        if file_path.name.startswith(".") or file_path.name.startswith("~$"):
            continue
        if file_path.suffix.lower() != ".mp4":
            continue

        try:
            stat = file_path.stat()
            # 跳过 0 字节空文件
            if stat.st_size == 0:
                skipped_empty += 1
                continue
            mp4_files.append({
                "name": file_path.name,
                "path": str(file_path.resolve()),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "size_readable": _format_size(stat.st_size),
            })
        except OSError:
            continue

    if skipped_empty > 0:
        log(f"  [提示] 已跳过 {skipped_empty} 个 0 字节空文件")

    return mp4_files


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _get_memory_usage() -> Optional[str]:
    """获取当前进程内存占用（MB），psutil 不可用时返回 None"""
    if not PSUTIL_AVAILABLE:
        return None
    try:
        process = psutil.Process(os.getpid())
        mem = process.memory_info().rss / (1024 * 1024)
        return f"{mem:.1f} MB"
    except Exception:
        return None


# ============================================================
# 模块 4：哈希缓存管理
# ============================================================
def load_cache(cache_path: str) -> dict:
    """加载哈希缓存，损坏时自动备份旧缓存再重建"""
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        raise ValueError("缓存格式异常")
    except (json.JSONDecodeError, IOError, ValueError) as e:
        log(f"  [警告] 缓存文件损坏: {e}")
        backup_path = cache_path + ".bak"
        try:
            shutil.copy2(cache_path, backup_path)
            log(f"  [警告] 已备份旧缓存 → {backup_path}")
        except Exception:
            pass
        return {}


def save_cache(cache_path: str, cache: dict):
    """保存哈希缓存"""
    try:
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except (IOError, OSError) as e:
        log(f"  [警告] 缓存保存失败: {e}")


def is_cache_valid(cache_entry: dict, file_info: dict) -> bool:
    """检查缓存条目有效性（路径+大小+修改时间）"""
    return (
        cache_entry.get("size") == file_info["size"]
        and abs(cache_entry.get("mtime", 0) - file_info["mtime"]) < 1.0
    )


def clean_invalid_cache(cache_path: str) -> tuple[int, int]:
    """
    清理无效缓存条目（文件已删除或已修改）。
    返回 (已清理数量, 剩余数量)
    """
    cache = load_cache(cache_path)
    if not cache:
        return 0, 0

    removed = 0
    kept = {}

    for path, entry in cache.items():
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
        kept[path] = entry

    save_cache(cache_path, kept)
    return removed, len(kept)


# ============================================================
# 模块 5：哈希提取（多线程 + 超时 + 容错 + 错误分类）
# ============================================================
# 错误类型常量
ERR_READ_FAILED = "read_failed"      # 视频打开失败
ERR_ZERO_FRAMES = "zero_frames"      # 帧数为 0
ERR_TIMEOUT = "timeout"              # 提取超时
ERR_DECODE_ERROR = "decode_error"    # 解码异常


def _extract_hashes_single(video_path: str, num_frames: int) -> tuple[Optional[list], Optional[str]]:
    """
    单视频哈希提取（独立线程）。
    使用 try/finally 确保 VideoCapture 释放。
    返回 (哈希列表或None, 错误类型或None)
    """
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None, ERR_READ_FAILED

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            return None, ERR_ZERO_FRAMES

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

        hashes = []
        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb_frame)
            phash = imagehash.phash(pil_img, hash_size=HASH_SIZE)
            hashes.append(phash)

        if hashes:
            return hashes, None
        return None, ERR_DECODE_ERROR

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
    use_cache: bool = True,
) -> tuple[dict, list[dict], dict]:
    """
    批量提取视频哈希（多线程 + 缓存 + 超时）。

    Returns:
        (video_hashes, bad_videos, cache)
        - video_hashes: {index: [hash_obj, ...]}
        - bad_videos: [{"path": str, "error_type": str}, ...]
        - cache: 更新后的缓存字典
    """
    global _global_cache

    cache = load_cache(cache_path) if use_cache else {}
    _global_cache = cache  # 保存全局引用供异常恢复
    video_hashes = {}
    bad_videos = []
    to_compute = {}

    # 第一阶段：缓存判定
    for i, file_info in enumerate(mp4_files):
        path = file_info["path"]
        if use_cache and path in cache and is_cache_valid(cache[path], file_info):
            try:
                hex_list = cache[path]["hashes"]
                video_hashes[i] = [
                    imagehash.hex_to_hash(h) for h in hex_list
                ]
            except Exception:
                to_compute[i] = file_info
        else:
            to_compute[i] = file_info

    log(f"哈希提取：{len(video_hashes)} 个缓存命中，{len(to_compute)} 个待计算")

    if not to_compute:
        return video_hashes, bad_videos, cache

    # 第二阶段：多线程提取
    total = len(to_compute)
    completed = 0

    # 进度条
    if TQDM_AVAILABLE:
        pbar = _tqdm(total=total, desc="提取哈希", unit="视频", ncols=80)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {}
        for idx, file_info in to_compute.items():
            future = executor.submit(
                _extract_hashes_single, file_info["path"], num_frames
            )
            future_to_idx[future] = (idx, file_info)

        for future in as_completed(future_to_idx):
            idx, file_info = future_to_idx[future]
            path = file_info["path"]

            try:
                result, err_type = future.result(timeout=FRAME_TIMEOUT)
            except FutureTimeout:
                result, err_type = None, ERR_TIMEOUT
            except Exception:
                result, err_type = None, ERR_DECODE_ERROR

            if result is not None and len(result) > 0:
                video_hashes[idx] = result
                cache[path] = {
                    "hashes": [str(h) for h in result],
                    "size": file_info["size"],
                    "mtime": file_info["mtime"],
                }
                _global_cache = cache
            else:
                bad_videos.append({
                    "path": path,
                    "error_type": err_type or ERR_DECODE_ERROR,
                })

            completed += 1
            mem_info = _get_memory_usage()
            extra = f" 内存: {mem_info}" if mem_info else ""

            if TQDM_AVAILABLE:
                pbar.set_postfix_str(f"{extra}")
                pbar.update(1)
            else:
                pct = completed / total * 100
                log(f"  哈希进度: {completed}/{total} ({pct:.0f}%){extra}", "\r")

            # 每 10 个保存一次缓存
            if use_cache and completed % 10 == 0:
                save_cache(cache_path, cache)

    if TQDM_AVAILABLE:
        pbar.close()
    log("")

    # 最终保存
    if use_cache:
        save_cache(cache_path, cache)

    return video_hashes, bad_videos, cache


# ============================================================
# 模块 6：视频相似度比对
# ============================================================
def compute_similarity(hash_list1: list, hash_list2: list) -> dict:
    """
    计算两组感知哈希的相似度。
    使用 imagehash 原生减法，归一化后返回。
    """
    if not hash_list1 or not hash_list2:
        return {"distance": 1.0, "similarity": 0.0}

    distances = []
    for h1 in hash_list1:
        min_dist_bit = min(h1 - h2 for h2 in hash_list2)
        norm_dist = min_dist_bit / (HASH_SIZE ** 2)
        distances.append(norm_dist)

    avg_distance = float(np.mean(distances))
    similarity = max(0.0, min(1.0, 1.0 - avg_distance))
    return {"distance": avg_distance, "similarity": similarity}


def pre_filter_by_metadata(mp4_files: list[dict]) -> dict:
    """
    快速预筛：按 (size, mtime) 分组。
    完全一致的文件对直接标记为 100% 重复，跳过哈希比对。

    Returns:
        (metadata_duplicates, skip_pairs)
        - metadata_duplicates: [(idx_a, idx_b), ...] 直接判定重复的文件对
        - skip_pairs: set of (min_idx, max_idx) 需跳过的哈希比对对
    """
    groups = {}
    for i, f in enumerate(mp4_files):
        key = (f["size"], round(f["mtime"], 0))
        groups.setdefault(key, []).append(i)

    meta_dups = []
    skip_pairs = set()
    for key, indices in groups.items():
        if len(indices) > 1:
            indices.sort()
            for a in range(len(indices)):
                for b in range(a + 1, len(indices)):
                    pair = (indices[a], indices[b])
                    meta_dups.append(pair)
                    skip_pairs.add(pair)

    return meta_dups, skip_pairs


def find_similar_pairs(
    video_hashes: dict,
    mp4_files: list[dict],
    threshold: float,
    skip_pairs: Optional[set] = None,
) -> list[dict]:
    """
    两两比对视频相似度。支持快速预筛跳过。
    """
    keys = sorted(video_hashes.keys())
    n = len(keys)
    total_pairs = n * (n - 1) // 2
    compared = 0
    similar_pairs = []
    skipped_count = 0

    log(f"开始两两比对，共 {total_pairs} 对视频...")
    if skip_pairs:
        log(f"  预筛跳过 {len(skip_pairs)} 对（元数据完全一致）")

    if TQDM_AVAILABLE:
        pbar = _tqdm(total=total_pairs, desc="比对相似度", unit="对", ncols=80)

    for i in range(n):
        for j in range(i + 1, n):
            idx_a, idx_b = keys[i], keys[j]

            # 跳过预筛标记的对
            pair_key = (min(idx_a, idx_b), max(idx_a, idx_b))
            if skip_pairs and pair_key in skip_pairs:
                compared += 1
                skipped_count += 1
                if TQDM_AVAILABLE:
                    pbar.update(1)
                continue

            # 空值保护
            if idx_a not in video_hashes or idx_b not in video_hashes:
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

            result = compute_similarity(hashes_a, hashes_b)
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

    return similar_pairs


# ============================================================
# 模块 7：连通图分组算法
# ============================================================
def build_groups(similar_pairs: list[dict], mp4_files: list[dict]) -> list[dict]:
    """
    基于并查集的连通图分组，完整合并传递相似视频。
    优化：使用 (min_idx, max_idx) 字典实现 O(1) 相似度查找。
    """
    # 并查集
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    # 初始化 + 合并
    for pair in similar_pairs:
        a, b = pair["idx_a"], pair["idx_b"]
        if a not in parent:
            parent[a] = a
        if b not in parent:
            parent[b] = b
        union(a, b)

    # 构建分组
    group_map = {}
    for idx in parent:
        root = find(idx)
        group_map.setdefault(root, []).append(idx)

    # 构建快速查找字典 {(min, max): similarity}
    sim_lookup = {}
    for pair in similar_pairs:
        key = (min(pair["idx_a"], pair["idx_b"]), max(pair["idx_a"], pair["idx_b"]))
        sim_lookup[key] = pair["similarity"]

    # 构建分组详情
    groups = []
    for root, members in group_map.items():
        # 排序：按文件大小降序
        member_files = [(idx, mp4_files[idx]) for idx in members]
        member_files.sort(key=lambda x: x[1]["size"], reverse=True)

        # 收集组内相似度（O(1) 查找）
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
            "retain_idx": member_files[0][0],
        })

    groups.sort(key=lambda g: len(g["members"]), reverse=True)
    return groups


# ============================================================
# 模块 8：结果导出
# ============================================================
def export_csv(similar_pairs: list[dict], csv_path: str, threshold: float):
    """导出比对明细 CSV"""
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "视频A路径", "视频B路径", "归一化距离", "相似度", "是否判定重复",
            ])
            for pair in similar_pairs:
                is_dup = "是" if pair["similarity"] >= threshold else "否"
                writer.writerow([
                    pair["path_a"],
                    pair["path_b"],
                    f"{pair['distance']:.6f}",
                    f"{pair['similarity']:.4f}",
                    is_dup,
                ])
        log(f"[导出] CSV → {csv_path}")
    except (IOError, OSError) as e:
        log(f"[错误] CSV 导出失败: {e}")


def export_groups_txt(
    groups: list[dict],
    mp4_files: list[dict],
    group_path: str,
    threshold: float,
):
    """导出分组报告（TXT 格式）"""
    try:
        with open(group_path, "w", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write(f"  MP4 相似视频分组报告\n")
            f.write(f"  相似度阈值: {threshold:.0%}\n")
            f.write(f"  生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 60 + "\n\n")

            if not groups:
                f.write("未发现相似视频分组。\n")
            else:
                for gi, group in enumerate(groups, 1):
                    f.write(f"【第 {gi} 组】（共 {len(group['members'])} 个视频）\n")
                    f.write("-" * 50 + "\n")

                    for fi, (idx, info) in enumerate(group["members"]):
                        is_retain = (idx == group["retain_idx"])
                        mark = " ★ 建议保留" if is_retain else " ✗ 建议清理"
                        f.write(f"  {fi + 1}. {info['name']}{mark}\n")
                        f.write(f"     路径: {info['path']}\n")
                        f.write(f"     大小: {info['size_readable']}\n")
                        mtime_str = time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(info["mtime"])
                        )
                        f.write(f"     修改时间: {mtime_str}\n\n")

                    f.write("-" * 50 + "\n\n")
        log(f"[导出] 分组报告(TXT) → {group_path}")
    except (IOError, OSError) as e:
        log(f"[错误] TXT 导出失败: {e}")


def export_groups_md(
    groups: list[dict],
    mp4_files: list[dict],
    md_path: str,
    threshold: float,
):
    """导出分组报告（Markdown 格式）"""
    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# MP4 相似视频分组报告\n\n")
            f.write(f"- **相似度阈值**: {threshold:.0%}\n")
            f.write(f"- **生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- **分组总数**: {len(groups)}\n\n")

            if not groups:
                f.write("> 未发现相似视频分组。\n")
            else:
                for gi, group in enumerate(groups, 1):
                    f.write(f"## 第 {gi} 组（{len(group['members'])} 个视频）\n\n")
                    f.write("| # | 文件名 | 大小 | 修改时间 | 建议 |\n")
                    f.write("|---|--------|------|----------|------|\n")

                    for fi, (idx, info) in enumerate(group["members"]):
                        is_retain = (idx == group["retain_idx"])
                        mark = "✅ 保留" if is_retain else "❌ 清理"
                        mtime_str = time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(info["mtime"])
                        )
                        f.write(
                            f"| {fi + 1} | `{info['name']}` | "
                            f"{info['size_readable']} | {mtime_str} | {mark} |\n"
                        )

                    # 组内相似度矩阵
                    if len(group["members"]) > 1:
                        f.write("\n**组内相似度:**\n\n")
                        f.write("|  |")
                        for _, info in group["members"]:
                            short_name = info["name"][:10]
                            f.write(f" {short_name} |")
                        f.write("\n")
                        f.write("|" + "---|" * (len(group["members"]) + 1) + "\n")

                        for i_idx, (_, info_i) in enumerate(group["members"]):
                            f.write(f"| {info_i['name'][:10]} |")
                            for j_idx, (_, info_j) in enumerate(group["members"]):
                                if i_idx == j_idx:
                                    f.write(" - |")
                                else:
                                    key = (min(i_idx, j_idx), max(i_idx, j_idx))
                                    sim = group["similarities"].get(key, -1)
                                    if sim >= 0:
                                        f.write(f" {sim:.0%} |")
                                    else:
                                        f.write(" - |")
                            f.write("\n")

                    f.write("\n")
        log(f"[导出] 分组报告(MD) → {md_path}")
    except (IOError, OSError) as e:
        log(f"[错误] MD 导出失败: {e}")


def export_bad_videos(bad_videos: list[dict], bad_path: str):
    """导出损坏视频清单（带故障分类）"""
    try:
        error_labels = {
            ERR_READ_FAILED: "视频打开失败",
            ERR_ZERO_FRAMES: "帧数为0",
            ERR_TIMEOUT: "提取超时",
            ERR_DECODE_ERROR: "解码异常",
        }
        counts = {}
        for bv in bad_videos:
            t = bv["error_type"]
            counts[t] = counts.get(t, 0) + 1

        with open(bad_path, "w", encoding="utf-8") as f:
            f.write(f"# 损坏视频清单（共 {len(bad_videos)} 个）\n")
            f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            for err_type, count in sorted(counts.items()):
                label = error_labels.get(err_type, err_type)
                f.write(f"## {label}: {count} 个\n\n")

            for bv in bad_videos:
                label = error_labels.get(bv["error_type"], bv["error_type"])
                f.write(f"- [{label}] {bv['path']}\n")
        log(f"[导出] 损坏清单 → {bad_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 损坏清单导出失败: {e}")


def export_hash_backup(video_hashes: dict, mp4_files: list[dict], export_path: str):
    """导出全量哈希 JSON 备份"""
    try:
        backup = {}
        for idx, hash_list in video_hashes.items():
            if idx < len(mp4_files):
                backup[mp4_files[idx]["path"]] = {
                    "name": mp4_files[idx]["name"],
                    "size": mp4_files[idx]["size"],
                    "hashes": [str(h) for h in hash_list],
                }
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
        log(f"[导出] 哈希备份 → {export_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 哈希备份导出失败: {e}")


def generate_cleanup_script(
    groups: list[dict],
    mp4_files: list[dict],
    output_dir: str,
):
    """生成批量清理重复视频脚本（Windows .bat / Linux .sh）"""
    try:
        # Windows BAT
        bat_path = os.path.join(output_dir, CLEANUP_SCRIPT_WIN)
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write("@echo off\r\n")
            f.write("chcp 65001 >nul\r\n")
            f.write("REM ============================================\r\n")
            f.write(f"REM  MP4 重复视频清理脚本\r\n")
            f.write(f"REM  生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\r\n")
            f.write("REM  默认保留每组中体积最大的视频\r\n")
            f.write("REM  使用方法：双击运行或在 CMD 中执行\r\n")
            f.write("REM ============================================\r\n\r\n")

            for gi, group in enumerate(groups, 1):
                f.write(f"REM --- 第 {gi} 组 ---\r\n")
                retain_path = mp4_files[group["retain_idx"]]["path"]
                f.write(f"REM 保留: {retain_path}\r\n")
                for idx, info in group["members"]:
                    if idx != group["retain_idx"]:
                        path = info["path"]
                        f.write(f'del "{path}"\r\n')
                f.write("\r\n")

            f.write("echo.\r\n")
            f.write("echo 清理完成！\r\n")
            f.write("pause\r\n")

        # Linux SH
        sh_path = os.path.join(output_dir, CLEANUP_SCRIPT_LINUX)
        with open(sh_path, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n")
            f.write("# ============================================\n")
            f.write(f"#  MP4 重复视频清理脚本\n")
            f.write(f"#  生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("#  默认保留每组中体积最大的视频\n")
            f.write("#  使用方法: chmod +x cleanup_duplicates.sh && ./cleanup_duplicates.sh\n")
            f.write("# ============================================\n\n")

            for gi, group in enumerate(groups, 1):
                f.write(f"# --- 第 {gi} 组 ---\n")
                retain_path = mp4_files[group["retain_idx"]]["path"]
                f.write(f'# 保留: "{retain_path}"\n')
                for idx, info in group["members"]:
                    if idx != group["retain_idx"]:
                        path = info["path"]
                        f.write(f'rm -f "{path}"\n')
                f.write("\n")

        log(f"[导出] 清理脚本 → {bat_path} / {sh_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 清理脚本生成失败: {e}")


# ============================================================
# 模块 9：统计汇总
# ============================================================
def print_summary(
    mp4_files: list[dict],
    video_hashes: dict,
    bad_videos: list[dict],
    groups: list[dict],
):
    """打印统计汇总"""
    total = len(mp4_files)
    success = len(video_hashes)
    failed = len(bad_videos)
    group_count = len(groups)
    dup_total = sum(len(g["members"]) for g in groups)

    log("")
    log("=" * 60)
    log("  统计汇总")
    log("=" * 60)
    log(f"  扫描视频总数:     {total}")
    log(f"  哈希成功数量:     {success}")
    log(f"  解析失败数量:     {failed}")
    log(f"  相似分组总数:     {group_count}")
    log(f"  涉及重复视频数:   {dup_total}")
    mem = _get_memory_usage()
    if mem:
        log(f"  峰值内存占用:     {mem}")
    log("=" * 60)

    if bad_videos:
        log("\n  损坏视频清单：")
        error_labels = {
            ERR_READ_FAILED: "视频打开失败",
            ERR_ZERO_FRAMES: "帧数为0",
            ERR_TIMEOUT: "提取超时",
            ERR_DECODE_ERROR: "解码异常",
        }
        for bv in bad_videos:
            label = error_labels.get(bv["error_type"], bv["error_type"])
            log(f"    ✗ [{label}] {bv['path']}")

    if groups:
        log("\n  相似分组概览：")
        for gi, group in enumerate(groups, 1):
            names = [info["name"] for _, info in group["members"]]
            log(f"    第{gi}组: {', '.join(names)}")


# ============================================================
# 模块 10：全局异常处理与主入口
# ============================================================
def _crash_save(output_dir: str, cache_path: str = None):
    """异常退出时保存缓存和部分结果"""
    global _global_cache, _global_results_saved
    try:
        if cache_path and _global_cache:
            save_cache(cache_path, _global_cache)
            log("[异常恢复] 已保存哈希缓存")
        _global_results_saved = True
    except Exception:
        pass


def _signal_handler(signum, frame):
    """Ctrl+C 信号处理"""
    log("\n[中断] 正在保存数据...")
    _crash_save(os.getcwd())
    log("[中断] 数据已保存，可安全退出")
    sys.exit(130)


def _print_version():
    """打印版本信息"""
    log(f"MP4 视频相似度查重工具 v{__version__}")
    log(f"Python: {sys.version}")
    log(f"OpenCV: {cv2.__version__}")
    log(f"NumPy: {np.__version__}")
    try:
        log(f"Pillow: {Image.__version__}")
    except Exception:
        log("Pillow: 未知")
    try:
        log(f"imagehash: {imagehash.__version__}")
    except Exception:
        log("imagehash: 未知")
    log(f"tqdm: {'已安装' if TQDM_AVAILABLE else '未安装（可选）'}")
    log(f"psutil: {'已安装' if PSUTIL_AVAILABLE else '未安装（可选）'}")


def main():
    args = parse_args()
    validate_args(args)

    # --version 模式
    if args.version:
        _print_version()
        return

    # --clean-cache 模式
    if args.clean_cache:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cache_path = os.path.join(script_dir, CACHE_FILE)
        log(f"清理缓存: {cache_path}")
        removed, remaining = clean_invalid_cache(cache_path)
        log(f"  已清理 {removed} 条无效缓存，剩余 {remaining} 条")
        return

    # 正常运行模式
    folder_path = _resolve_path(args.dir)
    recursive = not args.no_recursive
    threshold = args.threshold
    num_frames = args.frames
    max_workers = args.workers
    use_cache = not args.no_cache

    # --fast 模式调整参数
    if args.fast:
        num_frames = min(num_frames, FAST_FRAMES)
        threshold = max(threshold, FAST_THRESHOLD)

    # 输出目录
    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(output_dir, exist_ok=True)

    # 初始化日志
    log_init(output_dir)

    # 注册信号处理
    signal.signal(signal.SIGINT, _signal_handler)

    # 构建路径
    cache_path = os.path.join(output_dir, CACHE_FILE)

    log("=" * 60)
    log("       MP4 视频相似度查重工具 v2.0")
    log("=" * 60)
    log(f"  扫描目录:   {folder_path}")
    log(f"  递归子目录: {'是' if recursive else '否'}")
    log(f"  相似度阈值: {threshold:.0%}")
    log(f"  抽取帧数:   {num_frames}")
    log(f"  线程数:     {max_workers}")
    log(f"  使用缓存:   {'是' if use_cache else '否'}")
    log(f"  输出目录:   {output_dir}")
    if args.fast:
        log(f"  快速模式:   已开启")
    if TQDM_AVAILABLE:
        log(f"  进度条:     已启用 (tqdm)")
    mem = _get_memory_usage()
    if mem:
        log(f"  初始内存:   {mem}")

    try:
        # 1. 扫描 MP4 文件
        log("\n[步骤1] 扫描 MP4 文件...")
        try:
            mp4_files = scan_mp4_files(folder_path, recursive)
        except (FileNotFoundError, NotADirectoryError) as e:
            log(f"错误: {e}")
            sys.exit(1)

        if not mp4_files:
            log("未找到 MP4 文件，程序退出。")
            return

        log(f"  共找到 {len(mp4_files)} 个 MP4 文件")

        # 2. 提取哈希
        log("\n[步骤2] 提取感知哈希...")
        video_hashes, bad_videos, cache = extract_hashes_with_cache(
            mp4_files, cache_path, num_frames, max_workers, use_cache,
        )

        # 3. 快速预筛
        meta_dups = []
        skip_pairs = None
        if not args.check_only and len(video_hashes) >= 2:
            log("\n[步骤3] 快速预筛（元数据匹配）...")
            meta_dups, skip_pairs = pre_filter_by_metadata(mp4_files)
            if meta_dups:
                log(f"  发现 {len(meta_dups)} 对元数据完全一致的视频，将直接判定重复")

        # 4. 相似度比对
        similar_pairs = []
        groups = []

        if args.check_only:
            log("\n[步骤4] check-only 模式，跳过相似度比对")
        elif len(video_hashes) < 2:
            log("\n[步骤4] 有效视频不足 2 个，无法比对。")
        else:
            log("\n[步骤4] 视频相似度比对...")
            similar_pairs = find_similar_pairs(
                video_hashes, mp4_files, threshold, skip_pairs
            )

            # 将元数据预筛的对也加入相似对列表
            for idx_a, idx_b in meta_dups:
                similar_pairs.append({
                    "idx_a": idx_a,
                    "idx_b": idx_b,
                    "name_a": mp4_files[idx_a]["name"],
                    "name_b": mp4_files[idx_b]["name"],
                    "path_a": mp4_files[idx_a]["path"],
                    "path_b": mp4_files[idx_b]["path"],
                    "distance": 0.0,
                    "similarity": 1.0,
                })

            groups = build_groups(similar_pairs, mp4_files)

        # 5. 导出结果
        log("\n[步骤5] 导出结果文件...")

        csv_path = os.path.join(output_dir, RESULT_CSV)
        group_path = os.path.join(output_dir, RESULT_GROUPS)
        bad_path = os.path.join(output_dir, BAD_VIDEO_LIST)

        export_csv(similar_pairs, csv_path, threshold)

        if args.format == "md":
            md_path = os.path.join(output_dir, RESULT_GROUPS_MD)
            export_groups_md(groups, mp4_files, md_path, threshold)
        else:
            export_groups_txt(groups, mp4_files, group_path, threshold)

        export_bad_videos(bad_videos, bad_path)

        if args.export_hash and video_hashes:
            export_path = os.path.join(output_dir, HASH_EXPORT)
            export_hash_backup(video_hashes, mp4_files, export_path)

        if args.gen_cleanup and groups:
            generate_cleanup_script(groups, mp4_files, output_dir)

        # 6. 统计汇总
        print_summary(mp4_files, video_hashes, bad_videos, groups)

        log(f"\n完成！所有结果文件已保存至: {output_dir}")

    except Exception as e:
        # 全局异常捕获
        log(f"\n[严重错误] 程序异常终止: {e}")
        log(f"[严重错误] {traceback.format_exc()}")
        _crash_save(output_dir, cache_path)
        log("[严重错误] 已尽可能保存已完成的数据，请检查日志和导出文件")
        sys.exit(1)
    finally:
        log_close()


if __name__ == "__main__":
    main()
