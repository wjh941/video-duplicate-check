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
from .constants import __version__  # noqa: F401  (star import 跳过下划线名)
from .context import log, log_init, log_close, _APP_CTX, repo_dir, _get_memory_usage
from .utils import (_resolve_path, _parse_size_str, _format_size,
                    _normalize_path, _atomic_write_text, _atomic_write_lines,
                    _prefixed_path)
from .scanner import (scan_mp4_files, parse_duration_filter, match_duration,
                      apply_duration_resolution_filter, apply_skip_low_quality,
                      _load_duplicateignore, _load_globalignore, _match_ignore_rule)
from .cache import (load_cache, save_cache, is_cache_valid, clean_expired_cache,
                    clean_invalid_cache, merge_caches, save_scan_progress,
                    load_scan_progress, clear_scan_progress)
from .hasher import (extract_hashes_with_cache, _compute_frame_hashes,
                     _compute_file_sha256, _compute_file_md5, _detect_keyframes,
                     _extract_audio_hash, _extract_hashes_single,
                     _video_quality_score)
from .compare import (compute_similarity, _hash_list_similarity,
                      _build_lsh_buckets, find_similar_pairs,
                      pre_filter_by_metadata)
from .grouper import (build_groups, _split_group_min_sim, _select_retain,
                      _get_keep_strategy)
from .reporter import (export_csv, export_bad_paths, export_groups_txt,
                       export_groups_md, export_groups_html, export_groups_xlsx,
                       export_paths_list, export_clean_list, export_bad_videos,
                       export_hash_backup, export_summary_json, print_summary,
                       _extract_video_thumbnail_base64, _similarity_level,
                       _summary_extensions, _summary_quality_buckets,
                       _summary_exit_status)
from .cleanup import (_run_execute_plan, _run_purge_operations,
                      _run_list_operations, _run_restore_operation,
                      _run_validate_plan, _run_gen_restore,
                      generate_cleanup_script, export_audit_log)

# ai_semantic 模块（v2.2 AI 语义分析，可选）—— v2.9 起延迟加载：
# torch 导入耗时约 8-10s，非 AI 路径（version/--help/纯哈希扫描）不应付出此代价。
# _ai_probe() 仅用 find_spec 探测依赖是否安装（毫秒级）；
# _ai_mod() 在真正调用 CLIP/语义接口时才导入 ai_semantic。
_AI_STATE = {"loaded": False, "ok": False, "torch_ok": False,
             "clip_ok": False, "sklearn_ok": False, "cuda_ok": False}
_AI_MODULE_OBJ = None


def _ai_probe() -> dict:
    """轻量探测 AI 依赖安装状态（不导入 torch，毫秒级）。"""
    if not _AI_STATE["loaded"]:
        import importlib.util

        def _has(name):
            try:
                return importlib.util.find_spec(name) is not None
            except (ImportError, ValueError):
                return False

        _AI_STATE.update({
            "loaded": True,
            "ok": _has("ai_semantic") and _has("torch") and _has("open_clip"),
            "torch_ok": _has("torch"),
            "clip_ok": _has("open_clip"),
            "sklearn_ok": _has("sklearn"),
            "cuda_ok": False,  # 真实 CUDA 状态在 _ai_mod() 导入后更新
        })
    return _AI_STATE


def _ai_mod():
    """按需导入 ai_semantic 模块；失败返回 None（纯哈希模式降级）。"""
    global _AI_MODULE_OBJ
    if _AI_MODULE_OBJ is None:
        try:
            import ai_semantic as _m
        except ImportError:
            _AI_MODULE_OBJ = False
            return None
        _AI_MODULE_OBJ = _m
        _AI_STATE["ok"] = True
        _AI_STATE["torch_ok"] = getattr(_m, "TORCH_OK", False)
        _AI_STATE["clip_ok"] = getattr(_m, "CLIP_OK", False)
        _AI_STATE["sklearn_ok"] = getattr(_m, "SKLEARN_OK", False)
        _AI_STATE["cuda_ok"] = getattr(_m, "CUDA_OK", False)
        _APP_CTX.semantic_clip_device = (
            "cuda" if getattr(_m, "CUDA_OK", False) else "cpu")
    return _AI_MODULE_OBJ or None



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
    parser.add_argument("--format", choices=["txt", "md", "html", "xlsx"], default="txt")
    parser.add_argument("--fast", action="store_true", default=False)
    parser.add_argument("--check-only", action="store_true", default=False)
    parser.add_argument("--version", action="store_true", default=False)
    parser.add_argument("--clean-cache", action="store_true", default=False)
    parser.add_argument("--export-hash", action="store_true", default=False)
    parser.add_argument("--summary-json", action="store_true", default=False,
                        help="导出机器可读的扫描摘要 JSON，便于脚本和看板集成")
    parser.add_argument("--gen-cleanup", action="store_true", default=False)
    parser.add_argument("--confirm-cleanup", action="store_true", default=False,
                        help="确认执行清理计划（仅 execute-plan 使用，默认仅预览）")
    # v2.1 新增参数
    parser.add_argument(
        "--ext", type=str,
        default="mp4,mov,mkv,avi,webm,m4v,flv",
        help="支持的视频后缀，逗号分隔，默认 mp4,mov,mkv,avi,webm,m4v,flv",
    )
    parser.add_argument("--double-check", action="store_true", default=False,
                        help="二次校验模式")
    parser.add_argument("--lsh-buckets", type=int, default=32,
                        help="LSH 分桶数，默认 32")
    parser.add_argument("--mem-limit", type=int, default=0,
                        help="内存限制MB，0为不限制")
    parser.add_argument("--incremental", action="store_true", default=False,
                        help="增量模式，仅处理新增/修改视频")
    # 【v2.6 新增】MD5 校验
    parser.add_argument("--use-md5", action="store_true", default=False,
                        help="启用文件 MD5 校验，检测内容真实变更（v2.6 新增，更精确但更慢）")
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
    parser.add_argument("--semantic-workers", type=int, default=0,
                        help="AI推理线程数，默认0=自动适配CPU核心")
    parser.add_argument("--purpose", type=str, default="",
                        help="数据集用途筛选（dataset-filter子命令专用）")
    # v2.3 新增参数
    parser.add_argument("--no-store-embed", action="store_true", default=False,
                        help="不保存CLIP特征向量到缓存，缩小缓存体积")
    parser.add_argument("--clip-model-path", type=str, default="",
                        help="指定本地CLIP模型目录，避免在线下载")
    parser.add_argument("--lite-csv", action="store_true", default=False,
                        help="导出轻量化CSV（仅路径、相似度）")
    parser.add_argument("--export-bad-paths", action="store_true", default=False,
                        help="单独输出损坏视频纯路径清单")
    parser.add_argument("--backup-path", type=str, default="",
                        help="清理前备份视频到指定目录")
    parser.add_argument("--protect-file", type=str, default="",
                        help="批量导入保护目录清单文件")
    parser.add_argument("--cluster-num", type=int, default=0,
                        help="语义聚类数量，0=自动")
    parser.add_argument("--cache-expire-days", type=int, default=0,
                        help="自动清理超过指定天数未修改的缓存，0=不清理")
    parser.add_argument("--compress-cache", action="store_true", default=False,
                        help="保存缓存时输出.gz压缩包")
    # v2.4 新增：时长筛选统计参数
    parser.add_argument("--duration-filter", type=str, default="",
                        help="时长筛选条件，如 >=60、<30、>120&<=360（单位秒）")
    parser.add_argument("--duration-stat", action="store_true", default=False,
                        help="仅统计符合时长条件视频数量，不执行查重")
    parser.add_argument("--duration-export", action="store_true", default=False,
                        help="导出符合时长条件视频路径清单到txt")
    # v2.4 新增：功能补充参数
    parser.add_argument("--no-store-frames", action="store_true", default=False,
                        help="不将预览帧持久化存入缓存，降低内存占用")
    parser.add_argument("--min-res", type=int, default=0,
                        help="筛选最小分辨率宽度，如 1920")
    parser.add_argument("--max-res", type=int, default=0,
                        help="筛选最大分辨率宽度，如 3840")
    parser.add_argument("--gen-restore", action="store_true", default=False,
                        help="根据审计日志生成视频恢复脚本")
    parser.add_argument("--export-clean-list", action="store_true", default=False,
                        help="单独输出仅待清理视频路径清单")
    parser.add_argument("--output-prefix", type=str, default="",
                        help="自定义输出文件前缀，多批次扫描不覆盖报告")
    parser.add_argument("--path-mask", action="store_true", default=False,
                        help="审计日志隐藏路径中间层级，保护素材隐私")
    # 【v2.6 新增】交互式配置向导
    parser.add_argument("--interactive", action="store_true", default=False,
                        help="交互式配置向导，分步问答输入参数，降低新手使用门槛")
    parser.add_argument("--cluster-thresh", type=float, default=0.5,
                        help="语义聚类松紧阈值，默认 0.5")
    parser.add_argument("--skip-low-quality", action="store_true", default=False,
                        help="过滤AI判定低质量模糊暗光视频")
    parser.add_argument("--link-mode", action="store_true", default=False,
                        help="数据集拆分使用硬链接，不重复复制视频")
    # 【v2.5 新增】AI 自动分类参数
    parser.add_argument("--execute", action="store_true", default=False,
                        help="执行文件操作（默认 dry-run 预览模式）")
    parser.add_argument("--n-clusters", type=int, default=0,
                        help="自动分类目标聚类数 (0=自动估算)")
    parser.add_argument("--classify-only", action="store_true", default=False,
                        help="仅执行 AI 自动分类，不查重")
    parser.add_argument("--classify-method", type=str, default="kmeans",
                        help="分类算法 (kmeans)")
    # 【v2.7 新增】分组质量与标注联动
    parser.add_argument("--group-min-sim", type=float, default=0.0,
                        help="组内最低相似度约束 (0=关闭)。按 complete-linkage 策略拆分"
                             "连通图分组中点对相似度不达标的组，遏制传递性误差")
    parser.add_argument("--label-regex", type=str, default="",
                        help="标注提取正则（第1捕获组为标签），用于分组混合标注告警，"
                             "如 \"cam01_(.+?)-(?:pos|neg)\"")
    # 【v2.8 新增】场景预设
    parser.add_argument("--preset", type=str, default="general",
                        choices=["general", "surveillance", "footage"],
                        help="场景预设：surveillance=固定机位监控(threshold=0.85+group-min-sim=0.8+frames=6)，"
                             "footage=个人素材库(threshold=0.7)；显式指定的参数优先于预设")
    return parser


