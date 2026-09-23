#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MP4 视频相似度查重工具 v2.8 —— 兼容入口（瘦壳）
================================================
v2.9 起全部实现拆分至 video_dedup/ 包，按职责分为
constants / context / utils / scanner / cache / hasher / compare /
grouper / reporter / cleanup / cli 十一个模块。

本文件只做两件事：
1. 聚合导出包内全部公开名字，保证既有用法不受影响：
       from find_mp4 import load_cache, log, CACHE_FILE, ...
       python find_mp4.py --dir D:\\Videos
2. 作为命令行入口调用 video_dedup.cli.main()。

完整文档见 README.md；架构说明见 video_dedup/__init__.py 与 docs/algorithm-notes.md。
"""
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import video_dedup  # noqa: E402,F401  — 触发 sys.path 引导与版本号加载
from video_dedup import cli as _cli  # noqa: E402

# ---- 兼容层：把包内实现聚合到本模块命名空间 -----------------------------
# 使 `from find_mp4 import X`（batch_tools / media_analyze / report_generator /
# dashboard / test_demo / test_regression 等既有脚本）与 `find_mp4.X` 继续可用。
for _name in dir(_cli):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_cli, _name)

from video_dedup.constants import __version__  # noqa: E402,F401

if __name__ == "__main__":
    main()  # noqa: F821  — 由上方兼容层注入（video_dedup.cli.main）
