#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 语义分析模块 —— 兼容入口（瘦壳）
====================================

v2.9 起真实实现全部位于 video_dedup/ai_semantic.py（包内模块）；
本文件仅为兼容既有 `import ai_semantic` / `from ai_semantic import ...`
用法做转发，不包含任何实现。依赖 torch / open-clip-torch / scikit-learn
（均为可选，未安装时实现模块内部自动降级）。

使用示例（等价于 from video_dedup.ai_semantic import ...）：
    from ai_semantic import (
        load_clip_model, semantic_analyze_video,
        cluster_videos_by_semantic, export_dataset_catalog,
    )

    model, preprocess, device = load_clip_model()
    result = semantic_analyze_video("test.mp4", model, preprocess, device)
"""
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from video_dedup.ai_semantic import *  # noqa: F401,F403,E402
from video_dedup.ai_semantic import (  # noqa: E402,F401  常用入口的显式再导出
    CLIP_OK,
    CUDA_OK,
    SKLEARN_OK,
    TORCH_OK,
    cluster_videos_by_semantic,
    export_dataset_catalog,
    export_dataset_stats,
    export_scene_cluster_html,
    export_train_sample_list,
    load_clip_model,
    semantic_analyze_video,
)
