# -*- coding: utf-8 -*-
"""video_dedup.label_verify — 转发层。

预标注一致性验证的实现保留在仓库根目录 ``label_verify.py``：
它是可独立运行的 CLI 工具（``python label_verify.py --dir D:\\labeled_videos``），
也能被看板 / pipeline 复用。此模块仅做包内转发，方便统一从 ``video_dedup``
命名空间导入；CLI 实际执行仍通过根目录脚本，避免两份模块状态分裂。
"""
from label_verify import *  # noqa: F401,F403
from label_verify import (  # noqa: F401  常用入口的显式再导出
    assign_labels,
    compute_video_signature,
    hash_similarity,
    motion_similarity,
    parse_args,
    scan_videos,
    verdict_of,
    main,
)
