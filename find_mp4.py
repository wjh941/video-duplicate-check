#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MP4 视频相似度查重工具 v2.6
==============================
功能：扫描指定目录下的视频文件，基于多哈希融合（pHash+dHash）检测内容相似/重复的视频。
支持 LSH 加速、增量扫描、缓存管理、多格式导出、安全清理、子命令架构。
v2.2 新增：AI 语义内容分析、场景聚类、数据集自动标注、训练集清单导出。
v2.4 新增：时长筛选统计、分辨率过滤、Excel导出、HTML缩略图预览、硬链接拆分、
          路径脱敏、缓存版本迁移、自定义权重配置、全局过滤规则、恢复脚本等。
v2.5 新增：AI 自动分类（auto-classify 子命令）、AppContext 全局状态管理、
          分类与时长/分辨率/低质筛选联动、dry-run 安全预览模式。
v2.6 新增：可视化看板（dashboard.py）、增强报告系统（report_generator.py）、
          批量处理工具（batch_tools.py）、媒体分析工具（media_analyze.py）、
          子命令委托架构、多目录批量扫描、素材标签系统、语义检索、PDF导出等。

模块结构：
    0. 全局配置常量 + 退出码 + AI依赖检测 + AppContext 上下文类
    1. 日志系统（双输出 + quiet 模式）
    2. 命令行参数解析（子命令 + 扁平参数兼容）
    3. 文件扫描与过滤（多后缀 + 排除规则 + .duplicateignore + .globalignore）
    4. 哈希缓存管理（版本化 + 分块 + 合并/校验 + 语义字段 + 自动迁移）
    5. 哈希提取（双哈希融合 + 32x32预处理 + FFmpeg兜底 + 音频哈希 + --no-store-frames）
    6. 视频相似度比对（时长预筛 + LSH分桶空桶跳过 + double-check + 自定义权重）
    7. 连通图分组（多维度保留策略）
    8. 结果导出（CSV/TXT/MD/HTML+xlsx/纯路径清单/清理清单/审计日志/安全清理脚本/恢复脚本）
    9. 全局异常处理与主入口
    10. AI 语义分析模块（CLIP特征提取 + 场景分类 + 用途判定）
    11. 语义聚类与数据集导出
    12. v2.4 时长筛选统计模块
    13. v2.5 AI 自动分类模块（K-Means 聚类 + 自动命名 + 文件组织）
    14. v2.6 子命令委托架构（dashboard/batch_tools/media_analyze/report_generator）

================ v2.5 参数说明 ================

【v2.6 新增拓展工具子命令】（委托到独立模块执行，与主程序解耦）
  dashboard                  启动 Streamlit 可视化看板（需 streamlit/plotly/pandas）
  batch-scan                 多目录批量扫描（委托 batch_tools.py）
  export-thumbnails          批量导出分组缩略图集（委托 batch_tools.py）
  backup-duplicates          批量备份重复素材（委托 batch_tools.py）
  replace-hardlinks          批量替换重复视频为硬链接（委托 batch_tools.py）
  organize                   素材移动整理（按AI分类/时长/分辨率，委托 batch_tools.py）
  extract-segments           重复画面片段提取（委托 batch_tools.py）
  media-info                 批量导出视频元数据到 Excel（委托 media_analyze.py）
  space-analyze              磁盘占用分析报告（委托 media_analyze.py）
  tag-manage                 素材标签管理（增删查导出，委托 media_analyze.py）
  similar-search             语义相似度检索（输入文字找视频，委托 media_analyze.py）
  export-snapshot            导出缓存+报告快照 zip（委托 media_analyze.py）
  diff-scan                  两次扫描对比报告（委托 media_analyze.py）
  full-report                生成综合汇总 HTML 报告（委托 report_generator.py）
  export-pdf                 HTML 报告转 PDF（委托 report_generator.py）
  diff-report                数据对比报告（委托 report_generator.py）
  quality-report             AI 数据集质检报告（委托 report_generator.py）
  archive                    批量打包所有报告为 zip（委托 report_generator.py）

【v2.6 新增命令行参数】
  --report-name <名称>       自定义所有报告前缀名称
  --export-pdf               扫描完成自动生成 PDF 完整报告（需 reportlab）
  --batch-dir-list <txt>     批量扫描文件夹 txt 路径文件（一行一个目录）
  --tag <标签>               扫描时过滤带指定标签素材
  --similar-search "文本"    语义检索指定画面视频

【v2.6 新增子命令使用示例】
# 启动可视化看板
streamlit run dashboard.py
python find_mp4.py dashboard

# 批量扫描多目录
python find_mp4.py batch-scan --batch-dir-list dirs.txt --dir D:\\fallback

# 语义检索视频
python find_mp4.py similar-search "城市街道夜景" --dir D:\\Videos

# 生成综合报告 + PDF
python find_mp4.py full-report --dir D:\\Videos --project-name "项目A"
python find_mp4.py export-pdf --input full_report.html

# 磁盘空间分析
python find_mp4.py space-analyze --dir D:\\Videos

# 素材标签管理
python find_mp4.py tag-manage add --video D:\\v.mp4 --tag 精品素材
python find_mp4.py tag-manage list
python find_mp4.py tag-manage export --output tags.txt

# 导出快照存档
python find_mp4.py export-snapshot --output snapshot.zip

================ v2.5 参数说明 ================

【v2.5 新增 AI 自动分类参数】
  auto-classify              AI 自动分类子命令（基于 CLIP 特征聚类）
  --classify-only            仅执行 AI 自动分类，不查重（可替代 auto-classify 子命令）
  --classify-method <方法>   分类算法（目前支持 kmeans）
  --n-clusters <数量>        目标聚类数 (0=自动估算)
  --execute                  执行文件操作（默认 dry-run 预览模式）
  --link-mode                分类时使用硬链接（默认移动）

【v2.5 新增 AI 自动分类示例】
# AI 自动分类（默认 dry-run 预览模式，不修改文件）
python find_mp4.py auto-classify --dir D:\\Videos
# AI 自动分类并执行硬链接组织文件
python find_mp4.py auto-classify --dir D:\\Videos --execute --link-mode
# AI 自动分类 + 时长筛选联动
python find_mp4.py auto-classify --dir D:\\Videos --duration-filter ">=60"
# AI 自动分类 + 分辨率筛选 + 低质量过滤
python find_mp4.py auto-classify --dir D:\\Videos --min-res 1920 --skip-low-quality
# 仅分类（扁平参数模式）
python find_mp4.py --dir D:\\Videos --classify-only --n-clusters 8