def parse_args():
    """解析命令行参数（子命令 + 扁平参数兼容）"""
    shared = _build_shared_parser()

    # 检查第一个有效参数是否为子命令
    subcommands = {"scan", "clean-cache", "merge-cache", "verify-cache", "version", "help", "validate-plan", "execute-plan", "restore-operation", "list-operations", "purge-operations",
                   "semantic-analyze", "dataset-filter", "cluster-scene",
                   "clear-semantic-cache", "dataset-split",
                   "duration-stat", "reload-labels", "test",
                   "auto-classify",  # 【v2.5 新增】AI 自动分类子命令
                   # 【v2.6 新增】可视化看板与拓展工具子命令（委托到独立模块）
                   "dashboard", "batch-scan", "media-info", "space-analyze",
                   "tag-manage", "similar-search", "export-snapshot", "diff-scan",
                   "full-report", "export-pdf", "diff-report", "quality-report",
                   "archive", "export-thumbnails", "backup-duplicates",
                   "replace-hardlinks", "organize", "extract-segments",
                   "label-verify", "pipeline"}

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
            description="MP4 视频相似度查重工具 v2.4",
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
    elif first_arg in ("validate-plan", "execute-plan"):
        parser.add_argument("plan_file", nargs="?", default=CLEANUP_PLAN_FILE,
                            help="要处理的 cleanup_plan.json 路径")
    elif first_arg == "restore-operation":
        parser.add_argument("operation_file", nargs="?", default="operation.json",
                            help="要恢复的 operation.json 路径")
        parser.add_argument("--confirm-restore", action="store_true", default=False,
                            help="确认恢复文件（默认仅预览）")
    elif first_arg in ("list-operations", "purge-operations"):
        parser.add_argument("trash_dir", nargs="?", default="trash",
                            help="隔离区目录，默认当前目录下 trash")
        parser.add_argument("--older-than", type=int, default=30,
                            help="仅处理超过指定天数的操作，默认 30")
        parser.add_argument("--confirm-purge", action="store_true", default=False,
                            help="确认永久删除过期隔离区（默认仅预览）")

    # 【v2.7 修改】parse_known_args：允许委托子命令携带模块专属参数
    #（如 label-verify --suspect-threshold），这些参数由被委托模块自行解析
    args, _unknown_extra = parser.parse_known_args()
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
        # 【改造 v2.4/v2.5】新增参数映射，支持 config.ini 自定义
        "duration_filter": "duration-filter",
        "duration_export": "duration-export",
        "no_store_frames": "no-store-frames",
        "min_res": "min-res", "max_res": "max-res",
        "export_clean_list": "export-clean-list",
        "output_prefix": "output-prefix",
        "path_mask": "path-mask",
        "cluster_thresh": "cluster-thresh",
        "skip_low_quality": "skip-low-quality",
        "link_mode": "link-mode",
        "compress_cache": "compress-cache",
        "hard_delete": "hard-delete",
        "backup_path": "backup-path",
        "protect_folder": "protect-folder",
        "protect_file": "protect-file",
        "gen_restore": "gen-restore",
        # 【v2.5 新增】AI 自动分类参数映射
        "classify_only": "classify-only",
        "classify_method": "classify-method",
        "n_clusters": "n-clusters",
        "execute": "execute",
    }
    bool_fields = {
        "double-check", "audio-check", "fast", "incremental", "keep-latest",
        "keep-max-res", "keep-max-bitrate", "semantic", "cluster-semantic",
        "export-dataset", "quiet", "dry-run", "duration-export", "no-store-frames",
        "export-clean-list", "path-mask", "skip-low-quality", "link-mode",
        "compress-cache", "hard-delete", "gen-restore", "classify-only", "execute",
    }
    def parse_bool(value: str) -> bool:
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y", "on", "是", "开启"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "否", "关闭", ""}:
            return False
        raise ValueError(f"配置布尔值无效: {value!r}")

    for config_key, arg_key in mapping.items():
        if config_key not in sec:
            continue
        dest = arg_key.replace("-", "_")
        current = getattr(args, dest, None)
        # argparse 默认值不能区分“未传入”和默认值；保持历史行为，
        # 但必须把配置值转换成正确类型，尤其是 false 不能成为 truthy 字符串。
        if current not in (None, False, 0, ""):
            continue
        raw = sec[config_key]
        try:
            if arg_key in bool_fields:
                val = parse_bool(raw)
            elif dest in {"threshold", "min_sim", "scene_thresh", "cluster_thresh"}:
                val = float(raw)
            elif dest in {"frames", "workers", "min_res", "max_res", "n_clusters", "semantic_workers"}:
                val = int(raw)
            else:
                val = raw.strip()
        except ValueError as exc:
            log(f"  [警告] 忽略无效配置 {config_key}: {exc}")
            continue
        setattr(args, dest, val)
    return args




def validate_args(args):
    """参数二次校验（v2.3 增强：参数冲突检测 + AI 依赖降级提示）"""

    if getattr(args, "quiet", False):
        _APP_CTX.quiet_mode = True

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

    # v2.3 新增：参数冲突校验
    if getattr(args, "fast", False) and getattr(args, "double_check", False):
        print("错误: --fast 与 --double-check 互斥，不可同时使用")
        sys.exit(EXIT_BAD_ARGS)

    if getattr(args, "embed_cache", False) and getattr(args, "no_semantic_cache", False):
        print("错误: --embed-cache 与 --no-semantic-cache 互斥")
        sys.exit(EXIT_BAD_ARGS)

    # v2.3 新增：AI 参数无依赖时降级提示（不报错，仅警告）
    ai_flags = ["semantic", "cluster_semantic", "export_dataset"]
    ai_needed = any(getattr(args, f, False) for f in ai_flags)
    semantic_cmd = cmd in ("semantic-analyze", "dataset-filter", "cluster-scene",
                           "clear-semantic-cache", "dataset-split")

    if semantic_cmd and not _ai_probe()['ok']:
        # clear-semantic-cache 不需要 AI 模块
        if cmd == "clear-semantic-cache":
            pass
        elif cmd == "dataset-split":
            pass  # dataset-split 可在无 AI 时用缓存运行
        else:
            print("错误: AI 子命令需要 ai_semantic 模块，请安装 torch/open-clip/sklearn")
            sys.exit(EXIT_BAD_ARGS)

    if ai_needed and not _ai_probe()['ok'] and not semantic_cmd:
        if not _APP_CTX.quiet_mode:
            print("[警告] AI 依赖未安装，--semantic/--cluster-semantic/--export-dataset 将自动降级为纯哈希查重")
        # 自动关闭 AI 相关参数
        args.semantic = False
        args.cluster_semantic = False
        args.export_dataset = False


# ============================================================
# 模块 3：文件扫描与过滤
# ============================================================




def load_weights_config(config_path: str = "") -> dict:
    """
    从 config.ini 加载相似度权重配置（v2.4 新增）。
    支持 phash_weight、dhash_weight、audio_weight 自定义加权值，
    替代代码硬编码 0.7/0.3/0.8。
    返回 {"phash_weight": float, "dhash_weight": float, "audio_weight": float}
    """
    weights = {
        "phash_weight": 0.7,
        "dhash_weight": 0.3,
        "audio_weight": 0.8,
    }
    if not config_path:
        # 默认查找程序同目录 config.ini
        script_dir = repo_dir()
        config_path = os.path.join(script_dir, "config.ini")
    if not os.path.exists(config_path):
        return weights
    try:
        config = configparser.ConfigParser()
        config.read(config_path, encoding="utf-8")
        if config.has_section("weights"):
            for key in ["phash_weight", "dhash_weight", "audio_weight"]:
                if key in config["weights"]:
                    try:
                        weights[key] = float(config["weights"][key])
                    except ValueError:
                        pass
            log(f"  [权重] 已加载 config.ini 自定义权重: {weights}")
    except Exception as e:
        log(f"  [警告] 读取 config.ini 权重失败: {e}，使用默认权重")
    return weights


