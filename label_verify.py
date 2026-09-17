#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
label_verify.py — 预标注一致性验证工具（v0.2）
====================================================

用途：当视频已经带有"预标注"（来自文件夹名 / 文件名前缀 / labels.csv /
文件名正则）时，本工具对每个视频抽帧，计算画面哈希（pHash+dHash）、
运动能量与（可选）运动前景哈希，比较【同标签视频之间】与【不同标签
视频之间】的相似度，回答："带相同标签的视频，是否真的表现出相同的行为？"

v0.2 新增：
    --purpose            数据用途问卷（train/detection/retrieval/archive/general，
                         未指定且终端交互时询问；报告中给出针对性参考建议）
    --motion-hash        运动前景哈希：以多帧中值估计背景，仅对前景差异区域计算
                         第二组哈希（固定机位监控数据的查重/验证核心）
    --verify-neg         接 CLIP 判断 neg 样本是否"疑似包含目标行为"（需 --use-clip）
    每组建议和总结       报告中每个标签给出自动生成的结论与下一步动作
    HTML 点击播放        抽帧对比图中点击"▶ 播放"直接内嵌播放原视频
    HTML 折叠分页        标签分组用 details 折叠 + 顶部锚点导航，超大数据不卡顿

它不修改任何文件，只生成报告：
    label_verify_report.md     分析报告（Markdown）
    label_verify_suspects.csv  疑似标注不一致的视频清单（含建议标签）
    label_verify_frames.html   抽帧对比图（按标签分组、可点击播放、疑似项标红）

预标注识别来源（--label-from）：
    auto    自动选择：优先文件夹名，其次文件名前缀（默认）
    folder  用视频所在一级子文件夹名作为标签
    prefix  用文件名首个 "_" / "-" 前的前缀作为标签
    csv     从 --labels-csv 指定的 CSV 读取（列名支持 path/filename + label/标签）
    regex   用 --label-regex 从文件名提取（第 1 个捕获组为标签）

判定逻辑：
    组内相似度 intra = 同标签视频两两综合相似度的均值
    组间相似度 inter = 不同标签视频两两综合相似度的均值
    疑似标注不一致（满足任一即标记）：
        a) 离群分 < --suspect-threshold（默认 0.5）
        b) 低于本标签均值 2.0 个标准差（组内>=3 个视频时）
        c) 与某其他标签的平均相似度高出本组 0.1 以上（跨标签吸引力，
           同时给出"建议标签"，这是最直接的错标信号）
        d) --verify-neg 时：neg 样本被 CLIP 判断疑似包含目标行为

用法示例：
    python label_verify.py --dir D:\\labeled_videos
    python label_verify.py --dir D:\\cam_data --label-regex "cam01_(.+?)-(?:pos|neg)"
    python label_verify.py --dir D:\\cam_data --purpose train --motion-hash
    python label_verify.py --dir D:\\cam_data --use-clip --verify-neg

