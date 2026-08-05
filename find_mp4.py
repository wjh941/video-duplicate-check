#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MP4 视频相似度查重工具 v2.2
==============================
功能：扫描指定目录下的视频文件，基于多哈希融合（pHash+dHash）检测内容相似/重复的视频。
支持 LSH 加速、增量扫描、缓存管理、多格式导出、安全清理、子命令架构。
v2.2 新增：AI 语义内容分析、场景聚类、数据集自动标注、训练集清单导出。

模块结构：
    0. 全局配置常量 + 退出码 + AI依赖检测
    1. 日志系统（双输出 + quiet 模式）
    2. 命令行参数解析（子命令 + 扁平参数兼容）
    3. 文件扫描与过滤（多后缀 + 排除规则 + .duplicateignore）
    4. 哈希缓存管理（版本化 + 分块 + 合并/校验 + 语义字段）
    5. 哈希提取（双哈希融合 + 32x32预处理 + FFmpeg兜底 + 音频哈希）
    6. 视频相似度比对（时长预筛 + LSH分桶 + double-check）
    7. 连通图分组（多维度保留策略）
    8. 结果导出（CSV/TXT/MD/HTML/纯路径清单/审计日志/安全清理脚本）
    9. 全局异常处理与主入口
    10. AI 语义分析模块（CLIP特征提取 + 场景分类 + 用途判定）
    11. 语义聚类与数据集导出

使用示例：
    # 子命令模式（v2.2 推荐）
    python find_mp4.py scan --dir D:\\Videos
    python find_mp4.py scan --dir D:\\Videos --semantic --export-dataset
    python find_mp4.py semantic-analyze --dir D:\\video
    python find_mp4.py dataset-filter --purpose 监控 --dir D:\\car_data
    python find_mp4.py scan --dir D:\\camera --cluster-semantic
    python find_mp4.py scan --dir D:\\video --semantic --incremental --embed-cache
    python find_mp4.py clean-cache
    python find_mp4.py verify-cache

    # 扁平参数模式（v2.1 兼容）
    python find_mp4.py --dir D:\\Videos
    python find_mp4.py --version
