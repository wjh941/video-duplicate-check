# -*- coding: utf-8 -*-
"""
AI 语义分析模块 - 用于 find_mp4.py v2.2
==========================================

功能：基于 CLIP 视觉模型的视频内容语义分析、场景分类、用途判定、
质量评估、语义聚类与数据集导出。

依赖：torch, open_clip, scikit-learn（均为可选，自动降级）

使用示例：
    from ai_semantic import (
        load_clip_model, semantic_analyze_video,
        cluster_videos_by_semantic, export_dataset_catalog,
    )

    model, preprocess, device = load_clip_model()
    result = semantic_analyze_video("test.mp4", model, preprocess, device)
"""

import os
import time
import json
import csv
import math
from collections import defaultdict
from typing import Optional, Union

import numpy as np

# ============================================================
# 1. 依赖检测（try/except 优雅降级）
# ============================================================

TORCH_OK = False
CLIP_OK = False
SKLEARN_OK = False
CUDA_OK = False

try:
    import torch
    TORCH_OK = True
    try:
        import open_clip
        CLIP_OK = True
    except ImportError:
        pass
except ImportError:
    pass

try:
    from sklearn.cluster import KMeans
    SKLEARN_OK = True
except ImportError:
    pass

if TORCH_OK:
    try:
        CUDA_OK = torch.cuda.is_available()
    except Exception:
        CUDA_OK = False

# CLIP 模型全局引用（延迟加载）
_clip_model = None
_clip_preprocess = None
_clip_device = "cuda" if CUDA_OK else "cpu"


# ============================================================
# 2. AI 标签库（内置中文标签）
# ============================================================

SCENE_TAGS = [
    "室内楼道", "室外街道", "停车场", "小区", "办公室",
    "教室", "户外公园", "车内", "夜晚监控", "夜晚暗光",
]

OBJECT_TAGS = [
    "行人", "电动车", "轿车", "货车", "监控设备",
    "桌椅", "绿植", "猫狗", "人脸",
]

ACTION_TAGS = [
    "行走", "跑动", "静止", "骑车",
]

PURPOSE_RULES = {
    "监控训练集": ["行人", "监控", "楼道", "道路"],
    "自动驾驶数据集": ["车辆", "路面", "车流"],
    "人像素材": ["人脸", "人物", "特写"],
    "影视素材": ["电影", "剧情", "镜头"],
    "风景素材": ["自然", "山水", "天空", "无人物"],
    "游戏录屏": ["游戏", "UI", "角色"],
}

# CLIP 中文标签映射（用于与模型输出的英文标签做语义桥接）
SCENE_CLIP_MAP = {
    "室内楼道": ["indoor hallway", "corridor", "stairs", "building interior"],
    "室外街道": ["outdoor street", "road", "city street", "sidewalk"],
    "停车场": ["parking lot", "parking garage", "car park"],
    "小区": ["residential area", "apartment", "community", "housing estate"],
    "办公室": ["office", "workspace", "desk", "meeting room"],
    "教室": ["classroom", "lecture hall", "school", "university"],
    "户外公园": ["park", "outdoor", "garden", "forest", "nature"],
    "车内": ["inside car", "vehicle interior", "dashboard", "car cockpit"],
    "夜晚监控": ["night surveillance", "night scene", "dark", "CCTV"],
    "夜晚暗光": ["low light", "night", "dark scene", "dim"],
}

OBJECT_CLIP_MAP = {
    "行人": ["person", "pedestrian", "walking people"],
    "电动车": ["electric bike", "e-bike", "scooter", "motorcycle"],
    "轿车": ["car", "sedan", "automobile", "vehicle"],
    "货车": ["truck", "van", "lorry", "delivery vehicle"],
    "监控设备": ["camera", "surveillance camera", "webcam"],
    "桌椅": ["desk", "chair", "table", "furniture"],
    "绿植": ["plant", "tree", "flower", "greenery"],
    "猫狗": ["dog", "cat", "pet", "animal"],
    "人脸": ["face", "portrait", "close-up face", "person face"],
}

ACTION_CLIP_MAP = {
    "行走": ["walking", "walk", "strolling"],
    "跑动": ["running", "sprinting", "jogging"],
    "静止": ["standing", "still", "not moving", "static"],
    "骑车": ["cycling", "biking", "riding bicycle", "riding bike"],
}

# 所有标签汇总（用于 CLIP 文本编码）
ALL_CLIP_LABELS = []
ALL_LABEL_NAMES = []

for name, eng_list in SCENE_CLIP_MAP.items():
    for eng in eng_list:
        ALL_CLIP_LABELS.append(eng)
        ALL_LABEL_NAMES.append(("scene", name))

for name, eng_list in OBJECT_CLIP_MAP.items():
    for eng in eng_list:
        ALL_CLIP_LABELS.append(eng)
        ALL_LABEL_NAMES.append(("object", name))

for name, eng_list in ACTION_CLIP_MAP.items():
    for eng in eng_list:
        ALL_CLIP_LABELS.append(eng)
        ALL_LABEL_NAMES.append(("action", name))


# ============================================================
# 3. 核心函数
# ============================================================

