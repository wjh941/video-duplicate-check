# -*- coding: utf-8 -*-
"""
media_analyze.py - MP4 视频查重工具扩展分析模块 (v1.0)
=====================================================
基于 find_mp4.py v2.5，提供媒体元数据导出、磁盘空间优化分析、素材标签管理、
语义相似度检索、快照导出、两次扫描对比等扩展功能。

子命令：
    media-info       批量导出视频完整元数据到 Excel
    space-analyze    磁盘空间优化分析（重复分组占用统计）
    tag-manage       素材标签管理（增删查导出）
    similar-search   语义相似度检索（文字描述匹配画面）
    export-snapshot  导出快照（打包缓存+报告到 zip）
    diff-scan        两次扫描对比（新增/清理/变动）

依赖（部分可选，自动降级）：
    - opencv-python (cv2)   必需：视频元数据读取
    - openpyxl              可选：Excel 导出（缺失则提示安装）
    - numpy                 可选：语义相似度计算
    - torch + open_clip     可选：CLIP 语义检索（缺失则降级关键词匹配）

一键安装：
    pip install opencv-python openpyxl numpy
    pip install torch open_clip_torch   # AI 语义检索（可选）

使用示例见文件末尾。
"""

import os
import sys
import json
import time
import zipfile
import argparse
from argparse import Namespace
from typing import Optional

# ============================================================
# 1. 依赖检测（缺失时给出 pip 安装提示）
# ============================================================

CV2_OK = False
OPENPYXL_OK = False
NUMPY_OK = False

try:
    import cv2
    CV2_OK = True
except ImportError:
    pass

try:
    import openpyxl
    OPENPYXL_OK = True
except ImportError:
    pass

try:
    import numpy as np
    NUMPY_OK = True
except ImportError:
    pass


def _check_deps() -> None:
    """检查核心依赖并输出安装提示（仅供启动时调用）。"""
    missing = []
    if not CV2_OK:
        missing.append(("opencv-python", "视频元数据读取"))
    if not OPENPYXL_OK:
        missing.append(("openpyxl", "Excel 导出"))
    if not NUMPY_OK:
        missing.append(("numpy", "语义相似度计算"))
    if missing:
        print("[依赖提示] 以下可选依赖未安装，对应功能将受限：")
        for pkg, desc in missing:
            print(f"  - {pkg:<20} → {desc}")
        print("一键安装：")
        print(f"  pip install {' '.join(p for p, _ in missing)}")
        print()


# ============================================================
# 2. 复用 find_mp4.py 中的函数与常量
# ============================================================

try:
    from find_mp4 import (
        load_cache,
        scan_mp4_files,
        _resolve_path,
        _normalize_path,
        parse_duration_filter,
        match_duration,
        log,
        CACHE_FILE,
        SEMANTIC_META,
    )
except ImportError as _e:
    print("[致命错误] 无法导入 find_mp4 模块，请确认 media_analyze.py 与 find_mp4.py 位于同一目录。")
    print(f"  原始错误: {_e}")
    print("  解决方法：将 media_analyze.py 放到 find_mp4.py 所在目录后重试。")
    sys.exit(1)

# 复用 ai_semantic.py 中的 CLIP 能力（可选，缺失自动降级）
try:
    from ai_semantic import load_clip_model, CLIP_OK, TORCH_OK
except ImportError:
    # ai_semantic 缺失时降级为关键词匹配
    load_clip_model = None
    CLIP_OK = False
    TORCH_OK = False


# ============================================================
# 3. 常量
# ============================================================

SPACE_REPORT = "space_optimization_report.md"
DIFF_REPORT = "diff_scan_report.md"
TAGS_DEFAULT_FILE = "video_tags.json"
SNAPSHOT_PREFIX = "snapshot_"

# 预定义标签集（用户可在此基础上增删）
PREDEFINED_TAGS = ["备用", "待删", "精品素材", "已审核"]

# 快照打包时收集的报告文件后缀
REPORT_EXTS = (".md", ".txt", ".csv", ".html", ".json", ".log")


# ============================================================
# 4. media-info 子命令：批量导出视频完整元数据到 Excel
# ============================================================