"""

import argparse
import configparser
import csv
import fnmatch
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image
import imagehash

# tqdm 可选
try:
    from tqdm import tqdm as _tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# psutil 可选
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# ai_semantic 模块（v2.2 AI 语义分析，可选）
try:
    from ai_semantic import (
        TORCH_OK as _AI_TORCH_OK,
        CLIP_OK as _AI_CLIP_OK,
        SKLEARN_OK as _AI_SKLEARN_OK,
        CUDA_OK as _AI_CUDA_OK,
        load_clip_model,
        semantic_analyze_video,
        cluster_videos_by_semantic,
        export_dataset_catalog,
        export_train_sample_list,
        export_dataset_stats,
        export_scene_cluster_html,
    )
    AI_MODULE_AVAILABLE = True
except ImportError:
    AI_MODULE_AVAILABLE = False
    _AI_TORCH_OK = False
    _AI_CLIP_OK = False
    _AI_SKLEARN_OK = False
    _AI_CUDA_OK = False

# FFmpeg 探测
FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg"))


# ============================================================
# 模块 0：全局配置常量 + 退出码
# ============================================================
__version__ = "2.2.0"

# 缓存版本号（算法变更时自动作废旧缓存）
CACHE_VERSION = "2.2"

# 基础参数
HASH_SIZE = 8
FRAME_TIMEOUT = 30
FRAME_SAMPLE_RANGE = (0.1, 0.9)
FAST_FRAMES = 5
FAST_THRESHOLD = 0.85

# 输出文件名
CACHE_FILE = "video_hash_cache.json"
RESULT_CSV = "similar_result.csv"
RESULT_GROUPS = "duplicate_groups.txt"
RESULT_GROUPS_MD = "duplicate_groups.md"
RESULT_GROUPS_HTML = "duplicate_groups.html"
RESULT_PATHS = "duplicate_paths.txt"
BAD_VIDEO_LIST = "bad_video_list.txt"
HASH_EXPORT = "hash_export.json"
AUDIT_LOG = "cleanup_audit.log"
CLEANUP_SCRIPT_WIN = "cleanup_duplicates.bat"
CLEANUP_SCRIPT_LINUX = "cleanup_duplicates.sh"
LOG_FILE = "run_log.txt"
IGNORE_FILE = ".duplicateignore"

# v2.2 AI 相关输出文件
SEMANTIC_META = "video_semantic_meta.json"
DATASET_CATALOG = "dataset_catalog.csv"
TRAIN_SAMPLE_LIST = "train_sample_list.txt"
DATASET_STATS = "dataset_stats.md"
SCENE_CLUSTER_HTML = "scene_cluster.html"

# 错误类型
ERR_READ_FAILED = "read_failed"
ERR_ZERO_FRAMES = "zero_frames"
ERR_TIMEOUT = "timeout"
ERR_DECODE_ERROR = "decode_error"
ERR_ENCRYPTED = "encrypted"
ERR_PERMISSION = "permission"
ERR_DISK_ERROR = "disk_error"
ERR_LOW_QUALITY = "low_quality"  # v2.2 新增：画面模糊/暗光无有效主体

# 退出码
EXIT_OK = 0            # 无重复
EXIT_HAS_DUPLICATES = 1  # 存在重复分组
EXIT_PARSE_ERROR = 2    # 视频解析失败
EXIT_BAD_ARGS = 3      # 参数错误

# CLIP 模型全局引用（延迟加载，v2.2）
_semantic_clip_model = None
_semantic_clip_preprocess = None
_semantic_clip_device = "cuda" if _AI_CUDA_OK else "cpu"

# 全局状态
_global_cache = {}
_global_exit_code = EXIT_OK
_global_results_saved = False
_quiet_mode = False
_semantic_stats = {}   # v2.2 新增：AI 语义统计


# ============================================================
# 模块 1：日志系统
# ============================================================
_log_file_handle = None


def log_init(log_dir: str):
    """初始化日志系统"""
    global _log_file_handle
    if _quiet_mode:
        return
    log_path = os.path.join(log_dir, LOG_FILE)
    try:
        _log_file_handle = open(log_path, "a", encoding="utf-8")
    except (IOError, OSError):
        _log_file_handle = None


def log(msg: str = "", end: str = "\n", force: bool = False):
    """双输出：控制台 + 日志文件。force=True 时忽略 quiet 模式"""
    global _log_file_handle
    if _quiet_mode and not force:
        return
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


# ============================================================
# 模块 2：命令行参数解析
# ============================================================
def _build_shared_parser():
    """构建共享参数组"""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dir", type=str, default=None)
    parser.add_argument("--no-recursive", action="store_true", default=False)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument("--frames", type=int, default=10)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--no-cache", action="store_true", default=False)
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--format", choices=["txt", "md", "html"], default="txt")
    parser.add_argument("--fast", action="store_true", default=False)
    parser.add_argument("--check-only", action="store_true", default=False)
    parser.add_argument("--version", action="store_true", default=False)
    parser.add_argument("--clean-cache", action="store_true", default=False)
    parser.add_argument("--export-hash", action="store_true", default=False)
    parser.add_argument("--gen-cleanup", action="store_true", default=False)
    # v2.1 新增参数
    parser.add_argument("--ext", type=str, default="mp4",
                        help="支持的视频后缀，逗号分隔，默认 mp4")
    parser.add_argument("--double-check", action="store_true", default=False,
                        help="二次校验模式")
    parser.add_argument("--lsh-buckets", type=int, default=32,
                        help="LSH 分桶数，默认 32")
    parser.add_argument("--mem-limit", type=int, default=0,
                        help="内存限制MB，0为不限制")
    parser.add_argument("--incremental", action="store_true", default=False,
                        help="增量模式，仅处理新增/修改视频")
    parser.add_argument("--keep-max-size", action="store_true", default=False)
    parser.add_argument("--keep-latest", action="store_true", default=False)
    parser.add_argument("--keep-max-res", action="store_true", default=False)
    parser.add_argument("--keep-max-bitrate", action="store_true", default=False)
    parser.add_argument("--hard-delete", action="store_true", default=False,
                        help="生成永久删除脚本（危险！）")
    parser.add_argument("--protect-folder", type=str, default="",
                        help="保护文件夹，逗号分隔")
    parser.add_argument("--audio-check", action="store_true", default=False,
                        help="音频辅助比对（需 FFmpeg）")
    parser.add_argument("--exclude-folder", type=str, default="",
                        help="排除子文件夹名，逗号分隔")
    parser.add_argument("--exclude-size-lt", type=str, default="",
                        help="过滤小于指定大小，如 100MB")
    parser.add_argument("--exclude-size-gt", type=str, default="",
                        help="过滤大于指定大小，如 50GB")
    parser.add_argument("--min-sim", type=float, default=0.0,
                        help="仅导出高于此相似度的分组")
    parser.add_argument("--config", type=str, default=None,
                        help="配置文件路径")
    parser.add_argument("--quiet", action="store_true", default=False,
                        help="静默模式，仅输出最终统计")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="试运行，不生成删除脚本和缓存外文件")
    parser.add_argument("--merge-cache", type=str, default=None,
                        help="合并多个缓存文件")
    parser.add_argument("--verify-cache", action="store_true", default=False,
                        help="校验缓存有效性")
    # v2.2 AI 语义分析参数
    parser.add_argument("--semantic", action="store_true", default=False,
                        help="开启AI视频内容语义分析")
    parser.add_argument("--purpose-filter", type=str, default="",
                        help="仅保留指定用途的视频，逗号分隔（监控,自动驾驶...）")
    parser.add_argument("--cluster-semantic", action="store_true", default=False,
                        help="基于画面内容聚类相似视频")
    parser.add_argument("--export-dataset", action="store_true", default=False,
                        help="导出AI训练集目录清单、标注文件")
    parser.add_argument("--scene-thresh", type=float, default=0.6,
                        help="场景标签置信度阈值，默认 0.6")
    parser.add_argument("--embed-cache", action="store_true", default=False,
                        help="持久化CLIP特征向量到缓存")
    parser.add_argument("--no-semantic-cache", action="store_true", default=False,
                        help="关闭语义特征缓存")
    parser.add_argument("--semantic-workers", type=int, default=1,
                        help="AI推理线程数，默认 1")
    parser.add_argument("--purpose", type=str, default="",
                        help="数据集用途筛选（dataset-filter子命令专用）")
    return parser


def parse_args():
    """解析命令行参数（子命令 + 扁平参数兼容）"""
    shared = _build_shared_parser()

    # 检查第一个有效参数是否为子命令
    subcommands = {"scan", "clean-cache", "merge-cache", "verify-cache", "version", "help",
                   "semantic-analyze", "dataset-filter", "cluster-scene"}

    # 提取第一个非flag参数来判断模式
    first_arg = None
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            first_arg = arg
            break

    is_subcommand = first_arg in subcommands

    if not is_subcommand:
        # 扁平参数模式（v2.0 兼容）
        parser = argparse.ArgumentParser(
            description="MP4 视频相似度查重工具 v2.2",
            parents=[shared],
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        args = parser.parse_args()
        if args.version:
            args.command = "version"
        elif args.clean_cache:
            args.command = "clean-cache"
        elif args.verify_cache:
            args.command = "verify-cache"
        else:
            args.command = "scan"
        return args

    # 子命令模式
    parser = argparse.ArgumentParser(
        description=f"MP4 视频查重工具 v2.2 - {first_arg}",
        parents=[shared],
    )
    parser.add_argument("command", nargs="?", default=first_arg)

    if first_arg == "merge-cache":
        parser.add_argument("cache_files", nargs="+",
                            help="要合并的缓存文件路径")
    elif first_arg == "dataset-filter":
        parser.add_argument("purpose", nargs="?", default="",
                            help="数据集用途筛选，如 监控、自动驾驶")

    args = parser.parse_args()
    args.command = first_arg
    return args


def load_config_file(config_path: str, args):
    """从配置文件加载默认参数，命令行参数优先"""
    if not config_path or not os.path.exists(config_path):
        return args
    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf-8")
    if not config.has_section("scan"):
        return args
    sec = config["scan"]
    mapping = {
        "dir": "dir", "threshold": "threshold", "frames": "frames",
        "workers": "workers", "output_dir": "output-dir",
        "format": "format", "ext": "ext", "min_sim": "min-sim",
        "double_check": "double-check", "audio_check": "audio-check",
        "fast": "fast", "incremental": "incremental",
        "keep_latest": "keep-latest", "keep_max_res": "keep-max-res",
        "keep_max_bitrate": "keep-max-bitrate",
        "semantic": "semantic", "purpose_filter": "purpose-filter",
        "cluster_semantic": "cluster-semantic", "export_dataset": "export-dataset",
        "scene_thresh": "scene-thresh", "embed_cache": "embed-cache",
        "quiet": "quiet", "dry_run": "dry-run",
    }
    for config_key, arg_key in mapping.items():
        if config_key in sec:
            # 仅当命令行未显式传入时才覆盖
            if getattr(args, arg_key.replace("-", "_"), None) in (None, False, 0):
                val = sec[config_key]
                dest = arg_key.replace("-", "_")
                current = getattr(args, dest, None)
                if current is None or current == 0 or current is False:
                    setattr(args, dest, val)
    return args


def validate_args(args):
    """参数二次校验"""
    global _quiet_mode

    if getattr(args, "quiet", False):
        _quiet_mode = True

    cmd = getattr(args, "command", "scan")

    if cmd in ("scan", None) and not getattr(args, "dir", None):
        print("错误: 必须指定 --dir 文件夹路径")
        sys.exit(EXIT_BAD_ARGS)

    threshold = getattr(args, "threshold", 0.7)
    if threshold < 0.0 or threshold > 1.0:
        print("错误: --threshold 必须在 0.0 ~ 1.0 之间")
        sys.exit(EXIT_BAD_ARGS)

    min_sim = getattr(args, "min_sim", 0.0)
    if min_sim < 0.0 or min_sim > 1.0:
        print("错误: --min-sim 必须在 0.0 ~ 1.0 之间")
        sys.exit(EXIT_BAD_ARGS)

    frames = getattr(args, "frames", 10)
    if frames < 1:
        print("错误: --frames 必须 >= 1")
        sys.exit(EXIT_BAD_ARGS)

    workers = getattr(args, "workers", 0)
    if workers < 0:
        print("错误: --workers 必须 >= 0")
        sys.exit(EXIT_BAD_ARGS)

    scene_thresh = getattr(args, "scene_thresh", 0.6)
    if scene_thresh < 0.0 or scene_thresh > 1.0:
        print("错误: --scene-thresh 必须在 0.0 ~ 1.0 之间")
        sys.exit(EXIT_BAD_ARGS)

    semantic_cmd = cmd in ("semantic-analyze", "dataset-filter", "cluster-scene")
    if semantic_cmd and not AI_MODULE_AVAILABLE:
        print("错误: AI 子命令需要 ai_semantic 模块，请安装 torch/open-clip/sklearn")
        sys.exit(EXIT_BAD_ARGS)


# ============================================================
# 模块 3：文件扫描与过滤
# ============================================================
def _resolve_path(path_str: str) -> str:
    """路径转绝对路径，支持 Windows 长路径"""
    resolved = str(Path(path_str).resolve())
    if len(resolved) > 248 and sys.platform == "win32" and not resolved.startswith("\\\\"):
        resolved = "\\\\?\\" + resolved
    return resolved


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


def _match_ignore_rule(file_path: Path, rules: list[str]) -> bool:
    """检查文件是否匹配任意排除规则"""
    name = file_path.name
    rel = str(file_path)
    for rule in rules:
        if fnmatch.fnmatch(name, rule) or fnmatch.fnmatch(rel, rule):
            return True
    return False


def scan_mp4_files(args) -> list[dict]:
    """
    扫描 MP4 文件。
    支持：多后缀、排除文件夹、大小限制、.duplicateignore 规则。
    """
    folder_path = _resolve_path(args.dir)
    recursive = not args.no_recursive
    ext_str = args.ext or "mp4"
    extensions = set(f".{e.lower().lstrip('.')}" for e in ext_str.split(","))
    exclude_folders = set(f.strip().lower() for f in (args.exclude_folder or "").split(",") if f.strip())
    min_size = _parse_size_str(args.exclude_size_lt)
    max_size = _parse_size_str(args.exclude_size_gt)
    ignore_rules = _load_duplicateignore(folder_path)

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

    iterator = folder.rglob("*") if recursive else folder.iterdir()

    for file_path in iterator:
        if not file_path.is_file():
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
        log(f"  [提示] 跳过 {skipped_empty} 个0字节空文件")
    if skipped_size > 0:
        log(f"  [提示] 跳过 {skipped_size} 个超出大小限制的文件")
    if skipped_ignore > 0:
        log(f"  [提示] 跳过 {skipped_ignore} 个匹配 .duplicateignore 规则的文件")

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


# ============================================================
# 模块 4：哈希缓存管理（版本化 + 分块 + 合并/校验）
# ============================================================
def load_cache(cache_path: str) -> dict:
    """加载缓存，版本不匹配时自动作废旧缓存"""
    if not os.path.exists(cache_path):
        return {"_version": CACHE_VERSION}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("缓存格式异常")
        # 版本校验
        stored_version = data.get("_version", "0")
        if stored_version != CACHE_VERSION:
            log(f"  [警告] 缓存版本不匹配 (当前v{CACHE_VERSION}, 缓存v{stored_version})，将重建缓存")
            backup_path = cache_path + ".bak"
            try:
                shutil.copy2(cache_path, backup_path)
                log(f"  [警告] 已备份旧缓存 → {backup_path}")
            except Exception:
                pass
            return {"_version": CACHE_VERSION}
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


def save_cache(cache_path: str, cache: dict, chunk_size: int = 500):
    """
    保存缓存。条目超过 chunk_size 时自动分块。
    """
    try:
        items = [(k, v) for k, v in cache.items() if not k.startswith("_")]
        meta = {k: v for k, v in cache.items() if k.startswith("_")}
        meta["_version"] = CACHE_VERSION

        if len(items) <= chunk_size:
            os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
            full = dict(meta)
            full.update(dict(items))
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(full, f, ensure_ascii=False, indent=2)
            return

        # 分块存储
        base = os.path.splitext(cache_path)[0]
        for old in Path(cache_path).parent.glob(os.path.basename(base) + "_part*.json"):
            old.unlink()

        for i in range(0, len(items), chunk_size):
            chunk = dict(items[i:i + chunk_size])
            part_path = f"{base}_part{i // chunk_size + 1}.json"
            part_data = dict(meta)
            part_data.update(chunk)
            with open(part_path, "w", encoding="utf-8") as f:
                json.dump(part_data, f, ensure_ascii=False, indent=2)

        # 写主索引
        meta["_chunks"] = (len(items) + chunk_size - 1) // chunk_size
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    except (IOError, OSError) as e:
        log(f"  [警告] 缓存保存失败: {e}")


def is_cache_valid(cache_entry: dict, file_info: dict) -> bool:
    """检查缓存条目有效性"""
    return (
        cache_entry.get("size") == file_info["size"]
        and abs(cache_entry.get("mtime", 0) - file_info["mtime"]) < 1.0
    )


def clean_invalid_cache(cache_path: str) -> tuple[int, int]:
    """清理无效缓存"""
    cache = load_cache(cache_path)
    removed = 0
    kept = {"_version": CACHE_VERSION}

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
        kept[path] = entry

    save_cache(cache_path, kept)
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
def _compute_frame_hashes(gray_frame: np.ndarray) -> tuple:
    """对灰度帧计算 pHash + dHash"""
    pil_img = Image.fromarray(gray_frame)
    phash = imagehash.phash(pil_img, hash_size=HASH_SIZE)
    dhash = imagehash.dhash(pil_img, hash_size=HASH_SIZE)
    return phash, dhash


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
    use_audio: bool = False,
) -> tuple[Optional[dict], Optional[str]]:
    """
    单视频哈希提取。
    返回 (hash_dict或None, 错误类型或None)
    hash_dict = {"phash": [hash_obj,...], "dhash": [hash_obj,...],
                 "duration": float, "width": int, "height": int,
                 "fps": float, "audio": [hash_obj,...]或None}
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

        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            # 缩放至 32x32 灰度
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
            ph, dh = _compute_frame_hashes(small)
            phashes.append(ph)
            dhashes.append(dh)

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
    global _global_cache

    cache = load_cache(cache_path) if use_cache else {"_version": CACHE_VERSION}
    _global_cache = cache
    video_hashes = {}
    bad_videos = []
    to_compute = {}

    double_check = args.double_check
    use_audio = getattr(args, "audio_check", False)
    incremental = args.incremental

    # 缓存判定
    for i, file_info in enumerate(mp4_files):
        path = file_info["path"]
        if use_cache and path in cache and is_cache_valid(cache[path], file_info):
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
        pbar = _tqdm(total=total, desc="提取哈希", unit="视频", ncols=80)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {}
        for idx, file_info in to_compute.items():
            future = executor.submit(
                _extract_hashes_single, file_info["path"],
                num_frames, double_check, use_audio,
            )
            future_to_idx[future] = (idx, file_info)

        for future in as_completed(future_to_idx):
            idx, file_info = future_to_idx[future]
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
                }
                _global_cache = cache
            else:
                bad_videos.append({
                    "path": path,
                    "error_type": err_type or ERR_DECODE_ERROR,
                })

            completed += 1
            mem_info = _get_memory_usage()
            extra = f" 内存:{mem_info}" if mem_info else ""

            if TQDM_AVAILABLE:
                pbar.set_postfix_str(f"{extra}")
                pbar.update(1)
            else:
                pct = completed / total * 100
                log(f"  哈希进度: {completed}/{total} ({pct:.0f}%){extra}", "\r")

            # 分批内存管控
            if mem_limit > 0 and completed % 10 == 0:
                mem = _get_memory_usage()
                if mem:
                    try:
                        mem_val = float(mem.replace(" MB", ""))
                        if mem_val > mem_limit:
                            save_cache(cache_path, cache)
                            log(f"  [内存管控] 已达 {mem}，强制落地缓存")
                    except ValueError:
                        pass

            # 每 10 个保存一次缓存
            if use_cache and completed % 10 == 0:
                save_cache(cache_path, cache)

    if TQDM_AVAILABLE:
        pbar.close()
    log("")

    if use_cache:
        save_cache(cache_path, cache)

    return video_hashes, bad_videos, cache


