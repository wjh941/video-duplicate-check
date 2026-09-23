# -*- coding: utf-8 -*-
"""video_dedup.constants — 全局常量 / 输出文件名 / 错误码 / 退出码。"""
import shutil


# FFmpeg 探测
FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg"))


# ============================================================
# 模块 0：全局配置常量 + 退出码
# ============================================================
__version__ = "2.9.0"

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
