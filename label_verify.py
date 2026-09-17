#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
label_verify.py — 预标注一致性验证工具（v0.1 原型）
====================================================

用途：当视频已经带有"预标注"（来自文件夹名 / 文件名前缀 / labels.csv）时，
本工具对每个视频抽帧，计算画面哈希（pHash+dHash）与运动能量特征，
比较【同标签视频之间】与【不同标签视频之间】的相似度，
回答："带相同标签的视频，是否真的表现出相同的行为/画面？"

它不修改任何文件，只生成报告：
    label_verify_report.md     分析报告（Markdown）
    label_verify_suspects.csv  疑似标注不一致的视频清单
    label_verify_frames.html   抽帧对比图（按标签分组，疑似项标红）

预标注识别来源（--label-from）：
    auto    自动选择：优先文件夹名，其次文件名前缀（默认）
    folder  用视频所在一级子文件夹名作为标签
    prefix  用文件名首个 "_" / "-" 前的前缀作为标签
    csv     从 --labels-csv 指定的 CSV 读取（列名支持 path/filename + label/标签）

特征与相似度：
    1. 帧哈希相似度  —— 抽 N 帧均匀采样（10%~90% 区间），pHash+dHash 双哈希，
       与 find_mp4.py 相同的最近邻匹配算法（--hash-weight，默认 0.6）
    2. 运动能量相似度 —— 在视频 3 个位置各抓一小段连续帧，计算帧间差分能量，
       用于近似"行为强度"是否一致（--motion-weight，默认 0.4）
    3. CLIP 语义相似度（可选）—— --use-clip 且已安装 torch/open_clip 时启用
       （首次运行需下载 ViT-B-32 权重；下载失败自动降级为前两种）

判定逻辑：
    组内相似度 intra = 同标签视频两两综合相似度的均值
    组间相似度 inter = 不同标签视频两两综合相似度的均值
    每个视频的离群分 = 它与同标签其他视频的平均相似度
    疑似标注不一致（满足任一即标记）：
        a) 离群分 < --suspect-threshold（默认 0.5）
        b) 低于本标签均值 2.0 个标准差（组内>=3 个视频时）
        c) 与某其他标签的平均相似度高出本组 0.1 以上（跨标签吸引力，
           同时给出"建议标签"，这是最直接的错标信号）

用法示例：
    python label_verify.py --dir D:\\labeled_videos
    python label_verify.py --dir D:\\data --label-from prefix
    python label_verify.py --dir D:\\data --labels-csv labels.csv --frames 12
    python label_verify.py --dir D:\\data --use-clip --output-dir report_out