# ============================================================
# 模块 6：视频相似度比对（时长预筛 + LSH + double-check）
# ============================================================
def compute_similarity(hash_dict1: dict, hash_dict2: dict) -> dict:
    """
    双哈希融合相似度计算：pHash(0.7) + dHash(0.3) + 音频辅助。
    """
    if not hash_dict1 or not hash_dict2:
        return {"distance": 1.0, "similarity": 0.0}

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

    # 加权融合
    if phash_list1 and dhash_list1:
        similarity = 0.7 * phash_sim + 0.3 * dhash_sim
    elif phash_list1:
        similarity = phash_sim
    else:
        similarity = dhash_sim

    # 音频辅助（如可用）
    audio1 = hash_dict1.get("audio")
    audio2 = hash_dict2.get("audio")
    if audio1 and audio2:
        audio_sim = _hash_list_similarity(audio1, audio2)
        # 音频相似度低于0.3时给予惩罚
        if audio_sim < 0.3:
            similarity *= 0.8

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
    """
    构建 LSH 分桶索引：将视频按哈希前缀分桶，快速缩小比对范围。
    """
    buckets = defaultdict(list)
    for idx, hash_dict in video_hashes.items():
        phash_list = hash_dict.get("phash", [])
        if phash_list:
            # 取第一个 pHash 的前 N 位作为桶键
            ph = phash_list[0]
            bucket_bits = max(1, int(64 / num_buckets))
            bucket_key = str(ph)[:bucket_bits]
            buckets[bucket_key].append(idx)
        else:
            buckets[f"__empty__"].append(idx)
    return buckets


def find_similar_pairs(
    video_hashes: dict,
    mp4_files: list[dict],
    threshold: float,
    skip_pairs: Optional[set] = None,
    lsh_buckets: int = 0,
) -> list[dict]:
    """
    两两比对。支持 LSH 加速和预筛跳过。
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

    # LSH 加速
    use_lsh = lsh_buckets > 0 and n > 50
    if use_lsh:
        buckets = _build_lsh_buckets(video_hashes, lsh_buckets)
        log(f"  LSH 分桶: {len(buckets)} 个桶")

    if TQDM_AVAILABLE:
        pbar = _tqdm(total=total_pairs, desc="比对相似度", unit="对", ncols=80)

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


def pre_filter_by_metadata(mp4_files: list[dict]) -> tuple:
    """快速预筛：按 size+mtime 分组"""
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


# ============================================================
# 模块 7：连通图分组（多维度保留策略）
# ============================================================
def build_groups(
    similar_pairs: list[dict],
    mp4_files: list[dict],
    video_hashes: dict,
    keep_strategy: str = "max-size",
) -> list[dict]:
    """
    连通图分组，支持多维度保留策略。
    keep_strategy: max-size / latest / max-res / max-bitrate
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

    groups = []
    for root, members in group_map.items():
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
def export_csv(similar_pairs: list[dict], csv_path: str, threshold: float, semantic_data: dict = None):
    """导出比对明细 CSV（v2.2 扩展语义标签列）"""
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            header = ["视频A路径", "视频B路径", "归一化距离", "相似度", "是否判定重复"]
            if semantic_data:
                header.extend(["A场景标签", "A数据集用途", "B场景标签", "B数据集用途"])
            writer.writerow(header)
            for pair in similar_pairs:
                is_dup = "是" if pair["similarity"] >= threshold else "否"
                row = [
                    pair["path_a"], pair["path_b"],
                    f"{pair['distance']:.6f}", f"{pair['similarity']:.4f}", is_dup,
                ]
                if semantic_data:
                    sd_a = semantic_data.get(pair["path_a"], {})
                    sd_b = semantic_data.get(pair["path_b"], {})
                    scene_a = ", ".join(sd_a.get("scene_tags", [])[:3]) or "-"
                    purpose_a = sd_a.get("dataset_purpose", "-") or "-"
                    scene_b = ", ".join(sd_b.get("scene_tags", [])[:3]) or "-"
                    purpose_b = sd_b.get("dataset_purpose", "-") or "-"
                    row.extend([scene_a, purpose_a, scene_b, purpose_b])
                writer.writerow(row)
        log(f"[导出] CSV → {csv_path}")
    except (IOError, OSError) as e:
        log(f"[错误] CSV 导出失败: {e}")