def load_dataset_labels() -> dict:
    """
    加载外置标签配置文件 dataset_labels.ini（v2.3 新增）。
    用户无需修改源码即可自定义场景、物体、行为、用途分类标签。
    返回 {"scene": [...], "object": [...], "action": [...],
          "purpose_rules": {用途: [关键词]}, "quality": {参数: 值}}
    """
    import configparser
    script_dir = repo_dir()
    ini_path = os.path.join(script_dir, "dataset_labels.ini")

    # 默认标签
    labels = {
        "scene": ["室内监控楼道", "室外道路", "停车场", "小区", "办公室", "教室",
                   "户外公园", "车内", "夜晚监控", "夜晚暗光"],
        "object": ["行人", "电动车", "轿车", "货车", "监控设备", "桌椅",
                    "绿植", "猫狗", "人脸"],
        "action": ["行走", "跑动", "静止", "骑车"],
        "purpose_rules": {
            "监控训练集": ["行人", "监控", "楼道", "道路", "夜晚"],
            "自动驾驶数据集": ["车辆", "轿车", "货车", "道路", "路面", "车流"],
            "人像素材": ["人脸", "人像", "人物", "特写"],
            "影视素材": ["电影", "剧情", "镜头", "人物"],
            "风景素材": ["自然", "山水", "天空", "公园"],
            "游戏录屏": ["游戏", "UI", "界面", "角色"],
        },
        "quality": {
            "min_clarity": 0.3,
            "min_brightness": 0.2,
            "min_subject_ratio": 0.1,
            "training_score_threshold": 0.4,
        },
    }

    if not os.path.exists(ini_path):
        return labels

    try:
        config = configparser.ConfigParser()
        config.read(ini_path, encoding="utf-8")

        if config.has_section("scene"):
            raw = config.get("scene", "labels", fallback="").strip()
            if raw:
                labels["scene"] = [s.strip() for s in raw.split(",") if s.strip()]

        if config.has_section("object"):
            raw = config.get("object", "labels", fallback="").strip()
            if raw:
                labels["object"] = [s.strip() for s in raw.split(",") if s.strip()]

        if config.has_section("action"):
            raw = config.get("action", "labels", fallback="").strip()
            if raw:
                labels["action"] = [s.strip() for s in raw.split(",") if s.strip()]

        if config.has_section("purpose_rules"):
            rules = {}
            for key in config.options("purpose_rules"):
                val = config.get("purpose_rules", key)
                rules[key] = [k.strip() for k in val.split(",") if k.strip()]
            if rules:
                labels["purpose_rules"] = rules

        if config.has_section("quality"):
            for key in config.options("quality"):
                try:
                    labels["quality"][key] = float(config.get("quality", key))
                except ValueError:
                    pass

        log("  [标签] 已加载 dataset_labels.ini 自定义标签配置")
    except Exception as e:
        log(f"  [警告] 读取 dataset_labels.ini 失败: {e}，使用默认标签")

    return labels


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


def _run_duration_stat(args):
    """时长统计子命令（v2.4 新增）：仅统计不执行查重"""
    _APP_CTX.quiet_mode = args.quiet
    folder_path = _resolve_path(args.dir)
    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = repo_dir()
    os.makedirs(output_dir, exist_ok=True)
    log_init(output_dir)
    _register_signal_handler()  # 【v2.6 修复】统一信号注册，避免重复

    log("=" * 60, force=True)
    log("  视频时长统计", force=True)
    log("=" * 60, force=True)
    log(f"  扫描目录: {folder_path}", force=True)

    filter_str = getattr(args, "duration_filter", "") or getattr(args, "purpose", "")
    if not filter_str:
        filter_str = ">=0"  # 默认统计全部
    conditions = parse_duration_filter(filter_str)
    log(f"  筛选条件: {filter_str} (单位:秒)", force=True)

    # 扫描并提取时长
    mp4_files = scan_mp4_files(args)
    if not mp4_files:
        log("未找到视频文件", force=True)
        return

    log(f"  共找到 {len(mp4_files)} 个视频文件", force=True)
    log("  正在读取视频时长...", force=True)

    matched_files = []
    total_duration = 0.0
    matched_duration = 0.0

    for fi in mp4_files:
        path = fi["path"]
        try:
            cap = cv2.VideoCapture(path)
            if cap.isOpened():
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                fps = cap.get(cv2.CAP_PROP_FPS)
                duration = total_frames / fps if fps > 0 else 0
                cap.release()
            else:
                duration = 0
        except Exception:
            duration = 0

        total_duration += duration
        if match_duration(duration, conditions):
            matched_files.append({"path": path, "name": fi["name"],
                                  "duration": duration, "size": fi["size"],
                                  "size_readable": fi["size_readable"]})
            matched_duration += duration

    # 统计汇总
    total = len(mp4_files)
    matched = len(matched_files)
    pct = (matched / total * 100) if total > 0 else 0

    log("\n" + "=" * 60, force=True)
    log("  时长统计汇总", force=True)
    log("=" * 60, force=True)
    log(f"  总视频数:       {total}", force=True)
    log(f"  符合条件数量:   {matched}", force=True)
    log(f"  占比:           {pct:.1f}%", force=True)
    log(f"  符合条件总时长: {matched_duration:.1f}秒 ({matched_duration/60:.1f}分钟)", force=True)
    if total > 0:
        log(f"  全部视频总时长: {total_duration:.1f}秒 ({total_duration/60:.1f}分钟)", force=True)
    log("=" * 60, force=True)

    # 导出清单
    if getattr(args, "duration_export", False) and matched_files:
        export_path = os.path.join(output_dir, "duration_filter_list.txt")
        try:
            with open(export_path, "w", encoding="utf-8") as f:
                f.write(f"# 时长筛选清单 (条件: {filter_str})\n")
                f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# 符合条件: {matched}/{total}\n\n")
                for mf in matched_files:
                    f.write(f"{mf['path']}\t{mf['duration']:.1f}秒\t{mf['size_readable']}\n")
            log(f"[导出] 时长清单 → {export_path}", force=True)
        except (IOError, OSError) as e:
            log(f"[错误] 导出失败: {e}")




# ============================================================
# 模块 9：全局异常处理与主入口
# ============================================================
def _crash_save(output_dir: str, cache_path: str = None, partial_results: dict = None):
    """异常退出时保存缓存和部分结果"""
    try:
        if cache_path and _APP_CTX.global_cache:
            save_cache(cache_path, _APP_CTX.global_cache)
            log("[异常恢复] 已保存哈希缓存", force=True)
        if partial_results:
            try:
                crash_path = os.path.join(output_dir, "crash_partial.json")
                with open(crash_path, "w", encoding="utf-8") as f:
                    json.dump(partial_results, f, ensure_ascii=False, indent=2)
                log("[异常恢复] 已保存部分结果", force=True)
            except Exception:
                pass
        _APP_CTX.global_results_saved = True
    except Exception:
        pass


def _signal_handler(signum, frame):
    """Ctrl+C 信号处理"""
    log("\n[中断] 正在保存数据...", force=True)
    try:
        script_dir = repo_dir()
        cache_path = os.path.join(script_dir, CACHE_FILE)
        if _APP_CTX.global_cache:
            save_cache(cache_path, _APP_CTX.global_cache)
            log("[中断] 哈希缓存已保存", force=True)
    except Exception as e:
        log(f"[中断] 缓存保存失败: {e}", force=True)
    log("[中断] 数据已保存，可安全退出", force=True)
    sys.exit(130)


def _register_signal_handler():
    """
    统一注册信号处理器（v2.6 修复：避免重复注册覆盖）。
    全局只注册一次，使用模块级标志位防止重复。
    """
    if getattr(_register_signal_handler, '_registered', False):
        return  # 已注册，跳过
    try:
        signal.signal(signal.SIGINT, _signal_handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, OSError):
        pass  # 非主线程无法注册信号，忽略
    _register_signal_handler._registered = True


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
    log(f"AI 模块: {'已加载' if _ai_probe()['ok'] else '未安装'}", force=True)
    if _ai_probe()['ok']:
        log(f"  PyTorch: {'已安装' if _ai_probe()['torch_ok'] else '未安装'}", force=True)
        log(f"  CLIP: {'已安装' if _ai_probe()['clip_ok'] else '未安装'}", force=True)
        log(f"  sklearn: {'已安装' if _ai_probe()['sklearn_ok'] else '未安装'}", force=True)
        log(f"  CUDA: {'可用' if _ai_probe()['cuda_ok'] else '不可用'}", force=True)




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
    if _APP_CTX.semantic_clip_model is not None:
        return _APP_CTX.semantic_clip_model, _APP_CTX.semantic_clip_preprocess, _APP_CTX.semantic_clip_device
    if not _ai_probe()['ok']:
        log("[AI] ai_semantic 模块未加载", force=True)
        return None, None, None
    if not _ai_probe()['torch_ok'] or not _ai_probe()['clip_ok']:
        log("[AI] PyTorch/CLIP 未安装，AI 功能不可用", force=True)
        return None, None, None
    try:
        model, preprocess, device = _ai_mod().load_clip_model()
        _APP_CTX.semantic_clip_model = model
        _APP_CTX.semantic_clip_preprocess = preprocess
        _APP_CTX.semantic_clip_device = device
        return model, preprocess, device
    except Exception as e:
        log(f"[AI] CLIP 模型加载失败: {e}", force=True)
        return None, None, None