退出码：0=全部标签一致；1=发现疑似标注不一致；2=有视频解析失败；3=参数/数据错误
依赖：opencv-python numpy Pillow imagehash（与 find_mp4.py 基础依赖一致）
"""

import argparse
import base64
import csv
import os
import sys
import time
from collections import defaultdict

import cv2
import numpy as np
from PIL import Image
import imagehash

# ============================================================
# 常量（与 find_mp4.py 保持一致的风格）
# ============================================================
HASH_SIZE = 8
FRAME_SAMPLE_RANGE = (0.1, 0.9)          # 抽帧区间（避开片头片尾）
MOTION_BURST_RATIOS = (0.15, 0.5, 0.85)  # 运动采样段位置
MOTION_RESIZE = 64                       # 运动能量计算的缩放尺寸
THUMB_WIDTH = 160                        # HTML 缩略图宽
HTML_THUMB_FRAMES = 4                    # HTML 中每个视频展示的帧数

REPORT_MD = "label_verify_report.md"
REPORT_CSV = "label_verify_suspects.csv"
REPORT_HTML = "label_verify_frames.html"

EXIT_CONSISTENT = 0
EXIT_SUSPECTS = 1
EXIT_PARSE_ERROR = 2
EXIT_BAD_ARGS = 3

_STDOUT_READY = False

# 可选 CLIP 依赖（延迟检测）
_CLIP = {"checked": False, "ok": False, "model": None, "preprocess": None, "device": "cpu"}


def log(msg: str = "", force: bool = True):
    # Windows 控制台默认 GBK，中文/特殊符号可能无法编码：统一重配为 UTF-8
    global _STDOUT_READY
    if not _STDOUT_READY:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        _STDOUT_READY = True
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
    """读取标注 CSV，返回 {basename: label}（path 列兼容完整路径或纯文件名）"""
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
                f"CSV 缺少必要列：需要 文件名列(path/filename/文件名) 与 标签列(label/标签)，实际列：{reader.fieldnames}")
        for row in reader:
            key = os.path.basename(str(row[path_col]).strip())
            label = str(row[label_col]).strip()
            if key and label:
                mapping[key] = label
    return mapping


def assign_labels(videos: list, mode: str, root: str, labels_csv: str, label_regex: str = "") -> tuple:
    """
    为每个视频分配预标注。
    返回 (label_map {path: label|None}, mode_used: str)
    label_regex: 正则（应用于去扩展名的文件名），取第 1 个捕获组为标签
    """
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
                "或使用 文件名前缀，或提供 --labels-csv 标注文件。")
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


def compute_video_signature(video_path: str, n_frames: int,
                            motion_bursts: int = 3, burst_frames: int = 6) -> dict:
    """
    单视频特征提取：
      - n_frames 帧均匀采样 → pHash/dHash（与 find_mp4 相同参数）
      - motion_bursts 段连续帧 → 帧间差分运动能量 (mean/std)
      - 保留前 HTML_THUMB_FRAMES 帧的缩略图（BGR array）
    失败返回 {"ok": False, "err": 类型}
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

        # v0.2 优化：短视频用"单次顺序解码"代替 20+ 次 cap.set 随机 seek。
        # 实测 431 个监控短片：seek 模式 >30 分钟未完成，顺序模式约 2 分钟。
        # 长视频仍走 seek 模式（顺序解码整个视频不划算）。
        sequential = total <= 6000
        motions = []
        if sequential:
            need = set(indices)
            burst_plan = {}   # burst 起始帧 -> 剩余待收集数
            for ratio in MOTION_BURST_RATIOS[:max(1, motion_bursts)]:
                burst_plan[int(total * ratio)] = max(2, burst_frames)
            active_prev = None
            active_left = 0
            burst_diffs = []
            frame_no = 0
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                if frame_no in need:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
                    phashes.append(imagehash.phash(Image.fromarray(small), hash_size=HASH_SIZE))
                    dhashes.append(imagehash.dhash(Image.fromarray(small), hash_size=HASH_SIZE))
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
        else:
            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
                phashes.append(imagehash.phash(Image.fromarray(small), hash_size=HASH_SIZE))
                dhashes.append(imagehash.dhash(Image.fromarray(small), hash_size=HASH_SIZE))
                if thumb_budget > 0:
                    h, w = frame.shape[:2]
                    scale = THUMB_WIDTH / w if w > THUMB_WIDTH else 1.0
                    thumb = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                                       interpolation=cv2.INTER_AREA) if scale < 1.0 else frame
                    thumbs.append(thumb)
                    thumb_budget -= 1

            # ---- 运动能量：若干段连续帧的帧间差分（seek 模式） ----
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

        if not phashes:
            return {"ok": False, "err": "decode_error"}

        motion_mean = float(np.mean(motions)) if motions else 0.0
        motion_std = float(np.std(motions)) if len(motions) > 1 else 0.0

        return {
            "ok": True, "err": None,
            "phash": phashes, "dhash": dhashes,
            "motion_mean": motion_mean, "motion_std": motion_std,
            "thumbs": thumbs,
            "duration": total / fps if fps > 0 else 0.0,
        }
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


def hash_similarity(sig_a: dict, sig_b: dict) -> float:
    """对称的双哈希融合相似度（pHash/dHash 各取双向最近邻均值后平均）"""
    ph = (_hash_list_sim(sig_a["phash"], sig_b["phash"]) +
          _hash_list_sim(sig_b["phash"], sig_a["phash"])) / 2
    dh = (_hash_list_sim(sig_a["dhash"], sig_b["dhash"]) +
          _hash_list_sim(sig_b["dhash"], sig_a["dhash"])) / 2
    return (ph + dh) / 2


def motion_similarity(sig_a: dict, sig_b: dict, scale: float) -> float:
    """运动能量相似度：对数尺度下能量差越小越相似（抗少数高运动视频拉爆尺度）"""
    ma = float(np.log1p(max(0.0, sig_a["motion_mean"])))
    mb = float(np.log1p(max(0.0, sig_b["motion_mean"])))
    diff = abs(ma - mb)
    if scale <= 1e-9:
        return 1.0 if diff < 1e-9 else 0.0
    return max(0.0, 1.0 - diff / scale)