def export_groups_txt(groups: list[dict], mp4_files: list[dict], group_path: str,
                      threshold: float, min_sim: float = 0.0, semantic_data: dict = None):
    """导出分组报告 TXT（v2.2 扩展语义标签）"""
    try:
        filtered = []
        for g in groups:
            max_sim = max(g["similarities"].values()) if g["similarities"] else 1.0
            if max_sim >= min_sim:
                filtered.append(g)

        with open(group_path, "w", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write(f"  MP4 相似视频分组报告\n")
            f.write(f"  相似度阈值: {threshold:.0%}\n")
            f.write(f"  生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"  分组总数: {len(filtered)}\n")
            f.write("=" * 60 + "\n\n")

            if not filtered:
                f.write("未发现符合条件的相似视频分组。\n")
            else:
                for gi, group in enumerate(filtered, 1):
                    f.write(f"【第 {gi} 组】（共 {len(group['members'])} 个视频）\n")
                    f.write("-" * 50 + "\n")
                    # 统计该组的数据集用途
                    if semantic_data:
                        purposes = []
                        for idx, info in group["members"]:
                            sd = semantic_data.get(info["path"], {})
                            p = sd.get("dataset_purpose", "")
                            if p:
                                purposes.append(p)
                        if purposes:
                            from collections import Counter
                            pc = Counter(purposes)
                            f.write(f"  内容归类: {dict(pc)}\n")
                    for fi, (idx, info) in enumerate(group["members"]):
                        mark = " ★ 建议保留" if idx == group["retain_idx"] else " ✗ 建议清理"
                        f.write(f"  {fi + 1}. {info['name']}{mark}\n")
                        f.write(f"     路径: {info['path']}\n")
                        f.write(f"     大小: {info['size_readable']}\n")
                        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info["mtime"]))
                        f.write(f"     修改时间: {mtime}\n")
                        # v2.2 语义标签
                        if semantic_data:
                            sd = semantic_data.get(info["path"], {})
                            if sd.get("scene_tags"):
                                f.write(f"     场景: {', '.join(sd['scene_tags'][:3])}\n")
                            if sd.get("dataset_purpose"):
                                f.write(f"     用途: {sd['dataset_purpose']}\n")
                            if sd.get("quality_score", 0) > 0:
                                qs = sd["quality_score"]
                                quality_label = "✓适合训练" if sd.get("is_training_ready") else "✗质量不足"
                                f.write(f"     质量: {qs:.2f} ({quality_label})\n")
                        f.write("\n")
                    f.write("-" * 50 + "\n\n")
        log(f"[导出] 分组报告(TXT) → {group_path}")
    except (IOError, OSError) as e:
        log(f"[错误] TXT 导出失败: {e}")


def export_groups_md(groups: list[dict], mp4_files: list[dict], md_path: str,
                      threshold: float, min_sim: float = 0.0, semantic_data: dict = None):
    """导出分组报告 Markdown（v2.2 扩展语义标签）"""
    try:
        filtered = []
        for g in groups:
            max_sim = max(g["similarities"].values()) if g["similarities"] else 1.0
            if max_sim >= min_sim:
                filtered.append(g)

        total_savable = 0
        for g in filtered:
            for idx, info in g["members"]:
                if idx != g["retain_idx"]:
                    total_savable += info["size"]

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# MP4 相似视频分组报告\n\n")
            f.write(f"- **相似度阈值**: {threshold:.0%}\n")
            f.write(f"- **生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- **分组总数**: {len(filtered)}\n")
            f.write(f"- **可节省空间**: {_format_size(total_savable)}\n\n")

            if not filtered:
                f.write("> 未发现符合条件的相似视频分组。\n")
            else:
                for gi, group in enumerate(filtered, 1):
                    f.write(f"## 第 {gi} 组（{len(group['members'])} 个视频）\n\n")
                    # v2.2 内容归类统计
                    if semantic_data:
                        purposes = []
                        for idx, info in group["members"]:
                            sd = semantic_data.get(info["path"], {})
                            p = sd.get("dataset_purpose", "")
                            if p:
                                purposes.append(p)
                        if purposes:
                            from collections import Counter
                            pc = Counter(purposes)
                            f.write(f"> 内容归类: {dict(pc)}\n\n")
                    f.write("| # | 文件名 | 大小 | 修改时间 | 建议 |")
                    if semantic_data:
                        f.write(" 场景 | 用途 |")
                    f.write("\n")
                    f.write("|---|--------|------|----------|------|")
                    if semantic_data:
                        f.write("------|------|")
                    f.write("\n")
                    for fi, (idx, info) in enumerate(group["members"]):
                        mark = "✅ 保留" if idx == group["retain_idx"] else "❌ 清理"
                        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info["mtime"]))
                        line = f"| {fi + 1} | `{info['name']}` | {info['size_readable']} | {mtime} | {mark} |"
                        if semantic_data:
                            sd = semantic_data.get(info["path"], {})
                            scene = ", ".join(sd.get("scene_tags", [])[:2]) or "-"
                            purpose = sd.get("dataset_purpose", "-") or "-"
                            line += f" {scene} | {purpose} |"
                        f.write(line + "\n")
                    f.write("\n")
        log(f"[导出] 分组报告(MD) → {md_path}")
    except (IOError, OSError) as e:
        log(f"[错误] MD 导出失败: {e}")


def export_groups_html(groups: list[dict], mp4_files: list[dict], html_path: str,
                        threshold: float, min_sim: float = 0.0, semantic_data: dict = None):
    """导出 HTML 可视化报告（v2.2 扩展语义标签）"""
    try:
        filtered = []
        for g in groups:
            max_sim = max(g["similarities"].values()) if g["similarities"] else 1.0
            if max_sim >= min_sim:
                filtered.append(g)

        total_savable = 0
        for g in filtered:
            for idx, info in g["members"]:
                if idx != g["retain_idx"]:
                    total_savable += info["size"]

        # v2.2 语义统计
        semantic_summary = {}
        if semantic_data:
            for path, sd in semantic_data.items():
                p = sd.get("dataset_purpose", "未分类")
                semantic_summary[p] = semantic_summary.get(p, 0) + 1

        html_parts = [
            "<!DOCTYPE html>\n<html lang='zh-CN'>\n<head>\n",
            "<meta charset='UTF-8'>\n",
            "<title>MP4 相似视频报告</title>\n",
            "<style>",
            "body{font-family:sans-serif;margin:20px;background:#f5f5f5}",
            "h1{color:#333}h2{color:#555;margin-top:30px}",
            ".group{background:#fff;border-radius:8px;padding:15px;margin:10px 0;",
            "box-shadow:0 2px 4px rgba(0,0,0,.1)}",
            ".video{display:flex;align-items:center;padding:8px;border-bottom:1px solid #eee}",
            ".video:last-child{border-bottom:none}",
            ".badge{padding:2px 8px;border-radius:4px;font-size:12px;margin-left:10px}",
            ".retain{background:#4CAF50;color:#white}",
            ".clean{background:#f44336;color:#white}",
            ".stats{background:#fff;padding:15px;border-radius:8px;margin:15px 0}",
            ".summary{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}",
            ".summary div{background:#f8f8f8;padding:10px;border-radius:4px;text-align:center}",
            ".semantic-tag{background:#2196F3;color:#white;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:5px}",
            ".purpose-tag{background:#FF9800;color:white;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:5px}",
            "</style>\n</head>\n<body>\n",
            f"<h1>MP4 相似视频分组报告</h1>\n",
            f"<div class='stats'><div class='summary'>",
            f"<div><h3>{len(mp4_files)}</h3><p>总视频数</p></div>",
            f"<div><h3>{len(filtered)}</h3><p>相似分组</p></div>",
            f"<div><h3>{_format_size(total_savable)}</h3><p>可节省空间</p></div>",
            f"<div><h3>{threshold:.0%}</h3><p>相似度阈值</p></div>",
            f"</div></div>\n",
        ]

        # v2.2 语义统计面板
        if semantic_summary:
            html_parts.append("<div class='stats'><h3>📊 AI 语义统计</h3>")
            html_parts.append("<div class='summary'>")
            for purpose, count in sorted(semantic_summary.items(), key=lambda x: -x[1]):
                html_parts.append(f"<div><h3>{count}</h3><p>{purpose}</p></div>")
            html_parts.append("</div></div>\n")

        for gi, group in enumerate(filtered, 1):
            html_parts.append(f"<div class='group'><h2>第 {gi} 组</h2>\n")
            for fi, (idx, info) in enumerate(group["members"]):
                mark = "retain" if idx == group["retain_idx"] else "clean"
                text = "保留" if idx == group["retain_idx"] else "清理"
                extra_tags = ""
                if semantic_data:
                    sd = semantic_data.get(info["path"], {})
                    if sd.get("scene_tags"):
                        extra_tags += "".join(
                            f"<span class='semantic-tag'>{t}</span>"
                            for t in sd["scene_tags"][:2]
                        )
                    if sd.get("dataset_purpose"):
                        extra_tags += f"<span class='purpose-tag'>{sd['dataset_purpose']}</span>"
                html_parts.append(
                    f"<div class='video'>"
                    f"<span>{fi + 1}. <b>{info['name']}</b></span>"
                    f"<span style='margin-left:auto'>{info['size_readable']}</span>"
                    f"<span class='badge {mark}'>{text}</span>"
                    f"{extra_tags}"
                    f"</div>\n"
                )
            html_parts.append("</div>\n")

        html_parts.append("</body>\n</html>")

        with open(html_path, "w", encoding="utf-8") as f:
            f.writelines(html_parts)
        log(f"[导出] HTML 报告 → {html_path}")
    except (IOError, OSError) as e:
        log(f"[错误] HTML 导出失败: {e}")


def export_paths_list(groups: list[dict], mp4_files: list[dict], paths_path: str):
    """导出纯路径清单"""
    try:
        with open(paths_path, "w", encoding="utf-8") as f:
            for gi, group in enumerate(groups, 1):
                f.write(f"# 第 {gi} 组\n")
                for idx, info in group["members"]:
                    f.write(f"{info['path']}\n")
                f.write("\n")
        log(f"[导出] 路径清单 → {paths_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 路径清单导出失败: {e}")