def _probe_video_meta(video_path: str) -> dict:
    """
    使用 cv2.VideoCapture 读取单个视频的元数据。
    返回字段：duration/fps/width/height/codec/bitrate_kbps/audio_channels。
    cv2 无法稳定获取音频通道数，统一标记为 "N/A"。
    """
    meta = {
        "duration": 0.0,
        "fps": 0.0,
        "width": 0,
        "height": 0,
        "codec": "N/A",
        "bitrate_kbps": 0,
        "audio_channels": "N/A",
    }
    if not CV2_OK:
        return meta

    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return meta

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # FOURCC 编码格式解码为 4 字符
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        codec = "".join([chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)])
        codec = codec.strip("\x00") or "N/A"

        duration = (total_frames / fps) if fps and fps > 0 else 0.0
        # 码率：根据文件大小与时长估算（kbps）
        bitrate_kbps = 0
        if duration > 0:
            try:
                size_bytes = os.path.getsize(video_path)
                bitrate_kbps = int((size_bytes * 8) / duration / 1000)
            except OSError:
                pass

        meta.update({
            "duration": round(duration, 2),
            "fps": round(fps, 2) if fps and fps > 0 else 0.0,
            "width": width,
            "height": height,
            "codec": codec,
            "bitrate_kbps": bitrate_kbps,
        })
        # 注：cv2 不暴露音频通道数，保持 "N/A"
    except Exception:
        pass
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
    return meta