================ v2.4 参数说明 ================

【新增时长筛选统计参数】
  --duration-filter <条件>   时长筛选条件，格式：>=60、<30、>120&<=360（单位秒）
                             支持 & 多条件并列组合，仅处理匹配时长条件的视频
  --duration-stat            仅统计符合时长条件视频数量，不执行哈希查重、不生成报告
  --duration-export          导出符合时长条件视频路径清单到 txt

【新增功能补充参数】
  --no-store-frames          不将预览帧持久化存入缓存，降低内存占用（默认保留供AI复用）
  --min-res <宽度>           筛选最小分辨率宽度，如 1920
  --max-res <宽度>           筛选最大分辨率宽度，如 3840
  --gen-restore              根据审计日志生成视频恢复 bat/sh 脚本
  --export-clean-list        单独输出仅待清理视频路径清单，不含保留素材
  --output-prefix <前缀>     自定义输出文件前缀，多批次扫描不覆盖报告
  --path-mask                审计日志、报告隐藏路径中间层级，保护素材隐私
  --cluster-thresh <阈值>    语义聚类松紧阈值，默认 0.5
  --skip-low-quality         过滤AI判定低质量模糊暗光视频，不参与后续查重
  --link-mode                数据集拆分使用硬链接，不重复复制视频节省磁盘
  --format xlsx              Excel 导出（需 openpyxl 可选依赖）

【新增子命令】
  duration-stat              时长统计子命令（纯计数快速模式）
  reload-labels              热加载 dataset_labels.ini 标签配置
  test                       内置核心逻辑自动化测试
  gen-restore                根据审计日志生成恢复脚本（等同 --gen-restore）

【参数冲突校验】
  --fast 与 --double-check 互斥
  --embed-cache 与 --no-semantic-cache 互斥
  --hard-delete 需二次确认（控制台输入 YES）

【安全特性】
  --hard-delete              生成永久删除脚本（危险！需二次确认）
  --path-mask                路径脱敏
  保护目录使用完整分隔符精准匹配（杜绝 video 误匹配 video123）
  --backup-path              清理前备份视频到指定目录

================ 全套使用示例 ================

# === 纯时长统计（不查重） ===
python find_mp4.py duration-stat --dir D:\\Videos --duration-filter ">=60"
python find_mp4.py duration-stat --dir D:\\Videos --duration-filter ">120&<=360" --duration-export

# === 带时长筛选的查重 ===
python find_mp4.py --dir D:\\Videos --duration-filter ">=60" --threshold 0.7
python find_mp4.py --dir D:\\Videos --duration-filter "<30" --format html --gen-cleanup

# === 导出时长清单 ===
python find_mp4.py --dir D:\\Videos --duration-filter ">=60" --duration-export

# === 分辨率过滤查重 ===
python find_mp4.py --dir D:\\Videos --min-res 1920 --max-res 3840

# === AI 语义聚类 ===
python find_mp4.py scan --dir D:\\Videos --semantic --cluster-semantic --cluster-thresh 0.4
python find_mp4.py cluster-scene --dir D:\\Videos

# === 数据集筛选与拆分 ===
python find_mp4.py dataset-filter --purpose 监控 --dir D:\\car_data
python find_mp4.py dataset-split --dir D:\\Videos --link-mode

# === 缓存合并 ===
python find_mp4.py merge-cache cache1.json cache2.json

# === 清理与恢复 ===
python find_mp4.py --dir D:\\Videos --gen-cleanup --hard-delete
python find_mp4.py --dir D:\\Videos --gen-cleanup --backup-path D:\\backup
python find_mp4.py --gen-restore
python find_mp4.py --dir D:\\Videos --export-clean-list --path-mask

# === Excel 导出 ===
python find_mp4.py --dir D:\\Videos --format xlsx --output-prefix batch1_

# === 自定义权重（config.ini） ===
python find_mp4.py --dir D:\\Videos --config config.ini

# === 子命令模式 ===
python find_mp4.py scan --dir D:\\Videos --semantic --export-dataset
python find_mp4.py semantic-analyze --dir D:\\video
python find_mp4.py reload-labels
python find_mp4.py test
python find_mp4.py clean-cache
python find_mp4.py verify-cache