def export_bad_videos(bad_videos: list[dict], bad_path: str):
    """导出损坏清单（带故障分类）"""
    try:
        error_labels = {
            ERR_READ_FAILED: "视频打开失败",
            ERR_ZERO_FRAMES: "帧数为0",
            ERR_TIMEOUT: "提取超时",
            ERR_DECODE_ERROR: "解码异常",
            ERR_PERMISSION: "权限不足",
            ERR_DISK_ERROR: "磁盘读取错误",
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
    """导出全量哈希备份"""
    try:
        backup = {}
        for idx, hash_dict in video_hashes.items():
            if idx < len(mp4_files):
                backup[mp4_files[idx]["path"]] = {
                    "name": mp4_files[idx]["name"],
                    "size": mp4_files[idx]["size"],
                    "phash": [str(h) for h in hash_dict.get("phash", [])],
                    "dhash": [str(h) for h in hash_dict.get("dhash", [])],
                    "duration": hash_dict.get("duration", 0),
                    "width": hash_dict.get("width", 0),
                    "height": hash_dict.get("height", 0),
                    "fps": hash_dict.get("fps", 0),
                }
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
        log(f"[导出] 哈希备份 → {export_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 哈希备份导出失败: {e}")


def generate_cleanup_script(
    groups: list[dict], mp4_files: list[dict],
    output_dir: str, hard_delete: bool = False,
    protect_folders: set = None,
):
    """
    生成清理脚本。
    默认：移动至回收站/垃圾桶（安全模式）。
    --hard-delete：永久删除。
    """
    protect_folders = protect_folders or set()
    protect_folders.add(os.path.expanduser("~/Desktop"))
    protect_folders.add(os.path.expanduser("~/"))

    def is_protected(path: str) -> bool:
        path_lower = path.lower()
        for pf in protect_folders:
            if path_lower.startswith(pf.lower()):
                return True
        return False

    # Windows BAT
    bat_path = os.path.join(output_dir, CLEANUP_SCRIPT_WIN)
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write("@echo off\r\n")
        f.write("chcp 65001 >nul\r\n")
        f.write(f"REM 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\r\n")
        f.write(f"REM 模式: {'永久删除 (危险!)' if hard_delete else '安全模式（移动至回收站）'}\r\n")
        f.write("echo.\r\n")
        f.write("echo ============================================\r\n")
        f.write("echo   MP4 重复视频清理脚本\r\n")
        f.write("echo ============================================\r\n")
        f.write("echo.\r\n")
        f.write("echo 以下文件将被处理：\r\n")
        f.write("echo.\r\n")

        to_delete = []
        for gi, group in enumerate(groups, 1):
            retain_path = mp4_files[group["retain_idx"]]["path"]
            f.write(f"REM --- 第 {gi} 组 (保留: {mp4_files[group['retain_idx']]['name']}) ---\r\n")
            for idx, info in group["members"]:
                if idx != group["retain_idx"]:
                    path = info["path"]
                    if is_protected(path):
                        f.write(f"REM [已保护] {path}\r\n")
                        continue
                    f.write(f"echo   {path}\r\n")
                    to_delete.append(path)
            f.write("\r\n")

        f.write("echo.\r\n")
        f.write("set /p CONFIRM=输入 CONFIRM 执行操作: \r\n")
        f.write('if /i not "%CONFIRM%"=="CONFIRM" (\r\n')
        f.write('    echo 已取消\r\n')
        f.write('    pause\r\n')
        f.write('    exit /b 0\r\n')
        f.write(")\r\n\r\n")

        for path in to_delete:
            if hard_delete:
                f.write(f'del /f /q "{path}"\r\n')
            else:
                f.write(f'move "{path}" "%TEMP%\\trash_%RANDOM%_"\r\n')

        f.write("\r\necho.\r\n")
        f.write("echo 清理完成！\r\n")
        f.write("pause\r\n")

    # Linux SH
    sh_path = os.path.join(output_dir, CLEANUP_SCRIPT_LINUX)
    with open(sh_path, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# 模式: {'永久删除 (危险!)' if hard_delete else '安全模式（移动至 ~/.trash）'}\n\n")
        f.write('echo "==========================================="\n')
        f.write('echo "  MP4 重复视频清理脚本"\n')
        f.write('echo "==========================================="\n')
        f.write("echo.\n")

        to_delete = []
        for gi, group in enumerate(groups, 1):
            f.write(f'# --- 第 {gi} 组 ---\n')
            for idx, info in group["members"]:
                if idx != group["retain_idx"]:
                    path = info["path"]
                    if is_protected(path):
                        f.write(f"# [已保护] {path}\n")
                        continue
                    f.write(f'echo "  {path}"\n')
                    to_delete.append(path)
            f.write("\n")

        f.write('if [ "$CONFIRM" != "CONFIRM" ]; then\n')
        f.write('    echo "已取消"\n')
        f.write('    exit 0\n')
        f.write("fi\n\n")

        for path in to_delete:
            if hard_delete:
                f.write(f'rm -f "{path}"\n')
            else:
                f.write(f'mv "{path}" ~/.trash/\n')

        f.write('\necho "清理完成！"\n')

    log(f"[导出] 清理脚本 → {bat_path} / {sh_path}")


def export_audit_log(output_dir: str, groups: list[dict], mp4_files: list[dict], hard_delete: bool, semantic_data: dict = None):
    """导出清理操作审计日志（v2.2 扩展语义备注）"""
    try:
        audit_path = os.path.join(output_dir, AUDIT_LOG)
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"模式: {'永久删除' if hard_delete else '安全移动'}\n")
            f.write(f"分组数: {len(groups)}\n")
            for gi, group in enumerate(groups, 1):
                f.write(f"\n第{gi}组:\n")
                retain_info = mp4_files[group['retain_idx']]
                f.write(f"  保留: {retain_info['path']}")
                if semantic_data:
                    sd = semantic_data.get(retain_info["path"], {})
                    if sd.get("dataset_purpose"):
                        f.write(f" ({sd['dataset_purpose']})")
                f.write("\n")
                for idx, info in group["members"]:
                    if idx != group["retain_idx"]:
                        f.write(f"  清理: {info['path']} ({info['size_readable']})")
                        if semantic_data:
                            sd = semantic_data.get(info["path"], {})
                            if sd.get("dataset_purpose"):
                                f.write(f" [{sd['dataset_purpose']}]")
                        f.write("\n")
        log(f"[导出] 审计日志 → {audit_path}")
    except (IOError, OSError):
        pass


def print_summary(
    mp4_files: list[dict],
    video_hashes: dict,
    bad_videos: list[dict],
    groups: list[dict],
    semantic_results: dict = None,
):
    """打印统计汇总（v2.2 扩展语义统计）"""
    total = len(mp4_files)
    success = len(video_hashes)
    failed = len(bad_videos)
    group_count = len(groups)
    dup_total = sum(len(g["members"]) for g in groups)

    log("")
    log("=" * 60, force=True)
    log("  统计汇总", force=True)
    log("=" * 60, force=True)
    log(f"  扫描视频总数:     {total}", force=True)
    log(f"  哈希成功数量:     {success}", force=True)
    log(f"  解析失败数量:     {failed}", force=True)
    log(f"  相似分组总数:     {group_count}", force=True)
    log(f"  涉及重复视频数:   {dup_total}", force=True)
    mem = _get_memory_usage()
    if mem:
        log(f"  峰值内存占用:     {mem}", force=True)

    # v2.2 AI 语义统计
    if semantic_results:
        log("", force=True)
        log("  📊 AI 语义分析统计:", force=True)
        total_analyzed = len(semantic_results)
        log(f"    已分析视频:     {total_analyzed}", force=True)
        from collections import Counter
        purpose_counts = Counter()
        scene_counts = Counter()
        train_ready = 0
        for idx, sd in semantic_results.items():
            p = sd.get("dataset_purpose", "未分类")
            purpose_counts[p] += 1
            for s in sd.get("scene_tags", [])[:2]:
                scene_counts[s] += 1
            if sd.get("is_training_ready"):
                train_ready += 1
        for purpose, count in purpose_counts.most_common():
            log(f"    {purpose}: {count} 个", force=True)
        if scene_counts:
            log("    高频场景:", force=True)
            for scene, count in scene_counts.most_common(5):
                log(f"      {scene}: {count} 个", force=True)
        log(f"    适合训练:       {train_ready} 个", force=True)

    log("=" * 60, force=True)

    if bad_videos:
        log("\n  损坏视频清单：", force=True)
        error_labels = {
            ERR_READ_FAILED: "视频打开失败",
            ERR_ZERO_FRAMES: "帧数为0",
            ERR_TIMEOUT: "提取超时",
            ERR_DECODE_ERROR: "解码异常",
            ERR_PERMISSION: "权限不足",
            ERR_DISK_ERROR: "磁盘读取错误",
            ERR_LOW_QUALITY: "画面质量低",
        }
        for bv in bad_videos:
            label = error_labels.get(bv["error_type"], bv["error_type"])
            log(f"    ✗ [{label}] {bv['path']}", force=True)

    if groups:
        log("\n  相似分组概览：", force=True)
        for gi, group in enumerate(groups, 1):
            names = [info["name"] for _, info in group["members"]]
            log(f"    第{gi}组: {', '.join(names)}", force=True)


# ============================================================
# 模块 9：全局异常处理与主入口
# ============================================================
def _crash_save(output_dir: str, cache_path: str = None, partial_results: dict = None):
    """异常退出时保存缓存和部分结果"""
    global _global_cache, _global_results_saved
    try:
        if cache_path and _global_cache:
            save_cache(cache_path, _global_cache)
            log("[异常恢复] 已保存哈希缓存", force=True)
        if partial_results:
            try:
                crash_path = os.path.join(output_dir, "crash_partial.json")
                with open(crash_path, "w", encoding="utf-8") as f:
                    json.dump(partial_results, f, ensure_ascii=False, indent=2)
                log("[异常恢复] 已保存部分结果", force=True)
            except Exception:
                pass
        _global_results_saved = True
    except Exception:
        pass


def _signal_handler(signum, frame):
    """Ctrl+C 信号处理"""
    global _global_cache
    log("\n[中断] 正在保存数据...", force=True)
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cache_path = os.path.join(script_dir, CACHE_FILE)
        if _global_cache:
            save_cache(cache_path, _global_cache)
            log("[中断] 哈希缓存已保存", force=True)
    except Exception:
        pass
    log("[中断] 数据已保存，可安全退出", force=True)
    sys.exit(130)


def _print_version():
    """打印版本信息"""
    log(f"MP4 视频相似度查重工具 v{__version__}", force=True)
    log(f"Python: {sys.version}", force=True)
    log(f"OpenCV: {cv2.__version__}", force=True)
    log(f"NumPy: {np.__version__}", force=True)
    log(f"Pillow: {Image.__version__}", force=True)
    log(f"imagehash: {imagehash.__version__}", force=True)
    log(f"tqdm: {'已安装' if TQDM_AVAILABLE else '未安装（可选）'}", force=True)
    log(f"psutil: {'已安装' if PSUTIL_AVAILABLE else '未安装（可选）'}", force=True)
    log(f"FFmpeg: {'已安装' if FFMPEG_AVAILABLE else '未安装（可选）'}", force=True)
    log(f"缓存版本: {CACHE_VERSION}", force=True)
    log(f"AI 模块: {'已加载' if AI_MODULE_AVAILABLE else '未安装'}", force=True)
    if AI_MODULE_AVAILABLE:
        log(f"  PyTorch: {'已安装' if _AI_TORCH_OK else '未安装'}", force=True)
        log(f"  CLIP: {'已安装' if _AI_CLIP_OK else '未安装'}", force=True)
        log(f"  sklearn: {'已安装' if _AI_SKLEARN_OK else '未安装'}", force=True)
        log(f"  CUDA: {'可用' if _AI_CUDA_OK else '不可用'}", force=True)


def _get_keep_strategy(args) -> str:
    """从参数确定保留策略"""
    if getattr(args, "keep_latest", False):
        return "latest"
    elif getattr(args, "keep_max_res", False):
        return "max-res"
    elif getattr(args, "keep_max_bitrate", False):
        return "max-bitrate"
    return "max-size"


def _auto_workers(args) -> int:
    """确定线程数：取 min(CPU*1.2, workers)"""
    cpu_count = os.cpu_count() or 4
    auto = max(1, int(cpu_count * 1.2))
    workers = args.workers
    if workers <= 0:
        return auto
    return min(auto, workers)


# ============================================================
# v2.2 AI 辅助函数
# ============================================================
def _ensure_clip_model():
    """确保 CLIP 模型已加载"""
    global _semantic_clip_model, _semantic_clip_preprocess, _semantic_clip_device
    if _semantic_clip_model is not None:
        return _semantic_clip_model, _semantic_clip_preprocess, _semantic_clip_device
    if not AI_MODULE_AVAILABLE:
        log("[AI] ai_semantic 模块未加载", force=True)
        return None, None, None
    if not _AI_TORCH_OK or not _AI_CLIP_OK:
        log("[AI] PyTorch/CLIP 未安装，AI 功能不可用", force=True)
        return None, None, None
    try:
        model, preprocess, device = load_clip_model()
        _semantic_clip_model = model
        _semantic_clip_preprocess = preprocess
        _semantic_clip_device = device
        return model, preprocess, device
    except Exception as e:
        log(f"[AI] CLIP 模型加载失败: {e}", force=True)
        return None, None, None


def _run_semantic_analysis(
    mp4_files: list[dict], cache_path: str,
    model, preprocess, device, args,
) -> dict:
    """对所有视频执行语义分析，返回 {path: semantic_data}"""
    global _semantic_stats
    scene_thresh = getattr(args, "scene_thresh", 0.6)
    embed = getattr(args, "embed_cache", False) and not getattr(args, "no_semantic_cache", False)
    semantic_results = {}
    use_cache = not getattr(args, "no_cache", False) and embed

    # 尝试从缓存加载已有语义数据
    cache = load_cache(cache_path) if use_cache else {"_version": CACHE_VERSION}
    to_analyze = []

    for i, file_info in enumerate(mp4_files):
        path = file_info["path"]
        if use_cache and path in cache and cache[path].get("scene_tags"):
            entry = cache[path]
            semantic_results[i] = {
                "scene_tags": entry.get("scene_tags", []),
                "object_tags": entry.get("object_tags", []),
                "action_tags": entry.get("action_tags", []),
                "dataset_purpose": entry.get("dataset_purpose", ""),
                "semantic_conf": entry.get("semantic_conf", {}),
                "quality_score": entry.get("quality_score", 0.0),
                "is_training_ready": entry.get("is_training_ready", False),
            }
            continue
        to_analyze.append((i, file_info))

    if not to_analyze:
        log("  AI 语义分析: 全部缓存命中，无需重新计算")
        return semantic_results

    log(f"  AI 语义分析: {len(to_analyze)} 个视频待分析...")
    total = len(to_analyze)
    completed = 0

    if TQDM_AVAILABLE:
        pbar = _tqdm(total=total, desc="AI语义分析", unit="视频", ncols=80)

    for idx, file_info in to_analyze:
        path = file_info["path"]
        try:
            result = semantic_analyze_video(
                path, model, preprocess, device,
                num_frames=10, scene_thresh=scene_thresh,
            )
            if result:
                semantic_results[idx] = result
                if embed:
                    cache[path] = cache.get(path, {})
                    cache[path].update({
                        "scene_tags": result.get("scene_tags", []),
                        "object_tags": result.get("object_tags", []),
                        "action_tags": result.get("action_tags", []),
                        "dataset_purpose": result.get("dataset_purpose", ""),
                        "semantic_conf": result.get("semantic_conf", {}),
                        "quality_score": result.get("quality_score", 0.0),
                        "is_training_ready": result.get("is_training_ready", False),
                    })
            else:
                semantic_results[idx] = {"scene_tags": [], "object_tags": [],
                                          "action_tags": [], "dataset_purpose": "",
                                          "semantic_conf": {}, "quality_score": 0.0,
                                          "is_training_ready": False}
        except Exception as e:
            log(f"  [警告] AI 分析失败: {path} - {e}")
            semantic_results[idx] = {"scene_tags": [], "object_tags": [],
                                      "action_tags": [], "dataset_purpose": "",
                                      "semantic_conf": {}, "quality_score": 0.0,
                                      "is_training_ready": False}

        completed += 1
        if TQDM_AVAILABLE:
            pbar.update(1)
        else:
            pct = completed / total * 100
            log(f"  AI进度: {completed}/{total} ({pct:.0f}%)", "\r")

        if use_cache and completed % 5 == 0:
            save_cache(cache_path, cache)

    if TQDM_AVAILABLE:
        pbar.close()
    log("")

    if use_cache:
        save_cache(cache_path, cache)

    # 统计
    _semantic_stats = {}
    for idx, sd in semantic_results.items():
        purpose = sd.get("dataset_purpose", "未分类")
        _semantic_stats[purpose] = _semantic_stats.get(purpose, 0) + 1

    log(f"  AI 分析完成: {len(semantic_results)} 个视频", force=True)
    for purpose, count in sorted(_semantic_stats.items()):
        log(f"    {purpose}: {count} 个", force=True)

    return semantic_results


def _run_semantic_analyze(args):
    """执行纯语义分析子命令"""
    global _quiet_mode
    _quiet_mode = args.quiet
    folder_path = _resolve_path(args.dir)
    recursive = not args.no_recursive

    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(output_dir, exist_ok=True)

    log_init(output_dir)
    signal.signal(signal.SIGINT, _signal_handler)

    if not AI_MODULE_AVAILABLE:
        log("错误: ai_semantic 模块未加载，请先安装依赖", force=True)
        sys.exit(EXIT_BAD_ARGS)

    model, preprocess, device = _ensure_clip_model()
    if model is None:
        sys.exit(EXIT_BAD_ARGS)

    log("=" * 60, force=True)
    log("  MP4 视频 AI 语义分析", force=True)
    log("=" * 60, force=True)
    log(f"  扫描目录: {folder_path}", force=True)

    mp4_files = scan_mp4_files(args)
    if not mp4_files:
        log("未找到视频文件", force=True)
        return

    log(f"  共 {len(mp4_files)} 个视频", force=True)

    cache_path = os.path.join(output_dir, CACHE_FILE)
    semantic_results = _run_semantic_analysis(
        mp4_files, cache_path, model, preprocess, device, args,
    )

    # 导出
    _export_semantic_results(mp4_files, semantic_results, output_dir, args)

    log(f"\n完成！结果已保存至: {output_dir}", force=True)


def _run_dataset_filter(args):
    """数据集筛选子命令"""
    global _quiet_mode
    _quiet_mode = args.quiet
    folder_path = _resolve_path(args.dir)
    recursive = not args.no_recursive
    purpose_filter = args.purpose or getattr(args, "purpose_filter", "")

    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(output_dir, exist_ok=True)

    log_init(output_dir)
    signal.signal(signal.SIGINT, _signal_handler)

    if not AI_MODULE_AVAILABLE:
        log("错误: ai_semantic 模块未加载", force=True)
        sys.exit(EXIT_BAD_ARGS)

    model, preprocess, device = _ensure_clip_model()
    if model is None:
        sys.exit(EXIT_BAD_ARGS)

    log("=" * 60, force=True)
    log(f"  数据集筛选 (用途: {purpose_filter or '全部'})", force=True)
    log("=" * 60, force=True)

    mp4_files = scan_mp4_files(args)
    if not mp4_files:
        log("未找到视频文件", force=True)
        return

    log(f"  共 {len(mp4_files)} 个视频", force=True)

    cache_path = os.path.join(output_dir, CACHE_FILE)
    semantic_results = _run_semantic_analysis(
        mp4_files, cache_path, model, preprocess, device, args,
    )

    # 按用途筛选
    if purpose_filter:
        purposes = [p.strip() for p in purpose_filter.split(",") if p.strip()]
        filtered = {i: sd for i, sd in semantic_results.items()
                    if sd.get("dataset_purpose", "") in purposes}
        log(f"  筛选后: {len(filtered)} 个视频符合条件", force=True)
    else:
        filtered = semantic_results

    # 导出
    _export_semantic_results(mp4_files, filtered, output_dir, args,
                              purpose_filter=purpose_filter)
    log(f"\n完成！结果已保存至: {output_dir}", force=True)


def _run_cluster_scene(args):
    """场景聚类子命令"""
    global _quiet_mode
    _quiet_mode = args.quiet
    folder_path = _resolve_path(args.dir)
    recursive = not args.no_recursive

    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(output_dir, exist_ok=True)

    log_init(output_dir)
    signal.signal(signal.SIGINT, _signal_handler)

    if not AI_MODULE_AVAILABLE:
        log("错误: ai_semantic 模块未加载", force=True)
        sys.exit(EXIT_BAD_ARGS)
    if not _AI_SKLEARN_OK:
        log("错误: scikit-learn 未安装，聚类功能不可用", force=True)
        sys.exit(EXIT_BAD_ARGS)

    model, preprocess, device = _ensure_clip_model()
    if model is None:
        sys.exit(EXIT_BAD_ARGS)

    log("=" * 60, force=True)
    log("  视频场景聚类", force=True)
    log("=" * 60, force=True)

    mp4_files = scan_mp4_files(args)
    if not mp4_files:
        log("未找到视频文件", force=True)
        return

    log(f"  共 {len(mp4_files)} 个视频", force=True)

    cache_path = os.path.join(output_dir, CACHE_FILE)
    semantic_results = _run_semantic_analysis(
        mp4_files, cache_path, model, preprocess, device, args,
    )

    # 聚类
    embeddings = []
    idx_to_path = {}
    for i, sd in semantic_results.items():
        emb = sd.get("semantic_emb")
        if emb is not None:
            embeddings.append(emb)
            idx_to_path[len(embeddings) - 1] = mp4_files[i]["path"]

    if len(embeddings) < 2:
        log("有效视频特征不足 2 个，无法聚类", force=True)
        return

    embeddings_array = np.array(embeddings)
    clusters = cluster_videos_by_semantic(embeddings_array)

    # 构建语义数据索引
    semantic_data = {}
    for i, sd in semantic_results.items():
        semantic_data[mp4_files[i]["path"]] = sd

    # 导出聚类结果
    html_path = os.path.join(output_dir, SCENE_CLUSTER_HTML)
    export_scene_cluster_html(clusters, semantic_data, html_path)

    log(f"\n聚类完成！结果已保存至: {output_dir}", force=True)


def _export_semantic_results(
    mp4_files: list[dict], semantic_results: dict,
    output_dir: str, args, purpose_filter: str = "",
):
    """导出语义分析结果"""
    if not semantic_results:
        return

    # 构建语义数据索引
    semantic_data = {}
    for i, sd in semantic_results.items():
        if i < len(mp4_files):
            semantic_data[mp4_files[i]["path"]] = sd

    # 导出数据集目录
    catalog_path = os.path.join(output_dir, DATASET_CATALOG)
    export_dataset_catalog(semantic_data, catalog_path)

    # 导出训练样本清单
    list_path = os.path.join(output_dir, TRAIN_SAMPLE_LIST)
    purpose_list = [p.strip() for p in purpose_filter.split(",")] if purpose_filter else None
    export_train_sample_list(semantic_data, list_path, purpose_filter=purpose_list)

    # 导出统计
    stats_path = os.path.join(output_dir, DATASET_STATS)
    export_dataset_stats(semantic_data, stats_path)

    # 可视化 HTML
    if getattr(args, "format", "txt") == "html":
        html_path = os.path.join(output_dir, SCENE_CLUSTER_HTML)
        if _AI_SKLEARN_OK and len(semantic_data) >= 2:
            embeddings = []
            idx_to_path = {}
            for i, sd in semantic_results.items():
                emb = sd.get("semantic_emb")
                if emb is not None and i < len(mp4_files):
                    embeddings.append(emb)
                    idx_to_path[len(embeddings) - 1] = mp4_files[i]["path"]
            if len(embeddings) >= 2:
                clusters = cluster_videos_by_semantic(np.array(embeddings))
                export_scene_cluster_html(clusters, semantic_data, html_path)


def main():
    global _global_cache, _global_exit_code, _quiet_mode

    args = parse_args()
    validate_args(args)

    # 加载配置文件
    if args.config:
        args = load_config_file(args.config, args)

    cmd = getattr(args, "command", "scan")

    # --version
    if cmd == "version" or args.version:
        _print_version()
        return

    # --clean-cache
    if cmd == "clean-cache" or args.clean_cache:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cache_path = os.path.join(script_dir, CACHE_FILE)
        log(f"清理缓存: {cache_path}", force=True)
        removed, remaining = clean_invalid_cache(cache_path)
        log(f"  已清理 {removed} 条无效缓存，剩余 {remaining} 条", force=True)
        return

    # --merge-cache
    if cmd == "merge-cache" or args.merge_cache:
        cache_files = args.merge_cache.split(",") if args.merge_cache else []
        if hasattr(args, "cache_files") and args.cache_files:
            cache_files = args.cache_files
        if not cache_files:
            log("错误: 请提供要合并的缓存文件路径", force=True)
            sys.exit(EXIT_BAD_ARGS)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(script_dir, CACHE_FILE)
        total, count = merge_caches(cache_files, output_path)
        log(f"合并完成: 共合并 {total} 条，总条目 {count} 条", force=True)
        return

    # --verify-cache
    if cmd == "verify-cache" or args.verify_cache:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cache_path = os.path.join(script_dir, CACHE_FILE)
        log(f"校验缓存: {cache_path}", force=True)
        removed, remaining = clean_invalid_cache(cache_path)
        log(f"  已清理 {removed} 条无效缓存，剩余 {remaining} 条", force=True)
        return

    # ============ v2.2 AI 子命令 ============
    # --semantic-analyze
    if cmd == "semantic-analyze":
        _run_semantic_analyze(args)
        return

    # --dataset-filter
    if cmd == "dataset-filter":
        _run_dataset_filter(args)
        return

    # --cluster-scene
    if cmd == "cluster-scene":
        _run_cluster_scene(args)
        return

    # ============ 正常扫描模式 ============
    folder_path = _resolve_path(args.dir)
    recursive = not args.no_recursive
    threshold = args.threshold
    num_frames = args.frames
    max_workers = _auto_workers(args)
    use_cache = not args.no_cache
    dry_run = args.dry_run
    quiet = args.quiet
    _quiet_mode = quiet

    if args.fast:
        num_frames = min(num_frames, FAST_FRAMES)
        threshold = max(threshold, FAST_THRESHOLD)

    # 输出目录
    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(output_dir, exist_ok=True)

    # 日志
    log_init(output_dir)
    signal.signal(signal.SIGINT, _signal_handler)

    cache_path = os.path.join(output_dir, CACHE_FILE)
    keep_strategy = _get_keep_strategy(args)

    log("=" * 60, force=True)
    log("       MP4 视频相似度查重工具 v2.2", force=True)
    log("=" * 60, force=True)
    log(f"  扫描目录:   {folder_path}", force=True)
    log(f"  递归子目录: {'是' if recursive else '否'}", force=True)
    log(f"  相似度阈值: {threshold:.0%}", force=True)
    log(f"  抽取帧数:   {num_frames}", force=True)
    log(f"  线程数:     {max_workers}", force=True)
    log(f"  使用缓存:   {'是' if use_cache else '否'}", force=True)
    log(f"  输出目录:   {output_dir}", force=True)
    log(f"  保留策略:   {keep_strategy}", force=True)
    if args.fast:
        log("  快速模式:   已开启", force=True)
    if dry_run:
        log("  试运行:     已开启（不生成删除脚本）", force=True)
    if AI_MODULE_AVAILABLE:
        log(f"  AI语义:     {'是' if args.semantic else '否'}", force=True)
        if args.semantic:
            log(f"  AI设备:     {_semantic_clip_device}", force=True)
        if args.cluster_semantic:
            log("  语义聚类:   已开启", force=True)
        if args.export_dataset:
            log("  数据集导出: 已开启", force=True)
    if args.purpose_filter:
        log(f"  用途筛选:   {args.purpose_filter}", force=True)
    if TQDM_AVAILABLE:
        log("  进度条:     已启用 (tqdm)", force=True)
    if FFMPEG_AVAILABLE:
        log("  FFmpeg:     已就绪", force=True)
    mem = _get_memory_usage()
    if mem:
        log(f"  初始内存:   {mem}", force=True)

    try:
        # 1. 扫描
        log("\n[步骤1] 扫描视频文件...", force=True)
        try:
            mp4_files = scan_mp4_files(args)
        except (FileNotFoundError, NotADirectoryError) as e:
            log(f"错误: {e}", force=True)
            sys.exit(EXIT_BAD_ARGS)

        if not mp4_files:
            log("未找到视频文件，程序退出。", force=True)
            _global_exit_code = EXIT_OK
            return

        log(f"  共找到 {len(mp4_files)} 个视频文件", force=True)

        # 2. 提取哈希
        log("\n[步骤2] 提取感知哈希...", force=True)
        video_hashes, bad_videos, cache = extract_hashes_with_cache(
            mp4_files, cache_path, num_frames, max_workers, args, use_cache,
        )

        # 2.5 AI 语义分析（可选）
        semantic_results = {}
        if args.semantic and AI_MODULE_AVAILABLE:
            log("\n[步骤2.5] AI 语义分析...", force=True)
            model, preprocess, device = _ensure_clip_model()
            if model is not None:
                semantic_results = _run_semantic_analysis(
                    mp4_files, cache_path, model, preprocess, device, args,
                )

                # 用途筛选
                purpose_filter = args.purpose_filter
                if purpose_filter and semantic_results:
                    purposes = [p.strip() for p in purpose_filter.split(",") if p.strip()]
                    filtered_indices = set()
                    for i, sd in semantic_results.items():
                        if sd.get("dataset_purpose", "") in purposes:
                            filtered_indices.add(i)
                    # 从 video_hashes 中过滤
                    filtered_hashes = {}
                    for idx, vh in video_hashes.items():
                        if idx in filtered_indices:
                            filtered_hashes[idx] = vh
                    if len(filtered_hashes) < len(video_hashes):
                        log(f"  用途筛选: {len(video_hashes)} → {len(filtered_hashes)} 个视频", force=True)
                        video_hashes = filtered_hashes
            else:
                log("  [警告] CLIP 模型不可用，跳过语义分析", force=True)

        # 3. 快速预筛
        meta_dups = []
        skip_pairs = None
        if not args.check_only and len(video_hashes) >= 2:
            log("\n[步骤3] 快速预筛（元数据匹配）...", force=True)
            meta_dups, skip_pairs = pre_filter_by_metadata(mp4_files)
            if meta_dups:
                log(f"  发现 {len(meta_dups)} 对元数据完全一致的视频，将直接判定重复", force=True)

        # 4. 相似度比对
        similar_pairs = []
        groups = []

        if args.check_only:
            log("\n[步骤4] check-only 模式，跳过相似度比对", force=True)
        elif len(video_hashes) < 2:
            log("\n[步骤4] 有效视频不足 2 个，无法比对。", force=True)
        else:
            log("\n[步骤4] 视频相似度比对...", force=True)
            lsh = getattr(args, "lsh_buckets", 32)
            similar_pairs = find_similar_pairs(
                video_hashes, mp4_files, threshold, skip_pairs, lsh
            )

            # 元数据预筛对加入
            for idx_a, idx_b in meta_dups:
                similar_pairs.append({
                    "idx_a": idx_a, "idx_b": idx_b,
                    "name_a": mp4_files[idx_a]["name"],
                    "name_b": mp4_files[idx_b]["name"],
                    "path_a": mp4_files[idx_a]["path"],
                    "path_b": mp4_files[idx_b]["path"],
                    "distance": 0.0, "similarity": 1.0,
                })

            groups = build_groups(similar_pairs, mp4_files, video_hashes, keep_strategy)

        # 5. 导出
        log("\n[步骤5] 导出结果文件...", force=True)
        min_sim = getattr(args, "min_sim", 0.0)

        # 构建语义数据索引
        semantic_data = {}
        if semantic_results:
            for i, sd in semantic_results.items():
                if i < len(mp4_files):
                    semantic_data[mp4_files[i]["path"]] = sd

        if not dry_run:
            csv_path = os.path.join(output_dir, RESULT_CSV)
            group_path = os.path.join(output_dir, RESULT_GROUPS)
            bad_path = os.path.join(output_dir, BAD_VIDEO_LIST)
            paths_path = os.path.join(output_dir, RESULT_PATHS)

            export_csv(similar_pairs, csv_path, threshold, semantic_data)

            fmt = args.format
            if fmt == "md":
                md_path = os.path.join(output_dir, RESULT_GROUPS_MD)
                export_groups_md(groups, mp4_files, md_path, threshold, min_sim, semantic_data)
            elif fmt == "html":
                html_path = os.path.join(output_dir, RESULT_GROUPS_HTML)
                export_groups_html(groups, mp4_files, html_path, threshold, min_sim, semantic_data)
            else:
                export_groups_txt(groups, mp4_files, group_path, threshold, min_sim, semantic_data)

            export_paths_list(groups, mp4_files, paths_path)
            export_bad_videos(bad_videos, bad_path)

            if args.export_hash and video_hashes:
                export_path = os.path.join(output_dir, HASH_EXPORT)
                export_hash_backup(video_hashes, mp4_files, export_path)

            if args.gen_cleanup and groups:
                protect = set(f.strip() for f in (args.protect_folder or "").split(",") if f.strip())
                generate_cleanup_script(groups, mp4_files, output_dir, args.hard_delete, protect)
                export_audit_log(output_dir, groups, mp4_files, args.hard_delete, semantic_data)

            # v2.2 AI 数据集导出
            if args.export_dataset and semantic_data:
                log("\n[步骤5.5] 导出 AI 数据集文件...", force=True)
                catalog_path = os.path.join(output_dir, DATASET_CATALOG)
                export_dataset_catalog(semantic_data, catalog_path)

                list_path = os.path.join(output_dir, TRAIN_SAMPLE_LIST)
                purpose_list = [p.strip() for p in args.purpose_filter.split(",")] if args.purpose_filter else None
                export_train_sample_list(semantic_data, list_path, purpose_filter=purpose_list)

                stats_path = os.path.join(output_dir, DATASET_STATS)
                export_dataset_stats(semantic_data, stats_path)

            # v2.2 语义聚类导出
            if args.cluster_semantic and semantic_data and _AI_SKLEARN_OK:
                log("\n[步骤5.6] 语义聚类分析...", force=True)
                embeddings = []
                for i, sd in semantic_results.items():
                    emb = sd.get("semantic_emb")
                    if emb is not None:
                        embeddings.append(emb)
                if len(embeddings) >= 2:
                    clusters = cluster_videos_by_semantic(np.array(embeddings))
                    cluster_html = os.path.join(output_dir, SCENE_CLUSTER_HTML)
                    export_scene_cluster_html(clusters, semantic_data, cluster_html)

            # v2.2 语义元数据导出
            if semantic_data:
                meta_path = os.path.join(output_dir, SEMANTIC_META)
                try:
                    with open(meta_path, "w", encoding="utf-8") as f:
                        json.dump(semantic_data, f, ensure_ascii=False, indent=2, default=str)
                    log(f"[导出] 语义元数据 → {meta_path}", force=True)
                except (IOError, OSError):
                    pass
        else:
            log("  [试运行] 跳过文件写入", force=True)

        # 6. 汇总
        print_summary(mp4_files, video_hashes, bad_videos, groups, semantic_results)

        # 设置退出码
        if groups:
            _global_exit_code = EXIT_HAS_DUPLICATES
        elif bad_videos:
            _global_exit_code = EXIT_PARSE_ERROR
        else:
            _global_exit_code = EXIT_OK

        log(f"\n完成！所有结果文件已保存至: {output_dir}", force=True)

    except Exception as e:
        log(f"\n[严重错误] 程序异常终止: {e}", force=True)
        log(f"[严重错误] {traceback.format_exc()}", force=True)
        _crash_save(output_dir, cache_path)
        _global_exit_code = EXIT_PARSE_ERROR
        log("[严重错误] 已尽可能保存已完成的数据", force=True)
    finally:
        log_close()

    sys.exit(_global_exit_code)


if __name__ == "__main__":
    main()