def _clip_check() -> bool:
    """检测 CLIP 可用性并尝试加载模型（失败降级返回 False）"""
    if _CLIP["checked"]:
        return _CLIP["ok"]
    _CLIP["checked"] = True
    try:
        import torch  # noqa: F401
        import open_clip
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k", device=device)
        _CLIP.update({"ok": True, "model": model, "preprocess": preprocess, "device": device})
    except Exception as exc:
        log("[提示] CLIP 不可用（%s），仅使用 帧哈希+运动能量 特征。" % type(exc).__name__)
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


# ============================================================
# 4. 一致性分析
# ============================================================
def analyze(label_map: dict, sigs: dict, pairs: dict, args) -> dict:
    """
    输入：label_map {path: label}，sigs {path: sig}，pairs {(a,b): combined_sim}
    输出分析结果 dict（组内/组间统计、离群视频、标签混淆矩阵等）
    """
    by_label = defaultdict(list)
    for p, lbl in label_map.items():
        if lbl and p in sigs:
            by_label[lbl].append(p)
    labels = sorted(by_label.keys())
    paths = sorted(sigs.keys())

    # 组内 / 组间相似度（直接遍历配对字典，保证每个视频都被双向统计）
    intra = defaultdict(list)          # label -> [sim,...]
    inter = defaultdict(list)          # (labelA,labelB) -> [sim,...]
    video_intra_sims = defaultdict(list)  # path -> [与同标签其他视频的相似度,...]
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

    # 标签间混淆矩阵（平均相似度）
    confusion = {}
    for (la, lb), sims in inter.items():
        confusion[(la, lb)] = float(np.mean(sims))

    # 每个视频与其他各标签的平均相似度（用于"更佳标签"判断）
    video_other_means = {}   # path -> {other_label: mean_sim}
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
        # 跨标签吸引力：与某其他标签的平均相似度显著高于本组 → 疑似标错
        for m, sim_m in video_other_means.get(p, {}).items():
            if len(by_label[m]) >= 2 and sim_m >= mean_sim + 0.1:
                reasons.append("更接近标签『%s』（相似度 %.2f > 本组 %.2f）" % (m, sim_m, mean_sim))
                suggested = m
                break
        if reasons:
            suspects.append({"path": p, "label": lbl, "score": mean_sim,
                             "reason": "；".join(reasons), "suggested": suggested})
    suspects.sort(key=lambda s: s["score"])

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

    return {
        "labels": labels,
        "by_label": by_label,
        "label_stats": label_stats,
        "global_intra": global_intra,
        "global_inter": global_inter,
        "separation": global_intra - global_inter,
        "confusion": confusion,
        "suspects": suspects,
        "video_other_means": video_other_means,
        "video_intra_mean": video_intra_mean,
        "silhouette": silhouette,
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
# 5. 报告导出
# ============================================================
def _thumb_data_uri(frame_bgr, quality=70) -> str:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def write_report_md(out_path: str, result: dict, label_map: dict, sigs: dict, args, elapsed: float):
    lines = ["# 预标注一致性验证报告", "",
             "- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
             "- 扫描目录：`%s`" % args.dir,
             "- 标签来源：`%s`" % args.label_from,
             "- 抽帧数：%d / 视频；运动采样：%d 段 x %d 帧" % (args.frames, args.motion_bursts, args.burst_frames),
             "- 特征权重：帧哈希 %s + 运动能量 %s%s" % (args.hash_weight, args.motion_weight,
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

    lines.append("## 各标签一致性")
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
    lines.append("- 帧哈希衡量的是**画面外观**相似度；运动能量近似**行为强度**。"
                 "同标签视频若场景完全不同但行为相同（如不同街道的“行走”），"
                 "哈希相似度会偏低，属于预期现象，此时应结合运动能量与 CLIP 语义综合判断。")
    lines.append("- 本工具输出的是“预标注质量参考”，不能替代人工复核。")
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
            w.writerow(["（无）", "", "", ""])


def write_report_html(out_path: str, result: dict, label_map: dict, sigs: dict, args):
    """抽帧对比图：按标签分组展示每个视频的采样帧缩略图，疑似项标红"""
    suspect_set = {s["path"] for s in result["suspects"]}
    card_w = THUMB_WIDTH + 24
    html = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
            "<title>抽帧对比 — 预标注一致性验证</title><style>",
            "body{font-family:'Microsoft YaHei',sans-serif;margin:24px;background:#f7f8fa;}",
            "h1{font-size:22px;} h2{font-size:17px;margin:28px 0 8px;padding:6px 10px;"
            "background:#eef1f6;border-left:4px solid #4a78c2;}",
            "h2.bad{border-left-color:#d9534f;}",
            ".grid{display:flex;flex-wrap:wrap;gap:14px;}",
            ".card{background:#fff;border:1px solid #dde;border-radius:8px;padding:8px;width:%dpx;}" % card_w,
            ".card.suspect{border:2px solid #d9534f;background:#fff5f5;}",
            ".card img{width:100%;border-radius:4px;display:block;margin-bottom:4px;}",
            ".name{font-size:12px;word-break:break-all;}",
            ".meta{font-size:11px;color:#667;margin-top:4px;}",
            ".tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:8px;"
            "background:#4a78c2;color:#fff;margin-bottom:4px;}",
            ".tag.bad{background:#d9534f;}",
            "table{border-collapse:collapse;margin-top:8px;background:#fff;}",
            "td,th{border:1px solid #ccd;padding:4px 10px;font-size:12px;}",
            "</style></head><body>",
            "<h1>抽帧对比 — 预标注一致性验证</h1>",
            "<p>目录：<code>%s</code> ｜ 标签来源：<b>%s</b> ｜ "
            "组内平均相似度 <b>%.3f</b> ｜ 组间平均相似度 <b>%.3f</b> ｜ "
            "疑似不一致 <b style='color:#d9534f'>%d</b> 个</p>"
            % (args.dir, args.label_from, result["global_intra"], result["global_inter"],
               len(result["suspects"]))]

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

    for lbl in result["labels"]:
        st = result["label_stats"][lbl]
        v = verdict_of(st, result)
        bad = v.startswith("可疑") or any(s["label"] == lbl for s in result["suspects"])
        cls = "bad" if bad else ""
        intra = ("%.3f" % st["intra_mean"]) if st["intra_mean"] is not None else "—"
        html.append("<h2 class='%s'>标签：%s ｜ %d 个视频 ｜ 组内平均相似度 %s ｜ %s</h2><div class='grid'>"
                    % (cls, lbl, st["count"], intra, v))
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
                "<div class='card%s'>"
                "<span class='tag%s'>%s</span>"
                "%s<div class='name'>%s</div>"
                "<div class='meta'>运动能量 %.1f ｜ 组内相似度 %s%s</div></div>"
                % (" suspect" if is_suspect else "",
                   " bad" if is_suspect else "",
                   "疑似不一致" if is_suspect else lbl,
                   imgs, rel, sig["motion_mean"], vim_txt, extra))
        html.append("</div>")

    html.append("<p style='margin-top:30px;color:#889;font-size:12px'>"
                "注：帧哈希=画面外观相似；运动能量=行为强度近似（帧间差分）。"
                "标红卡片为疑似标注不一致，建议人工复核。</p></body></html>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html))


# ============================================================
# 6. 主流程
# ============================================================
def parse_args():
    ap = argparse.ArgumentParser(description="预标注一致性验证：抽帧对比同标签视频是否真的表现相同行为")
    ap.add_argument("--dir", required=True, help="视频根目录")
    ap.add_argument("--labels-csv", default="", help="标注 CSV（列：filename/path + label/标签）")
    ap.add_argument("--label-from", choices=["auto", "folder", "prefix", "csv", "regex"], default="auto",
                    help="预标注来源（默认 auto；regex=用 --label-regex 从文件名提取）")
    ap.add_argument("--label-regex", default="",
                    help=r"标签提取正则，第1个捕获组为标签，如 r'cam01_(.+?)-(?:pos|neg)'")
    ap.add_argument("--frames", type=int, default=8, help="每个视频抽帧数（默认 8）")
    ap.add_argument("--motion-bursts", type=int, default=3, help="运动采样段数（默认 3）")
    ap.add_argument("--burst-frames", type=int, default=6, help="每段连续帧数（默认 6）")
    ap.add_argument("--hash-weight", type=float, default=0.6, help="帧哈希权重（默认 0.6）")
    ap.add_argument("--motion-weight", type=float, default=0.4, help="运动能量权重（默认 0.4）")
    ap.add_argument("--clip-weight", type=float, default=0.5, help="CLIP 权重（--use-clip 时生效）")
    ap.add_argument("--use-clip", action="store_true", help="启用 CLIP 语义特征（需下载权重）")
    ap.add_argument("--suspect-threshold", type=float, default=0.5,
                    help="疑似不一致阈值：与同标签平均相似度低于此值即标记（默认 0.5）")
    ap.add_argument("--ext", default="mp4,mov,mkv,avi,webm,m4v,flv", help="视频后缀")
    ap.add_argument("--no-recursive", action="store_true", help="不递归子目录")
    ap.add_argument("--output-dir", default="", help="报告输出目录（默认 --dir 下 _label_verify）")
    return ap.parse_args()


def main():
    args = parse_args()
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
    log("      标签来源: %s ｜ 已标注 %d 个 ｜ 未标注 %d 个 ｜ 标签数 %d"
        % (used_mode, len(labeled), unlabeled, n_labels))
    if len(labeled) < 2 or n_labels < 1:
        log("[错误] 有效标注不足 2 个，无法进行一致性验证")
        return EXIT_BAD_ARGS

    # 特征提取
    log("[2/4] 抽帧与特征提取（%d 帧/视频, 运动采样 %dx%d）..."
        % (args.frames, args.motion_bursts, args.burst_frames))
    clip_on = args.use_clip and _clip_check()
    sigs, errors = {}, []
    total_steps = len(videos)
    done = 0
    for v in videos:
        done += 1
        if not label_map.get(v["path"]):
            continue  # 未标注视频不参与验证
        sig = compute_video_signature(v["path"], args.frames, args.motion_bursts, args.burst_frames)
        if not sig["ok"]:
            errors.append({"path": v["path"], "err": sig["err"]})
            log("  [失败] %s: %s" % (v["name"], sig["err"]))
            continue
        if clip_on and sig["thumbs"]:
            try:
                add_clip_embedding(sig, sig["thumbs"])
            except Exception:
                pass  # 单视频 CLIP 失败不阻塞
        sigs[v["path"]] = sig
        if done % 20 == 0 or done == total_steps:
            log("  进度 %d/%d" % (done, total_steps))

    if len(sigs) < 2:
        log("[错误] 成功解析的有效视频不足 2 个")
        return EXIT_BAD_ARGS if not errors else EXIT_PARSE_ERROR

    # 两两比对
    log("[3/4] 两两相似度比对...")
    w_hash, w_motion = args.hash_weight, args.motion_weight
    w_clip = args.clip_weight if clip_on else 0.0
    w_total = w_hash + w_motion + w_clip
    if w_total <= 0:
        log("[错误] 特征权重之和必须 > 0")
        return EXIT_BAD_ARGS
    motion_logs = [float(np.log1p(max(0.0, s["motion_mean"]))) for s in sigs.values()]
    motion_scale = max(0.5, 2.0 * float(np.std(motion_logs)))  # 稳健尺度：2 倍对数标准差
    paths = sorted(sigs.keys())
    pairs = {}
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            sim = (w_hash * hash_similarity(sigs[a], sigs[b]) +
                   w_motion * motion_similarity(sigs[a], sigs[b], motion_scale)) / w_total
            if clip_on:
                sim = sim + w_clip * clip_similarity(sigs[a], sigs[b]) / w_total
            pairs[(a, b)] = max(0.0, min(1.0, sim))

    # 分析与报告
    log("[4/4] 一致性分析与报告生成...")
    result = analyze(label_map, sigs, pairs, args)

    out_dir = os.path.abspath(args.output_dir) if args.output_dir else os.path.join(root, "_label_verify")
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, REPORT_MD)
    csv_path = os.path.join(out_dir, REPORT_CSV)
    html_path = os.path.join(out_dir, REPORT_HTML)
    write_report_md(md_path, result, label_map, sigs, args, time.time() - t0)
    write_suspects_csv(csv_path, result)
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