def load_clip_model() -> tuple:
    """
    延迟加载 ViT-B/32 模型，使用全局变量缓存，支持 CPU/GPU。
    返回 (model, preprocess, device)，失败返回 (None, None, "cpu")。
    """
    global _clip_model, _clip_preprocess, _clip_device

    if _clip_model is not None:
        return _clip_model, _clip_preprocess, _clip_device

    if not TORCH_OK or not CLIP_OK:
        return None, None, "cpu"

    try:
        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32",
            pretrained="laion2b_s34b_b79k",
            device=_clip_device,
        )
        _clip_model = model
        _clip_preprocess = preprocess
        return _clip_model, _clip_preprocess, _clip_device
    except Exception:
        try:
            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32",
                pretrained="laion400m_e31",
                device=_clip_device,
            )
            _clip_model = model
            _clip_preprocess = preprocess
            return _clip_model, _clip_preprocess, _clip_device
        except Exception:
            return None, None, "cpu"


def extract_frame_features(
    frames_list: list,
    model,
    preprocess,
    device: str,
) -> Optional[np.ndarray]:
    """
    接收 PIL Image 列表，批量提取 CLIP 特征向量。
    返回 numpy array（shape: [N, 512]），失败返回 None。
    """
    if not TORCH_OK or model is None or preprocess is None:
        return None

    if not frames_list:
        return None

    try:
        batch_inputs = []
        for img in frames_list:
            if img.mode != "RGB":
                img = img.convert("RGB")
            batch_inputs.append(preprocess(img))

        batch_tensor = torch.stack(batch_inputs).to(device)

        with torch.no_grad():
            features = model.encode_image(batch_tensor)
            features = features / features.norm(dim=-1, keepdim=True)

        return features.cpu().numpy()
    except Exception:
        return None


def classify_video(
    video_path: str,
    num_frames: int,
    model,
    preprocess,
    device: str,
    scene_thresh: float = 0.6,
) -> dict:
    """
    读取视频指定帧，提取特征，计算与各标签的相似度。
    返回 {"scene": [...], "object": [...], "action": [...], "confidence": float}
    """
    if not TORCH_OK or model is None:
        return {
            "scene": [],
            "object": [],
            "action": [],
            "confidence": 0.0,
        }

    try:
        import cv2
    except ImportError:
        return {"scene": [], "object": [], "action": [], "confidence": 0.0}

    frames = _sample_video_frames(video_path, num_frames)
    if not frames:
        return {"scene": [], "object": [], "action": [], "confidence": 0.0}

    features = extract_frame_features(frames, model, preprocess, device)
    if features is None:
        return {"scene": [], "object": [], "action": [], "confidence": 0.0}

    # 聚合帧特征（取平均）
    agg_feature = features.mean(axis=0)
    agg_feature = agg_feature / (np.linalg.norm(agg_feature) + 1e-8)

    # 计算与所有文本标签的相似度
    text_features = _get_text_features(model, device)
    if text_features is None:
        return {"scene": [], "object": [], "action": [], "confidence": 0.0}

    similarities = np.dot(agg_feature, text_features.T)  # shape: [num_labels]

    # 按类型分组取 top3
    scene_scores = defaultdict(list)
    object_scores = defaultdict(list)
    action_scores = defaultdict(list)

    for idx, (label_type, label_name) in enumerate(ALL_LABEL_NAMES):
        score = float(similarities[idx])
        if label_type == "scene":
            scene_scores[label_name].append(score)
        elif label_type == "object":
            object_scores[label_name].append(score)
        elif label_type == "action":
            action_scores[label_name].append(score)

    def _top_labels(scores_dict: dict, top_k: int = 3) -> list:
        avg_scores = [(name, np.mean(scores)) for name, scores in scores_dict.items()]
        avg_scores.sort(key=lambda x: x[1], reverse=True)
        return [
            {"label": name, "score": round(score, 4)}
            for name, score in avg_scores[:top_k]
        ]

    scene_top = _top_labels(scene_scores)
    object_top = _top_labels(object_scores)
    action_top = _top_labels(action_scores)

    confidence = 0.0
    if scene_top:
        confidence = scene_top[0]["score"]

    return {
        "scene": scene_top,
        "object": object_top,
        "action": action_top,
        "confidence": round(confidence, 4),
    }


# 文本特征缓存
_text_features_cache = None
_text_labels_list_cache = None


def _get_text_features(model, device: str) -> Optional[np.ndarray]:
    """获取所有标签的文本特征向量（带缓存）"""
    global _text_features_cache, _text_labels_list_cache

    if _text_features_cache is not None:
        return _text_features_cache

    if not TORCH_OK or model is None:
        return None

    try:
        tokenizer = open_clip.get_tokenizer("ViT-B-32")
        texts = tokenizer(ALL_CLIP_LABELS)
        texts = texts.to(device)

        with torch.no_grad():
            text_features = model.encode_text(texts)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)

        _text_features_cache = text_features.cpu().numpy()
        return _text_features_cache
    except Exception:
        return None