def _format_duration(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS 字符串。"""
    if seconds <= 0:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _scan_video_files(folder_path: str, recursive: bool = True) -> list[dict]:
    """
    复用 find_mp4.scan_mp4_files 扫描视频文件。
    构造最小可用 args 命名空间，避免重复实现扫描逻辑。
    """
    args = Namespace(
        dir=folder_path,
        no_recursive=not recursive,
        ext="mp4",
        exclude_folder="",
        exclude_size_lt="",
        exclude_size_gt="",
        audio_check=False,
    )
    try:
        return scan_mp4_files(args)
    except Exception as e:
        log(f"[错误] 扫描文件失败: {e}")
        return []


def export_media_info(
    folder_path: str,
    output_path: str,
    recursive: bool = True,
    dry_run: bool = False,
) -> int:
    """
    批量导出视频完整元数据到 Excel。

    Args:
        folder_path: 待扫描的文件夹路径
        output_path: 输出 Excel 文件路径（.xlsx）
        recursive: 是否递归子目录
        dry_run: 预览模式，仅打印不写文件

    Returns:
        成功导出的视频条数
    """
    if not CV2_OK:
        log("[错误] 缺少 opencv-python，无法读取视频元数据")
        log("  安装: pip install opencv-python")
        return 0

    folder_path = _resolve_path(folder_path)
    if not os.path.isdir(folder_path):
        log(f"[错误] 路径不存在或非文件夹: {folder_path}")
        return 0

    log(f"[media-info] 扫描目录: {folder_path} (递归={recursive})")
    files = _scan_video_files(folder_path, recursive)
    if not files:
        log("[media-info] 未发现视频文件")
        return 0

    log(f"[media-info] 共发现 {len(files)} 个视频，开始读取元数据...")

    rows = []
    for fi in files:
        meta = _probe_video_meta(fi["path"])
        rows.append({
            "路径": fi["path"],
            "文件名": fi["name"],
            "大小": fi.get("size_readable", ""),
            "时长": _format_duration(meta["duration"]),
            "宽度": meta["width"],
            "高度": meta["height"],
            "帧率": meta["fps"],
            "编码": meta["codec"],
            "码率(kbps)": meta["bitrate_kbps"],
            "音频通道数": meta["audio_channels"],
        })

    if dry_run:
        log(f"[dry-run] 预览模式：将导出 {len(rows)} 条记录到 {output_path}（未实际写入）")
        for r in rows[:5]:
            log(f"  预览: {r['文件名']} | {r['时长']} | {r['宽度']}x{r['高度']} | {r['编码']}")
        if len(rows) > 5:
            log(f"  ... 其余 {len(rows) - 5} 条略")
        return len(rows)

    if not OPENPYXL_OK:
        log("[错误] 缺少 openpyxl，无法写入 Excel")
        log("  安装: pip install openpyxl")
        return 0

    headers = ["路径", "文件名", "大小", "时长", "宽度", "高度", "帧率", "编码", "码率(kbps)", "音频通道数"]
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "视频元数据"
        ws.append(headers)
        for r in rows:
            ws.append([r[h] for h in headers])
        # 列宽自适应（简单估算）
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(12, min(60, len(h) * 2 + 8))
        wb.save(output_path)
        log(f"[导出] Excel → {output_path}（共 {len(rows)} 条）")
    except (IOError, OSError) as e:
        log(f"[错误] Excel 写入失败: {e}")
        return 0

    return len(rows)


# ============================================================
# 5. space-analyze 子命令：磁盘空间优化分析
# ============================================================

def _group_duplicates_by_hash(cache: dict) -> list[dict]:
    """
    按缓存中的 phash 聚合重复视频分组。
    返回 [{"hash_key": str, "members": [(path, entry), ...]}, ...]，仅保留成员数 > 1 的分组。
    """
    groups_map: dict[str, list[tuple[str, dict]]] = {}
    for path, entry in cache.items():
        if path.startswith("_"):
            continue
        if not isinstance(entry, dict):
            continue
        phash = entry.get("phash")
        if not phash:
            continue
        # phash 为哈希对象列表（缓存中已转为字符串），用元组作为分组键
        key = "|".join(str(h) for h in phash)
        if not key:
            continue
        groups_map.setdefault(key, []).append((path, entry))

    groups = []
    for key, members in groups_map.items():
        if len(members) > 1:
            groups.append({"hash_key": key, "members": members})
    return groups


def analyze_space(
    cache_path: str,
    output_path: str,
    dry_run: bool = False,
) -> int:
    """
    磁盘空间优化分析：统计重复分组占用容量，按可节省空间降序排序。

    Args:
        cache_path: video_hash_cache.json 路径
        output_path: 输出 Markdown 报告路径
        dry_run: 预览模式，仅打印不写文件

    Returns:
        重复分组数量
    """
    cache_path = _resolve_path(cache_path)
    if not os.path.exists(cache_path):
        log(f"[错误] 缓存文件不存在: {cache_path}")
        return 0

    cache = load_cache(cache_path)
    if not cache:
        log("[space-analyze] 缓存为空")
        return 0

    groups = _group_duplicates_by_hash(cache)
    if not groups:
        log("[space-analyze] 未发现重复分组")
        return 0

    # 计算每组可节省空间（保留最大文件，其余可清理）
    report_rows = []
    total_savable = 0
    total_redundant = 0
    for g in groups:
        members = g["members"]
        sizes = [entry.get("size", 0) for _, entry in members]
        group_total = sum(sizes)
        keep_size = max(sizes) if sizes else 0
        savable = group_total - keep_size
        total_savable += savable
        total_redundant += len(members) - 1
        report_rows.append({
            "count": len(members),
            "group_total": group_total,
            "savable": savable,
            "keep_size": keep_size,
            "sample": os.path.basename(members[0][0]) if members else "",
            "paths": [p for p, _ in members],
        })

    # 按可节省空间降序排序
    report_rows.sort(key=lambda x: x["savable"], reverse=True)

    def _fmt(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 ** 2:
            return f"{size_bytes / 1024:.2f} KB"
        elif size_bytes < 1024 ** 3:
            return f"{size_bytes / (1024 ** 2):.2f} MB"
        return f"{size_bytes / (1024 ** 3):.2f} GB"

    lines = [
        "# 磁盘空间优化报告",
        "",
        f"- **生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **缓存文件**: {os.path.basename(cache_path)}",
        f"- **重复分组数**: {len(groups)}",
        f"- **冗余视频数**: {total_redundant}",
        f"- **可节省空间**: {_fmt(total_savable)}",
        "",
        "## 清理优先级（按可节省空间降序）",
        "",
        "| 优先级 | 分组大小 | 视频数 | 可节省 | 保留大小 | 示例文件 |",
        "|--------|----------|--------|--------|----------|----------|",
    ]
    for i, r in enumerate(report_rows, 1):
        lines.append(
            f"| {i} | {_fmt(r['group_total'])} | {r['count']} | "
            f"{_fmt(r['savable'])} | {_fmt(r['keep_size'])} | `{r['sample']}` |"
        )

    lines.extend(["", "## 详细分组", ""])
    for i, r in enumerate(report_rows, 1):
        lines.append(f"### 第 {i} 组（{r['count']} 个视频，可节省 {_fmt(r['savable'])}）")
        lines.append("")
        for p in r["paths"]:
            lines.append(f"- `{p}`")
        lines.append("")

    content = "\n".join(lines) + "\n"

    if dry_run:
        log(f"[dry-run] 预览模式：将生成 {len(groups)} 组空间报告 → {output_path}（未实际写入）")
        log(f"  可节省空间合计: {_fmt(total_savable)}")
        return len(groups)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        log(f"[导出] 空间优化报告 → {output_path}")
        log(f"  重复分组: {len(groups)} | 冗余视频: {total_redundant} | 可节省: {_fmt(total_savable)}")
    except (IOError, OSError) as e:
        log(f"[错误] 报告写入失败: {e}")
        return 0

    return len(groups)


# ============================================================
# 6. tag-manage 子命令：素材标签管理
# ============================================================

def _load_tags(tags_file: str) -> dict:
    """读取标签 JSON，返回 {video_path: [tag, ...]}。"""
    if not os.path.exists(tags_file):
        return {}
    try:
        with open(tags_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (IOError, OSError, json.JSONDecodeError):
        pass
    return {}


def _save_tags(tags_file: str, tags: dict) -> bool:
    """保存标签 JSON。"""
    try:
        with open(tags_file, "w", encoding="utf-8") as f:
            json.dump(tags, f, ensure_ascii=False, indent=2)
        return True
    except (IOError, OSError) as e:
        log(f"[错误] 标签保存失败: {e}")
        return False


def manage_tags(
    action: str,
    video_path: str,
    tag: Optional[str] = None,
    tags_file: str = TAGS_DEFAULT_FILE,
    export_path: Optional[str] = None,
    dry_run: bool = False,
) -> int:
    """
    素材标签管理。

    Args:
        action: "add" / "remove" / "list" / "export"
        video_path: 视频路径（list/export 时可为空字符串）
        tag: 待操作的标签（add/remove 必填）
        tags_file: 标签存储 JSON 文件
        export_path: export 动作的输出 txt 路径（默认 tags_export.txt）
        dry_run: 预览模式，不写文件

    Returns:
        操作影响的条目数（list 返回视频数，export 返回导出条数）
    """
    tags = _load_tags(tags_file)

    if action == "add":
        if not video_path or not tag:
            log("[错误] add 操作需要提供 video_path 和 tag")
            return 0
        video_path = _normalize_path(video_path)
        cur = tags.get(video_path, [])
        if tag in cur:
            log(f"[tag-manage] 标签已存在: {tag} @ {os.path.basename(video_path)}")
            return 0
        cur.append(tag)
        tags[video_path] = cur
        if dry_run:
            log(f"[dry-run] 预览：添加标签 '{tag}' → {os.path.basename(video_path)}（未写入）")
        else:
            if _save_tags(tags_file, tags):
                log(f"[tag-manage] 已添加 '{tag}' → {os.path.basename(video_path)}")
        return 1

    elif action == "remove":
        if not video_path or not tag:
            log("[错误] remove 操作需要提供 video_path 和 tag")
            return 0
        video_path = _normalize_path(video_path)
        cur = tags.get(video_path, [])
        if tag not in cur:
            log(f"[tag-manage] 标签不存在: {tag} @ {os.path.basename(video_path)}")
            return 0
        cur.remove(tag)
        if cur:
            tags[video_path] = cur
        else:
            tags.pop(video_path, None)
        if dry_run:
            log(f"[dry-run] 预览：移除标签 '{tag}' ← {os.path.basename(video_path)}（未写入）")
        else:
            if _save_tags(tags_file, tags):
                log(f"[tag-manage] 已移除 '{tag}' ← {os.path.basename(video_path)}")
        return 1

    elif action == "list":
        if video_path:
            video_path = _normalize_path(video_path)
            cur = tags.get(video_path, [])
            log(f"[tag-manage] {os.path.basename(video_path)} 的标签: {cur or '无'}")
            return len(cur)
        else:
            log(f"[tag-manage] 共 {len(tags)} 个视频有标签")
            for p, ts in tags.items():
                log(f"  {os.path.basename(p)}: {ts}")
            return len(tags)

    elif action == "export":
        out = export_path or "tags_export.txt"
        lines = ["# 视频标签清单", f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}", f"# 共 {len(tags)} 个视频", ""]
        for p, ts in tags.items():
            lines.append(f"{p}\t{'|'.join(ts)}")
        content = "\n".join(lines) + "\n"
        if dry_run:
            log(f"[dry-run] 预览：导出 {len(tags)} 条标签 → {out}（未写入）")
        else:
            try:
                with open(out, "w", encoding="utf-8") as f:
                    f.write(content)
                log(f"[导出] 标签清单 → {out}（共 {len(tags)} 条）")
            except (IOError, OSError) as e:
                log(f"[错误] 标签导出失败: {e}")
                return 0
        return len(tags)

    else:
        log(f"[错误] 未知 action: {action}（支持 add/remove/list/export）")
        return 0


# ============================================================
# 7. similar-search 子命令：语义相似度检索
# ============================================================

def _cosine_similarity(a, b) -> float:
    """计算两个向量的余弦相似度（numpy 实现）。"""
    if not NUMPY_OK:
        return 0.0
    try:
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        na = np.linalg.norm(va)
        nb = np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    except Exception:
        return 0.0


def _keyword_match_score(query_text: str, entry: dict) -> float:
    """
    降级方案：无 CLIP 时基于关键词匹配 scene_tags/object_tags。
    返回 0-1 的匹配分数。
    """
    # 分词：按空格/逗号/分词切分
    keywords = [w.strip() for w in query_text.replace(",", " ").replace("，", " ").split() if w.strip()]
    if not keywords:
        return 0.0

    # 收集该视频的标签文本
    tag_texts = []
    for field in ("scene_tags", "object_tags", "action_tags"):
        tags = entry.get(field, [])
        for t in tags:
            if isinstance(t, dict):
                tag_texts.append(t.get("label", ""))
            elif isinstance(t, str):
                tag_texts.append(t)
    purpose = entry.get("dataset_purpose", "")
    if purpose:
        tag_texts.append(purpose)

    blob = " ".join(tag_texts)
    if not blob:
        return 0.0

    matched = sum(1 for kw in keywords if kw in blob)
    return matched / len(keywords)


def similar_search(
    query_text: str,
    cache_path: str,
    top_k: int = 10,
    dry_run: bool = False,
) -> list[tuple[str, float]]:
    """
    语义相似度检索：输入文字描述，检索画面匹配的视频。

    Args:
        query_text: 文字描述（如 "夜晚街道行人监控"）
        cache_path: video_hash_cache.json 路径（含 semantic_emb 字段）
        top_k: 返回前 K 条结果
        dry_run: 预览模式（检索为只读，dry_run 仅控制是否打印）

    Returns:
        [(path, score), ...] 按分数降序的 Top-K 结果
    """
    cache_path = _resolve_path(cache_path)
    if not os.path.exists(cache_path):
        log(f"[错误] 缓存文件不存在: {cache_path}")
        return []

    cache = load_cache(cache_path)
    if not cache:
        log("[similar-search] 缓存为空")
        return []

    results: list[tuple[str, float]] = []

    # 优先：CLIP 文本编码 + 余弦相似度
    use_clip = CLIP_OK and TORCH_OK and load_clip_model is not None and NUMPY_OK
    text_feature = None
    if use_clip:
        try:
            import torch
            import open_clip
            model, preprocess, device = load_clip_model()
            if model is not None:
                tokenizer = open_clip.get_tokenizer("ViT-B-32")
                with torch.no_grad():
                    tf = model.encode_text(tokenizer([query_text]).to(device))
                    tf = tf / tf.norm(dim=-1, keepdim=True)
                text_feature = tf.cpu().numpy()[0]
        except Exception:
            text_feature = None

    if text_feature is not None:
        # CLIP 路径：与缓存 semantic_emb 余弦相似度比对
        for path, entry in cache.items():
            if path.startswith("_") or not isinstance(entry, dict):
                continue
            emb = entry.get("semantic_emb")
            if not emb:
                continue
            score = _cosine_similarity(text_feature, emb)
            results.append((path, score))
        log(f"[similar-search] 使用 CLIP 语义检索，候选 {len(results)} 个视频")
    else:
        # 降级路径：关键词匹配 scene_tags/object_tags
        for path, entry in cache.items():
            if path.startswith("_") or not isinstance(entry, dict):
                continue
            score = _keyword_match_score(query_text, entry)
            if score > 0:
                results.append((path, score))
        log(f"[similar-search] CLIP 不可用，降级为关键词匹配，候选 {len(results)} 个视频")

    # 按分数降序取 Top-K
    results.sort(key=lambda x: x[1], reverse=True)
    top = results[:top_k]

    if dry_run or top:
        log(f"[similar-search] 查询: \"{query_text}\" | Top-{len(top)} 结果:")
        for i, (p, s) in enumerate(top, 1):
            log(f"  {i}. [{s:.4f}] {os.path.basename(p)}")
            if dry_run:
                log(f"      {p}")

    return top


# ============================================================
# 8. export-snapshot 子命令：导出快照
# ============================================================

def export_snapshot(
    output_zip: str,
    cache_path: str,
    reports_dir: str,
    dry_run: bool = False,
) -> str:
    """
    导出快照：打包当前缓存 + 所有报告到 zip。
    自动按扫描时间命名：snapshot_YYYYMMDD_HHMMSS.zip。

    Args:
        output_zip: 输出 zip 路径，若为目录则自动生成文件名；若以 .zip 结尾则直接使用
        cache_path: video_hash_cache.json 路径
        reports_dir: 报告所在目录
        dry_run: 预览模式，仅打印将打包的文件清单

    Returns:
        实际生成的 zip 路径（dry_run 返回预览路径）
    """
    cache_path = _resolve_path(cache_path)
    reports_dir = _resolve_path(reports_dir)

    # 确定 zip 输出路径
    if output_zip.lower().endswith(".zip"):
        zip_path = output_zip
    else:
        # 视为目录，自动生成文件名
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        zip_name = f"{SNAPSHOT_PREFIX}{timestamp}.zip"
        zip_path = os.path.join(output_zip, zip_name) if os.path.isdir(output_zip) else zip_name

    # 收集待打包文件
    files_to_pack: list[tuple[str, str]] = []  # (磁盘路径, zip 内归档名)

    if os.path.exists(cache_path):
        files_to_pack.append((cache_path, os.path.basename(cache_path)))
    else:
        log(f"[警告] 缓存文件不存在: {cache_path}")

    if os.path.isdir(reports_dir):
        for name in os.listdir(reports_dir):
            full = os.path.join(reports_dir, name)
            if os.path.isfile(full) and name.lower().endswith(REPORT_EXTS):
                # 跳过快照自身避免递归打包
                if name.startswith(SNAPSHOT_PREFIX) and name.lower().endswith(".zip"):
                    continue
                files_to_pack.append((full, name))
    else:
        log(f"[警告] 报告目录不存在: {reports_dir}")

    if not files_to_pack:
        log("[export-snapshot] 无可打包文件")
        return ""

    if dry_run:
        log(f"[dry-run] 预览：将打包 {len(files_to_pack)} 个文件 → {zip_path}（未实际写入）")
        for _, arc in files_to_pack:
            log(f"  - {arc}")
        return zip_path

    try:
        os.makedirs(os.path.dirname(os.path.abspath(zip_path)), exist_ok=True)
    except OSError:
        pass

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for full, arc in files_to_pack:
                try:
                    zf.write(full, arc)
                except (IOError, OSError) as e:
                    log(f"[警告] 跳过文件 {arc}: {e}")
        log(f"[导出] 快照 → {zip_path}（共 {len(files_to_pack)} 个文件）")
    except (IOError, OSError) as e:
        log(f"[错误] 快照打包失败: {e}")
        return ""

    return zip_path


# ============================================================
# 9. diff-scan 子命令：两次扫描对比
# ============================================================

def _build_hash_index(cache: dict) -> dict[str, list[str]]:
    """构建 hash_key -> [path, ...] 索引，用于识别重复分组。"""
    index: dict[str, list[str]] = {}
    for path, entry in cache.items():
        if path.startswith("_") or not isinstance(entry, dict):
            continue
        phash = entry.get("phash")
        if not phash:
            continue
        key = "|".join(str(h) for h in phash)
        if key:
            index.setdefault(key, []).append(path)
    return index


def diff_scan(
    old_cache_path: str,
    new_cache_path: str,
    output_path: str,
    dry_run: bool = False,
) -> int:
    """
    两次扫描对比：对比新增视频、新增重复、已清理视频、素材变动。

    Args:
        old_cache_path: 旧缓存 video_hash_cache.json 路径
        new_cache_path: 新缓存 video_hash_cache.json 路径
        output_path: 输出 Markdown 差异报告路径
        dry_run: 预览模式，仅打印不写文件

    Returns:
        差异条目总数
    """
    old_cache_path = _resolve_path(old_cache_path)
    new_cache_path = _resolve_path(new_cache_path)

    if not os.path.exists(old_cache_path):
        log(f"[错误] 旧缓存不存在: {old_cache_path}")
        return 0
    if not os.path.exists(new_cache_path):
        log(f"[错误] 新缓存不存在: {new_cache_path}")
        return 0

    old = load_cache(old_cache_path)
    new = load_cache(new_cache_path)

    old_paths = {p for p in old if not p.startswith("_")}
    new_paths = {p for p in new if not p.startswith("_")}

    added = sorted(new_paths - old_paths)
    removed = sorted(old_paths - new_paths)
    common = sorted(new_paths & old_paths)

    # 新增重复分组：新缓存中成员数 > 1 且旧缓存中无此分组的
    old_index = _build_hash_index(old)
    new_index = _build_hash_index(new)

    new_dup_groups = []
    for key, paths in new_index.items():
        if len(paths) > 1:
            old_members = old_index.get(key, [])
            if len(old_members) <= 1:
                new_dup_groups.append(paths)
    new_dup_groups.sort(key=lambda g: -len(g))

    # 素材变动：共同视频中 scene_tags/dataset_purpose 发生变化
    changed = []
    for p in common:
        o = old.get(p, {}) or {}
        n = new.get(p, {}) or {}
        o_scene = str(o.get("scene_tags", []))
        n_scene = str(n.get("scene_tags", []))
        o_purpose = o.get("dataset_purpose", "")
        n_purpose = n.get("dataset_purpose", "")
        if o_scene != n_scene or o_purpose != n_purpose:
            changed.append({
                "path": p,
                "old_purpose": o_purpose or "-",
                "new_purpose": n_purpose or "-",
            })

    total_diff = len(added) + len(removed) + len(new_dup_groups) + len(changed)

    lines = [
        "# 扫描对比差异报告",
        "",
        f"- **生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **旧缓存**: {os.path.basename(old_cache_path)}（{len(old_paths)} 个视频）",
        f"- **新缓存**: {os.path.basename(new_cache_path)}（{len(new_paths)} 个视频）",
        f"- **差异条目**: {total_diff}",
        "",
        "## 概览",
        "",
        f"| 类别 | 数量 |",
        f"|------|------|",
        f"| 新增视频 | {len(added)} |",
        f"| 已清理视频 | {len(removed)} |",
        f"| 新增重复分组 | {len(new_dup_groups)} |",
        f"| 素材变动 | {len(changed)} |",
        "",
    ]

    lines.append("## 新增视频")
    lines.append("")
    if added:
        for p in added:
            lines.append(f"- `{p}`")
    else:
        lines.append("> 无")
    lines.append("")

    lines.append("## 已清理视频")
    lines.append("")
    if removed:
        for p in removed:
            lines.append(f"- `{p}`")
    else:
        lines.append("> 无")
    lines.append("")

    lines.append("## 新增重复分组")
    lines.append("")
    if new_dup_groups:
        for i, g in enumerate(new_dup_groups, 1):
            lines.append(f"### 第 {i} 组（{len(g)} 个视频）")
            for p in g:
                lines.append(f"- `{p}`")
            lines.append("")
    else:
        lines.append("> 无")
        lines.append("")

    lines.append("## 素材变动")
    lines.append("")
    if changed:
        lines.append("| 视频 | 旧用途 | 新用途 |")
        lines.append("|------|--------|--------|")
        for c in changed:
            lines.append(f"| `{os.path.basename(c['path'])}` | {c['old_purpose']} | {c['new_purpose']} |")
    else:
        lines.append("> 无")
    lines.append("")

    content = "\n".join(lines) + "\n"

    if dry_run:
        log(f"[dry-run] 预览：将生成差异报告 → {output_path}（未实际写入）")
        log(f"  新增 {len(added)} | 清理 {len(removed)} | 新增重复组 {len(new_dup_groups)} | 变动 {len(changed)}")
        return total_diff

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        log(f"[导出] 差异报告 → {output_path}")
        log(f"  新增 {len(added)} | 清理 {len(removed)} | 新增重复组 {len(new_dup_groups)} | 变动 {len(changed)}")
    except (IOError, OSError) as e:
        log(f"[错误] 差异报告写入失败: {e}")
        return 0

    return total_diff


# ============================================================
# 10. 命令行入口
# ============================================================

def _build_arg_parser() -> argparse.ArgumentParser:
    """构建 argparse 子命令解析器。"""
    parser = argparse.ArgumentParser(
        prog="media_analyze.py",
        description="MP4 视频查重工具扩展分析模块（基于 find_mp4.py v2.5）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式：仅打印不写文件（全局开关，传递给各子命令）",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # media-info
    p_info = sub.add_parser("media-info", help="批量导出视频完整元数据到 Excel")
    p_info.add_argument("folder", help="待扫描的文件夹路径")
    p_info.add_argument("-o", "--output", default="media_info.xlsx", help="输出 Excel 路径（默认 media_info.xlsx）")
    p_info.add_argument("--no-recursive", action="store_true", help="不递归子目录")

    # space-analyze
    p_space = sub.add_parser("space-analyze", help="磁盘空间优化分析")
    p_space.add_argument("cache", help=f"缓存文件路径（默认 {CACHE_FILE}）", nargs="?", default=CACHE_FILE)
    p_space.add_argument("-o", "--output", default=SPACE_REPORT, help=f"输出报告路径（默认 {SPACE_REPORT}）")

    # tag-manage
    p_tag = sub.add_parser("tag-manage", help="素材标签管理")
    p_tag.add_argument("action", choices=["add", "remove", "list", "export"], help="操作类型")
    p_tag.add_argument("video", nargs="?", default="", help="视频路径（list/export 时可省略）")
    p_tag.add_argument("-t", "--tag", default=None, help="标签名（add/remove 必填）")
    p_tag.add_argument("-f", "--tags-file", default=TAGS_DEFAULT_FILE, help=f"标签存储文件（默认 {TAGS_DEFAULT_FILE}）")
    p_tag.add_argument("--export-path", default=None, help="export 动作的输出 txt 路径")

    # similar-search
    p_sim = sub.add_parser("similar-search", help="语义相似度检索")
    p_sim.add_argument("query", help="文字描述（如 \"夜晚街道行人监控\"）")
    p_sim.add_argument("cache", help=f"缓存文件路径（默认 {CACHE_FILE}）", nargs="?", default=CACHE_FILE)
    p_sim.add_argument("-k", "--top-k", type=int, default=10, help="返回前 K 条结果（默认 10）")

    # export-snapshot
    p_snap = sub.add_parser("export-snapshot", help="导出快照（打包缓存+报告到 zip）")
    p_snap.add_argument("output", help="输出 zip 路径或目录（目录则自动命名 snapshot_YYYYMMDD_HHMMSS.zip）")
    p_snap.add_argument("cache", help=f"缓存文件路径（默认 {CACHE_FILE}）", nargs="?", default=CACHE_FILE)
    p_snap.add_argument("-r", "--reports-dir", default=".", help="报告所在目录（默认当前目录）")

    # diff-scan
    p_diff = sub.add_parser("diff-scan", help="两次扫描对比")
    p_diff.add_argument("old_cache", help="旧缓存路径")
    p_diff.add_argument("new_cache", help="新缓存路径")
    p_diff.add_argument("-o", "--output", default=DIFF_REPORT, help=f"输出报告路径（默认 {DIFF_REPORT}）")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """命令行入口：分发子命令到对应核心函数。"""
    _check_deps()
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    dry = getattr(args, "dry_run", False)

    if args.command == "media-info":
        export_media_info(
            folder_path=args.folder,
            output_path=args.output,
            recursive=not args.no_recursive,
            dry_run=dry,
        )

    elif args.command == "space-analyze":
        analyze_space(
            cache_path=args.cache,
            output_path=args.output,
            dry_run=dry,
        )

    elif args.command == "tag-manage":
        manage_tags(
            action=args.action,
            video_path=args.video,
            tag=args.tag,
            tags_file=args.tags_file,
            export_path=args.export_path,
            dry_run=dry,
        )

    elif args.command == "similar-search":
        similar_search(
            query_text=args.query,
            cache_path=args.cache,
            top_k=args.top_k,
            dry_run=dry,
        )

    elif args.command == "export-snapshot":
        export_snapshot(
            output_zip=args.output,
            cache_path=args.cache,
            reports_dir=args.reports_dir,
            dry_run=dry,
        )

    elif args.command == "diff-scan":
        diff_scan(
            old_cache_path=args.old_cache,
            new_cache_path=args.new_cache,
            output_path=args.output,
            dry_run=dry,
        )

    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())


# ============================================================
# 使用示例
# ============================================================
#
# 1. 导出视频元数据到 Excel
#    python media_analyze.py media-info "D:\videos" -o media_info.xlsx
#    python media_analyze.py media-info "D:\videos" --no-recursive --dry-run
#
# 2. 磁盘空间优化分析（基于缓存）
#    python media_analyze.py space-analyze video_hash_cache.json -o space_report.md
#    python media_analyze.py space-analyze --dry-run
#
# 3. 标签管理
#    python media_analyze.py tag-manage add "D:\videos\a.mp4" -t 精品素材
#    python media_analyze.py tag-manage remove "D:\videos\a.mp4" -t 精品素材
#    python media_analyze.py tag-manage list
#    python media_analyze.py tag-manage list "D:\videos\a.mp4"
#    python media_analyze.py tag-manage export --export-path tags.txt
#
# 4. 语义相似度检索（需 CLIP，否则降级关键词匹配）
#    python media_analyze.py similar-search "夜晚街道行人监控" -k 5
#    python media_analyze.py similar-search "停车场监控" video_hash_cache.json
#
# 5. 导出快照（打包缓存+报告）
#    python media_analyze.py export-snapshot ./snapshots video_hash_cache.json -r .
#    python media_analyze.py export-snapshot snapshot.zip --dry-run
#
# 6. 两次扫描对比
#    python media_analyze.py diff-scan old_cache.json new_cache.json -o diff.md
#    python media_analyze.py diff-scan old_cache.json new_cache.json --dry-run
#
# 全局参数 --dry-run 可附加到任意子命令前以预览：
#    python media_analyze.py --dry-run media-info "D:\videos"
#
# 预定义标签：备用 / 待删 / 精品素材 / 已审核