退出码：0=全部标签一致；1=发现疑似标注不一致；2=有视频解析失败；3=参数/数据错误
依赖：opencv-python numpy Pillow imagehash；CLIP 相关功能另需 torch open_clip_torch
"""

import argparse
import base64
import csv
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
import imagehash

# ============================================================
# 常量
# ============================================================
HASH_SIZE = 8
FRAME_SAMPLE_RANGE = (0.1, 0.9)          # 抽帧区间（避开片头片尾）
MOTION_BURST_RATIOS = (0.15, 0.5, 0.85)  # 运动采样段位置
MOTION_RESIZE = 64                       # 运动能量计算的缩放尺寸
THUMB_WIDTH = 160                        # HTML 缩略图宽
HTML_THUMB_FRAMES = 4                    # HTML 中每个视频展示的帧数
SEQ_MAX_FRAMES = 6000                    # 顺序解码快速路径的帧数上限
COLLAPSE_THRESHOLD = 30                  # 标签组视频数超过该值时 HTML 默认折叠

REPORT_MD = "label_verify_report.md"
REPORT_CSV = "label_verify_suspects.csv"
REPORT_HTML = "label_verify_frames.html"

EXIT_CONSISTENT = 0
EXIT_SUSPECTS = 1
EXIT_PARSE_ERROR = 2
EXIT_BAD_ARGS = 3

STDOUT_READY = False
_CLIP = {"checked": False, "ok": False, "model": None, "preprocess": None, "device": "cpu"}

PURPOSES = {
    "general": "通用查重与素材整理",
    "train": "行为识别 / 分类训练集",
    "detection": "目标检测数据集",
    "retrieval": "素材检索库",
    "archive": "长期归档",
}


def log(msg: str = "", force: bool = True):
    # Windows 控制台默认 GBK：统一重配为 UTF-8，避免中文/特殊符号崩溃
    global STDOUT_READY
    if not STDOUT_READY:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        STDOUT_READY = True
    print(msg)


# ============================================================
# 1. 视频扫描与预标注识别
# ============================================================
def scan_videos(root: str, exts: list, recursive: bool) -> list:
    """扫描目录下的视频文件，返回 [{path,name,size}]"""
    found = []
    if recursive:
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if os.path.splitext(fn)[1].lower().lstrip(".") in exts:
                    p = os.path.join(dirpath, fn)
                    found.append({"path": p, "name": fn, "size": os.path.getsize(p)})
    else:
        for fn in os.listdir(root):
            p = os.path.join(root, fn)
            if os.path.isfile(p) and os.path.splitext(fn)[1].lower().lstrip(".") in exts:
                found.append({"path": p, "name": fn, "size": os.path.getsize(p)})
    found.sort(key=lambda f: f["path"].lower())
    return found


def _valid_label(token: str) -> bool:
    """标签需 >=2 字符且包含字母/中文（避免把 20240501 这类数字串当标签）"""
    token = token.strip()
    if len(token) < 2:
        return False
    return any(ch.isalpha() or "\u4e00" <= ch <= "\u9fff" for ch in token)


def _load_labels_csv(csv_path: str) -> dict:
    """读取标注 CSV，返回 {basename: label}"""
    mapping = {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return mapping
        cols = {c.lower().strip(): c for c in reader.fieldnames}
        path_col = next((cols[k] for k in ("path", "file", "filename", "文件名", "路径") if k in cols), None)
        label_col = next((cols[k] for k in ("label", "标签", "class", "类别") if k in cols), None)
        if not path_col or not label_col:
            raise ValueError(
                "CSV 缺少必要列：需要 文件名列(path/filename/文件名) 与 标签列(label/标签)，实际列："
                + str(reader.fieldnames))
        for row in reader:
            key = os.path.basename(str(row[path_col]).strip())
            label = str(row[label_col]).strip()
            if key and label:
                mapping[key] = label
    return mapping


def assign_labels(videos: list, mode: str, root: str, labels_csv: str,
                  label_regex: str = "") -> tuple:
    """为每个视频分配预标注。返回 (label_map {path: label|None}, mode_used: str)"""
    label_map = {v["path"]: None for v in videos}
    used = mode

    if mode == "csv":
        mapping = _load_labels_csv(labels_csv)
        hit = 0
        for v in videos:
            lbl = mapping.get(v["name"])
            if lbl:
                label_map[v["path"]] = lbl
                hit += 1
        if hit == 0:
            raise ValueError(f"CSV 中没有任何条目匹配到扫描到的视频（共 {len(videos)} 个）")

    elif mode == "regex":
        if not label_regex:
            raise ValueError("--label-from regex 需要 --label-regex 提供正则表达式")
        import re as _re
        pat = _re.compile(label_regex)
        hit = 0
        for v in videos:
            stem = os.path.splitext(v["name"])[0]
            m = pat.search(stem)
            if m and m.group(1):
                label_map[v["path"]] = m.group(1).strip()
                hit += 1
        if hit == 0:
            raise ValueError(f"正则 {label_regex!r} 没有匹配到任何文件名（检查捕获组）")

    elif mode == "folder":
        root_abs = os.path.abspath(root)
        for v in videos:
            parent_dir = os.path.dirname(os.path.abspath(v["path"]))
            parent = os.path.basename(parent_dir)
            if parent and parent_dir != root_abs and _valid_label(parent):
                label_map[v["path"]] = parent

    elif mode == "prefix":
        for v in videos:
            stem = os.path.splitext(v["name"])[0]
            token = stem.replace("-", "_").split("_", 1)[0]
            if _valid_label(token):
                label_map[v["path"]] = token

    elif mode == "auto":
        folder_map, _ = assign_labels(videos, "folder", root, "")
        prefix_map, _ = assign_labels(videos, "prefix", root, "")
        n_folder = sum(1 for x in folder_map.values() if x)
        n_prefix = sum(1 for x in prefix_map.values() if x)
        if n_folder >= 2 or (n_folder > 0 and n_folder >= n_prefix):
            label_map, used = folder_map, "folder(auto)"
        elif n_prefix > 0:
            label_map, used = prefix_map, "prefix(auto)"
        else:
            raise ValueError(
                "未识别到任何预标注。请把视频放入以标签命名的子文件夹，"
                "或使用 文件名前缀 / --label-regex / --labels-csv。")
    else:
        raise ValueError(f"未知的 --label-from 模式: {mode}")

    return label_map, used


# ============================================================
# 2. 抽帧与特征提取
# ============================================================
def _open_video(video_path: str):
    """打开视频；打不开时回退为临时副本重试（Windows 编码路径兼容）"""
    cap = cv2.VideoCapture(video_path)
    if cap.isOpened():
        return cap, None
    try:
        import shutil
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(),
                           "lv_%d%s" % (abs(hash(video_path)) % 10 ** 10,
                                        os.path.splitext(video_path)[1] or ".mp4"))
        shutil.copyfile(video_path, tmp)
        cap2 = cv2.VideoCapture(tmp)
        if cap2.isOpened():
            return cap2, tmp
        cap2.release()
    except Exception:
        pass
    return None, None


def _hash_one(small: np.ndarray):
    return (imagehash.phash(Image.fromarray(small), hash_size=HASH_SIZE),
            imagehash.dhash(Image.fromarray(small), hash_size=HASH_SIZE))


def _fg_hashes(smalls: list, fg_threshold: int):
    """
    运动前景哈希（v0.2 新增）：多帧逐像素中值估计背景，
    前景差异区域保留原值、背景区域置为中值背景，再计算第二组哈希。
    返回 (fg_phash_list, fg_dhash_list, motion_ratio, smalls_bg)
    """
    if len(smalls) < 3:
        return [], [], 1.0, None
    stack = np.stack(smalls).astype(np.float32)
    bg = np.median(stack, axis=0).astype(np.uint8)
    fg_phs, fg_dhs = [], []
    ratios = []
    for small in smalls:
        diff = cv2.absdiff(small, bg)
        mask = diff > fg_threshold
        ratios.append(float(np.mean(mask)))
        fg = np.where(mask, small, bg)
        ph, dh = _hash_one(fg)
        fg_phs.append(ph)
        fg_dhs.append(dh)
    return fg_phs, fg_dhs, float(np.mean(ratios)), bg


def compute_video_signature(video_path: str, n_frames: int,
                            motion_bursts: int = 3, burst_frames: int = 6,
                            motion_hash: bool = False,
                            fg_threshold: int = 18) -> dict:
    """
    单视频特征提取（v0.2：短视频单次顺序解码快速路径 + 可选运动前景哈希）
      - n_frames 帧均匀采样 → pHash/dHash
      - motion_bursts 段连续帧 → 帧间差分运动能量 (mean/std)
      - motion_hash=True → 前景差分帧的第二组哈希 + 静止机位判定
      - 保留前 HTML_THUMB_FRAMES 帧缩略图（BGR）
    """
    cap, tmp_file = _open_video(video_path)
    if cap is None:
        return {"ok": False, "err": "read_failed"}

    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        if total <= 0:
            return {"ok": False, "err": "zero_frames"}

        start_f = max(0, int(total * FRAME_SAMPLE_RANGE[0]))
        end_f = min(total, int(total * FRAME_SAMPLE_RANGE[1]))
        span = max(1, end_f - start_f)
        actual = max(1, min(n_frames, span))
        indices = [start_f + int(span * i / actual) for i in range(actual)]

        phashes, dhashes, thumbs = [], [], []
        thumb_budget = HTML_THUMB_FRAMES
        sequential = total <= SEQ_MAX_FRAMES
        motions = []

        if sequential:
            need = set(indices)
            burst_plan = {}
            for ratio in MOTION_BURST_RATIOS[:max(1, motion_bursts)]:
                burst_plan[int(total * ratio)] = max(2, burst_frames)
            smalls = []
            active_prev = None
            active_left = 0
            burst_diffs = []
            frame_no = 0
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                if frame_no in need:
                    small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                                       (32, 32), interpolation=cv2.INTER_AREA)
                    smalls.append(small)
                    ph, dh = _hash_one(small)
                    phashes.append(ph)
                    dhashes.append(dh)
                    if thumb_budget > 0:
                        h, w = frame.shape[:2]
                        scale = THUMB_WIDTH / w if w > THUMB_WIDTH else 1.0
                        thumb = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                                           interpolation=cv2.INTER_AREA) if scale < 1.0 else frame
                        thumbs.append(thumb)
                        thumb_budget -= 1
                if frame_no in burst_plan:
                    active_prev = None
                    active_left = burst_plan[frame_no]
                    burst_diffs = []
                if active_left > 0:
                    g = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                                   (MOTION_RESIZE, MOTION_RESIZE), interpolation=cv2.INTER_AREA)
                    if active_prev is not None:
                        burst_diffs.append(float(np.mean(cv2.absdiff(g, active_prev))))
                    active_prev = g
                    active_left -= 1
                    if active_left == 0 and burst_diffs:
                        motions.append(float(np.mean(burst_diffs)))
                frame_no += 1
            if motion_hash and smalls:
                fg_phs, fg_dhs, motion_ratio, _bg = _fg_hashes(smalls, fg_threshold)
        else:
            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                                   (32, 32), interpolation=cv2.INTER_AREA)
                ph, dh = _hash_one(small)
                phashes.append(ph)
                dhashes.append(dh)
                if thumb_budget > 0:
                    h, w = frame.shape[:2]
                    scale = THUMB_WIDTH / w if w > THUMB_WIDTH else 1.0
                    thumb = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                                       interpolation=cv2.INTER_AREA) if scale < 1.0 else frame
                    thumbs.append(thumb)
                    thumb_budget -= 1
            for ratio in MOTION_BURST_RATIOS[:max(1, motion_bursts)]:
                bstart = int(total * ratio)
                cap.set(cv2.CAP_PROP_POS_FRAMES, bstart)
                prev_small = None
                burst_diffs = []
                for _ in range(max(2, burst_frames)):
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    g = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                                   (MOTION_RESIZE, MOTION_RESIZE), interpolation=cv2.INTER_AREA)
                    if prev_small is not None:
                        burst_diffs.append(float(np.mean(cv2.absdiff(g, prev_small))))
                    prev_small = g
                if burst_diffs:
                    motions.append(float(np.mean(burst_diffs)))
            fg_phs, fg_dhs, motion_ratio = [], [], 1.0

        if not phashes:
            return {"ok": False, "err": "decode_error"}

        sig = {
            "ok": True, "err": None,
            "phash": phashes, "dhash": dhashes,
            "thumbs": thumbs,
            "duration": total / fps if fps > 0 else 0.0,
        }
        if motion_hash:
            sig["fg_phash"] = fg_phs
            sig["fg_dhash"] = fg_dhs
            sig["motion_ratio"] = float(motion_ratio)
            sig["static_cam"] = bool(len(smalls) >= 3 if sequential else False) and motion_ratio < 0.015
        motion_mean = float(np.mean(motions)) if motions else 0.0
        motion_std = float(np.std(motions)) if len(motions) > 1 else 0.0
        sig["motion_mean"] = motion_mean
        sig["motion_std"] = motion_std
        return sig
    except Exception:
        return {"ok": False, "err": "decode_error"}
    finally:
        cap.release()
        if tmp_file:
            try:
                os.remove(tmp_file)
            except OSError:
                pass


# ============================================================
# 3. 相似度计算
# ============================================================
def _hash_list_sim(list1: list, list2: list) -> float:
    """两组哈希的最近邻匹配相似度（与 find_mp4._hash_list_similarity 同算法）"""
    if not list1 or not list2:
        return 0.0
    dists = []
    for h1 in list1:
        d = min(h1 - h2 for h2 in list2)
        dists.append(d / (HASH_SIZE ** 2))
    return max(0.0, min(1.0, 1.0 - float(np.mean(dists))))


def hash_similarity(sig_a: dict, sig_b: dict, use_fg: bool = False) -> float:
    """对称双哈希融合相似度；use_fg 时与前景哈希各占一半"""
    ph = (_hash_list_sim(sig_a["phash"], sig_b["phash"]) +
          _hash_list_sim(sig_b["phash"], sig_a["phash"])) / 2
    dh = (_hash_list_sim(sig_a["dhash"], sig_b["dhash"]) +
          _hash_list_sim(sig_b["dhash"], sig_a["dhash"])) / 2
    base = (ph + dh) / 2
    if use_fg and sig_a.get("fg_phash") and sig_b.get("fg_phash"):
        fph = (_hash_list_sim(sig_a["fg_phash"], sig_b["fg_phash"]) +
               _hash_list_sim(sig_b["fg_phash"], sig_a["fg_phash"])) / 2
        fdh = (_hash_list_sim(sig_a["fg_dhash"], sig_b["fg_dhash"]) +
               _hash_list_sim(sig_b["fg_dhash"], sig_a["fg_dhash"])) / 2
        return (base + (fph + fdh) / 2) / 2
    return base


def motion_similarity(sig_a: dict, sig_b: dict, scale: float) -> float:
    """运动能量相似度：对数尺度下能量差越小越相似"""
    ma = float(np.log1p(max(0.0, sig_a["motion_mean"])))
    mb = float(np.log1p(max(0.0, sig_b["motion_mean"])))
    diff = abs(ma - mb)
    if scale <= 1e-9:
        return 1.0 if diff < 1e-9 else 0.0
    return max(0.0, 1.0 - diff / scale)


def _clip_check() -> bool:
    """检测 CLIP 可用性并尝试加载模型；失败自动尝试 hf-mirror 镜像后仍失败则降级"""
    if _CLIP["checked"]:
        return _CLIP["ok"]
    _CLIP["checked"] = True
    try:
        import torch  # noqa: F401
        import open_clip
        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="laion2b_s34b_b79k", device=device)
        except Exception:
            log("[提示] CLIP 官方源下载失败/不存在，尝试 hf-mirror 镜像...")
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="laion2b_s34b_b79k", device=device)
        _CLIP.update({"ok": True, "model": model, "preprocess": preprocess, "device": device})
    except Exception as exc:
        log("[提示] CLIP 不可用（%s），相关功能自动降级。" % type(exc).__name__)
        _CLIP["ok"] = False
    return _CLIP["ok"]


def clip_similarity(sig_a: dict, sig_b: dict) -> float:
    ea, eb = sig_a.get("clip_emb"), sig_b.get("clip_emb")
    if ea is None or eb is None:
        return 0.0
    return float(np.dot(ea, eb))


def add_clip_embedding(sig: dict, thumbs: list) -> None:
    """用缩略图帧提取 CLIP 平均嵌入（写入 sig['clip_emb']）"""
    import torch
    model, preprocess, device = _CLIP["model"], _CLIP["preprocess"], _CLIP["device"]
    imgs = [Image.fromarray(cv2.cvtColor(t, cv2.COLOR_BGR2RGB)) for t in thumbs]
    batch = torch.stack([preprocess(im) for im in imgs]).to(device)
    with torch.no_grad():
        feats = model.encode_image(batch)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    mean = feats.mean(dim=0)
    mean = mean / (mean.norm() + 1e-8)
    sig["clip_emb"] = mean.cpu().numpy()


def _build_behavior_prompts(labels: set, neg_marker: str = "neg") -> tuple:
    """从标签集合提炼行为词表并生成 CLIP 文本提示。返回 (prompts, token_names)"""
    drop = {"pos", "neg", "daytime", "night", "black", "white", "full", "color",
            "gray", "grey", "ir", neg_marker.lower()}
    tokens = set()
    for lbl in labels:
        for part in lbl.replace("+", "-").split("-"):
            part = part.strip()
            if part and part.lower() not in drop and not part.isdigit() and len(part) >= 3:
                tokens.add(part)
    prompts, names = [], []
    for t in sorted(tokens):
        prompts.append("a surveillance video of " + t.replace("_", " "))
        names.append(t)
    return prompts, names


def verify_neg_with_clip(label_map: dict, sigs: dict, neg_marker: str = "neg",
                         margin: float = 0.03) -> list:
    """
    neg 样本专用验证（v0.2 新增）：对名称带 neg 标记的视频，
    用 CLIP 判断其是否疑似包含目标行为。返回 findings 列表。
    """
    import torch
    import open_clip
    model, preprocess, device = _CLIP["model"], _CLIP["preprocess"], _CLIP["device"]
    labels = {l for l in label_map.values() if l}
    prompts, names = _build_behavior_prompts(labels, neg_marker)
    if not prompts:
        return []
    tokenizer = open_clip.get_tokenizer("ViT-B-32")
    with torch.no_grad():
        tf = model.encode_text(tokenizer(prompts).to(device))
        tf = tf / tf.norm(dim=-1, keepdim=True)
        ep = model.encode_text(tokenizer(
            ["an empty scene with no people and no animals"]).to(device))
        ep = ep / ep.norm(dim=-1, keepdim=True)
    tf_np = tf.cpu().numpy()
    empty_vec = ep.squeeze(0).cpu().numpy()

    findings = []
    for p, sig in sigs.items():
        lbl = label_map.get(p) or ""
        emb = sig.get("clip_emb")
        if emb is None:
            continue
        low_tokens = [t.lower() for t in lbl.replace("+", "-").split("-")]
        if neg_marker.lower() not in low_tokens:
            continue
        sims = tf_np @ emb
        j = int(np.argmax(sims))
        empty_sim = float(np.dot(empty_vec, emb))
        gap = float(sims[j]) - empty_sim
        if gap > margin:
            findings.append({"path": p, "label": lbl, "token": names[j],
                             "score": float(sims[j]), "gap": gap})
    return findings


# ============================================================
# 4. 一致性分析
# ============================================================
def analyze(label_map: dict, sigs: dict, pairs: dict, args) -> dict:
    """组内/组间统计、离群检测、标签混淆矩阵、轮廓系数"""
    by_label = defaultdict(list)
    for p, lbl in label_map.items():
        if lbl and p in sigs:
            by_label[lbl].append(p)
    labels = sorted(by_label.keys())
    paths = sorted(sigs.keys())

    intra = defaultdict(list)
    inter = defaultdict(list)
    video_intra_sims = defaultdict(list)
    for (a, b), sim in pairs.items():
        la, lb = label_map.get(a), label_map.get(b)
        if la and la == lb:
            intra[la].append(sim)
            video_intra_sims[a].append(sim)
            video_intra_sims[b].append(sim)
        elif la and lb:
            inter[(min(la, lb), max(la, lb))].append(sim)
    video_intra_mean = {p: float(np.mean(s)) for p, s in video_intra_sims.items() if s}

    label_stats = {}
    for lbl in labels:
        members = by_label[lbl]
        stats = {
            "count": len(members),
            "intra_mean": float(np.mean(intra[lbl])) if intra[lbl] else None,
            "intra_min": float(np.min(intra[lbl])) if intra[lbl] else None,
            "motion_mean": float(np.mean([sigs[p]["motion_mean"] for p in members])),
            "motion_std": float(np.mean([sigs[p]["motion_std"] for p in members])),
        }
        others = [video_intra_mean[p] for p in members if p in video_intra_mean]
        stats["outlier_std"] = float(np.std(others)) if len(others) > 1 else 0.0
        label_stats[lbl] = stats

    global_intra = float(np.mean([s for lst in intra.values() for s in lst])) if intra else 0.0
    all_inter = [s for lst in inter.values() for s in lst]
    global_inter = float(np.mean(all_inter)) if all_inter else 0.0

    confusion = {}
    for (la, lb), sims in inter.items():
        confusion[(la, lb)] = float(np.mean(sims))

    # 每个视频与其他各标签的平均相似度（"更佳标签"判断）
    video_other_means = {}
    for p in paths:
        own = label_map.get(p)
        if not own:
            continue
        entry = {}
        for m in labels:
            if m == own:
                continue
            sims_m = []
            for q in by_label[m]:
                sim = pairs.get((min(p, q), max(p, q)))
                if sim is not None:
                    sims_m.append(sim)
            if sims_m:
                entry[m] = float(np.mean(sims_m))
        if entry:
            video_other_means[p] = entry

    # 疑似标注不一致
    suspects = []
    for p, mean_sim in video_intra_mean.items():
        lbl = label_map[p]
        st = label_stats[lbl]
        reasons = []
        suggested = ""
        if mean_sim < args.suspect_threshold:
            reasons.append("组内平均相似度 %.2f < 阈值 %.2f" % (mean_sim, args.suspect_threshold))
        others = [video_intra_mean[q] for q in by_label[lbl] if q in video_intra_mean]
        if len(others) >= 3 and st["outlier_std"] > 1e-6:
            z = (st["intra_mean"] - mean_sim) / st["outlier_std"]
            if z > 2.0:
                reasons.append("显著低于本标签均值（z=%.1f）" % z)
        for m, sim_m in video_other_means.get(p, {}).items():
            if len(by_label[m]) >= 2 and sim_m >= mean_sim + 0.1:
                reasons.append("更接近标签『%s』（相似度 %.2f > 本组 %.2f）" % (m, sim_m, mean_sim))
                suggested = m
                break
        if reasons:
            suspects.append({"path": p, "label": lbl, "score": mean_sim,
                             "reason": "；".join(reasons), "suggested": suggested})
    suspects.sort(key=lambda s: s["score"])

    suspects_by_label = defaultdict(list)
    for s in suspects:
        suspects_by_label[s["label"]].append(s)

    # 轮廓系数（可选，sklearn）
    silhouette = None
    if len(labels) >= 2 and len(paths) >= 3:
        try:
            from sklearn.metrics import silhouette_score
            D = np.zeros((len(paths), len(paths)))
            for i, a in enumerate(paths):
                for j, b in enumerate(paths):
                    if i == j:
                        continue
                    sim = pairs.get((min(a, b), max(a, b)), pairs.get((a, b)))
                    if sim is None:
                        sim = 1.0 if label_map.get(a) == label_map.get(b) else 0.0
                    D[i, j] = 1.0 - sim
            arr = np.array([labels.index(label_map[p]) if label_map.get(p) else -1 for p in paths])
            mask = arr >= 0
            if len(set(arr[mask].tolist())) >= 2:
                silhouette = float(silhouette_score(D[np.ix_(mask, mask)], arr[mask], metric="precomputed"))
        except Exception:
            silhouette = None

    static_ratio = 0.0
    if args.motion_hash:
        static_n = sum(1 for s in sigs.values() if s.get("static_cam"))
        static_ratio = static_n / max(1, len(sigs))

    return {
        "labels": labels,
        "by_label": by_label,
        "label_stats": label_stats,
        "global_intra": global_intra,
        "global_inter": global_inter,
        "separation": global_intra - global_inter,
        "confusion": confusion,
        "suspects": suspects,
        "suspects_by_label": dict(suspects_by_label),
        "video_other_means": video_other_means,
        "video_intra_mean": video_intra_mean,
        "silhouette": silhouette,
        "static_ratio": static_ratio,
    }


def verdict_of(stats: dict, result: dict) -> str:
    """单标签一致性判定文案"""
    if stats["count"] < 2:
        return "样本不足（单视频，无法验证）"
    if stats["intra_mean"] is None:
        return "无组内比对"
    margin = result["global_inter"] if result["global_inter"] > 0 else 0.0
    if stats["intra_mean"] >= max(0.7, margin + 0.15):
        return "一致 ✔（组内相似度高）"
    if stats["intra_mean"] >= max(0.5, margin + 0.05):
        return "基本一致（存在一定差异）"
    return "可疑 ✘（组内相似度低，标签可能混入异类）"


# ============================================================
# 5. 建议与总结（v0.2 新增）
# ============================================================
def _group_advice(lbl: str, st: dict, sus_list: list, result: dict, purpose: str) -> list:
    """为单个标签组生成结论与下一步建议"""
    tips = []
    v = verdict_of(st, result)
    if st["count"] < 2:
        tips.append("该标签仅 %d 个视频，无法验证一致性：建议补充同类样本，或并入相近标签。" % st["count"])
        return tips
    if v.startswith("一致"):
        tips.append("结论：%d 个视频组内平均相似度 %.2f，标签与画面/行为对应良好，可按现状使用。"
                    % (st["count"], st["intra_mean"]))
    elif v.startswith("基本"):
        tips.append("结论：%d 个视频组内相似度 %.2f，存在一定差异（多来自拍摄时段/场景变化）。"
                    % (st["count"], st["intra_mean"]))
        tips.append("建议：抽查组内相似度最低的视频，确认差异来自场景而非标注错误。")
    else:
        tips.append("结论：组内相似度仅 %.2f，混入异类风险高。" % (st["intra_mean"] or 0.0))
        tips.append("建议：优先人工复核下方/CSV 中本组嫌疑项，确认后再使用该标签。")
    if sus_list:
        names = [os.path.basename(s["path"]) for s in sus_list[:3]]
        more = (" 等 %d 个" % len(sus_list)) if len(sus_list) > 3 else ""
        tips.append("本组疑似项 %d 个：%s%s（完整名单见 CSV，含建议标签）。"
                    % (len(sus_list), "、".join(names), more))
    if purpose == "train":
        tips.append("训练用途：复核嫌疑项后再入库；建议按文件名序号分段切分 train/val，"
                    "避免时间上相邻的近重复片段同时落入训练与验证集造成泄漏。")
    elif purpose == "detection":
        tips.append("检测用途：建议先做运动前景抽帧再送标注工具；同一行为尽量覆盖多时段多视角。")
    elif purpose == "retrieval":
        tips.append("素材库用途：可结合 find_mp4.py 查重去重，保留分辨率/码率最高者。")
    elif purpose == "archive":
        tips.append("归档用途：建议按 时段/行为 两级目录整理，并生成 SHA-256 清单后冷存储。")
    return tips


def _purpose_guidance(purpose: str, result: dict, args) -> list:
    """全局"数据用途参考建议"（v0.2 新增）：结合数据形态给出针对性建议"""
    tips = []
    stats = result["label_stats"]
    labels = result["labels"]
    n_videos = sum(st["count"] for st in stats.values())
    multi = [l for l, st in stats.items() if st["count"] >= 2]
    counts = [stats[l]["count"] for l in multi] or [0]
    # v2.8 兼容：suspects 可为列表（本工具内部）或数量（pipeline 传入）
    _sus = result["suspects"]
    n_suspects = len(_sus) if isinstance(_sus, (list, tuple)) else int(_sus or 0)
    if purpose == "train":
        tips.append("训练集建议：先复核 %d 个疑似标注不一致项（见 CSV），再划分数据集；"
                    "neg 样本建议加跑 --use-clip --verify-neg 验证其确实不含目标行为。" % n_suspects)
        if counts and max(counts) >= 3 * max(1, min(counts)):
            tips.append("类别不平衡提醒：最大类 %d 个 vs 最小类 %d 个，训练时建议加权采样或过拟合小类。"
                        % (max(counts), min(counts)))
        tips.append("划分建议：按文件名序号分段（如序号 mod 10）切 train/val，"
                    "避免同一场景连续片段跨集合泄漏。")
    elif purpose == "detection":
        tips.append("检测数据集建议：优先挑选运动能量高、前景明显的片段做标注；"
                    "可开启 --motion-hash 让对比聚焦运动区域。")
    elif purpose == "retrieval":
        tips.append("素材库建议：先按查重结果去重（保留最高质量），再用 find_mp4.py similar-search 建语义检索索引。")
    elif purpose == "archive":
        tips.append("归档建议：按 时段/行为 两级目录整理；生成文件清单 + SHA-256；重复内容只保留一份。")
    else:
        tips.append("通用建议：先处理疑似标注不一致项（%d 个），再决定是否按查重结果去重。" % n_suspects)
    if result.get("static_ratio", 0) >= 0.5:
        tips.append("固定机位提醒：%.0f%% 的视频被判为低运动前景（静止机位场景），"
                    "整段画面哈希主要反映背景而非内容；查重请开启 --motion-hash 或改用前景/行为特征。"
                    % (result["static_ratio"] * 100))
    if result["separation"] < 0.15:
        tips.append("分离度仅 %+.2f（组内 %.2f vs 组间 %.2f）：当前标签与画面外观对应较弱，"
                    "属固定机位场景的预期现象，判断行为一致性请以运动能量/CLIP 特征为主。"
                    % (result["separation"], result["global_intra"], result["global_inter"]))
    if args.use_clip and not getattr(args, "verify_neg", False):
        neg_cnt = sum(1 for l in labels if "neg" in l.lower())
        if neg_cnt:
            tips.append("检测到 %d 个含 neg 的标签但未启用 --verify-neg：建议开启以验证 neg 样本确实不含目标行为。" % neg_cnt)
    tips.append("共 %d 个视频、%d 个标签（可验证标签 %d 个）。" % (n_videos, len(labels), len(multi)))
    return tips


def _apply_preset(args):
    """【v0.2 新增】场景预设：surveillance=固定机位监控（自动开运动前景哈希+收紧阈值）"""
    name = getattr(args, "preset", "general") or "general"
    if name != "surveillance":
        return args
    argv = sys.argv[1:]

    def _not_set(flag):
        return not any(a == flag or a.startswith(flag + "=") for a in argv)

    if _not_set("--motion-hash") and not getattr(args, "no_motion_hash", False):
        args.motion_hash = True
    if _not_set("--suspect-threshold"):
        args.suspect_threshold = 0.55
    log("[预设] surveillance（固定机位监控）: 运动前景哈希开启 ｜ 嫌疑阈值 0.55")
    return args


def _resolve_purpose(args) -> str:
    """解析数据用途：--purpose 显式优先；auto 且交互终端时询问；否则 general"""
    p = getattr(args, "purpose", "general") or "general"
    if p != "auto":
        return p if p in PURPOSES else "general"
    try:
        if sys.stdin and sys.stdin.isatty():
            print("")
            print("请选择这批数据的主要用途（将影响报告中的参考建议）:")
            keys = list(PURPOSES.keys())
            for i, k in enumerate(keys, 1):
                print("  %d. %-10s %s" % (i, k, PURPOSES[k]))
            raw = input("输入编号或名称 [general]: ").strip().lower()
            if raw.isdigit() and 1 <= int(raw) <= len(keys):
                return keys[int(raw) - 1]
            if raw in PURPOSES:
                return raw
            print("  未识别输入，使用 general")
    except Exception:
        pass
    return "general"


# ============================================================
# 6. 报告导出
# ============================================================
def _thumb_data_uri(frame_bgr, quality=70) -> str:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _file_uri(path: str) -> str:
    try:
        return Path(path).as_uri()
    except Exception:
        return "file:///" + path.replace("\\", "/")


def write_report_md(out_path: str, result: dict, label_map: dict, sigs: dict, args):
    purpose = getattr(args, "purpose", "general")
    lines = ["# 预标注一致性验证报告", "",
             "- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
             "- 扫描目录：`%s`" % args.dir,
             "- 标签来源：`%s`" % args.label_from,
             "- 数据用途：%s（%s）" % (purpose, PURPOSES.get(purpose, "")),
             "- 抽帧数：%d / 视频；运动采样：%d 段 x %d 帧%s"
             % (args.frames, args.motion_bursts, args.burst_frames,
                "；运动前景哈希：开启" if args.motion_hash else ""),
             "- 特征权重：帧哈希 %s + 运动能量 %s%s"
             % (args.hash_weight, args.motion_weight,
                (" + CLIP %s" % args.clip_weight) if args.use_clip else ""),
             "",
             "## 总体结论", ""]
    lines.append("- 组内平均相似度：**%.3f**，组间平均相似度：**%.3f**，分离度：**%+.3f**"
                 % (result["global_intra"], result["global_inter"], result["separation"])
                 + "（分离度越大，说明标签与画面/行为越对应）")
    if result["silhouette"] is not None:
        lines.append("- 轮廓系数（silhouette）：**%.3f**（>0.25 说明按当前标签分组结构较合理）"
                     % result["silhouette"])
    lines.append("- 疑似标注不一致：**%d** 个" % len(result["suspects"]))
    lines.append("")

    lines.append("## 数据用途参考建议（%s · %s）" % (purpose, PURPOSES.get(purpose, "")))
    lines.append("")
    for t in _purpose_guidance(purpose, result, args):
        lines.append("- " + t)
    lines.append("")

    lines.append("## 各标签一致性与建议")
    lines.append("")
    lines.append("| 标签 | 视频数 | 组内平均相似度 | 组内最低 | 运动能量(均值) | 判定 |")
    lines.append("|------|--------|----------------|----------|----------------|------|")
    for lbl in result["labels"]:
        st = result["label_stats"][lbl]
        intra = ("%.3f" % st["intra_mean"]) if st["intra_mean"] is not None else "—"
        intra_min = ("%.3f" % st["intra_min"]) if st["intra_min"] is not None else "—"
        lines.append("| %s | %d | %s | %s | %.1f | %s |"
                     % (lbl, st["count"], intra, intra_min, st["motion_mean"], verdict_of(st, result)))
    lines.append("")
    lines.append("### 分组建议与总结")
    lines.append("")
    for lbl in result["labels"]:
        st = result["label_stats"][lbl]
        lines.append("**%s**（%d 个）" % (lbl, st["count"]))
        for t in _group_advice(lbl, st, result["suspects_by_label"].get(lbl, []), result, purpose):
            lines.append("> " + t)
        lines.append("")

    if result["confusion"]:
        lines.append("## 标签间相似度（混淆风险，越高越容易混）")
        lines.append("")
        lines.append("| 标签 A | 标签 B | 平均相似度 |")
        lines.append("|--------|--------|------------|")
        for (la, lb), sim in sorted(result["confusion"].items(), key=lambda kv: -kv[1])[:15]:
            lines.append("| %s | %s | %.3f |" % (la, lb, sim))
        lines.append("")

    lines.append("## 疑似标注不一致清单")
    lines.append("")
    if result["suspects"]:
        for s in result["suspects"]:
            rel = os.path.relpath(s["path"], args.dir)
            sug = ("，建议改为 **%s**" % s["suggested"]) if s.get("suggested") else ""
            lines.append("- `%s`（标签 **%s**）— %s%s" % (rel, s["label"], s["reason"], sug))
    else:
        lines.append("未发现疑似标注不一致的视频。")
    lines.append("")

    lines.append("## 说明与局限")
    lines.append("")
    lines.append("- 帧哈希衡量**画面外观**，运动能量近似**行为强度**，运动前景哈希（--motion-hash）"
                 "聚焦前景差异区域。同标签视频若场景完全不同但行为相同，哈希相似度偏低属预期现象。")
    lines.append("- 本工具输出的是『预标注质量参考』，不能替代人工复核。")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_suspects_csv(out_path: str, result: dict):
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["文件路径", "当前标签", "建议标签", "组内平均相似度", "原因"])
        if result["suspects"]:
            for s in result["suspects"]:
                w.writerow([s["path"], s["label"], s.get("suggested", ""),
                            "%.3f" % s["score"], s["reason"]])
        else:
            w.writerow(["（无）", "", "", "", ""])


_HTML_JS = """
function toggleVideo(el){
  var card = el.closest('.card');
  var box = card.querySelector('.vidbox');
  if (box.style.display === 'none') {
    if (!box.querySelector('video')) {
      var v = document.createElement('video');
      v.src = card.getAttribute('data-v');
      v.controls = true; v.width = %d; v.preload = 'none';
      box.appendChild(v);
    }
    box.style.display = 'block';
    el.textContent = '✕ 关闭';
  } else {
    var v = box.querySelector('video'); if (v) { v.pause(); }
    box.style.display = 'none';
    el.textContent = '▶ 播放';
  }
}
""" % THUMB_WIDTH


def write_report_html(out_path: str, result: dict, label_map: dict, sigs: dict, args):
    """抽帧对比图：分组折叠导航 + 点击播放原视频 + 每组建议 + 疑似项标红"""
    purpose = getattr(args, "purpose", "general")
    suspect_set = {s["path"] for s in result["suspects"]}
    card_w = THUMB_WIDTH + 24
    html = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
            "<title>抽帧对比 — 预标注一致性验证</title><style>",
            "body{font-family:'Microsoft YaHei',sans-serif;margin:24px;background:#f7f8fa;}",
            "h1{font-size:22px;} .nav{position:sticky;top:0;background:#f7f8fa;padding:8px 0;"
            "border-bottom:1px solid #dde;z-index:9;}",
            ".nav a{margin-right:10px;font-size:13px;color:#2a5db0;text-decoration:none;}",
            "details{margin:10px 0;} summary{cursor:pointer;font-size:17px;font-weight:bold;"
            "padding:8px 12px;background:#eef1f6;border-left:4px solid #4a78c2;border-radius:4px;}",
            "summary.bad{border-left-color:#d9534f;}",
            ".advice{background:#fffbe8;border:1px solid #f0e2a0;border-radius:6px;"
            "padding:8px 12px;margin:10px 0;font-size:13px;}",
            ".advice li{margin:3px 0;}",
            ".grid{display:flex;flex-wrap:wrap;gap:14px;}",
            ".card{background:#fff;border:1px solid #dde;border-radius:8px;padding:8px;width:%dpx;}" % card_w,
            ".card.suspect{border:2px solid #d9534f;background:#fff5f5;}",
            ".card img{width:100%%;border-radius:4px;display:block;margin-bottom:4px;}",
            ".play{cursor:pointer;font-size:12px;color:#2a5db0;user-select:none;}",
            ".vidbox{margin-top:6px;}",
            ".name{font-size:12px;word-break:break-all;}",
            ".meta{font-size:11px;color:#667;margin-top:4px;}",
            ".tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:8px;"
            "background:#4a78c2;color:#fff;margin-bottom:4px;}",
            ".tag.bad{background:#d9534f;}",
            "table{border-collapse:collapse;margin-top:8px;background:#fff;}",
            "td,th{border:1px solid #ccd;padding:4px 10px;font-size:12px;}",
            "</style></head><body>",
            "<h1>抽帧对比 — 预标注一致性验证</h1>",
            "<p>目录：<code>%s</code> ｜ 标签来源：<b>%s</b> ｜ 数据用途：<b>%s</b> ｜ "
            "组内平均相似度 <b>%.3f</b> ｜ 组间平均相似度 <b>%.3f</b> ｜ "
            "疑似不一致 <b style='color:#d9534f'>%d</b> 个</p>"
            % (args.dir, args.label_from, purpose, result["global_intra"],
               result["global_inter"], len(result["suspects"]))]

    # 全局用途建议
    html.append("<div class='advice'><b>📋 数据用途参考建议（%s · %s）</b><ul>%s</ul></div>"
                % (purpose, PURPOSES.get(purpose, ""),
                   "".join("<li>%s</li>" % t for t in _purpose_guidance(purpose, result, args))))

    # 锚点导航
    html.append("<div class='nav'>" + "".join(
        "<a href='#lbl%d'>%s(%d)%s</a>" % (i, l, result["label_stats"][l]["count"],
                                           "⚠" if any(s["label"] == l for s in result["suspects"]) else "")
        for i, l in enumerate(result["labels"])) + "</div>")

    if result["confusion"]:
        labels = result["labels"]
        html.append("<h2>标签间平均相似度矩阵</h2><table><tr><th></th>"
                    + "".join("<th>%s</th>" % l for l in labels) + "</tr>")
        for la in labels:
            row = ["<tr><th>%s</th>" % la]
            for lb in labels:
                if la == lb:
                    row.append("<td style='background:#eef1f6'>—</td>")
                else:
                    sim = result["confusion"].get((min(la, lb), max(la, lb)))
                    hot = "background:#fdecea;color:#c00;" if (sim is not None and sim > result["global_intra"]) else ""
                    cell = ("%.3f" % sim) if sim is not None else "—"
                    row.append("<td style='%s'>%s</td>" % (hot, cell))
            html.append("".join(row) + "</tr>")
        html.append("</table>")

    for li, lbl in enumerate(result["labels"]):
        st = result["label_stats"][lbl]
        v = verdict_of(st, result)
        sus_list = result["suspects_by_label"].get(lbl, [])
        bad = v.startswith("可疑") or bool(sus_list)
        cls = "bad" if bad else ""
        intra = ("%.3f" % st["intra_mean"]) if st["intra_mean"] is not None else "—"
        open_attr = " open" if st["count"] <= COLLAPSE_THRESHOLD else ""
        html.append("<details id='lbl%d'%s><summary class='%s'>标签：%s ｜ %d 个视频 ｜ "
                    "组内平均相似度 %s ｜ %s</summary>" % (li, open_attr, cls, lbl, st["count"], intra, v))
        html.append("<div class='advice'><b>💡 建议与总结</b><ul>%s</ul></div>"
                    % "".join("<li>%s</li>" % t
                              for t in _group_advice(lbl, st, sus_list, result, purpose)))
        html.append("<div class='grid'>")
        for p in result["by_label"][lbl]:
            sig = sigs[p]
            is_suspect = p in suspect_set
            imgs = "".join("<img src='%s'/>" % _thumb_data_uri(t) for t in sig["thumbs"])
            rel = os.path.relpath(p, args.dir)
            reason = next((s["reason"] for s in result["suspects"] if s["path"] == p), "")
            extra = (" ｜ " + reason) if (is_suspect and reason) else ""
            vim = result["video_intra_mean"].get(p)
            vim_txt = ("%.3f" % vim) if vim is not None else "—"
            html.append(
                "<div class='card%s' data-v='%s'>"
                "<span class='tag%s'>%s</span>"
                "<span class='play' onclick='toggleVideo(this)'>▶ 播放</span>"
                "%s<div class='vidbox' style='display:none'></div>"
                "<div class='name'>%s</div>"
                "<div class='meta'>运动能量 %.1f ｜ 组内相似度 %s%s</div></div>"
                % (" suspect" if is_suspect else "",
                   _file_uri(p),
                   " bad" if is_suspect else "",
                   "疑似不一致" if is_suspect else lbl,
                   imgs, rel, sig["motion_mean"], vim_txt, extra))
        html.append("</div></details>")

    html.append("<script>%s</script>" % _HTML_JS)
    html.append("<p style='margin-top:30px;color:#889;font-size:12px'>"
                "注：帧哈希=画面外观相似；运动能量=行为强度近似（帧间差分）；"
                "%s标红卡片为疑似标注不一致，点击『▶ 播放』可内嵌播放原视频（浏览器需允许本地文件）。"
                % ("运动前景哈希已启用；" if args.motion_hash else "")
                + "</p></body></html>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html))


# ============================================================
# 7. 主流程
# ============================================================
def parse_args():
    ap = argparse.ArgumentParser(description="预标注一致性验证：抽帧对比同标签视频是否真的表现相同行为")
    ap.add_argument("--dir", required=True, help="视频根目录")
    ap.add_argument("--labels-csv", default="", help="标注 CSV（列：filename/path + label/标签）")
    ap.add_argument("--label-from", choices=["auto", "folder", "prefix", "csv", "regex"], default="auto",
                    help="预标注来源（默认 auto；regex=用 --label-regex 从文件名提取）")
    ap.add_argument("--label-regex", default="",
                    help=r"标签提取正则，第1个捕获组为标签，如 r'cam01_(.+?)-(?:pos|neg)'")
    ap.add_argument("--preset", default="general",
                    choices=["general", "surveillance", "footage"],
                    help="场景预设：surveillance 自动启用运动前景哈希并收紧嫌疑阈值（显式指定的参数优先）")
    ap.add_argument("--no-motion-hash", action="store_true",
                    help="禁用运动前景哈希（surveillance 预设下用于覆盖默认开启）")
    ap.add_argument("--summary-json", action="store_true",
                    help="额外导出机器可读摘要 label_verify_summary.json（供 pipeline 使用）")
    ap.add_argument("--purpose", default="auto",
                    choices=["auto", "general", "train", "detection", "retrieval", "archive"],
                    help="数据用途（决定报告中的参考建议；auto=交互终端时询问）")
    ap.add_argument("--frames", type=int, default=8, help="每个视频抽帧数（默认 8）")
    ap.add_argument("--motion-bursts", type=int, default=3, help="运动采样段数（默认 3）")
    ap.add_argument("--burst-frames", type=int, default=6, help="每段连续帧数（默认 6）")
    ap.add_argument("--motion-hash", action="store_true",
                    help="启用运动前景哈希：多帧中值估计背景，仅前景差异区域计算第二组哈希（固定机位数据推荐）")
    ap.add_argument("--fg-threshold", type=int, default=18, help="前景差分二值化阈值（默认 18）")
    ap.add_argument("--hash-weight", type=float, default=0.6, help="帧哈希权重（默认 0.6）")
    ap.add_argument("--motion-weight", type=float, default=0.4, help="运动能量权重（默认 0.4）")
    ap.add_argument("--clip-weight", type=float, default=0.5, help="CLIP 权重（--use-clip 时生效）")
    ap.add_argument("--use-clip", action="store_true", help="启用 CLIP 语义特征（需下载权重）")
    ap.add_argument("--verify-neg", action="store_true",
                    help="neg 样本专用验证：CLIP 判断 neg 视频是否疑似包含目标行为（需 --use-clip）")
    ap.add_argument("--neg-marker", default="neg", help="neg 标记词（默认 neg，按'-'分段匹配）")
    ap.add_argument("--suspect-threshold", type=float, default=0.5,
                    help="疑似不一致阈值：与同标签平均相似度低于此值即标记（默认 0.5）")
    ap.add_argument("--ext", default="mp4,mov,mkv,avi,webm,m4v,flv", help="视频后缀")
    ap.add_argument("--no-recursive", action="store_true", help="不递归子目录")
    ap.add_argument("--output-dir", default="", help="报告输出目录（默认 --dir 下 _label_verify）")
    return ap.parse_args()


def main():
    args = parse_args()
    args = _apply_preset(args)
    if getattr(args, "no_motion_hash", False):
        args.motion_hash = False
    t0 = time.time()
    root = os.path.abspath(os.path.expanduser(args.dir))
    if not os.path.isdir(root):
        log("[错误] 目录不存在: %s" % root)
        return EXIT_BAD_ARGS
    if args.label_from == "csv" and not args.labels_csv:
        log("[错误] --label-from csv 需要同时提供 --labels-csv")
        return EXIT_BAD_ARGS
    if args.label_regex:
        args.label_from = "regex"  # 显式正则优先
    args.purpose = _resolve_purpose(args)

    exts = [e.strip().lower().lstrip(".") for e in args.ext.split(",") if e.strip()]
    videos = scan_videos(root, exts, recursive=not args.no_recursive)
    log("[1/4] 扫描到 %d 个视频: %s" % (len(videos), root))
    if not videos:
        log("[错误] 未找到视频文件")
        return EXIT_BAD_ARGS

    try:
        label_map, used_mode = assign_labels(videos, args.label_from, root, args.labels_csv,
                                             getattr(args, "label_regex", ""))
    except (ValueError, OSError) as exc:
        log("[错误] 预标注识别失败: %s" % exc)
        return EXIT_BAD_ARGS
    args.label_from = used_mode
    labeled = {p: l for p, l in label_map.items() if l}
    unlabeled = len(videos) - len(labeled)
    n_labels = len(set(labeled.values()))
    log("      标签来源: %s ｜ 已标注 %d 个 ｜ 未标注 %d 个 ｜ 标签数 %d ｜ 用途: %s(%s)"
        % (used_mode, len(labeled), unlabeled, n_labels, args.purpose, PURPOSES.get(args.purpose, "")))
    if len(labeled) < 2 or n_labels < 1:
        log("[错误] 有效标注不足 2 个，无法进行一致性验证")
        return EXIT_BAD_ARGS

    # 特征提取
    log("[2/4] 抽帧与特征提取（%d 帧/视频, 运动采样 %dx%d%s）..."
        % (args.frames, args.motion_bursts, args.burst_frames,
           ", 运动前景哈希" if args.motion_hash else ""))
    clip_on = args.use_clip and _clip_check()
    if args.verify_neg and not clip_on:
        log("  [提示] --verify-neg 需要 CLIP（--use-clip），当前不可用，跳过 neg 验证")
    sigs, errors = {}, []
    total_steps = len(videos)
    done = 0
    for v in videos:
        done += 1
        if not label_map.get(v["path"]):
            continue
        sig = compute_video_signature(v["path"], args.frames, args.motion_bursts, args.burst_frames,
                                      motion_hash=args.motion_hash,
                                      fg_threshold=getattr(args, "fg_threshold", 18))
        if not sig["ok"]:
            errors.append({"path": v["path"], "err": sig["err"]})
            log("  [失败] %s: %s" % (v["name"], sig["err"]))
            continue
        if clip_on and sig["thumbs"]:
            try:
                add_clip_embedding(sig, sig["thumbs"])
            except Exception:
                pass
        sigs[v["path"]] = sig
        if done % 40 == 0 or done == total_steps:
            log("  进度 %d/%d" % (done, total_steps))

    if len(sigs) < 2:
        log("[错误] 成功解析的有效视频不足 2 个")
        return EXIT_BAD_ARGS if not errors else EXIT_PARSE_ERROR

    # 两两比对
    log("[3/4] 两两相似度比对...")
    w_hash, w_motion = args.hash_weight, args.motion_weight
    w_clip = args.clip_weight if clip_on else 0.0
    w_total = w_hash + w_motion + w_clip
    motion_logs = [float(np.log1p(max(0.0, s["motion_mean"]))) for s in sigs.values()]
    motion_scale = max(0.5, 2.0 * float(np.std(motion_logs)))
    paths = sorted(sigs.keys())
    pairs = {}
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            sim = (w_hash * hash_similarity(sigs[a], sigs[b], use_fg=args.motion_hash) +
                   w_motion * motion_similarity(sigs[a], sigs[b], motion_scale)) / w_total
            if clip_on:
                sim = sim + w_clip * clip_similarity(sigs[a], sigs[b]) / w_total
            pairs[(a, b)] = max(0.0, min(1.0, sim))

    # 分析与报告
    log("[4/4] 一致性分析与报告生成...")
    result = analyze(label_map, sigs, pairs, args)

    # neg 样本 CLIP 验证（合并进嫌疑清单）
    if args.verify_neg and clip_on:
        log("  neg 样本 CLIP 验证中...")
        try:
            findings = verify_neg_with_clip(label_map, sigs, neg_marker=args.neg_marker)
            have = {s["path"] for s in result["suspects"]}
            for fnd in findings:
                reason = ("neg 样本疑似包含目标行为『%s』（CLIP，行为-空景差值 %.2f）"
                          % (fnd["token"], fnd["gap"]))
                if fnd["path"] in have:
                    for s in result["suspects"]:
                        if s["path"] == fnd["path"]:
                            s["reason"] += "；" + reason
                            break
                else:
                    result["suspects"].append({"path": fnd["path"], "label": fnd["label"],
                                               "score": result["video_intra_mean"].get(fnd["path"], 0.0),
                                               "reason": reason, "suggested": ""})
            result["suspects"].sort(key=lambda s: s["score"])
            log("  neg 验证: 发现 %d 个疑似包含目标行为的 neg 样本" % len(findings))
        except Exception as exc:
            log("  [警告] neg 验证失败: %s" % exc)

    out_dir = os.path.abspath(args.output_dir) if args.output_dir else os.path.join(root, "_label_verify")
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, REPORT_MD)
    csv_path = os.path.join(out_dir, REPORT_CSV)
    html_path = os.path.join(out_dir, REPORT_HTML)
    write_report_md(md_path, result, label_map, sigs, args)
    write_suspects_csv(csv_path, result)
    if getattr(args, "summary_json", False):
        summary = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "dir": root, "purpose": args.purpose,
            "videos": len(sigs), "suspects": len(result["suspects"]),
            "global_intra": result["global_intra"],
            "global_inter": result["global_inter"],
            "separation": result["separation"],
            "silhouette": result["silhouette"],
            "static_ratio": result["static_ratio"],
            "labels": [{"label": lbl,
                        "count": result["label_stats"][lbl]["count"],
                        "intra": result["label_stats"][lbl]["intra_mean"],
                        "motion": result["label_stats"][lbl]["motion_mean"],
                        "verdict": verdict_of(result["label_stats"][lbl], result)}
                       for lbl in result["labels"]],
        }
        sj_path = os.path.join(out_dir, "label_verify_summary.json")
        with open(sj_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
        log("  机器可读摘要: %s" % sj_path)
    try:
        write_report_html(html_path, result, label_map, sigs, args)
    except Exception as exc:
        html_path = ""
        log("  [警告] HTML 生成失败: %s" % exc)

    # 控制台摘要
    log("")
    log("=" * 62)
    log("  预标注一致性验证结果")
    log("=" * 62)
    log("  组内平均相似度 %.3f ｜ 组间平均相似度 %.3f ｜ 分离度 %+.3f"
        % (result["global_intra"], result["global_inter"], result["separation"]))
    if result["silhouette"] is not None:
        log("  轮廓系数 %.3f" % result["silhouette"])
    if args.motion_hash:
        log("  静止机位占比 %.0f%%" % (result["static_ratio"] * 100))
    for lbl in result["labels"]:
        st = result["label_stats"][lbl]
        intra = ("%.3f" % st["intra_mean"]) if st["intra_mean"] is not None else " — "
        log("  %s: %d 个 ｜ 组内 %s ｜ 运动 %.1f ｜ %s"
            % (lbl, st["count"], intra, st["motion_mean"], verdict_of(st, result)))
    if result["suspects"]:
        log("  疑似标注不一致 %d 个:" % len(result["suspects"]))
        for s in result["suspects"][:10]:
            log("    - %s (%s): %s" % (os.path.relpath(s["path"], root), s["label"], s["reason"]))
        if len(result["suspects"]) > 10:
            log("    ... 其余 %d 个见 CSV" % (len(result["suspects"]) - 10))
    if errors:
        log("  解析失败 %d 个" % len(errors))
    log("")
    log("  报告: %s" % md_path)
    log("  疑似清单: %s" % csv_path)
    if html_path:
        log("  抽帧对比: %s" % html_path)
    log("  耗时 %.1fs" % (time.time() - t0))
    log("=" * 62)

    if errors:
        return EXIT_PARSE_ERROR
    return EXIT_SUSPECTS if result["suspects"] else EXIT_CONSISTENT


if __name__ == "__main__":
    sys.exit(main())