def _sample_video_frames(video_path: str, num_frames: int) -> list:
    """从视频中均匀采样帧，返回 PIL Image 列表"""
    try:
        import cv2
        from PIL import Image as PILImage
    except ImportError:
        return []

    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 25.0

        start_ratio, end_ratio = 0.1, 0.9
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

        frames = []
        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            if frame is None or frame.size == 0:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = PILImage.fromarray(rgb)
            frames.append(pil_img)

        return frames
    except Exception:
        return []
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def judge_dataset_purpose(
    scene_tags: list,
    object_tags: list,
    action_tags: list,
) -> str:
    """
    根据标签规则判定数据集用途。
    输入标签格式：[{"label": "xxx", "score": 0.xx}, ...]
    返回用途名称，无法匹配返回 "未知"。
    """
    all_labels = set()
    for tag_list in [scene_tags, object_tags, action_tags]:
        for item in tag_list:
            if isinstance(item, dict) and "label" in item:
                all_labels.add(item["label"])
            elif isinstance(item, str):
                all_labels.add(item)

    best_purpose = "未知"
    best_match_count = 0

    for purpose, keywords in PURPOSE_RULES.items():
        match_count = sum(1 for kw in keywords if kw in all_labels)
        if match_count > best_match_count:
            best_match_count = match_count
            best_purpose = purpose

    if best_match_count == 0:
        return "未知"

    return best_purpose


def compute_quality_score(frames_list: list) -> float:
    """
    计算视频质量：清晰度(方差)、亮度、主体占比、抖动程度。
    接收 PIL Image 列表，返回 0-1 分数。
    """
    if not frames_list:
        return 0.0

    scores = []

    # 1. 清晰度：灰度方差
    sharpness_scores = []
    for img in frames_list:
        try:
            gray = np.array(img.convert("L"))
            variance = np.var(gray)
            # 方差越大越清晰，归一化到 0-1（以500为参考）
            sharp = min(1.0, variance / 500.0)
            sharpness_scores.append(sharp)
        except Exception:
            sharpness_scores.append(0.0)

    avg_sharpness = np.mean(sharpness_scores) if sharpness_scores else 0.0

    # 2. 亮度：平均灰度值
    brightness_scores = []
    for img in frames_list:
        try:
            gray = np.array(img.convert("L"))
            mean_val = np.mean(gray)
            # 亮度适中为好（128左右最佳）
            brightness_score = 1.0 - abs(mean_val - 128) / 128.0
            brightness_score = max(0.0, brightness_score)
            brightness_scores.append(brightness_score)
        except Exception:
            brightness_scores.append(0.0)

    avg_brightness = np.mean(brightness_scores) if brightness_scores else 0.0

    # 3. 主体占比：基于边缘密度估计
    subject_scores = []
    for img in frames_list:
        try:
            arr = np.array(img.convert("L"))
            edges = np.abs(np.diff(arr.astype(float)))
            edge_density = np.mean(edges) / 255.0
            # 边缘密度适中表示有内容
            subject_score = min(1.0, edge_density * 8)
            subject_scores.append(subject_score)
        except Exception:
            subject_scores.append(0.0)

    avg_subject = np.mean(subject_scores) if subject_scores else 0.0

    # 4. 抖动程度：帧间差异
    jitter_score = 0.5
    if len(frames_list) >= 2:
        diffs = []
        for i in range(1, len(frames_list)):
            try:
                arr1 = np.array(frames_list[i - 1].convert("L")).astype(float)
                arr2 = np.array(frames_list[i].convert("L")).astype(float)
                diff = np.mean(np.abs(arr1 - arr2)) / 255.0
                diffs.append(diff)
            except Exception:
                pass
        if diffs:
            avg_diff = np.mean(diffs)
            # 抖动过大或过小都不好，适中为好
            jitter_score = 1.0 - min(1.0, abs(avg_diff - 0.02) * 20)
            jitter_score = max(0.0, jitter_score)

    # 加权综合
    quality = (
        0.35 * avg_sharpness
        + 0.25 * avg_brightness
        + 0.25 * avg_subject
        + 0.15 * jitter_score
    )

    return round(max(0.0, min(1.0, quality)), 4)


def semantic_analyze_video(
    video_path: str,
    model,
    preprocess,
    device: str,
    num_frames: int = 10,
    scene_thresh: float = 0.6,
    cached_frames: list = None,  # v2.3 新增：帧复用，避免重复解码
) -> dict:
    """
    主入口：对单个视频进行完整语义分析。
    v2.3 增强：支持 cached_frames 帧复用，避免重复打开视频解码。
    返回 dict:
    {
        "scene_tags": [...],
        "object_tags": [...],
        "action_tags": [...],
        "dataset_purpose": str,
        "semantic_emb": list (numpy array),
        "semantic_conf": float,
        "quality_score": float,
        "is_training_ready": bool,
    }
    """
    if not TORCH_OK or model is None:
        return {
            "scene_tags": [],
            "object_tags": [],
            "action_tags": [],
            "dataset_purpose": "未知",
            "semantic_emb": None,
            "semantic_conf": 0.0,
            "quality_score": 0.0,
            "is_training_ready": False,
        }

    # v2.3 新增：帧复用 - 如果已有缓存的 PIL 帧直接使用，避免重复解码
    if cached_frames and len(cached_frames) > 0:
        frames = cached_frames
    else:
        # 1. 采样帧
        frames = _sample_video_frames(video_path, num_frames)
    if not frames:
        return {
            "scene_tags": [],
            "object_tags": [],
            "action_tags": [],
            "dataset_purpose": "未知",
            "semantic_emb": None,
            "semantic_conf": 0.0,
            "quality_score": 0.0,
            "is_training_ready": False,
        }

    # 2. 提取特征
    features = extract_frame_features(frames, model, preprocess, device)

    # 3. 分类
    cls_result = classify_video(
        video_path, num_frames, model, preprocess, device, scene_thresh
    )

    # 4. 用途判定
    purpose = judge_dataset_purpose(
        cls_result["scene"], cls_result["object"], cls_result["action"]
    )

    # 5. 质量评分
    quality = compute_quality_score(frames)

    # 6. 是否适合训练
    is_ready = (
        cls_result["confidence"] >= scene_thresh
        and quality >= 0.4
        and purpose != "未知"
    )

    # 聚合特征
    semantic_emb = None
    if features is not None:
        semantic_emb = features.mean(axis=0).tolist()

    return {
        "scene_tags": cls_result["scene"],
        "object_tags": cls_result["object"],
        "action_tags": cls_result["action"],
        "dataset_purpose": purpose,
        "semantic_emb": semantic_emb,
        "semantic_conf": cls_result["confidence"],
        "quality_score": quality,
        "is_training_ready": is_ready,
    }


