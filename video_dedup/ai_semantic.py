# -*- coding: utf-8 -*-
"""video_dedup.ai_semantic — 转发层。

CLIP 语义分析的实现保留在仓库根目录 ``ai_semantic.py``（可独立运行，
依赖 torch / open-clip-torch / scikit-learn，未安装时自动降级）。
此模块仅做包内转发；主流程（video_dedup.cli）通过延迟加载调用它，
避免非 AI 路径付出 torch 的导入开销。
"""
from ai_semantic import *  # noqa: F401,F403
from ai_semantic import (  # noqa: F401  常用入口的显式再导出
    CUDA_OK,
    CLIP_OK,
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
