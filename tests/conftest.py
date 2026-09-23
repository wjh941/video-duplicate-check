# -*- coding: utf-8 -*-
"""pytest 共享 fixture：用 OpenCV 现场合成测试视频，不依赖任何真实素材。"""
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

# 保证仓库根目录在 sys.path（pytest 以 tests/ 为 rootdir 时未必包含）
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

W, H, FPS, N_FRAMES = 320, 240, 12, 24


# ------------------------------------------------------------------
# 帧图案生成器：确定性的合成画面（不使用随机噪声，保证测试可复现）
# ------------------------------------------------------------------
def make_frame(kind: str, i: int = 0, w: int = W, h: int = H) -> np.ndarray:
    """生成第 i 帧的 BGR 图案。kind 决定内容族，用于构造「同类/异类」视频。"""
    if kind == "stripes_move":
        # 移动的竖条纹
        img = np.zeros((h, w, 3), np.uint8)
        for k in range(-8, 20):
            x0 = (k * 40 + i * 5) % (w + 40)
            cv2.rectangle(img, (x0, 0), (x0 + 18, h), (60 + (k % 4) * 40, 120, 200), -1)
        return img
    if kind == "stripes_static":
        img = np.zeros((h, w, 3), np.uint8)
        for k in range(-8, 20):
            x0 = k * 40
            cv2.rectangle(img, (x0, 0), (x0 + 18, h), (60 + (k % 4) * 40, 120, 200), -1)
        return img
    if kind == "gradient":
        # 水平渐变 + 上下半区亮度差
        xx = np.tile(np.linspace(0, 255, w, dtype=np.uint8), (h, 1))
        img = cv2.cvtColor(xx, cv2.COLOR_GRAY2BGR)
        img[: h // 2] //= 2
        return img
    if kind == "checker":
        img = np.zeros((h, w, 3), np.uint8)
        step = 32 + (i % 2) * 4
        for y in range(0, h, step):
            for x in range(0, w, step):
                if (x // step + y // step) % 2 == 0:
                    cv2.rectangle(img, (x, y), (x + step, y + step), (200, 180, 90), -1)
        return img
    if kind == "rings":
        img = np.zeros((h, w, 3), np.uint8)
        for r in range(10, 160, 20):
            cv2.circle(img, (w // 2, h // 2), r + (i % 3), (90, 160, 220), 3)
        return img
    raise ValueError(f"unknown frame kind: {kind}")


def write_video(path, kind: str, n: int = N_FRAMES, w: int = W, h: int = H,
                fps: int = FPS) -> Path:
    """把一段合成图案写成 mp4 文件，返回路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert vw.isOpened(), f"VideoWriter 打不开: {path}"
    for i in range(n):
        vw.write(make_frame(kind, i, w, h))
    vw.release()
    assert path.exists() and path.stat().st_size > 0
    return path


# ------------------------------------------------------------------
# fixtures
# ------------------------------------------------------------------
@pytest.fixture()
def video_factory(tmp_path):
    """video_factory("stripes_move", name="a.mp4") -> Path"""
    counter = {"n": 0}

    def _factory(kind: str, name: str = None, **kwargs) -> Path:
        counter["n"] += 1
        name = name or f"video_{counter['n']}.mp4"
        return write_video(tmp_path / name, kind, **kwargs)

    return _factory


@pytest.fixture()
def gray_pair_identical():
    """两张完全相同的灰度图（64x64，结构丰富）"""
    img = np.zeros((64, 64), np.uint8)
    for k in range(0, 64, 8):
        cv2.rectangle(img, (k, 0), (k + 3, 63), 200, -1)
        cv2.circle(img, (32, 32), 10 + k // 4, 90, 1)
    return img, img.copy()


@pytest.fixture()
def gray_pair_different():
    """两张低频结构完全不同的灰度图（粗横条 vs 大块四象限）"""
    a = np.zeros((64, 64), np.uint8)
    for k in range(0, 64, 16):
        a[k:k + 8, :] = 220  # 粗横条纹
    b = np.zeros((64, 64), np.uint8)
    b[:32, :32] = 220
    b[32:, 32:] = 220  # 对角象限块
    return a, b


@pytest.fixture()
def fake_args():
    """extract_hashes_with_cache / scan_mp4_files 等所需的最小 args 命名空间"""
    from argparse import Namespace
    return Namespace(
        dir=None, ext="mp4", no_recursive=False, exclude_folder="",
        exclude_size_lt="", exclude_size_gt="",
        double_check=False, incremental=False, audio_check=False,
        no_store_frames=False, use_md5=False, audio_check_default=False,
    )