# === 扁平参数模式（v2.1 兼容） ===
python find_mp4.py --dir D:\\Videos
python find_mp4.py --version
"""

import argparse
import base64
import configparser
import csv
import fnmatch
import glob as _glob
import io
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED, TimeoutError as FutureTimeout
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
__version__ = "2.6.0"

# 缓存版本号（算法变更时自动作废旧缓存）
CACHE_VERSION = "2.6"

# 基础参数
HASH_SIZE = 8
FRAME_TIMEOUT = 30
FRAME_SAMPLE_RANGE = (0.1, 0.9)
FAST_FRAMES = 5
FAST_THRESHOLD = 0.85

# 输出文件名
CACHE_FILE = "video_hash_cache.json"
SQLITE_CACHE_FILE = "video_hash_cache.sqlite3"
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
GLOBAL_IGNORE_FILE = ".globalignore"  # v2.4 新增：程序同目录全局过滤规则文件

# v2.2 AI 相关输出文件
SEMANTIC_META = "video_semantic_meta.json"
DATASET_CATALOG = "dataset_catalog.csv"
TRAIN_SAMPLE_LIST = "train_sample_list.txt"
DATASET_STATS = "dataset_stats.md"
SCENE_CLUSTER_HTML = "scene_cluster.html"

# v2.4 新增输出文件
CLEAN_LIST_FILE = "clean_list.txt"  # 仅待清理视频路径清单
RESTORE_SCRIPT = "restore_duplicates.bat"  # 恢复脚本
CLEANUP_PLAN_FILE = "cleanup_plan.json"  # 可审计清理计划
DURATION_LIST_FILE = "duration_filter_list.txt"  # 时长筛选清单

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

# ============================================================
# 【改造 v2.4】全局状态重构：AppContext 上下文类
# 统一管理所有全局状态，解决多子命令状态污染、线程安全问题
# ============================================================

class AppContext:
    """应用全局状态容器（单例模式）"""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        # 【改造】原全局变量迁移到此处
        self.global_cache: dict = {}
        self.global_exit_code: int = EXIT_OK
        self.global_results_saved: bool = False
        self.quiet_mode: bool = False
        self.semantic_stats: dict = {}
        self.log_file_handle: Optional[object] = None
        
        # CLIP 模型状态
        self.semantic_clip_model: Optional[object] = None
        self.semantic_clip_preprocess: Optional[object] = None
        self.semantic_clip_device: str = "cuda" if _AI_CUDA_OK else "cpu"

        # 分类相关状态（v2.5 新增）
        self.classify_results: dict = {}
        self.classify_mapping: dict = {}

# 全局上下文实例
_APP_CTX = AppContext()

# 向后兼容的全局引用（将被逐步淘汰）
_global_cache = _APP_CTX.global_cache
_global_exit_code = _APP_CTX.global_exit_code
_quiet_mode = _APP_CTX.quiet_mode
_semantic_stats = _APP_CTX.semantic_stats
_semantic_clip_model = _APP_CTX.semantic_clip_model
_semantic_clip_preprocess = _APP_CTX.semantic_clip_preprocess
_semantic_clip_device = _APP_CTX.semantic_clip_device
_log_file_handle = _APP_CTX.log_file_handle
_global_results_saved = _APP_CTX.global_results_saved

# CLIP 模型全局引用（延迟加载，v2.2）
# 全局状态
# v2.5 改造：以上全局变量均通过 _APP_CTX 管理，预留兼容层




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
    parser.add_argument("--format", choices=["txt", "md", "html", "xlsx"], default="txt")
    parser.add_argument("--fast", action="store_true", default=False)
    parser.add_argument("--check-only", action="store_true", default=False)
    parser.add_argument("--version", action="store_true", default=False)
    parser.add_argument("--clean-cache", action="store_true", default=False)
    parser.add_argument("--export-hash", action="store_true", default=False)
    parser.add_argument("--summary-json", action="store_true", default=False,
                        help="导出机器可读的扫描摘要 JSON，便于脚本和看板集成")
    parser.add_argument("--gen-cleanup", action="store_true", default=False)
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
    # 【v2.6 新增】拓展工具配套参数
    parser.add_argument("--report-name", type=str, default="",
                        help="自定义所有报告前缀名称（v2.6 新增）")
    parser.add_argument("--export-pdf", action="store_true", default=False,
                        help="扫描完成自动生成 PDF 完整报告（v2.6 新增，需 reportlab）")
    parser.add_argument("--batch-dir-list", type=str, default="",
                        help="批量扫描文件夹 txt 路径文件（v2.6 新增，一行一个目录）")
    parser.add_argument("--tag", type=str, default="",
                        help="扫描时过滤带指定标签素材（v2.6 新增）")
    parser.add_argument("--similar-search", type=str, default="",
                        help='语义检索指定画面视频，传入描述文本（v2.6 新增）')
    return parser


def parse_args():
    """解析命令行参数（子命令 + 扁平参数兼容）"""
    shared = _build_shared_parser()

    # 检查第一个有效参数是否为子命令
    subcommands = {"scan", "clean-cache", "merge-cache", "verify-cache", "version", "help",
                   "semantic-analyze", "dataset-filter", "cluster-scene",
                   "clear-semantic-cache", "dataset-split",
                   "duration-stat", "reload-labels", "test",
                   "auto-classify",  # 【v2.5 新增】AI 自动分类子命令
                   # 【v2.6 新增】可视化看板与拓展工具子命令（委托到独立模块）
                   "dashboard", "batch-scan", "media-info", "space-analyze",
                   "tag-manage", "similar-search", "export-snapshot", "diff-scan",
                   "full-report", "export-pdf", "diff-report", "quality-report",
                   "archive", "export-thumbnails", "backup-duplicates",
                   "replace-hardlinks", "organize", "extract-segments"}

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
        # 【v2.6 新增】拓展工具参数映射
        "report_name": "report-name",
        "export_pdf": "export-pdf",
        "batch_dir_list": "batch-dir-list",
        "tag": "tag",
        "similar_search": "similar-search",
    }
    bool_fields = {
        "double-check", "audio-check", "fast", "incremental", "keep-latest",
        "keep-max-res", "keep-max-bitrate", "semantic", "cluster-semantic",
        "export-dataset", "quiet", "dry-run", "duration-export", "no-store-frames",
        "export-clean-list", "path-mask", "skip-low-quality", "link-mode",
        "compress-cache", "hard-delete", "gen-restore", "classify-only", "execute",
        "export-pdf",
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

    if semantic_cmd and not AI_MODULE_AVAILABLE:
        # clear-semantic-cache 不需要 AI 模块
        if cmd == "clear-semantic-cache":
            pass
        elif cmd == "dataset-split":
            pass  # dataset-split 可在无 AI 时用缓存运行
        else:
            print("错误: AI 子命令需要 ai_semantic 模块，请安装 torch/open-clip/sklearn")
            sys.exit(EXIT_BAD_ARGS)

    if ai_needed and not AI_MODULE_AVAILABLE and not semantic_cmd:
        if not _quiet_mode:
            print("[警告] AI 依赖未安装，--semantic/--cluster-semantic/--export-dataset 将自动降级为纯哈希查重")
        # 自动关闭 AI 相关参数
        args.semantic = False
        args.cluster_semantic = False
        args.export_dataset = False


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
    script_dir = os.path.dirname(os.path.abspath(__file__))
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
        script_dir = os.path.dirname(os.path.abspath(__file__))
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
    script_dir = os.path.dirname(os.path.abspath(__file__))
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
    global _quiet_mode
    _quiet_mode = args.quiet
    folder_path = _resolve_path(args.dir)
    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = os.path.dirname(os.path.abspath(__file__))
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
def _compute_frame_hashes(gray_frame: np.ndarray) -> tuple:
    """对灰度帧计算 pHash + dHash"""
    pil_img = Image.fromarray(gray_frame)
    phash = imagehash.phash(pil_img, hash_size=HASH_SIZE)
    dhash = imagehash.dhash(pil_img, hash_size=HASH_SIZE)
    return phash, dhash


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
    global _global_cache

    cache = load_cache(cache_path) if use_cache else {"_version": CACHE_VERSION}
    _global_cache = cache
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
                    _global_cache = cache
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


def export_csv(similar_pairs: list[dict], csv_path: str, threshold: float,
               semantic_data: dict = None, lite_csv: bool = False):
    """导出比对明细 CSV（v2.2 扩展语义标签列，v2.3 新增 lite_csv 轻量模式）"""
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            if lite_csv:
                # v2.3 新增：轻量化 CSV，仅路径和相似度
                header = ["视频A路径", "视频B路径", "相似度"]
            else:
                header = ["视频A路径", "视频B路径", "归一化距离", "相似度", "是否判定重复"]
                if semantic_data:
                    header.extend(["A场景标签", "A数据集用途", "B场景标签", "B数据集用途"])
            writer.writerow(header)
            for pair in similar_pairs:
                if lite_csv:
                    row = [pair["path_a"], pair["path_b"], f"{pair['similarity']:.4f}"]
                else:
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


def export_bad_paths(bad_videos: list[dict], output_dir: str):
    """v2.3 新增：单独输出损坏视频纯路径清单"""
    if not bad_videos:
        return
    bad_paths_file = os.path.join(output_dir, "bad_video_paths.txt")
    try:
        with open(bad_paths_file, "w", encoding="utf-8") as f:
            for bv in bad_videos:
                f.write(bv["path"] + "\n")
        log(f"[导出] 损坏路径清单 → {bad_paths_file}")
    except (IOError, OSError):
        pass


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


def _extract_video_thumbnail_base64(video_path: str, max_size: int = 160) -> str:
    """
    提取视频首帧并转为 base64 缩略图（v2.4 新增 HTML 缩略图预览）。
    返回 data URI 字符串，失败返回空字符串。
    """
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return ""
        # 跳过开头10%，取相对稳定帧
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total * 0.1)))
        ret, frame = cap.read()
        if not ret or frame is None:
            return ""
        # 缩放到 max_size 宽度
        h, w = frame.shape[:2]
        if w > max_size:
            scale = max_size / w
            frame = cv2.resize(frame, (max_size, int(h * scale)),
                               interpolation=cv2.INTER_AREA)
        # 转 JPEG base64
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ok:
            return ""
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return ""
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def export_groups_html(groups: list[dict], mp4_files: list[dict], html_path: str,
                        threshold: float, min_sim: float = 0.0, semantic_data: dict = None,
                        video_hashes: dict = None):
    """
    导出 HTML 可视化报告（v2.2 扩展语义标签，v2.4 新增首帧缩略图预览）。
    #【改造注释】重复分组 HTML 报告内嵌视频首帧 base64 缩略图，直观对比相似画面。
    """
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
            ".retain{background:#4CAF50;color:#fff}",
            ".clean{background:#f44336;color:#fff}",
            ".stats{background:#fff;padding:15px;border-radius:8px;margin:15px 0}",
            ".summary{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}",
            ".summary div{background:#f8f8f8;padding:10px;border-radius:4px;text-align:center}",
            ".semantic-tag{background:#2196F3;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:5px}",
            ".purpose-tag{background:#FF9800;color:white;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:5px}",
            ".thumb{width:80px;height:60px;object-fit:cover;border-radius:4px;margin-right:10px;border:1px solid #ddd}",
            ".thumb-placeholder{width:80px;height:60px;background:#eee;border-radius:4px;margin-right:10px;display:flex;align-items:center;justify-content:center;color:#999;font-size:11px}",
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

        # 【改造 v2.4】缩略图缓存，避免同组重复提取
        thumb_cache = {}

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

                # 【改造 v2.4】提取首帧缩略图（带缓存）
                thumb_html = ""
                path = info["path"]
                if path not in thumb_cache:
                    thumb_cache[path] = _extract_video_thumbnail_base64(path)
                thumb_b64 = thumb_cache[path]
                if thumb_b64:
                    thumb_html = f"<img class='thumb' src='{thumb_b64}' alt='缩略图'>"
                else:
                    thumb_html = "<div class='thumb-placeholder'>无预览</div>"

                html_parts.append(
                    f"<div class='video'>"
                    f"{thumb_html}"
                    f"<span>{fi + 1}. <b>{info['name']}</b></span>"
                    f"<span style='margin-left:auto'>{info['size_readable']}</span>"
                    f"<span class='badge {mark}'>{text}</span>"
                    f"{extra_tags}"
                    f"</div>\n"
                )
            html_parts.append("</div>\n")

        html_parts.append("</body>\n</html>")

        _atomic_write_text(html_path, "".join(html_parts))
        log(f"[导出] HTML 报告 → {html_path}")
    except (IOError, OSError) as e:
        log(f"[错误] HTML 导出失败: {e}")


def export_groups_xlsx(similar_pairs: list[dict], groups: list[dict],
                       mp4_files: list[dict], xlsx_path: str,
                       threshold: float, min_sim: float = 0.0,
                       semantic_data: dict = None):
    """
    导出 Excel 格式重复报告（v2.4 新增）。
    #【改造注释】依赖 openpyxl 可选库，缺失时给出精准 pip 安装命令。
    包含两个工作表：比对明细、分组汇总。
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        log("[错误] Excel 导出需要 openpyxl 库，请安装：")
        log("  pip install openpyxl")
        log("  或使用国内镜像: pip install openpyxl -i https://pypi.tuna.tsinghua.edu.cn/simple")
        return

    try:
        wb = Workbook()

        # 工作表1：比对明细
        ws1 = wb.active
        ws1.title = "比对明细"
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        headers = ["视频A", "视频B", "相似度", "距离", "是否重复"]
        if semantic_data:
            headers.extend(["A用途", "B用途"])
        ws1.append(headers)
        for col in range(1, len(headers) + 1):
            cell = ws1.cell(row=1, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for pair in similar_pairs:
            is_dup = "是" if pair["similarity"] >= threshold else "否"
            row = [pair["name_a"], pair["name_b"],
                   round(pair["similarity"], 4), round(pair["distance"], 6), is_dup]
            if semantic_data:
                sd_a = semantic_data.get(pair["path_a"], {})
                sd_b = semantic_data.get(pair["path_b"], {})
                row.append(sd_a.get("dataset_purpose", "-"))
                row.append(sd_b.get("dataset_purpose", "-"))
            ws1.append(row)

        # 工作表2：分组汇总
        ws2 = wb.create_sheet("分组汇总")
        headers2 = ["分组", "视频名", "路径", "大小", "建议", "相似度"]
        if semantic_data:
            headers2.extend(["场景", "用途"])
        ws2.append(headers2)
        for col in range(1, len(headers2) + 1):
            cell = ws2.cell(row=1, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        retain_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        clean_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

        for gi, group in enumerate(groups, 1):
            max_sim = max(group["similarities"].values()) if group["similarities"] else 1.0
            if max_sim < min_sim:
                continue
            for idx, info in group["members"]:
                is_retain = idx == group["retain_idx"]
                mark = "保留" if is_retain else "清理"
                row = [f"第{gi}组", info["name"], info["path"],
                       info["size_readable"], mark, f"{max_sim:.2%}"]
                if semantic_data:
                    sd = semantic_data.get(info["path"], {})
                    row.append(", ".join(sd.get("scene_tags", [])[:2]) or "-")
                    row.append(sd.get("dataset_purpose", "-") or "-")
                ws2.append(row)
                # 着色
                fill = retain_fill if is_retain else clean_fill
                for col in range(1, len(headers2) + 1):
                    ws2.cell(row=ws2.max_row, column=col).fill = fill

        # 调整列宽
        for ws in [ws1, ws2]:
            for col_cells in ws.columns:
                max_len = max(len(str(c.value or "")) for c in col_cells)
                ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 60)

        wb.save(xlsx_path)
        log(f"[导出] Excel 报告 → {xlsx_path}")
    except Exception as e:
        log(f"[错误] Excel 导出失败: {e}")


def export_paths_list(groups: list[dict], mp4_files: list[dict], paths_path: str):
    """导出纯路径清单"""
    try:
        buf = io.StringIO()
        for gi, group in enumerate(groups, 1):
            buf.write(f"# 第 {gi} 组\n")
            for idx, info in group["members"]:
                buf.write(f"{info['path']}\n")
            buf.write("\n")
        _atomic_write_text(paths_path, buf.getvalue())
        log(f"[导出] 路径清单 → {paths_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 路径清单导出失败: {e}")


def export_clean_list(groups: list[dict], mp4_files: list[dict],
                      clean_list_path: str, path_mask: bool = False):
    """
    导出仅待清理视频路径清单（v2.4 新增）。
    不含保留素材，仅列出建议清理的视频路径，便于外部脚本批量处理。
    """
    try:
        # 【改造 v2.4】路径脱敏处理
        def _mask(p: str) -> str:
            if not path_mask or not p:
                return p
            parts = p.replace("\\", "/").split("/")
            if len(parts) <= 4:
                return p
            return parts[0] + "//.../" + "/".join(parts[-2:])

        buf = io.StringIO()
        buf.write(f"# 待清理视频路径清单\n")
        buf.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        total_clean = 0
        for gi, group in enumerate(groups, 1):
            retain_name = mp4_files[group["retain_idx"]]["name"]
            buf.write(f"# 第{gi}组 (保留: {retain_name})\n")
            for idx, info in group["members"]:
                if idx != group["retain_idx"]:
                    buf.write(f"{_mask(info['path'])}\n")
                    total_clean += 1
            buf.write("\n")
        buf.write(f"# 共 {total_clean} 个待清理视频\n")
        _atomic_write_text(clean_list_path, buf.getvalue())
        log(f"[导出] 清理清单 → {clean_list_path} ({total_clean} 个)")
    except (IOError, OSError) as e:
        log(f"[错误] 清理清单导出失败: {e}")


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
    backup_path: str = "", protect_file: str = "",
    semantic_data: dict = None, path_mask: bool = False,
):
    """
    生成清理脚本（v2.3 增强：备份模式 + 批量保护目录 + 文件占用检测 + 用途备注）。
    v2.4 修复：保护目录使用完整分隔符精准匹配，杜绝 video 误保护 video123；
    新增 --path-mask 路径脱敏，审计日志隐藏中间层级保护素材隐私。
    默认：移动至回收站/垃圾桶（安全模式）。
    --hard-delete：永久删除。
    --backup-path：清理前先复制视频到备份目录。
    --protect-file：从文件批量导入保护目录。
    """
    protect_folders = protect_folders or set()
    protect_folders.add(os.path.expanduser("~/Desktop"))
    protect_folders.add(os.path.expanduser("~/"))

    # v2.3 新增：从文件批量导入保护目录
    if protect_file:
        try:
            with open(protect_file, "r", encoding="utf-8") as pf:
                for line in pf:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        protect_folders.add(_normalize_path(line))
            log(f"  [清理] 从 {protect_file} 导入保护目录")
        except (IOError, OSError):
            pass

    # 【改造修复 v2.4】保护目录使用完整分隔符精准匹配
    # 旧方案 startswith 会导致 video 误匹配 video123，新增 os.sep 边界检查
    def is_protected(path: str) -> bool:
        path_lower = path.lower().rstrip(os.sep)
        for pf in protect_folders:
            pf_norm = _normalize_path(pf).lower().rstrip(os.sep)
            if not pf_norm:
                continue
            # 精确匹配：路径等于保护目录，或以 保护目录+分隔符 开头
            if path_lower == pf_norm or path_lower.startswith(pf_norm + os.sep):
                return True
        return False

    # 【改造 v2.4】路径脱敏：隐藏路径中间层级，仅保留首尾两级
    def _mask_path(p: str) -> str:
        if not path_mask or not p:
            return p
        parts = p.replace("\\", "/").split("/")
        if len(parts) <= 4:
            return p
        return parts[0] + "//.../" + "/".join(parts[-2:])

    # v2.3 新增：获取用途备注
    def get_purpose_note(path: str) -> str:
        if semantic_data:
            sd = semantic_data.get(path, {})
            purpose = sd.get("dataset_purpose", "")
            if purpose:
                return f" [用途: {purpose}]"
        return ""

    # 写出结构化清理计划：先审阅/备份，再执行脚本，便于恢复和自动化。
    plan_path = os.path.join(output_dir, CLEANUP_PLAN_FILE)
    plan_rows = []
    for gi, group in enumerate(groups, 1):
        for idx, info in group.get("members", []):
            if idx == group.get("retain_idx") or is_protected(info["path"]):
                continue
            plan_rows.append({
                "group": gi, "source": info["path"],
                "size": int(info.get("size", 0) or 0),
                "retained": mp4_files[group["retain_idx"]]["path"],
                "action": "delete" if hard_delete else "move_to_trash",
            })
    plan = {"schema_version": 1, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "items": plan_rows, "count": len(plan_rows),
            "total_bytes": sum(row["size"] for row in plan_rows)}
    _atomic_write_text(plan_path, json.dumps(plan, ensure_ascii=False, indent=2))
    log(f"[导出] 清理计划 → {plan_path} ({len(plan_rows)} 个)")

    # Windows BAT
    bat_path = os.path.join(output_dir, CLEANUP_SCRIPT_WIN)
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write("@echo off\r\n")
        f.write("chcp 65001 >nul\r\n")
        f.write(f"REM 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\r\n")
        mode_str = '永久删除 (危险!)' if hard_delete else '安全模式（移动至回收站）'
        f.write(f"REM 模式: {mode_str}\r\n")
        if backup_path:
            f.write(f"REM 备份目录: {backup_path}\r\n")
        f.write("echo.\r\n")
        f.write("echo ============================================\r\n")
        f.write("echo   MP4 重复视频清理脚本\r\n")
        f.write("echo ============================================\r\n")
        f.write("echo.\r\n")
        f.write("echo 以下文件将被处理：\r\n")
        f.write("echo.\r\n")

        to_delete = []
        for gi, group in enumerate(groups, 1):
            retain_name = mp4_files[group["retain_idx"]]["name"]
            f.write(f"REM --- 第 {gi} 组 (保留: {retain_name}) ---\r\n")
            for idx, info in group["members"]:
                if idx != group["retain_idx"]:
                    path = info["path"]
                    if is_protected(path):
                        f.write(f"REM [已保护] {_mask_path(path)}\r\n")
                        continue
                    note = get_purpose_note(path)
                    # 【改造 v2.4】echo 显示行使用脱敏路径，实际操作行保留真实路径
                    f.write(f"echo   {_mask_path(path)}{note}\r\n")
                    to_delete.append(path)
            f.write("\r\n")

        f.write("echo.\r\n")
        f.write("set /p CONFIRM=输入 CONFIRM 执行操作: \r\n")
        f.write('if /i not "%CONFIRM%"=="CONFIRM" (\r\n')
        f.write('    echo 已取消\r\n')
        f.write('    pause\r\n')
        f.write('    exit /b 0\r\n')
        f.write(")\r\n\r\n")

        # v2.3 新增：备份模式
        if backup_path:
            f.write(f'if not exist "{backup_path}" mkdir "{backup_path}"\r\n')
            f.write("echo 正在备份文件...\r\n")
            for path in to_delete:
                fname = os.path.basename(path)
                f.write(f'copy "{path}" "{backup_path}\\{fname}" >nul 2>&1\r\n')
            f.write("echo 备份完成，开始清理...\r\n\r\n")

        for path in to_delete:
            if hard_delete:
                f.write(f'del /f /q "{path}" 2>nul\r\n')
                # v2.3 新增：文件占用检测
                f.write(f'if exist "{path}" echo [警告] 文件被占用无法删除: {path}\r\n')
            else:
                f.write(f'move "{path}" "%TEMP%\\trash_%RANDOM%_" 2>nul\r\n')
                f.write(f'if exist "{path}" echo [警告] 文件被占用无法移动: {path}\r\n')

        f.write("\r\necho.\r\n")
        f.write("echo 清理完成！\r\n")
        f.write("pause\r\n")

    # Linux SH
    sh_path = os.path.join(output_dir, CLEANUP_SCRIPT_LINUX)
    with open(sh_path, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# 模式: {'永久删除 (危险!)' if hard_delete else '安全模式（移动至 ~/.trash）'}\n")
        if backup_path:
            f.write(f"# 备份目录: {backup_path}\n")
        f.write("\n")
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
                        f.write(f"# [已保护] {_mask_path(path)}\n")
                        continue
                    # 【改造 v2.4】echo 显示行使用脱敏路径
                    f.write(f'echo "  {_mask_path(path)}"\n')
                    to_delete.append(path)
            f.write("\n")

        f.write('read -p "输入 CONFIRM 执行操作: " CONFIRM\n')
        f.write('if [ "$CONFIRM" != "CONFIRM" ]; then\n')
        f.write('    echo "已取消"\n')
        f.write('    exit 0\n')
        f.write("fi\n\n")

        # v2.3 新增：备份模式
        if backup_path:
            f.write(f'mkdir -p "{backup_path}"\n')
            f.write('echo "正在备份文件..."\n')
            for path in to_delete:
                fname = os.path.basename(path)
                f.write(f'cp "{path}" "{backup_path}/{fname}" 2>/dev/null\n')
            f.write('echo "备份完成，开始清理..."\n\n')

        for path in to_delete:
            if hard_delete:
                f.write(f'rm -f "{path}" 2>/dev/null\n')
            else:
                f.write(f'mv "{path}" ~/.trash/ 2>/dev/null\n')

        f.write('\necho "清理完成！"\n')

    log(f"[导出] 清理脚本 → {bat_path} / {sh_path}")


def export_audit_log(output_dir: str, groups: list[dict], mp4_files: list[dict],
                     hard_delete: bool, semantic_data: dict = None,
                     path_mask: bool = False):
    """导出清理操作审计日志（v2.2 扩展语义备注，v2.3 按日期分割文件，v2.4 路径脱敏）"""
    try:
        # v2.3 新增：审计日志按日期分割，避免单文件无限膨胀
        date_str = time.strftime("%Y%m")
        audit_name = AUDIT_LOG.replace(".log", f"_{date_str}.log")
        audit_path = os.path.join(output_dir, audit_name)

        # 【改造 v2.4】路径脱敏处理
        def _mask(p: str) -> str:
            if not path_mask or not p:
                return p
            parts = p.replace("\\", "/").split("/")
            if len(parts) <= 4:
                return p
            return parts[0] + "//.../" + "/".join(parts[-2:])

        # 【改造 v2.4】原子写入审计日志
        buf = io.StringIO()
        buf.write(f"\n{'=' * 60}\n")
        buf.write(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        buf.write(f"模式: {'永久删除' if hard_delete else '安全移动'}\n")
        buf.write(f"分组数: {len(groups)}\n")
        for gi, group in enumerate(groups, 1):
            buf.write(f"\n第{gi}组:\n")
            retain_info = mp4_files[group['retain_idx']]
            buf.write(f"  保留: {_mask(retain_info['path'])}")
            if semantic_data:
                sd = semantic_data.get(retain_info["path"], {})
                if sd.get("dataset_purpose"):
                    buf.write(f" ({sd['dataset_purpose']})")
            buf.write("\n")
            for idx, info in group["members"]:
                if idx != group["retain_idx"]:
                    buf.write(f"  清理: {_mask(info['path'])} ({info['size_readable']})")
                    if semantic_data:
                        sd = semantic_data.get(info["path"], {})
                        if sd.get("dataset_purpose"):
                            buf.write(f" [{sd['dataset_purpose']}]")
                    buf.write("\n")
        # 追加模式：先读后写（原子化追加）
        existing = ""
        if os.path.exists(audit_path):
            try:
                with open(audit_path, "r", encoding="utf-8") as fr:
                    existing = fr.read()
            except (IOError, OSError):
                pass
        _atomic_write_text(audit_path, existing + buf.getvalue())
        log(f"[导出] 审计日志 → {audit_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 审计日志导出失败: {e}")


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


def _similarity_level(similarity: float) -> str:
    """将相似度转换为便于人工和自动化使用的等级。"""
    if similarity >= 0.98:
        return "exact_or_reencoded"
    if similarity >= 0.85:
        return "highly_similar"
    if similarity >= 0.70:
        return "possibly_similar"
    return "weak_match"


def _summary_extensions(mp4_files: list[dict]) -> dict:
    counts = {}
    for info in mp4_files:
        suffix = os.path.splitext(info.get("name", ""))[1].lower() or "[unknown]"
        counts[suffix] = counts.get(suffix, 0) + 1
    return dict(sorted(counts.items()))


def _summary_quality_buckets(group_rows: list[dict]) -> dict:
    counts = {"exact_or_reencoded": 0, "highly_similar": 0,
              "possibly_similar": 0, "weak_match": 0}
    for row in group_rows:
        level = row.get("level", "weak_match")
        counts[level] = counts.get(level, 0) + 1
    return counts


def export_summary_json(path: str, mp4_files: list[dict], video_hashes: dict,
                        bad_videos: list[dict], groups: list[dict],
                        semantic_results: dict = None):
    """导出稳定的机器可读摘要，供 CI、看板和外部清理工具使用。"""
    duplicate_bytes = 0
    group_rows = []
    for number, group in enumerate(groups, 1):
        members = []
        for idx, info in group.get("members", []):
            retained = idx == group.get("retain_idx")
            if not retained:
                duplicate_bytes += int(info.get("size", 0) or 0)
            members.append({
                "index": idx, "name": info.get("name", ""),
                "path": info.get("path", ""),
                "size": int(info.get("size", 0) or 0),
                "quality_score": _video_quality_score(info, video_hashes.get(idx)),
                "retained": retained,
            })
        similarities = group.get("similarities", {})
        max_similarity = max(similarities.values()) if similarities else 1.0
        group_rows.append({"group": number, "members": members,
                           "max_similarity": max_similarity,
                           "level": _similarity_level(max_similarity),
                           "similarities": similarities})
    summary = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total_videos": len(mp4_files),
        "hash_success": len(video_hashes),
        "parse_failures": len(bad_videos),
        "duplicate_groups": len(groups),
        "duplicate_videos": sum(len(g.get("members", [])) for g in groups),
        "reclaimable_bytes": duplicate_bytes,
        "bad_videos": bad_videos,
        "groups": group_rows,
        "semantic_analyzed": len(semantic_results or {}),
        "quality_scoring": "metadata_resolution_bitrate_v1",
        "extensions": _summary_extensions(mp4_files),
        "quality_buckets": _summary_quality_buckets(group_rows),
    }
    _atomic_write_text(path, json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    log(f"[导出] 扫描摘要 JSON → {path}")


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
    video_hashes: dict = None,  # v2.3 新增：传入已提取的哈希数据用于帧复用
) -> dict:
    """对所有视频执行语义分析，返回 {path: semantic_data}
    v2.3 增强：支持 --no-store-embed、帧复用、AI线程自适应
    """
    global _semantic_stats
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

            result = semantic_analyze_video(
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
    _register_signal_handler()  # 【v2.6 修复】统一信号注册，避免重复

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
    _register_signal_handler()  # 【v2.6 修复】统一信号注册，避免重复

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
    _register_signal_handler()  # 【v2.6 修复】统一信号注册，避免重复

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


# ============================================================
# v2.3 新增子命令处理函数
# ============================================================
def _run_clear_semantic_cache(args):
    """清理缓存内 AI 特征向量，保留 pHash/dHash 视频哈希缓存（v2.3 新增）"""
    global _quiet_mode
    _quiet_mode = args.quiet
    script_dir = os.path.dirname(os.path.abspath(__file__))
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
    global _quiet_mode
    _quiet_mode = args.quiet
    folder_path = _resolve_path(args.dir)

    if args.output_dir:
        output_dir = _resolve_path(args.output_dir)
    else:
        output_dir = os.path.dirname(os.path.abspath(__file__))
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


def _run_gen_restore(args):
    """根据审计日志生成恢复脚本（v2.4 新增）"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = _resolve_path(args.output_dir) if args.output_dir else script_dir
    os.makedirs(output_dir, exist_ok=True)

    # 查找最新审计日志
    import glob as glob_mod
    audit_files = sorted(glob_mod.glob(os.path.join(output_dir, "cleanup_audit_*.log")))
    if not audit_files:
        audit_files = [os.path.join(output_dir, AUDIT_LOG)]
    if not os.path.exists(audit_files[-1]):
        log("错误: 未找到审计日志文件", force=True)
        return

    audit_path = audit_files[-1]
    log(f"读取审计日志: {audit_path}", force=True)

    restore_entries = []
    try:
        with open(audit_path, "r", encoding="utf-8") as f:
            for line in f:
                # 解析移动/删除记录
                if "移动" in line or "删除" in line or "→" in line or "→" in line:
                    parts = line.strip().split("\t")
                    if len(parts) >= 2:
                        restore_entries.append(parts)
    except (IOError, OSError) as e:
        log(f"错误: 读取审计日志失败: {e}", force=True)
        return

    if not restore_entries:
        log("审计日志中未找到可恢复的记录", force=True)
        return

    # 生成恢复脚本
    restore_bat = os.path.join(output_dir, "restore_duplicates.bat")
    try:
        with open(restore_bat, "w", encoding="utf-8") as f:
            f.write("@echo off\r\nchcp 65001 >nul\r\n")
            f.write(f"REM 恢复脚本生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\r\n")
            f.write("echo 正在恢复视频文件...\r\n")
            for entry in restore_entries:
                if len(entry) >= 2:
                    src = entry[0].strip()
                    dst = entry[1].strip() if len(entry) > 1 else ""
                    if dst:
                        f.write(f'move "{dst}" "{src}" 2>nul\r\n')
            f.write("echo 恢复完成！\r\npause\r\n")
        log(f"[导出] 恢复脚本 → {restore_bat}", force=True)
    except (IOError, OSError) as e:
        log(f"错误: 生成恢复脚本失败: {e}")


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
    script_dir = os.path.dirname(os.path.abspath(__file__))

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
            mod.main()
        finally:
            sys.argv = old_argv
    else:
        log(f"[错误] 模块 {module_name} 缺少 main() 入口函数", force=True)
        sys.exit(EXIT_BAD_ARGS)


# 【v2.5 新增】AI 自动分类子命令
def _run_auto_classify(args):
    """执行 AI 自动分类"""
    if not AI_MODULE_AVAILABLE:
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
    mp4_files = scan_videos(folder_path, recursive)
    log(f"  找到 {len(mp4_files)} 个视频", force=True)

    if not mp4_files:
        log("  无视频可处理", force=True)
        return

    # 1.5 【v2.5 联动】应用时长/分辨率筛选（复用现有函数）
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CACHE_FILE)
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
        model, preprocess, device = load_clip_model()
        
        for i, f in enumerate(to_analyze):
            path = f["path"]
            try:
                result = semantic_analyze_video(path, model, preprocess, device)
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


def main():
    global _global_cache, _global_exit_code, _quiet_mode

    args = parse_args()

    # 【v2.6 新增】交互式配置向导
    if getattr(args, "interactive", False):
        import sys as _sys
        _sys.argv = _run_interactive_wizard()
        args = parse_args()

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

    # v2.3 新增：--cache-expire-days 过期清理
    expire_days = getattr(args, "cache_expire_days", 0)
    if expire_days > 0:
        script_dir = os.path.dirname(os.path.abspath(__file__))
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
                _global_exit_code = EXIT_OK
                return

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
                export_dataset_catalog(semantic_data, catalog_path)

                list_path = _prefixed_path(output_dir, TRAIN_SAMPLE_LIST, _prefix)
                purpose_list = [p.strip() for p in args.purpose_filter.split(",")] if args.purpose_filter else None
                export_train_sample_list(semantic_data, list_path, purpose_filter=purpose_list)

                stats_path = _prefixed_path(output_dir, DATASET_STATS, _prefix)
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
                    cluster_html = _prefixed_path(output_dir, SCENE_CLUSTER_HTML, _prefix)
                    export_scene_cluster_html(clusters, semantic_data, cluster_html)

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

# ============================================================
# find_mp4.py v2.4 底部使用示例（全套命令样例）
# ============================================================
# 以下为常见使用场景命令，可直接复制修改后运行。
# 详细参数说明见文件头部"参数说明"章节，配置项参见 config.ini。
#
# === 0. 基础查重（默认输出 txt 报告） ===
# python find_mp4.py --dir D:\\Videos
# python find_mp4.py --dir D:\\Videos --threshold 0.75 --frames 12 --workers 8
#
# === 1. 纯时长统计（不查重，快速计数） ===
# python find_mp4.py duration-stat --dir D:\\Videos --duration-filter ">=60"
# python find_mp4.py duration-stat --dir D:\\Videos --duration-filter ">120&<=360" --duration-export
#
# === 2. 带时长筛选的查重 ===
# python find_mp4.py --dir D:\\Videos --duration-filter ">=60" --threshold 0.7
# python find_mp4.py --dir D:\\Videos --duration-filter "<30" --format html --gen-cleanup
#
# === 3. 导出时长清单 ===
# python find_mp4.py --dir D:\\Videos --duration-filter ">=60" --duration-export
#
# === 4. 分辨率过滤查重 ===
# python find_mp4.py --dir D:\\Videos --min-res 1920 --max-res 3840
#
# === 5. AI 语义聚类 ===
# python find_mp4.py scan --dir D:\\Videos --semantic --cluster-semantic --cluster-thresh 0.4
# python find_mp4.py cluster-scene --dir D:\\Videos
#
# === 6. 数据集筛选与拆分（支持硬链接节省磁盘） ===
# python find_mp4.py dataset-filter --purpose 监控 --dir D:\\car_data
# python find_mp4.py dataset-split --dir D:\\Videos --link-mode
#
# === 7. 缓存管理（合并/压缩/迁移） ===
# python find_mp4.py merge-cache cache1.json cache2.json
# python find_mp4.py --dir D:\\Videos --compress-cache
#
# === 8. 清理与恢复 ===
# python find_mp4.py --dir D:\\Videos --gen-cleanup                  # 默认移至回收站
# python find_mp4.py --dir D:\\Videos --gen-cleanup --hard-delete    # 永久删除（需输入 YES 确认）
# python find_mp4.py --dir D:\\Videos --gen-cleanup --backup-path D:\\backup
# python find_mp4.py --gen-restore                                   # 从审计日志生成恢复脚本
# python find_mp4.py --dir D:\\Videos --export-clean-list --path-mask
#
# === 9. Excel 导出（需 openpyxl） ===
# python find_mp4.py --dir D:\\Videos --format xlsx --output-prefix batch1_
#
# === 10. 安全特性组合 ===
# python find_mp4.py --dir D:\\Videos --path-mask --protect-folder D:\\keep,D:\\source
# python find_mp4.py --dir D:\\Videos --gen-cleanup --protect-file protect_list.txt
#
# === 11. 使用 config.ini 配置文件 ===
# python find_mp4.py --config config.ini
# python find_mp4.py --dir D:\\Videos --config config.ini --threshold 0.8
#
# === 12. 内置测试与标签热加载 ===
# python find_mp4.py test
# python find_mp4.py reload-labels
#
# === 13. AI 低质量过滤 + 帧不持久化（节省内存） ===
# python find_mp4.py --dir D:\\Videos --semantic --skip-low-quality --no-store-frames
#
# === 14. v2.5 AI 自动分类 ===
# # 预览模式（默认，不修改文件）
# python find_mp4.py auto-classify --dir D:\\Videos
# # 执行硬链接分类
# python find_mp4.py auto-classify --dir D:\\Videos --execute --link-mode
# # 分类 + 筛选联动
# python find_mp4.py auto-classify --dir D:\\Videos --duration-filter ">=60" --min-res 1920
# python find_mp4.py auto-classify --dir D:\\Videos --skip-low-quality --n-clusters 8
# # 仅分类（扁平参数模式）
# python find_mp4.py --dir D:\\Videos --classify-only
# python find_mp4.py --dir D:\\Videos --classify-only --execute --link-mode
# ============================================================