#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
label_verify.py — 预标注一致性验证工具 —— 兼容入口（瘦壳）
====================================================

v2.9 起真实实现全部位于 video_dedup/label_verify.py（包内模块）；
本文件仅为兼容旧命令行用法做转发：
    python label_verify.py --dir D:\labeled_videos
实现细节见包内模块 docstring。此处显式再导出测试与流水线
（tests/test_label_verify.py / test_regression.py / pipeline.py）引用的
公开名与私有名，保持 `import label_verify` 的既有行为。
"""
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from video_dedup.label_verify import (  # noqa: E402,F401  显式再导出（含私有名）
    _apply_preset,
    _purpose_guidance,
    _valid_label,
    assign_labels,
    compute_video_signature,
    hash_similarity,
    main,
    motion_similarity,
    parse_args,
    scan_videos,
    verdict_of,
)

if __name__ == "__main__":
    sys.exit(main())