# ============================================================
# 4. 聚类函数
# ============================================================

def auto_cluster_count(n_samples: int) -> int:
    """自动计算聚类数 sqrt(n)/2"""
    if n_samples <= 0:
        return 1
    return max(1, int(math.sqrt(n_samples) / 2))


def cluster_videos_by_semantic(
    semantic_data: list,
    n_clusters: Optional[int] = None,
) -> dict:
    """
    使用 KMeans 对特征向量聚类。
    semantic_data: [{"path": str, "semantic_emb": list}, ...]
    返回 {cluster_id: [video_paths]}
    """
    if not SKLEARN_OK:
        return {}

    valid_items = []
    embeddings = []
    for item in semantic_data:
        emb = item.get("semantic_emb")
        if emb is not None and isinstance(emb, (list, np.ndarray)) and len(emb) > 0:
            valid_items.append(item)
            embeddings.append(np.array(emb, dtype=np.float32))

    if not valid_items:
        return {}

    X = np.array(embeddings)

    if n_clusters is None:
        n_clusters = auto_cluster_count(len(valid_items))

    n_clusters = min(n_clusters, len(valid_items))

    try:
        kmeans = KMeans(
            n_clusters=n_clusters,
            n_init=10,
            random_state=42,
            max_iter=300,
        )
        labels = kmeans.fit_predict(X)

        clusters = defaultdict(list)
        for i, item in enumerate(valid_items):
            cluster_id = int(labels[i])
            clusters[cluster_id].append(item["path"])

        return dict(clusters)
    except Exception:
        return {}


# ============================================================
# 5. 数据集导出函数
# ============================================================

def export_dataset_catalog(
    semantic_data: list,
    catalog_path: str,
) -> None:
    """
    导出 CSV：路径、大小、场景标签、用途、置信度、质量分、是否适合训练。
    semantic_data: [{"path": str, "size": int, ...}, ...]
    """
    try:
        with open(catalog_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "路径", "大小(MB)", "场景标签", "物体标签",
                "行为标签", "数据集用途", "语义置信度",
                "质量分", "适合训练",
            ])
            for item in semantic_data:
                path = item.get("path", "")
                size_mb = round(item.get("size", 0) / (1024 * 1024), 2)
                scene = _tag_names(item.get("scene_tags", []))
                obj = _tag_names(item.get("object_tags", []))
                action = _tag_names(item.get("action_tags", []))
                purpose = item.get("dataset_purpose", "未知")
                conf = item.get("semantic_conf", 0.0)
                quality = item.get("quality_score", 0.0)
                ready = "是" if item.get("is_training_ready", False) else "否"

                writer.writerow([
                    path, size_mb, scene, obj, action,
                    purpose, conf, quality, ready,
                ])
    except (IOError, OSError):
        pass


def export_train_sample_list(
    semantic_data: list,
    output_path: str,
    purpose_filter: Optional[str] = None,
) -> None:
    """
    导出纯路径清单（可按用途筛选）。
    """
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            for item in semantic_data:
                if purpose_filter and item.get("dataset_purpose") != purpose_filter:
                    continue
                if not item.get("is_training_ready", False):
                    continue
                f.write(item.get("path", "") + "\n")
    except (IOError, OSError):
        pass