def _run_semantic_analysis(
    mp4_files: list[dict], cache_path: str,
    model, preprocess, device, args,
    video_hashes: dict = None,  # v2.3 新增：传入已提取的哈希数据用于帧复用
) -> dict:
    """对所有视频执行语义分析，返回 {path: semantic_data}
    v2.3 增强：支持 --no-store-embed、帧复用、AI线程自适应
    """
    scene_thresh = getattr(args, "scene_thresh", 0.6)
    embed = getattr(args, "embed_cache", False) and not getattr(args, "no_semantic_cache", False)
    no_store_embed = getattr(args, "no_store_embed", False)  # v2.3 新增
    semantic_results = {}
    use_cache = not getattr(args, "no_cache", False) and embed

    # v2.3 新增：AI 线程数自适应
    semantic_workers = getattr(args, "semantic_workers", 0)
    if semantic_workers <= 0:
        semantic_workers = min(os.cpu_count() or 1, 4)  # 默认最多4线程

    # v2.3 新增：加载外置标签配置
    labels_config = load_dataset_labels()

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

    log(f"  AI 语义分析: {len(to_analyze)} 个视频待分析 (线程数: {semantic_workers})...")
    total = len(to_analyze)
    completed = 0

    if TQDM_AVAILABLE:
        pbar = _tqdm(total=total, desc="AI语义分析", unit="视频", ncols=80)

    for idx, file_info in to_analyze:
        path = file_info["path"]
        try:
            # v2.3 新增：帧复用 - 如果 video_hashes 中已有 frames_pil，传入避免重复解码
            cached_frames = None
            if video_hashes and idx in video_hashes:
                cached_frames = video_hashes[idx].get("frames_pil")

            result = _ai_mod().semantic_analyze_video(
                path, model, preprocess, device,
                num_frames=10, scene_thresh=scene_thresh,
                cached_frames=cached_frames,  # v2.3 帧复用
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
                    # v2.3 新增：--no-store-embed 时不保存特征向量
                    if not no_store_embed and result.get("semantic_emb") is not None:
                        cache[path]["semantic_emb"] = result["semantic_emb"]
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
    _APP_CTX.semantic_stats = {}
    for idx, sd in semantic_results.items():
        purpose = sd.get("dataset_purpose", "未分类")
        _APP_CTX.semantic_stats[purpose] = _APP_CTX.semantic_stats.get(purpose, 0) + 1

    log(f"  AI 分析完成: {len(semantic_results)} 个视频", force=True)
    for purpose, count in sorted(_APP_CTX.semantic_stats.items()):
        log(f"    {purpose}: {count} 个", force=True)

    return semantic_results


def _run_semantic_analyze(args):
    """执行纯语义分析子命令"""
    _APP_CTX.quiet_mode = args.quiet
    folder_path = _resolve_path(args.dir)
    recursive = not args.no_recursive

    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = repo_dir()
    os.makedirs(output_dir, exist_ok=True)

    log_init(output_dir)
    _register_signal_handler()  # 【v2.6 修复】统一信号注册，避免重复

    if not _ai_probe()['ok']:
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
    _APP_CTX.quiet_mode = args.quiet
    folder_path = _resolve_path(args.dir)
    recursive = not args.no_recursive
    purpose_filter = args.purpose or getattr(args, "purpose_filter", "")

    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = repo_dir()
    os.makedirs(output_dir, exist_ok=True)

    log_init(output_dir)
    _register_signal_handler()  # 【v2.6 修复】统一信号注册，避免重复

    if not _ai_probe()['ok']:
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
    _APP_CTX.quiet_mode = args.quiet
    folder_path = _resolve_path(args.dir)
    recursive = not args.no_recursive

    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = repo_dir()
    os.makedirs(output_dir, exist_ok=True)

    log_init(output_dir)
    _register_signal_handler()  # 【v2.6 修复】统一信号注册，避免重复

    if not _ai_probe()['ok']:
        log("错误: ai_semantic 模块未加载", force=True)
        sys.exit(EXIT_BAD_ARGS)
    if not _ai_probe()['sklearn_ok']:
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
    clusters = _ai_mod().cluster_videos_by_semantic(embeddings_array)

    # 构建语义数据索引
    semantic_data = {}
    for i, sd in semantic_results.items():
        semantic_data[mp4_files[i]["path"]] = sd

    # 导出聚类结果
    html_path = os.path.join(output_dir, SCENE_CLUSTER_HTML)
    _ai_mod().export_scene_cluster_html(clusters, semantic_data, html_path)

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
    _ai_mod().export_dataset_catalog(semantic_data, catalog_path)

    # 导出训练样本清单
    list_path = os.path.join(output_dir, TRAIN_SAMPLE_LIST)
    purpose_list = [p.strip() for p in purpose_filter.split(",")] if purpose_filter else None
    _ai_mod().export_train_sample_list(semantic_data, list_path, purpose_filter=purpose_list)

    # 导出统计
    stats_path = os.path.join(output_dir, DATASET_STATS)
    _ai_mod().export_dataset_stats(semantic_data, stats_path)

    # 可视化 HTML
    if getattr(args, "format", "txt") == "html":
        html_path = os.path.join(output_dir, SCENE_CLUSTER_HTML)
        if _ai_probe()['sklearn_ok'] and len(semantic_data) >= 2:
            embeddings = []
            idx_to_path = {}
            for i, sd in semantic_results.items():
                emb = sd.get("semantic_emb")
                if emb is not None and i < len(mp4_files):
                    embeddings.append(emb)
                    idx_to_path[len(embeddings) - 1] = mp4_files[i]["path"]
            if len(embeddings) >= 2:
                clusters = _ai_mod().cluster_videos_by_semantic(np.array(embeddings))
                _ai_mod().export_scene_cluster_html(clusters, semantic_data, html_path)


# ============================================================
# v2.3 新增子命令处理函数
# ============================================================
def _run_clear_semantic_cache(args):
    """清理缓存内 AI 特征向量，保留 pHash/dHash 视频哈希缓存（v2.3 新增）"""
    _APP_CTX.quiet_mode = args.quiet
    script_dir = repo_dir()
    cache_path = os.path.join(script_dir, CACHE_FILE)

    log("清理 AI 语义缓存字段（保留哈希缓存）...", force=True)
    cache = load_cache(cache_path)
    cleared = 0
    _ai_fields = [
        "scene_tags", "object_tags", "action_tags",
        "dataset_purpose", "semantic_emb", "semantic_conf",
        "quality_score", "is_training_ready",
    ]
    for path, entry in cache.items():
        if path.startswith("_"):
            continue
        for field in _ai_fields:
            if field in entry:
                del entry[field]
                cleared += 1

    save_cache(cache_path, cache)
    total = len([k for k in cache if not k.startswith("_")])
    log(f"  已清理 {cleared} 个 AI 字段，保留 {total} 条哈希缓存", force=True)


def _run_dataset_split(args):
    """
    数据集分类拆分子命令（v2.3 新增）。
    根据视频用途自动新建分类文件夹，复制视频素材，生成目录清单。
    """
    _APP_CTX.quiet_mode = args.quiet
    folder_path = _resolve_path(args.dir)

    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = repo_dir()
    os.makedirs(output_dir, exist_ok=True)

    log_init(output_dir)
    _register_signal_handler()  # 【v2.6 修复】统一信号注册，避免重复

    log("=" * 60, force=True)
    log("  数据集分类拆分", force=True)
    log("=" * 60, force=True)
    log(f"  扫描目录: {folder_path}", force=True)

    mp4_files = scan_mp4_files(args)
    if not mp4_files:
        log("未找到视频文件", force=True)
        return

    log(f"  共 {len(mp4_files)} 个视频", force=True)

    # 从缓存加载语义数据
    cache_path = os.path.join(output_dir, CACHE_FILE)
    cache = load_cache(cache_path)

    # 按用途分组
    purpose_groups = defaultdict(list)
    no_purpose = []
    for file_info in mp4_files:
        path = file_info["path"]
        entry = cache.get(path, {})
        purpose = entry.get("dataset_purpose", "")
        if purpose and purpose != "未知":
            purpose_groups[purpose].append(file_info)
        else:
            no_purpose.append(file_info)

    # 创建分类文件夹并复制
    split_base = os.path.join(output_dir, "dataset_split")
    os.makedirs(split_base, exist_ok=True)
    catalog_lines = []

    # 【改造 v2.4】--link-mode 硬链接模式，不重复复制视频节省磁盘
    use_hardlink = getattr(args, "link_mode", False)
    link_count = 0
    copy_count = 0

    def _place_file(src: str, dst: str) -> str:
        """放置文件：硬链接优先，失败回退复制"""
        nonlocal link_count, copy_count
        if os.path.exists(dst):
            return "skip"
        if use_hardlink:
            try:
                os.link(src, dst)
                link_count += 1
                return "link"
            except OSError:
                # 硬链接失败（跨盘/权限），回退复制
                shutil.copy2(src, dst)
                copy_count += 1
                return "copy"
        else:
            shutil.copy2(src, dst)
            copy_count += 1
            return "copy"

    for purpose, files in purpose_groups.items():
        # 清理用途名中的特殊字符作为文件夹名
        safe_name = purpose.replace("/", "_").replace("\\", "_").replace(":", "_")
        purpose_dir = os.path.join(split_base, safe_name)
        os.makedirs(purpose_dir, exist_ok=True)
        log(f"  [{purpose}] {len(files)} 个视频 → {purpose_dir}", force=True)

        for file_info in files:
            src = file_info["path"]
            dst = os.path.join(purpose_dir, file_info["name"])
            try:
                _place_file(src, dst)
                catalog_lines.append(f"{purpose}\t{src}\t{dst}\t{file_info['size_readable']}")
            except (IOError, OSError) as e:
                log(f"  [警告] 放置失败: {file_info['name']} - {e}")

    # 未分类
    if no_purpose:
        unknown_dir = os.path.join(split_base, "未分类")
        os.makedirs(unknown_dir, exist_ok=True)
        log(f"  [未分类] {len(no_purpose)} 个视频 → {unknown_dir}", force=True)
        for file_info in no_purpose:
            src = file_info["path"]
            dst = os.path.join(unknown_dir, file_info["name"])
            try:
                _place_file(src, dst)
                catalog_lines.append(f"未分类\t{src}\t{dst}\t{file_info['size_readable']}")
            except (IOError, OSError) as e:
                log(f"  [警告] 放置失败: {file_info['name']} - {e}")

    if use_hardlink:
        log(f"  [拆分] 硬链接 {link_count} 个，复制 {copy_count} 个（跨盘回退）", force=True)

    # 导出清单
    catalog_path = os.path.join(split_base, "split_catalog.txt")
    try:
        with open(catalog_path, "w", encoding="utf-8") as f:
            f.write(f"# 数据集分类拆分清单\n")
            f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# 格式: 用途\\t原路径\\t目标路径\\t大小\n\n")
            for line in catalog_lines:
                f.write(line + "\n")
        log(f"[导出] 分类清单 → {catalog_path}", force=True)
    except (IOError, OSError):
        pass

    log(f"\n完成！分类结果已保存至: {split_base}", force=True)


# ============================================================
# v2.4 新增子命令处理函数
# ============================================================
def _run_reload_labels(args):
    """热加载标签配置文件（v2.4 新增）"""
    log("重新加载 dataset_labels.ini 标签配置...", force=True)
    labels = load_dataset_labels()
    log(f"  场景标签: {len(labels['scene'])} 个", force=True)
    log(f"  物体标签: {len(labels['object'])} 个", force=True)
    log(f"  行为标签: {len(labels['action'])} 个", force=True)
    log(f"  用途规则: {len(labels['purpose_rules'])} 条", force=True)
    log("  标签配置已重新加载", force=True)


def _run_test(args):
    """内置测试子命令（v2.4 新增）：一键校验核心逻辑"""
    log("=" * 60, force=True)
    log("  内置核心逻辑测试", force=True)
    log("=" * 60, force=True)

    passed = 0
    failed = 0

    # 测试1: LSH分桶均匀性
    try:
        # 使用分散的 64-bit 指纹，验证多 band 索引不会退化成单一大桶。
        test_hashes = {
            i: {"phash": [f"{((i * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)):016x}"]}
            for i in range(100)
        }
        buckets = _build_lsh_buckets(test_hashes, 32)
        max_bucket = max(len(v) for v in buckets.values())
        if max_bucket < 20:  # 均匀分布下每桶应<10
            passed += 1
            log("  [PASS] LSH分桶均匀性", force=True)
        else:
            failed += 1
            log(f"  [FAIL] LSH分桶不均匀，最大桶 {max_bucket}", force=True)
    except Exception as e:
        failed += 1
        log(f"  [FAIL] LSH测试异常: {e}", force=True)

    # 测试2: 时长筛选解析
    try:
        conditions = parse_duration_filter(">=60&<=360")
        assert len(conditions) == 2
        assert conditions[0] == (">=", 60.0)
        assert conditions[1] == ("<=", 360.0)
        assert match_duration(120, conditions) == True
        assert match_duration(30, conditions) == False
        assert match_duration(400, conditions) == False
        passed += 1
        log("  [PASS] 时长筛选解析", force=True)
    except Exception as e:
        failed += 1
        log(f"  [FAIL] 时长筛选异常: {e}", force=True)

    # 测试3: 相似度计算
    try:
        h1 = {"phash": [imagehash.phash(Image.new("L", (32, 32), 128))],
              "dhash": [imagehash.dhash(Image.new("L", (32, 32), 128))],
              "duration": 10.0}
        h2 = {"phash": [imagehash.phash(Image.new("L", (32, 32), 128))],
              "dhash": [imagehash.dhash(Image.new("L", (32, 32), 128))],
              "duration": 10.0}
        result = compute_similarity(h1, h2)
        assert result["similarity"] > 0.99
        passed += 1
        log(f"  [PASS] 相似度计算 (sim={result['similarity']:.4f})", force=True)
    except Exception as e:
        failed += 1
        log(f"  [FAIL] 相似度计算异常: {e}", force=True)

    # 测试4: 路径标准化
    try:
        p = _normalize_path("test.mp4")
        assert len(p) > 0
        passed += 1
        log("  [PASS] 路径标准化", force=True)
    except Exception as e:
        failed += 1
        log(f"  [FAIL] 路径标准化异常: {e}", force=True)

    # 测试5: 缓存读写
    try:
        test_cache = {"_version": CACHE_VERSION, "test_path": {"size": 100, "mtime": 0}}
        test_path = os.path.join(tempfile.gettempdir(), "test_cache_v24.json")
        save_cache(test_path, test_cache)
        loaded = load_cache(test_path)
        assert loaded.get("_version") == CACHE_VERSION
        assert "test_path" in loaded
        os.remove(test_path)
        passed += 1
        log("  [PASS] 缓存读写", force=True)
    except Exception as e:
        failed += 1
        log(f"  [FAIL] 缓存读写异常: {e}", force=True)

    log(f"\n{'=' * 60}", force=True)
    log(f"  测试结果: {passed} 通过 / {failed} 失败", force=True)
    log(f"{'=' * 60}", force=True)




# 【v2.6 新增】子命令委托函数：将新增子命令转发到独立模块执行
def _delegate_to_module(module_name: str, sub_cmd: Optional[str], args) -> None:
    """
    将子命令委托到独立模块执行（v2.6 新增）。
    保持主程序与拓展模块解耦，缺失模块时给出安装提示。

    Args:
        module_name: 目标模块名（不带 .py）
        sub_cmd: 子命令名（None 表示直接运行模块）
        args: 原始命令行参数
    """
    import importlib
    script_dir = repo_dir()

    # 特殊处理 dashboard：直接启动 Streamlit
    if module_name == "dashboard":
        dashboard_path = os.path.join(script_dir, "dashboard.py")
        if not os.path.exists(dashboard_path):
            log("[错误] 未找到 dashboard.py", force=True)
            log(f"  请确认文件位于: {dashboard_path}", force=True)
            log("  或重新创建该文件，参考 README 中看板模块说明", force=True)
            sys.exit(EXIT_BAD_ARGS)
        try:
            import streamlit  # noqa: F401
        except ImportError:
            log("[错误] 可视化看板需要 streamlit 库", force=True)
            log("  安装: pip install streamlit plotly pandas", force=True)
            log("  国内镜像: pip install streamlit plotly pandas -i https://pypi.tuna.tsinghua.edu.cn/simple", force=True)
            sys.exit(EXIT_BAD_ARGS)
        log("[启动] 正在启动可视化看板...", force=True)
        log("  浏览器访问: http://localhost:8501", force=True)
        log("  按 Ctrl+C 停止", force=True)
        os.system(f"streamlit run \"{dashboard_path}\"")
        return

    # 通用模块委托
    module_path = os.path.join(script_dir, f"{module_name}.py")
    if not os.path.exists(module_path):
        log(f"[错误] 未找到模块文件: {module_name}.py", force=True)
        log(f"  请确认文件位于: {module_path}", force=True)
        sys.exit(EXIT_BAD_ARGS)

    try:
        mod = importlib.import_module(module_name)
    except ImportError as e:
        log(f"[错误] 加载模块 {module_name} 失败: {e}", force=True)
        missing = str(e)
        if "streamlit" in missing:
            log("  安装: pip install streamlit", force=True)
        elif "plotly" in missing:
            log("  安装: pip install plotly", force=True)
        elif "openpyxl" in missing:
            log("  安装: pip install openpyxl", force=True)
        elif "reportlab" in missing:
            log("  安装: pip install reportlab", force=True)
        else:
            log(f"  请检查依赖安装: {e}", force=True)
        sys.exit(EXIT_BAD_ARGS)

    # 调用模块的 main 函数
    if hasattr(mod, "main"):
        # 重写 sys.argv 让子模块的 argparse 正确解析
        new_argv = [sys.argv[0]]
        if sub_cmd:
            new_argv.append(sub_cmd)
        # 附加剩余参数（跳过原始子命令）
        skip_first_sub = False
        for arg in sys.argv[1:]:
            if not skip_first_sub and not arg.startswith("-"):
                skip_first_sub = True
                continue
            new_argv.append(arg)
        old_argv = sys.argv
        sys.argv = new_argv
        try:
            _mod_rc = mod.main()
        finally:
            sys.argv = old_argv
        # 【v2.7 新增】透传模块 main() 返回的整数退出码（如 label_verify）
        if isinstance(_mod_rc, int) and _mod_rc != 0:
            sys.exit(_mod_rc)
    else:
        log(f"[错误] 模块 {module_name} 缺少 main() 入口函数", force=True)
        sys.exit(EXIT_BAD_ARGS)


# 【v2.5 新增】AI 自动分类子命令
def _run_auto_classify(args):
    """执行 AI 自动分类"""
    if not _ai_probe()['ok']:
        log("[错误] AI 自动分类需要 torch/open_clip/scikit-learn 库", force=True)
        log("  安装: pip install -r requirements_ai.txt", force=True)
        sys.exit(EXIT_BAD_ARGS)

    folder_path = _resolve_path(args.dir)
    recursive = not args.no_recursive
    output_dir = _resolve_path(args.output_dir) or os.path.join(folder_path, "_classify_output")
    dry_run = not getattr(args, "execute", False)
    n_clusters = getattr(args, "n_clusters", 0)
    classify_method = getattr(args, "classify_method", "kmeans")
    cluster_thresh = getattr(args, "cluster_thresh", 0.5)
    link_mode = "hardlink" if getattr(args, "link_mode", False) else "dry-run"
    
    # 【v2.5 联动】筛选条件
    skip_low_quality = getattr(args, "skip_low_quality", False)

    # 1. 扫描视频
    log(f"\n[步骤1] 扫描视频: {folder_path}", force=True)
    mp4_files = scan_mp4_files(args)  # v2.9 修复：原代码引用了未定义的 scan_videos

    log(f"  找到 {len(mp4_files)} 个视频", force=True)

    if not mp4_files:
        log("  无视频可处理", force=True)
        return

    # 1.5 【v2.5 联动】应用时长/分辨率筛选（复用现有函数）
    cache_path = os.path.join(repo_dir(), CACHE_FILE)
    mp4_files, filter_stats = apply_duration_resolution_filter(
        mp4_files, args, cache_path
    )
    if filter_stats.get("total_after", 0) < filter_stats.get("total_before", 0):
        log(f"  筛选后剩余 {len(mp4_files)} 个视频", force=True)

    # 2. 加载/提取语义特征
    log(f"\n[步骤2] 提取 AI 语义特征...", force=True)
    semantic_data = {}
    cache = load_cache(cache_path)

    # 已有缓存
    for f in mp4_files:
        path = f["path"]
        if path in cache and "semantic_emb" in cache[path]:
            semantic_data[path] = cache[path]

    log(f"  已有语义缓存: {len(semantic_data)} 条", force=True)

    # 需要新提取的
    to_analyze = [f for f in mp4_files if f["path"] not in semantic_data]
    if to_analyze:
        log(f"  新提取: {len(to_analyze)} 个视频", force=True)
        model, preprocess, device = _ai_mod().load_clip_model()
        
        for i, f in enumerate(to_analyze):
            path = f["path"]
            try:
                result = _ai_mod().semantic_analyze_video(path, model, preprocess, device)
                semantic_data[path] = result
                cache[path] = result
            except Exception as e:
                log(f"    [警告] 分析失败 {f['name']}: {e}")
            
            if (i + 1) % 50 == 0:
                log(f"    进度: {i + 1}/{len(to_analyze)}")
                save_cache(cache_path, cache)

        save_cache(cache_path, cache)
    
    # 2.5 【v2.5 联动】低质量过滤
    if skip_low_quality:
        log(f"\n[步骤2.5] 过滤低质量视频...", force=True)
        before = len(mp4_files)
        mp4_files, semantic_data, removed = apply_skip_low_quality(
            mp4_files, semantic_data, log_fn=log
        )
        after = len(mp4_files)
        log(f"  移除 {removed} 个低质量视频，剩余 {after} 个", force=True)

    # 3. 执行自动分类
    log(f"\n[步骤3] AI 自动分类 (方法={classify_method}, 聚类数={n_clusters or '自动'})...", force=True)
    from ai_semantic import auto_classify_videos
    
    classify_result = auto_classify_videos(
        semantic_data=semantic_data,
        output_dir=output_dir,
        classify_method=classify_method,
        n_clusters=n_clusters,
        cluster_thresh=cluster_thresh,
        link_mode=link_mode,
        dry_run=dry_run,
        _log_fn=log,
    )

    # 4. 汇总
    log(f"\n{'='*60}", force=True)
    log(f"  AI 自动分类完成", force=True)
    log(f"{'='*60}", force=True)
    log(f"  总视频数: {len(mp4_files)}", force=True)
    log(f"  分类数: {len(classify_result.get('cluster_names', {}))}", force=True)
    log(f"  报告路径: {classify_result.get('classify_report', '')}", force=True)
    if dry_run:
        log(f"\n  [试运行] 未执行文件操作。如需执行，添加 --execute 参数。", force=True)
        log(f"  示例: python find_mp4.py auto-classify --dir {folder_path} --execute", force=True)


def _run_interactive_wizard() -> list:
    """
    交互式配置向导（v2.6 新增）。
    分步问答引导用户输入扫描目录、相似度阈值、时长筛选等参数。
    返回构造的命令行参数列表（sys.argv 格式）。
    """
    print("=" * 60)
    print("  find_mp4.py v2.6 交互式配置向导")
    print("=" * 60)
    print("  按 Ctrl+C 可随时退出，直接回车使用默认值")
    print()

    cmd_args = ["find_mp4.py"]

    # 步骤 1：选择子命令
    print("【步骤 1/5】选择操作模式")
    print("  1. 视频查重扫描（默认）")
    print("  2. 纯时长统计（不查重）")
    print("  3. AI 自动分类")
    print("  4. 生成综合报告")
    choice = input("请选择 [1-4]（默认 1）: ").strip() or "1"

    if choice == "2":
        cmd_args.extend(["duration-stat"])
    elif choice == "3":
        cmd_args.extend(["auto-classify"])
    elif choice == "4":
        cmd_args.extend(["full-report"])
    # choice == "1" 不需要子命令

    # 步骤 2：扫描目录
    print("\n【步骤 2/5】输入扫描目录")
    default_dir = os.getcwd()
    dir_input = input(f"请输入视频目录路径（默认当前目录 {default_dir}）: ").strip()
    cmd_args.extend(["--dir", dir_input if dir_input else default_dir])

    # 步骤 3：相似度阈值
    if choice in ("1", "4"):
        print("\n【步骤 3/5】相似度阈值")
        print("  0.7 = 严格（推荐）  0.6 = 宽松  0.8 = 极严格")
        thresh = input("请输入阈值 0.0-1.0（默认 0.7）: ").strip()
        if thresh:
            try:
                val = float(thresh)
                if 0.0 <= val <= 1.0:
                    cmd_args.extend(["--threshold", str(val)])
            except ValueError:
                print("  [警告] 无效阈值，使用默认 0.7")

    # 步骤 4：时长筛选
    print("\n【步骤 4/5】时长筛选（可选）")
    print("  示例：>=60（≥1分钟）  <30（<30秒）  >120&<=360（2-6分钟）")
    dur = input("请输入时长条件（留空跳过）: ").strip()
    if dur:
        if choice == "2":
            cmd_args.extend(["--duration-filter", dur])
        else:
            cmd_args.extend(["--duration-filter", dur])

    # 步骤 5：输出格式
    if choice in ("1", "4"):
        print("\n【步骤 5/5】报告输出格式")
        print("  1. HTML（默认）  2. Markdown  3. Excel  4. 全部")
        fmt = input("请选择 [1-4]（默认 1）: ").strip() or "1"
        fmt_map = {"1": "html", "2": "md", "3": "xlsx", "4": "all"}
        if fmt in fmt_map:
            cmd_args.extend(["--format", fmt_map[fmt]])

    print("\n" + "=" * 60)
    print("  配置完成！即将执行：")
    print("  " + " ".join(f'"{a}"' if " " in a else a for a in cmd_args[1:]))
    print("=" * 60)
    confirm = input("\n确认执行？[Y/n]: ").strip().lower() or "y"
    if confirm not in ("y", "yes", "是"):
        print("已取消。")
        sys.exit(0)

    return cmd_args


def _apply_preset(args):
    """【v2.8 新增】场景预设：显式指定的命令行参数优先于预设值。"""
    name = getattr(args, "preset", "general") or "general"
    if name == "general":
        return args
    argv = sys.argv[1:]

    def _not_set(flag):
        return not any(a == flag or a.startswith(flag + "=") for a in argv)

    if name == "surveillance":
        if _not_set("--threshold"):
            args.threshold = 0.85
        if _not_set("--group-min-sim"):
            args.group_min_sim = 0.8
        if _not_set("--frames"):
            args.frames = 6
        log("[预设] surveillance（固定机位监控）: threshold=0.85 ｜ group-min-sim=0.8 ｜ frames=6", force=True)
    elif name == "footage":
        if _not_set("--threshold"):
            args.threshold = 0.7
        log("[预设] footage（个人素材库）: threshold=0.7", force=True)
    return args


def main():

    args = _apply_preset(parse_args())

    # 【v2.6 新增】交互式配置向导
    if getattr(args, "interactive", False):
        import sys as _sys
        _sys.argv = _run_interactive_wizard()
        args = _apply_preset(parse_args())

    # 先加载配置，再统一校验；否则配置注入的字符串/非法范围会绕过校验。
    if args.config:
        args = load_config_file(args.config, args)
    validate_args(args)

    cmd = getattr(args, "command", "scan")

    # --version
    if cmd == "version" or args.version:
        _print_version()
        return

    # --clean-cache
    if cmd == "clean-cache" or args.clean_cache:
        script_dir = repo_dir()
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
        script_dir = repo_dir()
        output_path = os.path.join(script_dir, CACHE_FILE)
        total, count = merge_caches(cache_files, output_path)
        log(f"合并完成: 共合并 {total} 条，总条目 {count} 条", force=True)
        return

    # --verify-cache
    if cmd == "verify-cache" or args.verify_cache:
        script_dir = repo_dir()
        cache_path = os.path.join(script_dir, CACHE_FILE)
        log(f"校验缓存: {cache_path}", force=True)
        removed, remaining = clean_invalid_cache(cache_path)
        log(f"  已清理 {removed} 条无效缓存，剩余 {remaining} 条", force=True)
        return

    # v2.3 新增：--cache-expire-days 过期清理
    expire_days = getattr(args, "cache_expire_days", 0)
    if expire_days > 0:
        script_dir = repo_dir()
        cache_path = os.path.join(script_dir, CACHE_FILE)
        expired = clean_expired_cache(cache_path, expire_days)
        if expired > 0:
            log(f"  已清理 {expired} 条超过 {expire_days} 天的缓存", force=True)

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

    # ============ v2.3 新增子命令 ============
    # --clear-semantic-cache
    if cmd == "clear-semantic-cache":
        _run_clear_semantic_cache(args)
        return

    # --dataset-split
    if cmd == "dataset-split":
        _run_dataset_split(args)
        return

    # ============ v2.4 新增子命令 ============
    # --duration-stat
    if cmd == "duration-stat":
        _run_duration_stat(args)
        return

    # --reload-labels
    if cmd == "reload-labels":
        _run_reload_labels(args)
        return

    # --test
    if cmd == "test":
        _run_test(args)
        return

    if cmd == "validate-plan":
        _APP_CTX.global_exit_code = _run_validate_plan(args)
        return
    if cmd == "execute-plan":
        _APP_CTX.global_exit_code = _run_execute_plan(args)
        return
    if cmd == "restore-operation":
        _APP_CTX.global_exit_code = _run_restore_operation(args)
        return
    if cmd == "list-operations":
        _APP_CTX.global_exit_code = _run_list_operations(args)
        return
    if cmd == "purge-operations":
        _APP_CTX.global_exit_code = _run_purge_operations(args)
        return

    # 【v2.5 新增】--auto-classify AI 自动分类
    if cmd == "auto-classify" or getattr(args, "classify_only", False):
        _run_auto_classify(args)
        return

    # ============ 【v2.6 新增】可视化看板与拓展工具子命令委托 ============
    # 所有 v2.6 新增子命令委托到独立模块执行，保持主程序解耦
    _V26_DELEGATE = {
        # dashboard.py - Streamlit 可视化看板
        "dashboard": ("dashboard", None),
        # batch_tools.py - 批量处理工具
        "batch-scan": ("batch_tools", "batch-scan"),
        "export-thumbnails": ("batch_tools", "export-thumbnails"),
        "backup-duplicates": ("batch_tools", "backup-duplicates"),
        "replace-hardlinks": ("batch_tools", "replace-hardlinks"),
        "organize": ("batch_tools", "organize"),
        "extract-segments": ("batch_tools", "extract-segments"),
        # media_analyze.py - 媒体分析工具
        "media-info": ("media_analyze", "media-info"),
        "space-analyze": ("media_analyze", "space-analyze"),
        "tag-manage": ("media_analyze", "tag-manage"),
        "similar-search": ("media_analyze", "similar-search"),
        "export-snapshot": ("media_analyze", "export-snapshot"),
        "diff-scan": ("media_analyze", "diff-scan"),
        # report_generator.py - 增强报告系统
        "full-report": ("report_generator", "full-report"),
        "export-pdf": ("report_generator", "export-pdf"),
        "diff-report": ("report_generator", "diff-report"),
        "quality-report": ("report_generator", "quality-report"),
        "archive": ("report_generator", "archive"),
        # label_verify.py - 预标注一致性验证（v2.7 新增）
        "label-verify": ("label_verify", None),
        # pipeline.py - 数据集一键体检（v2.8 新增）
        "pipeline": ("pipeline", None),
    }
    if cmd in _V26_DELEGATE:
        module_name, sub_cmd = _V26_DELEGATE[cmd]
        _delegate_to_module(module_name, sub_cmd, args)
        return

    # --gen-restore
    if getattr(args, "gen_restore", False):
        _run_gen_restore(args)
        return

    # v2.4 安全：永久删除无论 quiet 与否都必须显式确认。
    if getattr(args, "hard_delete", False):
        print("\n" + "=" * 60)
        print("  [警告] --hard-delete 将生成永久删除脚本！")
        print("  此操作不可逆，被删除的视频无法恢复。")
        print("=" * 60)
        # 非交互终端不能安全确认，默认拒绝，防止脚本/看板绕过保护。
        if not sys.stdin.isatty():
            print("  非交互终端拒绝 --hard-delete；请在交互终端输入 YES。")
            return
        confirm = input("  确认要生成永久删除脚本吗？(输入 YES 继续): ")
        if confirm.strip().upper() != "YES":
            print("  已取消永久删除脚本生成。")
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
    _APP_CTX.quiet_mode = quiet

    if args.fast:
        num_frames = min(num_frames, FAST_FRAMES)
        threshold = max(threshold, FAST_THRESHOLD)

    # 输出目录
    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = repo_dir()
    os.makedirs(output_dir, exist_ok=True)

    # 日志
    log_init(output_dir)
    _register_signal_handler()  # 【v2.6 修复】统一信号注册，避免重复

    cache_path = os.path.join(output_dir, CACHE_FILE)
    keep_strategy = _get_keep_strategy(args)

    log("=" * 60, force=True)
    log("       MP4 视频相似度查重工具 v2.4", force=True)
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
    if _ai_probe()['ok']:
        log(f"  AI语义:     {'是' if args.semantic else '否'}", force=True)
        if args.semantic:
            log(f"  AI设备:     {_APP_CTX.semantic_clip_device}", force=True)
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
            _APP_CTX.global_exit_code = EXIT_OK
            return

        log(f"  共找到 {len(mp4_files)} 个视频文件", force=True)

        # 【改造 v2.4】步骤1.5：应用时长筛选 + 分辨率筛选（哈希提取前过滤）
        duration_filter = getattr(args, "duration_filter", "")
        min_res = getattr(args, "min_res", 0)
        max_res = getattr(args, "max_res", 0)
        if duration_filter or min_res > 0 or max_res > 0:
            log("\n[步骤1.5] 应用时长/分辨率筛选...", force=True)
            mp4_files, filter_stats = apply_duration_resolution_filter(
                mp4_files, args, cache_path,
            )
            if not mp4_files:
                log("筛选后无剩余视频，程序退出。", force=True)
                _APP_CTX.global_exit_code = EXIT_OK
                return

        # 2. 提取哈希
        log("\n[步骤2] 提取感知哈希...", force=True)
        video_hashes, bad_videos, cache = extract_hashes_with_cache(
            mp4_files, cache_path, num_frames, max_workers, args, use_cache,
        )

        # 2.5 AI 语义分析（可选）
        semantic_results = {}
        if args.semantic and _ai_probe()['ok']:
            log("\n[步骤2.5] AI 语义分析...", force=True)
            model, preprocess, device = _ensure_clip_model()
            if model is not None:
                semantic_results = _run_semantic_analysis(
                    mp4_files, cache_path, model, preprocess, device, args,
                    video_hashes=video_hashes,  # v2.3 帧复用
                )

                # 用途筛选
                purpose_filter = args.purpose_filter
                if purpose_filter and semantic_results:
                    purposes = [p.strip() for p in purpose_filter.split(",") if p.strip()]
                    filtered_indices = set()
                    for i, sd in semantic_results.items():
                        if sd.get("dataset_purpose", "") in purposes:
                            filtered_indices.add(i)
                    # 【改造修复 v2.4】同步截断 mp4_files 与 video_hashes，
                    # 确保哈希比对、分组逻辑不再加载未符合用途的视频，筛选完全生效
                    if len(filtered_indices) < len(video_hashes):
                        log(f"  用途筛选: {len(video_hashes)} → {len(filtered_indices)} 个视频", force=True)
                        video_hashes = {idx: vh for idx, vh in video_hashes.items()
                                        if idx in filtered_indices}
                        # 同步过滤语义结果，保持索引一致
                        semantic_results = {idx: sd for idx, sd in semantic_results.items()
                                            if idx in filtered_indices}
            else:
                log("  [警告] CLIP 模型不可用，跳过语义分析", force=True)

        # 【改造 v2.4】步骤2.6：应用 --skip-low-quality 过滤低质量视频
        if getattr(args, "skip_low_quality", False) and semantic_results:
            mp4_files, semantic_results, removed = apply_skip_low_quality(
                mp4_files, semantic_results,
            )
            if removed > 0:
                # apply_skip_low_quality 已重建连续索引，按新索引同步哈希。
                keep_idx = set(semantic_results.keys())
                video_hashes = {idx: vh for idx, vh in video_hashes.items()
                                if idx in keep_idx}

        # 3. 快速预筛
        meta_dups = []
        skip_pairs = None
        if not args.check_only and len(video_hashes) >= 2:
            log("\n[步骤3] 快速预筛（元数据匹配）...", force=True)
            meta_dups, skip_pairs = pre_filter_by_metadata(mp4_files)
            if meta_dups:
                log(f"  发现 {len(meta_dups)} 对元数据相同的视频，将继续进行内容校验", force=True)

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
            # 【改造 v2.4】加载 config.ini 自定义权重
            weights = load_weights_config(getattr(args, "config", None) or "")
            similar_pairs = find_similar_pairs(
                video_hashes, mp4_files, threshold, skip_pairs, lsh, weights=weights
            )

            # 元数据相同仅作为候选提示，真实重复必须通过视觉/音频相似度确认。
            # 不再直接注入 similarity=1.0，避免同大小同时间戳文件误报。
            groups = build_groups(similar_pairs, mp4_files, video_hashes, keep_strategy,
                                  min_group_sim=getattr(args, "group_min_sim", 0.0))
            # 【v2.7 新增】查重与标注联动：识别混合标注分组并告警
            _label_regex = getattr(args, "label_regex", "")
            if _label_regex:
                import re as _re
                try:
                    _pat = _re.compile(_label_regex)
                    _mixed_cnt = 0
                    for _g in groups:
                        _lbls = set()
                        for _idx, _info in _g["members"]:
                            _m = _pat.search(os.path.splitext(_info.get("name", ""))[0])
                            if _m and _m.group(1):
                                _lbls.add(_m.group(1))
                        _g["labels"] = sorted(_lbls)
                        _g["mixed_labels"] = len(_lbls) > 1
                        if _g["mixed_labels"]:
                            _mixed_cnt += 1
                    if _mixed_cnt:
                        log(f"  [标注联动] {_mixed_cnt}/{len(groups)} 个分组混合了不同标注"
                            f"（pos/neg 或多行为），多为固定机位场景误聚，"
                            f"清理前务必人工复核", force=True)
                except _re.error as _exc:
                    log(f"  [警告] --label-regex 无效: {_exc}", force=True)

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
            # 【改造 v2.4】--output-prefix 自定义输出文件前缀
            _prefix = getattr(args, "output_prefix", "") or ""
            _path_mask = getattr(args, "path_mask", False)

            csv_path = _prefixed_path(output_dir, RESULT_CSV, _prefix)
            group_path = _prefixed_path(output_dir, RESULT_GROUPS, _prefix)
            bad_path = _prefixed_path(output_dir, BAD_VIDEO_LIST, _prefix)
            paths_path = _prefixed_path(output_dir, RESULT_PATHS, _prefix)

            export_csv(similar_pairs, csv_path, threshold, semantic_data,
                       lite_csv=getattr(args, "lite_csv", False))

            fmt = args.format
            if fmt == "md":
                md_path = _prefixed_path(output_dir, RESULT_GROUPS_MD, _prefix)
                export_groups_md(groups, mp4_files, md_path, threshold, min_sim, semantic_data)
            elif fmt == "html":
                html_path = _prefixed_path(output_dir, RESULT_GROUPS_HTML, _prefix)
                export_groups_html(groups, mp4_files, html_path, threshold, min_sim, semantic_data,
                                   video_hashes=video_hashes)
            elif fmt == "xlsx":
                # 【改造 v2.4】Excel 导出支持
                xlsx_path = _prefixed_path(output_dir, "similar_result.xlsx", _prefix)
                export_groups_xlsx(similar_pairs, groups, mp4_files, xlsx_path,
                                   threshold, min_sim, semantic_data)
            else:
                export_groups_txt(groups, mp4_files, group_path, threshold, min_sim, semantic_data)

            export_paths_list(groups, mp4_files, paths_path)
            export_bad_videos(bad_videos, bad_path)
            if getattr(args, "summary_json", False):
                summary_path = _prefixed_path(output_dir, "scan_summary.json", _prefix)
                export_summary_json(summary_path, mp4_files, video_hashes,
                                    bad_videos, groups, semantic_results)

            # v2.3 新增：损坏视频纯路径清单
            if getattr(args, "export_bad_paths", False):
                export_bad_paths(bad_videos, output_dir)

            # 【改造 v2.4】--export-clean-list 仅待清理视频路径清单
            if getattr(args, "export_clean_list", False) and groups:
                clean_list_path = _prefixed_path(output_dir, CLEAN_LIST_FILE, _prefix)
                export_clean_list(groups, mp4_files, clean_list_path, path_mask=_path_mask)

            if args.export_hash and video_hashes:
                export_path = _prefixed_path(output_dir, HASH_EXPORT, _prefix)
                export_hash_backup(video_hashes, mp4_files, export_path)

            if args.gen_cleanup and groups:
                protect = set(f.strip() for f in (args.protect_folder or "").split(",") if f.strip())
                generate_cleanup_script(
                    groups, mp4_files, output_dir, args.hard_delete, protect,
                    backup_path=getattr(args, "backup_path", ""),
                    protect_file=getattr(args, "protect_file", ""),
                    semantic_data=semantic_data if semantic_results else None,
                    path_mask=_path_mask,
                )
                export_audit_log(output_dir, groups, mp4_files, args.hard_delete,
                                 semantic_data, path_mask=_path_mask)

            # v2.2 AI 数据集导出
            if args.export_dataset and semantic_data:
                log("\n[步骤5.5] 导出 AI 数据集文件...", force=True)
                catalog_path = _prefixed_path(output_dir, DATASET_CATALOG, _prefix)
                _ai_mod().export_dataset_catalog(semantic_data, catalog_path)

                list_path = _prefixed_path(output_dir, TRAIN_SAMPLE_LIST, _prefix)
                purpose_list = [p.strip() for p in args.purpose_filter.split(",")] if args.purpose_filter else None
                _ai_mod().export_train_sample_list(semantic_data, list_path, purpose_filter=purpose_list)

                stats_path = _prefixed_path(output_dir, DATASET_STATS, _prefix)
                _ai_mod().export_dataset_stats(semantic_data, stats_path)

            # v2.2 语义聚类导出
            if args.cluster_semantic and semantic_data and _ai_probe()['sklearn_ok']:
                log("\n[步骤5.6] 语义聚类分析...", force=True)
                embeddings = []
                for i, sd in semantic_results.items():
                    emb = sd.get("semantic_emb")
                    if emb is not None:
                        embeddings.append(emb)
                if len(embeddings) >= 2:
                    clusters = _ai_mod().cluster_videos_by_semantic(np.array(embeddings))
                    cluster_html = _prefixed_path(output_dir, SCENE_CLUSTER_HTML, _prefix)
                    _ai_mod().export_scene_cluster_html(clusters, semantic_data, cluster_html)

            # v2.2 语义元数据导出
            if semantic_data:
                meta_path = _prefixed_path(output_dir, SEMANTIC_META, _prefix)
                try:
                    json_data = json.dumps(semantic_data, ensure_ascii=False, indent=2, default=str)
                    _atomic_write_text(meta_path, json_data)
                    log(f"[导出] 语义元数据 → {meta_path}", force=True)
                except (IOError, OSError) as e:
                    log(f"[错误] 语义元数据导出失败: {e}")
        else:
            log("  [试运行] 跳过文件写入", force=True)

        # 6. 汇总
        print_summary(mp4_files, video_hashes, bad_videos, groups, semantic_results)

        # 设置退出码
        if groups:
            _APP_CTX.global_exit_code = EXIT_HAS_DUPLICATES
        elif bad_videos:
            _APP_CTX.global_exit_code = EXIT_PARSE_ERROR
        else:
            _APP_CTX.global_exit_code = EXIT_OK

        log(f"\n完成！所有结果文件已保存至: {output_dir}", force=True)

    except Exception as e:
        log(f"\n[严重错误] 程序异常终止: {e}", force=True)
        log(f"[严重错误] {traceback.format_exc()}", force=True)
        _crash_save(output_dir, cache_path)
        _APP_CTX.global_exit_code = EXIT_PARSE_ERROR
        log("[严重错误] 已尽可能保存已完成的数据", force=True)
    finally:
        log_close()

    sys.exit(_APP_CTX.global_exit_code)