def export_dataset_stats(
    semantic_data: list,
    stats_path: str,
) -> None:
    """导出统计 Markdown"""
    try:
        total = len(semantic_data)
        if total == 0:
            lines = ["# 数据集统计", "", "暂无数据。"]
        else:
            purpose_counts = defaultdict(int)
            scene_counts = defaultdict(int)
            ready_count = 0
            quality_sum = 0.0
            conf_sum = 0.0

            for item in semantic_data:
                purpose = item.get("dataset_purpose", "未知")
                purpose_counts[purpose] += 1
                if item.get("is_training_ready", False):
                    ready_count += 1
                quality_sum += item.get("quality_score", 0.0)
                conf_sum += item.get("semantic_conf", 0.0)
                for st in item.get("scene_tags", []):
                    if isinstance(st, dict):
                        scene_counts[st.get("label", "")] += 1

            lines = [
                "# 数据集统计报告",
                "",
                f"- **生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"- **视频总数**: {total}",
                f"- **适合训练**: {ready_count} ({round(ready_count / total * 100, 1) if total > 0 else 0}%)",
                f"- **平均质量分**: {round(quality_sum / total, 3)}",
                f"- **平均语义置信度**: {round(conf_sum / total, 3)}",
                "",
                "## 按用途分布",
                "",
                "| 用途 | 数量 | 占比 |",
                "|------|------|------|",
            ]
            for purpose, count in sorted(purpose_counts.items(), key=lambda x: -x[1]):
                pct = round(count / total * 100, 1)
                lines.append(f"| {purpose} | {count} | {pct}% |")

            lines.extend(["", "## 场景标签分布", "", "| 场景 | 出现次数 |", "|------|----------|"])
            for scene, count in sorted(scene_counts.items(), key=lambda x: -x[1])[:15]:
                if scene:
                    lines.append(f"| {scene} | {count} |")

        with open(stats_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except (IOError, OSError):
        pass


def export_scene_cluster_html(
    clusters: dict,
    semantic_data: list,
    html_path: str,
) -> None:
    """
    导出聚类可视化 HTML 报告（含完整 CSS 样式和交互折叠）。
    """
    try:
        # 构建路径 -> 数据 索引
        path_index = {}
        for item in semantic_data:
            path_index[item.get("path", "")] = item

        total_clusters = len(clusters)
        total_videos = sum(len(paths) for paths in clusters.values())

        # 颜色方案
        color_palette = [
            "#4CAF50", "#2196F3", "#FF9800", "#E91E63", "#9C27B0",
            "#00BCD4", "#FF5722", "#795548", "#607D8B", "#3F51B5",
            "#8BC34A", "#CDDC39", "#FFC107", "#FFEB3B", "#F44336",
        ]

        html_parts = [
            "<!DOCTYPE html>\n",
            "<html lang='zh-CN'>\n",
            "<head>\n",
            "<meta charset='UTF-8'>\n",
            "<meta name='viewport' content='width=device-width, initial-scale=1.0'>\n",
            "<title>视频语义聚类报告</title>\n",
            "<style>\n",
            "* { box-sizing: border-box; margin: 0; padding: 0; }\n",
            "body {\n",
            "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;\n",
            "  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n",
            "  min-height: 100vh;\n",
            "  padding: 20px;\n",
            "}\n",
            ".container {\n",
            "  max-width: 1200px;\n",
            "  margin: 0 auto;\n",
            "  background: #fff;\n",
            "  border-radius: 16px;\n",
            "  box-shadow: 0 20px 60px rgba(0,0,0,0.3);\n",
            "  overflow: hidden;\n",
            "}\n",
            ".header {\n",
            "  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n",
            "  color: white;\n",
            "  padding: 30px;\n",
            "  text-align: center;\n",
            "}\n",
            ".header h1 { font-size: 28px; margin-bottom: 10px; }\n",
            ".header p { opacity: 0.9; font-size: 14px; }\n",
            ".stats {\n",
            "  display: grid;\n",
            "  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));\n",
            "  gap: 15px;\n",
            "  padding: 25px;\n",
            "  background: #f8f9fa;\n",
            "  border-bottom: 1px solid #eee;\n",
            "}\n",
            ".stat-card {\n",
            "  background: white;\n",
            "  border-radius: 12px;\n",
            "  padding: 20px;\n",
            "  text-align: center;\n",
            "  box-shadow: 0 2px 8px rgba(0,0,0,0.08);\n",
            "}\n",
            ".stat-card .number {\n",
            "  font-size: 32px;\n",
            "  font-weight: bold;\n",
            "  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n",
            "  -webkit-background-clip: text;\n",
            "  -webkit-text-fill-color: transparent;\n",
            "}\n",
            ".stat-card .label {\n",
            "  font-size: 14px;\n",
            "  color: #666;\n",
            "  margin-top: 5px;\n",
            "}\n",
            ".toolbar {\n",
            "  padding: 15px 25px;\n",
            "  background: #f8f9fa;\n",
            "  border-bottom: 1px solid #eee;\n",
            "  display: flex;\n",
            "  gap: 10px;\n",
            "  flex-wrap: wrap;\n",
            "  align-items: center;\n",
            "}\n",
            ".toolbar input[type=text] {\n",
            "  flex: 1;\n",
            "  min-width: 200px;\n",
            "  padding: 10px 15px;\n",
            "  border: 2px solid #e0e0e0;\n",
            "  border-radius: 8px;\n",
            "  font-size: 14px;\n",
            "  transition: border-color 0.3s;\n",
            "}\n",
            ".toolbar input[type=text]:focus {\n",
            "  outline: none;\n",
            "  border-color: #667eea;\n",
            "}\n",
            ".toolbar button {\n",
            "  padding: 10px 20px;\n",
            "  border: none;\n",
            "  border-radius: 8px;\n",
            "  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n",
            "  color: white;\n",
            "  font-size: 14px;\n",
            "  cursor: pointer;\n",
            "  transition: transform 0.2s, box-shadow 0.2s;\n",
            "}\n",
            ".toolbar button:hover {\n",
            "  transform: translateY(-2px);\n",
            "  box-shadow: 0 4px 12px rgba(102,126,234,0.4);\n",
            "}\n",
            ".clusters {\n",
            "  padding: 20px;\n",
            "  max-height: 70vh;\n",
            "  overflow-y: auto;\n",
            "}\n",
            ".cluster {\n",
            "  margin-bottom: 15px;\n",
            "  border-radius: 12px;\n",
            "  overflow: hidden;\n",
            "  box-shadow: 0 2px 12px rgba(0,0,0,0.1);\n",
            "  transition: box-shadow 0.3s;\n",
            "}\n",
            ".cluster:hover {\n",
            "  box-shadow: 0 4px 20px rgba(0,0,0,0.15);\n",
            "}\n",
            ".cluster-header {\n",
            "  padding: 15px 20px;\n",
            "  color: white;\n",
            "  cursor: pointer;\n",
            "  display: flex;\n",
            "  align-items: center;\n",
            "  justify-content: space-between;\n",
            "  user-select: none;\n",
            "  transition: opacity 0.2s;\n",
            "}\n",
            ".cluster-header:hover { opacity: 0.9; }\n",
            ".cluster-header .title {\n",
            "  font-size: 16px;\n",
            "  font-weight: 600;\n",
            "}\n",
            ".cluster-header .count {\n",
            "  background: rgba(255,255,255,0.3);\n",
            "  padding: 3px 10px;\n",
            "  border-radius: 12px;\n",
            "  font-size: 13px;\n",
            "}\n",
            ".cluster-header .arrow {\n",
            "  transition: transform 0.3s;\n",
            "  font-size: 12px;\n",
            "}\n",
            ".cluster.collapsed .arrow { transform: rotate(-90deg); }\n",
            ".cluster-body {\n",
            "  max-height: 2000px;\n",
            "  overflow: hidden;\n",
            "  transition: max-height 0.5s ease;\n",
            "  background: #fff;\n",
            "}\n",
            ".cluster.collapsed .cluster-body {\n",
            "  max-height: 0;\n",
            "}\n",
            ".video-list {\n",
            "  list-style: none;\n",
            "  padding: 10px 0;\n",
            "}\n",
            ".video-item {\n",
            "  padding: 10px 20px;\n",
            "  border-bottom: 1px solid #f0f0f0;\n",
            "  display: flex;\n",
            "  align-items: center;\n",
            "  gap: 15px;\n",
            "  transition: background 0.2s;\n",
            "}\n",
            ".video-item:hover { background: #f8f9ff; }\n",
            ".video-item:last-child { border-bottom: none; }\n",
            ".video-item .name {\n",
            "  flex: 1;\n",
            "  font-size: 13px;\n",
            "  color: #333;\n",
            "  word-break: break-all;\n",
            "}\n",
            ".video-item .meta {\n",
            "  display: flex;\n",
            "  gap: 8px;\n",
            "  flex-shrink: 0;\n",
            "}\n",
            ".badge {\n",
            "  padding: 3px 8px;\n",
            "  border-radius: 10px;\n",
            "  font-size: 11px;\n",
            "  font-weight: 500;\n",
            "}\n",
            ".badge-conf { background: #E3F2FD; color: #1565C0; }\n",
            ".badge-quality { background: #E8F5E9; color: #2E7D32; }\n",
            ".badge-ready { background: #FFF3E0; color: #E65100; }\n",
            ".badge-purpose { background: #F3E5F5; color: #6A1B9A; }\n",
            ".empty-state {\n",
            "  text-align: center;\n",
            "  padding: 60px 20px;\n",
            "  color: #999;\n",
            "}\n",
            ".footer {\n",
            "  text-align: center;\n",
            "  padding: 20px;\n",
            "  background: #f8f9fa;\n",
            "  color: #666;\n",
            "  font-size: 12px;\n",
            "}\n",
            "</style>\n",
            "</head>\n",
            "<body>\n",
            "<div class='container'>\n",
            "<div class='header'>\n",
            "<h1>🎬 视频语义聚类报告</h1>\n",
            f"<p>基于 CLIP 视觉特征的视频内容聚类分析</p>\n",
            "</div>\n",
            "<div class='stats'>\n",
            f"<div class='stat-card'><div class='number'>{total_clusters}</div><div class='label'>聚类数</div></div>\n",
            f"<div class='stat-card'><div class='number'>{total_videos}</div><div class='label'>视频总数</div></div>\n",
            f"<div class='stat-card'><div class='number'>{round(total_videos / max(1, total_clusters), 1)}</div><div class='label'>平均每类</div></div>\n",
            f"<div class='stat-card'><div class='number'>{time.strftime('%Y-%m-%d')}</div><div class='label'>生成日期</div></div>\n",
            "</div>\n",
            "<div class='toolbar'>\n",
            "<input type='text' id='searchInput' placeholder='🔍 搜索视频名称或标签...' onkeyup='filterClusters()'>\n",
            "<button onclick='expandAll()'>展开全部</button>\n",
            "<button onclick='collapseAll()'>折叠全部</button>\n",
            "</div>\n",
            "<div class='clusters' id='clusters'>\n",
        ]

        if not clusters:
            html_parts.append("<div class='empty-state'>暂无聚类数据</div>\n")
        else:
            for cluster_id in sorted(clusters.keys()):
                paths = clusters[cluster_id]
                color = color_palette[cluster_id % len(color_palette)]

                # 获取该聚类的代表标签
                cluster_tags = defaultdict(int)
                for p in paths:
                    item = path_index.get(p, {})
                    for st in item.get("scene_tags", []):
                        if isinstance(st, dict):
                            cluster_tags[st.get("label", "")] += 1
                top_tag = ""
                if cluster_tags:
                    top_tag = max(cluster_tags, key=cluster_tags.get)

                html_parts.append(
                    f"<div class='cluster' id='cluster-{cluster_id}'>\n"
                    f"<div class='cluster-header' style='background:{color}' "
                    f"onclick='toggleCluster({cluster_id})'>\n"
                    f"<span class='title'>聚类 #{cluster_id}: {top_tag or '未命名'}</span>\n"
                    f"<span><span class='count'>{len(paths)} 个视频</span> "
                    f"<span class='arrow'>▼</span></span>\n"
                    f"</div>\n"
                    f"<div class='cluster-body'>\n"
                    f"<ul class='video-list'>\n"
                )

                for p in paths:
                    item = path_index.get(p, {})
                    name = os.path.basename(p) if p else "unknown"
                    conf = item.get("semantic_conf", 0.0)
                    quality = item.get("quality_score", 0.0)
                    purpose = item.get("dataset_purpose", "")
                    ready = item.get("is_training_ready", False)

                    ready_badge = ""
                    if ready:
                        ready_badge = '<span class="badge badge-ready">✅ 可训练</span>'
                    purpose_badge = ""
                    if purpose and purpose != "未知":
                        purpose_badge = '<span class="badge badge-purpose">' + purpose + '</span>'
                    html_parts.append(
                        f"<li class='video-item'>\n"
                        f"<span class='name' title='{p}'>📹 {name}</span>\n"
                        f"<span class='meta'>\n"
                        f"<span class='badge badge-conf'>置信 {conf:.2f}</span>\n"
                        f"<span class='badge badge-quality'>质量 {quality:.2f}</span>\n"
                        f"{ready_badge}\n"
                        f"{purpose_badge}\n"
                        f"</span>\n"
                        f"</li>\n"
                    )

                html_parts.append("</ul>\n</div>\n</div>\n")

        html_parts.extend([
            "</div>\n",
            "<div class='footer'>\n",
            f"共 {total_clusters} 个聚类 · {total_videos} 个视频 · 由 AI 语义分析模块生成\n",
            "</div>\n",
            "</div>\n",
            "<script>\n",
            "function toggleCluster(id) {\n",
            "  var cluster = document.getElementById('cluster-' + id);\n",
            "  if (cluster) cluster.classList.toggle('collapsed');\n",
            "}\n",
            "function expandAll() {\n",
            "  document.querySelectorAll('.cluster').forEach(function(c) {\n",
            "    c.classList.remove('collapsed');\n",
            "  });\n",
            "}\n",
            "function collapseAll() {\n",
            "  document.querySelectorAll('.cluster').forEach(function(c) {\n",
            "    c.classList.add('collapsed');\n",
            "  });\n",
            "}\n",
            "function filterClusters() {\n",
            "  var input = document.getElementById('searchInput').value.toLowerCase();\n",
            "  document.querySelectorAll('.cluster').forEach(function(cluster) {\n",
            "    var items = cluster.querySelectorAll('.video-item');\n",
            "    var hasVisible = false;\n",
            "    items.forEach(function(item) {\n",
            "      var text = item.textContent.toLowerCase();\n",
            "      var match = text.indexOf(input) > -1;\n",
            "      item.style.display = match ? '' : 'none';\n",
            "      if (match) hasVisible = true;\n",
            "    });\n",
            "    cluster.style.display = hasVisible ? '' : 'none';\n",
            "  });\n",
            "}\n",
            "// 默认折叠所有，只展示概览\n",
            "document.addEventListener('DOMContentLoaded', function() {\n",
            "  document.querySelectorAll('.cluster').forEach(function(c) {\n",
            "    c.classList.add('collapsed');\n",
            "  });\n",
            "});\n",
            "</script>\n",
            "</body>\n",
            "</html>",
        ])

        with open(html_path, "w", encoding="utf-8") as f:
            f.writelines(html_parts)
    except (IOError, OSError):
        pass


# ============================================================
# 6. 工具函数
# ============================================================

def _tag_names(tag_list: list) -> str:
    """从标签列表提取名称字符串"""
    names = []
    for item in tag_list:
        if isinstance(item, dict):
            names.append(item.get("label", ""))
        elif isinstance(item, str):
            names.append(item)
    return ", ".join(names) if names else "-"


# ============================================================
# 【v2.5 新增】AI 自动分类模块
# ============================================================

def auto_classify_videos(
    semantic_data: dict,
    output_dir: str,
    classify_method: str = "kmeans",
    n_clusters: int = 10,
    cluster_thresh: float = 0.5,
    link_mode: str = "dry-run",
    dry_run: bool = True,
    _log_fn=None
) -> dict:
    """
    【v2.5 新增】AI 自动分类核心函数。
    基于 CLIP 特征向量执行 K-Means 聚类，自动生成类别名称，并按策略组织文件。

    Args:
        semantic_data: {video_path: {...semantic info...}}
        output_dir: 分类输出目录
        classify_method: 聚类算法 (kmeans)
        n_clusters: 目标聚类数 (0=自动估算)
        cluster_thresh: 聚类松紧阈值
        link_mode: 'dry-run'(预览) / 'hardlink'(硬链接) / 'copy'(复制) / 'move'(移动)
        dry_run: 是否仅预览不执行文件操作
        _log_fn: 日志输出函数

    Returns:
        dict: {
            'clusters': {cluster_id: [video_path, ...]},
            'cluster_names': {cluster_id: 'auto_name'},
            'classify_report': 'report_path'
        }
    """
    log = _log_fn or (lambda msg: None)

    if not SKLEARN_OK:
        log("[错误] AI 分类需要 scikit-learn 库")
        log("  安装: pip install scikit-learn")
        return {"clusters": {}, "cluster_names": {}, "classify_report": ""}

    # 1. 提取特征向量
    embeddings = []
    video_paths = []
    for path, info in semantic_data.items():
        emb = info.get("semantic_emb")
        if emb is not None:
            embeddings.append(emb)
            video_paths.append(path)

    if len(embeddings) < 2:
        log("[警告] 有效特征向量不足，无法执行分类")
        return {"clusters": {}, "cluster_names": {}, "classify_report": ""}

    embeddings_np = np.array(embeddings)

    # 2. 执行 K-Means 聚类
    from sklearn.cluster import KMeans

    if n_clusters <= 0:
        n_clusters = min(20, max(2, len(embeddings) // 5))

    try:
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(embeddings_np)
    except Exception as e:
        log(f"[错误] K-Means 聚类失败: {e}")
        return {"clusters": {}, "cluster_names": {}, "classify_report": ""}

    # 3. 构建分组映射
    clusters = {}
    for path, label in zip(video_paths, labels):
        lid = int(label)
        clusters.setdefault(lid, []).append(path)

    # 4. 自动生成类别名称
    cluster_names = {}
    for lid, paths in clusters.items():
        # 收集该聚类中所有视频的高频标签
        tag_counter = defaultdict(int)
        for p in paths:
            info = semantic_data.get(p, {})
            # 场景标签权重最高
            for t in info.get("scene_tags", []):
                if isinstance(t, dict):
                    tag_counter[t.get("label", "")] += 3
                elif isinstance(t, str):
                    tag_counter[t] += 3
            # 对象标签次之
            for t in info.get("object_tags", []):
                if isinstance(t, dict):
                    tag_counter[t.get("label", "")] += 2
                elif isinstance(t, str):
                    tag_counter[t] += 2
            # 动作标签
            for t in info.get("action_tags", []):
                if isinstance(t, dict):
                    tag_counter[t.get("label", "")] += 1
                elif isinstance(t, str):
                    tag_counter[t] += 1

        if tag_counter:
            top_tags = sorted(tag_counter.items(), key=lambda x: -x[1])[:3]
            name_parts = [t for t, _ in top_tags if t]
            cluster_names[lid] = "_".join(name_parts) if name_parts else f"类别_{lid}"
        else:
            cluster_names[lid] = f"类别_{lid}"

    # 5. 整理分类结果
    classify_result = {
        "total_videos": len(video_paths),
        "method": classify_method,
        "n_clusters": len(clusters),
        "clusters": {},
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    for lid, paths in clusters.items():
        cname = cluster_names.get(lid, f"类别_{lid}")
        # 清理非法字符
        safe_name = "".join(c for c in cname if c not in '<>:"/\\|?*')[:60]
        classify_result["clusters"][safe_name] = {
            "count": len(paths),
            "videos": [{"path": p, "name": os.path.basename(p)} for p in paths]
        }

    # 6. 导出分类报告
    report_path = os.path.join(output_dir, "auto_classify_report.json")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(classify_result, f, ensure_ascii=False, indent=2)
        log(f"[导出] 分类报告 → {report_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 分类报告导出失败: {e}")

    # 7. 执行文件操作
    if not dry_run and link_mode in ("hardlink", "copy", "move"):
        log(f"\n[执行] 开始 {link_mode} 分类操作...")
        for cname, cluster_data in classify_result["clusters"].items():
            cluster_dir = os.path.join(output_dir, cname)
            os.makedirs(cluster_dir, exist_ok=True)

            for item in cluster_data["videos"]:
                src = item["path"]
                dst = os.path.join(cluster_dir, os.path.basename(src))
                try:
                    if link_mode == "hardlink":
                        if os.path.exists(dst):
                            dst = dst.replace(".mp4", "_dup.mp4")
                        os.link(src, dst)
                    elif link_mode == "copy":
                        if os.path.exists(dst):
                            dst = dst.replace(".mp4", "_dup.mp4")
                        import shutil
                        shutil.copy2(src, dst)
                    elif link_mode == "move":
                        if os.path.exists(dst):
                            dst = dst.replace(".mp4", "_dup.mp4")
                        import shutil
                        shutil.move(src, dst)
                except (IOError, OSError) as e:
                    log(f"  [警告] 操作失败 {os.path.basename(src)}: {e}")
        log(f"[完成] 文件分类操作完成")
    elif dry_run:
        log(f"\n[试运行] 分类结果已预览，未执行文件操作。")
        log(f"  如需执行，请添加 --execute 参数（默认使用硬链接）")

    return {
        "clusters": {lid: paths for lid, paths in clusters.items()},
        "cluster_names": cluster_names,
        "classify_report": report_path,
    }