#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MP4 视频查重报告生成器
======================
基于 find_mp4.py v2.5 的缓存数据，生成查重、AI 语义、时长分辨率、磁盘空间、
数据集质检等多维度报告，支持单页 HTML 综合报告、PDF 导出、数据对比、
批量打包等功能。

依赖说明：
    必需：find_mp4.py（同目录）、imagehash、numpy、cv2、Pillow（随 find_mp4 引入）
    可选：reportlab、weasyprint（用于 PDF 导出，二选一即可）

一键安装可选依赖：
    pip install reportlab weasyprint

通过 `from find_mp4 import ...` 复用现有函数与常量，不重复实现查重核心逻辑。
"""

import os
import sys
import json
import time
import zipfile
import argparse
import shutil
from html.parser import HTMLParser
from typing import Optional, List, Dict, Any, Tuple
from xml.sax.saxutils import escape as _xml_escape

# ============================================================
# 模块 1：依赖检测与 find_mp4 复用
# ============================================================

# 复用 find_mp4 现有函数与常量（load_cache / _resolve_path / log 等）
try:
    from find_mp4 import (  # type: ignore
        load_cache,
        _resolve_path,
        log,
        _format_size,
        _atomic_write_text,
        compute_similarity,
        find_similar_pairs,
        build_groups,
        CACHE_FILE,
        SEMANTIC_META,
        AUDIT_LOG,
        CACHE_VERSION,
    )
    _FIND_MP4_OK = True
    _IMPORT_ERROR = ""
except ImportError as _e:  # find_mp4 或其依赖缺失
    _FIND_MP4_OK = False
    _IMPORT_ERROR = str(_e)

# imagehash 用于将缓存中的 hex 字符串还原为哈希对象（参与相似度计算）
try:
    import imagehash  # type: ignore
    _IMAGEHASH_OK = True
except ImportError:
    _IMAGEHASH_OK = False

# 可选 PDF 依赖：reportlab（优先）
try:
    from reportlab.lib.pagesizes import A4  # type: ignore
    from reportlab.lib.units import mm  # type: ignore
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # type: ignore
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer  # type: ignore
    from reportlab.pdfgen import canvas as _rl_canvas  # type: ignore
    _REPORTLAB_OK = True
except ImportError:
    _REPORTLAB_OK = False

# 可选 PDF 依赖：weasyprint（降级，HTML 转 PDF 保真度最高）
try:
    from weasyprint import HTML as _WHTML  # type: ignore
    _WEASYPRINT_OK = True
except ImportError:
    _WEASYPRINT_OK = False


def _check_deps() -> bool:
    """检测必需依赖是否就绪，未就绪时打印一键安装提示。"""
    if not _FIND_MP4_OK:
        print("[错误] 无法导入 find_mp4 模块及其依赖。")
        print(f"  原因: {_IMPORT_ERROR}")
        print("  请确保 find_mp4.py 与本文件在同一目录，并已安装核心依赖：")
        print("    pip install opencv-python numpy pillow imagehash")
        return False
    if not _IMAGEHASH_OK:
        print("[错误] 缺少 imagehash 库，请安装：pip install imagehash")
        return False
    return True


def _pdf_deps_hint() -> str:
    """返回 PDF 可选依赖的一键安装提示。"""
    return (
        "[提示] PDF 导出需要可选依赖 reportlab 或 weasyprint，请安装其一：\n"
        "    pip install reportlab        # 优先（轻量，文本结构化渲染）\n"
        "    pip install weasyprint       # 降级（HTML 原样转 PDF，保真度高）"
    )


# ============================================================
# 模块 2：通用辅助函数
# ============================================================

def _build_index_from_cache(cache: dict) -> Tuple[List[dict], dict]:
    """
    从缓存构建 find_mp4 比对所需的数据结构。

    将缓存中每条记录的 hex 哈希字符串还原为 imagehash 对象，
    并组装为 mp4_files（文件信息列表）与 video_hashes（索引→哈希字典）。

    Args:
        cache: load_cache 返回的缓存字典

    Returns:
        (mp4_files, video_hashes) 二元组
    """
    mp4_files: List[dict] = []
    video_hashes: Dict[int, dict] = {}

    for path, entry in cache.items():
        # 跳过缓存元字段（以 _ 开头的全局配置）
        if path.startswith("_") or not isinstance(entry, dict):
            continue
        phash_raw = entry.get("phash")
        # 无 phash 的条目（如损坏视频）不参与查重
        if not phash_raw:
            continue
        try:
            phash_objs = [imagehash.hex_to_hash(h) for h in phash_raw]
        except Exception:
            continue

        dhash_raw = entry.get("dhash", []) or []
        try:
            dhash_objs = [imagehash.hex_to_hash(h) for h in dhash_raw] if dhash_raw else []
        except Exception:
            dhash_objs = []

        audio_raw = entry.get("audio")
        audio_objs = None
        if audio_raw:
            try:
                audio_objs = [imagehash.hex_to_hash(h) for h in audio_raw]
            except Exception:
                audio_objs = None

        size = int(entry.get("size", 0) or 0)
        idx = len(mp4_files)
        mp4_files.append({
            "name": entry.get("name") or os.path.basename(path),
            "path": path,
            "size": size,
            "mtime": float(entry.get("mtime", 0) or 0),
            "size_readable": _format_size(size) if size else "0 B",
        })
        video_hashes[idx] = {
            "phash": phash_objs,
            "dhash": dhash_objs,
            "duration": float(entry.get("duration", 0) or 0),
            "width": int(entry.get("width", 0) or 0),
            "height": int(entry.get("height", 0) or 0),
            "fps": float(entry.get("fps", 0) or 0),
            "audio": audio_objs,
        }
    return mp4_files, video_hashes


def _compute_duplicate_groups(
    cache: dict, threshold: float = 0.85
) -> Tuple[List[dict], List[dict], dict]:
    """
    基于缓存数据计算重复分组（复用 find_mp4.find_similar_pairs + build_groups）。

    Args:
        cache: 缓存字典
        threshold: 相似度阈值，默认 0.85

    Returns:
        (groups, mp4_files, video_hashes) 三元组
    """
    mp4_files, video_hashes = _build_index_from_cache(cache)
    if len(mp4_files) < 2:
        return [], mp4_files, video_hashes
    # 复用 find_mp4 两两比对与连通图分组逻辑
    pairs = find_similar_pairs(video_hashes, mp4_files, threshold)
    groups = build_groups(pairs, mp4_files, video_hashes, keep_strategy="max-size")
    return groups, mp4_files, video_hashes


def _mask_path(path: str) -> str:
    """
    路径脱敏：保留盘符/首层与文件名，中间层级以 ... 替代。

    例如 D:\\videos\\a\\b\\c.mp4 → D:\\...\\c.mp4
    """
    if not path:
        return path
    clean = path
    # 去除 Windows 长路径前缀
    if clean.startswith("\\\\?\\"):
        clean = clean[4:]
    clean = clean.replace("/", "\\")
    parts = [p for p in clean.split("\\") if p]
    if len(parts) <= 2:
        return path
    return parts[0] + "\\...\\" + parts[-1]


def _maybe_mask(path: str, path_mask: bool) -> str:
    """根据开关决定是否脱敏路径。"""
    return _mask_path(path) if path_mask else path


def _atomic_write(file_path: str, content: str, encoding: str = "utf-8") -> None:
    """
    原子写入文本文件（先写 .tmp 再 rename）。

    优先复用 find_mp4._atomic_write_text，缺失时本地兜底实现。
    """
    if _FIND_MP4_OK:
        _atomic_write_text(file_path, content, encoding)
        return
    # 本地兜底：先写临时文件，完整后原子替换
    tmp_path = file_path + ".tmp"
    with open(tmp_path, "w", encoding=encoding) as f:
        f.write(content)
    os.replace(tmp_path, file_path)


def _duration_bucket(dur: float) -> str:
    """时长分桶标签。"""
    if dur <= 0:
        return "未知"
    if dur < 30:
        return "<30秒"
    if dur < 60:
        return "30-60秒"
    if dur < 180:
        return "1-3分钟"
    if dur < 600:
        return "3-10分钟"
    return ">10分钟"


def _resolution_tier(width: int, height: int) -> str:
    """分辨率分档标签（按短边判定）。"""
    min_dim = min(width, height) if width and height else 0
    if min_dim <= 0:
        return "未知"
    if min_dim >= 2160:
        return "4K+"
    if min_dim >= 1440:
        return "2K"
    if min_dim >= 1080:
        return "1080p"
    if min_dim >= 720:
        return "720p"
    return "SD"


def _count_distribution(items: List[str]) -> List[Tuple[str, int]]:
    """统计字符串列表中各值的出现频次，按频次降序返回。"""
    counter: Dict[str, int] = {}
    for it in items:
        if not it:
            continue
        if isinstance(it, list):
            # scene_tags / object_tags 可能是列表
            for sub in it:
                key = str(sub).strip()
                if key:
                    counter[key] = counter.get(key, 0) + 1
        else:
            key = str(it).strip()
            if key:
                counter[key] = counter.get(key, 0) + 1
    return sorted(counter.items(), key=lambda x: x[1], reverse=True)


def _group_member_paths(group: dict) -> List[str]:
    """获取一个重复分组的全部成员路径。"""
    paths = []
    for _idx, info in group.get("members", []):
        paths.append(info["path"])
    return paths


# ============================================================
# 模块 3：纯 HTML/CSS 图表生成
# ============================================================

_CHART_PALETTE = [
    "#667eea", "#764ba2", "#f093fb", "#4facfe",
    "#43e97b", "#fa709a", "#fee140", "#30cfd0",
    "#a8edea", "#ffd89b", "#ff9a9e", "#a18cd1",
]


def _bar_chart_html(items: List[Tuple[str, int]], color: str = "#667eea") -> str:
    """生成纯 CSS 柱状图（div 宽度按比例）。"""
    if not items:
        return '<p class="empty-hint">暂无数据</p>'
    max_val = max(v for _, v in items) or 1
    rows = []
    for label, value in items:
        pct = value / max_val * 100
        rows.append(
            f'<div class="bar-row">'
            f'<div class="bar-label">{_xml_escape(str(label))}</div>'
            f'<div class="bar-track"><div class="bar-fill" '
            f'style="width:{pct:.1f}%;background:linear-gradient(90deg,{color},{color}cc)"></div></div>'
            f'<div class="bar-value">{value}</div>'
            f'</div>'
        )
    return '<div class="bar-chart">' + "".join(rows) + "</div>"


def _pie_chart_html(items: List[Tuple[str, int]]) -> str:
    """生成纯 CSS 饼图（conic-gradient）+ 图例。"""
    if not items:
        return '<p class="empty-hint">暂无数据</p>'
    total = sum(v for _, v in items) or 1
    stops = []
    legend = []
    acc = 0.0
    for i, (label, value) in enumerate(items):
        pct = value / total * 100
        color = _CHART_PALETTE[i % len(_CHART_PALETTE)]
        start = acc
        acc += pct
        stops.append(f"{color} {start:.2f}% {acc:.2f}%")
        legend.append(
            f'<li><span class="dot" style="background:{color}"></span>'
            f'<span class="legend-label">{_xml_escape(str(label))}</span>'
            f'<b>{value}</b><span class="legend-pct">{pct:.1f}%</span></li>'
        )
    gradient = ", ".join(stops)
    return (
        '<div class="pie-wrap">'
        f'<div class="pie" style="background:conic-gradient({gradient})"></div>'
        '<ul class="legend">' + "".join(legend) + "</ul>"
        "</div>"
    )


# ============================================================
# 模块 4：generate_full_report 综合汇总 HTML 报告
# ============================================================

def generate_full_report(
    cache_path: str,
    output_path: str,
    project_name: str = "",
    operator: str = "",
    path_mask: bool = False,
) -> str:
    """
    生成单页综合汇总 HTML 报告，整合查重、AI 语义、时长分辨率、磁盘空间、质检数据。

    - 离线可打开（内联 CSS/JS，不依赖外部 CDN）
    - 图表使用纯 HTML/CSS（柱状图 div 宽度，饼图 conic-gradient）
    - path_mask=True 时隐藏路径中间层级

    Args:
        cache_path: 缓存文件路径
        output_path: 输出 HTML 路径
        project_name: 项目名称（显示于报告头部）
        operator: 操作人员（显示于报告头部）
        path_mask: 是否路径脱敏

    Returns:
        生成的 HTML 文件绝对路径
    """
    if not _check_deps():
        return ""
    cache = load_cache(cache_path)
    log(f"[报告] 加载缓存: {len([k for k in cache if not k.startswith('_')])} 条", force=True)

    # 计算重复分组（复用 find_mp4 比对逻辑）
    groups, mp4_files, video_hashes = _compute_duplicate_groups(cache, threshold=0.85)

    # ---------- 全局统计 ----------
    total_videos = len(mp4_files)
    # 缓存中所有条目（含无哈希的损坏视频）
    all_entries = {k: v for k, v in cache.items() if not k.startswith("_") and isinstance(v, dict)}
    total_cached = len(all_entries)
    dup_group_count = len(groups)
    dup_video_count = sum(len(g["members"]) for g in groups)
    redundant_count = dup_video_count - dup_group_count  # 可清理的冗余份数
    total_size = sum(f["size"] for f in mp4_files)
    # 可节省空间 = 各分组中非保留成员的大小之和
    reclaimable = 0
    for g in groups:
        for idx, info in g["members"]:
            if idx != g["retain_idx"]:
                reclaimable += info["size"]

    ai_tagged = sum(1 for e in all_entries.values() if e.get("scene_tags"))
    training_ready = sum(
        1 for e in all_entries.values()
        if e.get("is_training_ready") or (e.get("quality_score") is not None and float(e.get("quality_score", 0)) >= 0.6)
    )
    low_quality = sum(
        1 for e in all_entries.values()
        if e.get("quality_score") is not None and float(e.get("quality_score", 0)) < 0.5
    )

    # ---------- 分类数据收集 ----------
    # 重复分组规模分布（前 10 大）
    group_size_dist = [("分组" + str(i + 1), len(g["members"]))
                       for i, g in enumerate(groups[:10])]

    # 场景标签分布
    scene_tags = [e.get("scene_tags") for e in all_entries.values() if e.get("scene_tags")]
    scene_dist = _count_distribution(scene_tags)[:8]

    # 数据集用途分布
    purpose_dist = _count_distribution(
        [e.get("dataset_purpose") for e in all_entries.values() if e.get("dataset_purpose")]
    )[:8]

    # 时长分布
    dur_dist_items: Dict[str, int] = {}
    for f in mp4_files:
        h = video_hashes.get(_find_idx(mp4_files, f), {})
        bucket = _duration_bucket(h.get("duration", 0))
        dur_dist_items[bucket] = dur_dist_items.get(bucket, 0) + 1
    dur_order = ["<30秒", "30-60秒", "1-3分钟", "3-10分钟", ">10分钟", "未知"]
    dur_dist = [(k, dur_dist_items.get(k, 0)) for k in dur_order if dur_dist_items.get(k, 0) > 0]

    # 分辨率分布
    res_dist_items: Dict[str, int] = {}
    for f in mp4_files:
        idx = _find_idx(mp4_files, f)
        h = video_hashes.get(idx, {})
        tier = _resolution_tier(h.get("width", 0), h.get("height", 0))
        res_dist_items[tier] = res_dist_items.get(tier, 0) + 1
    res_order = ["SD", "720p", "1080p", "2K", "4K+", "未知"]
    res_dist = [(k, res_dist_items.get(k, 0)) for k in res_order if res_dist_items.get(k, 0) > 0]

    # 质量分分布
    q_buckets = {"<0.3": 0, "0.3-0.5": 0, "0.5-0.7": 0, "0.7-0.9": 0, ">=0.9": 0, "未评分": 0}
    for e in all_entries.values():
        qs = e.get("quality_score")
        if qs is None:
            q_buckets["未评分"] += 1
        else:
            qs = float(qs)
            if qs < 0.3:
                q_buckets["<0.3"] += 1
            elif qs < 0.5:
                q_buckets["0.3-0.5"] += 1
            elif qs < 0.7:
                q_buckets["0.5-0.7"] += 1
            elif qs < 0.9:
                q_buckets["0.7-0.9"] += 1
            else:
                q_buckets[">=0.9"] += 1
    q_dist = list(q_buckets.items())

    # 磁盘空间 Top 重复组（按可节省空间降序）
    space_rows = []
    for gi, g in enumerate(groups):
        wasted = sum(info["size"] for idx, info in g["members"] if idx != g["retain_idx"])
        if wasted > 0:
            retain_info = next((info for idx, info in g["members"] if idx == g["retain_idx"]), g["members"][0][1])
            space_rows.append((gi + 1, wasted, len(g["members"]), retain_info))
    space_rows.sort(key=lambda x: x[1], reverse=True)
    top_space = space_rows[:10]

    # 低质素材明细（前 15）
    low_q_details = []
    for path, e in all_entries.items():
        qs = e.get("quality_score")
        if qs is not None and float(qs) < 0.5:
            low_q_details.append((path, float(qs), e.get("scene_tags")))
    low_q_details.sort(key=lambda x: x[1])
    low_q_details = low_q_details[:15]

    # ---------- 组装 HTML ----------
    gen_time = time.strftime("%Y-%m-%d %H:%M:%S")
    html = _build_full_html(
        project_name=project_name or "未命名项目",
        operator=operator or "未指定",
        gen_time=gen_time,
        total_cached=total_cached,
        total_videos=total_videos,
        dup_group_count=dup_group_count,
        redundant_count=redundant_count,
        total_size=_format_size(total_size),
        reclaimable=_format_size(reclaimable),
        ai_tagged=ai_tagged,
        training_ready=training_ready,
        low_quality=low_quality,
        group_size_dist=group_size_dist,
        scene_dist=scene_dist,
        purpose_dist=purpose_dist,
        dur_dist=dur_dist,
        res_dist=res_dist,
        q_dist=q_dist,
        top_space=top_space,
        low_q_details=low_q_details,
        path_mask=path_mask,
    )
    _atomic_write(output_path, html)
    log(f"[报告] 综合报告已生成 → {output_path}", force=True)
    return os.path.abspath(output_path)


def _find_idx(mp4_files: List[dict], f: dict) -> int:
    """根据文件信息定位索引（辅助，O(n) 仅用于统计）。"""
    for i, mf in enumerate(mp4_files):
        if mf is f:
            return i
    return -1


def _build_full_html(
    project_name: str, operator: str, gen_time: str,
    total_cached: int, total_videos: int, dup_group_count: int,
    redundant_count: int, total_size: str, reclaimable: str,
    ai_tagged: int, training_ready: int, low_quality: int,
    group_size_dist: List[Tuple[str, int]],
    scene_dist: List[Tuple[str, int]],
    purpose_dist: List[Tuple[str, int]],
    dur_dist: List[Tuple[str, int]],
    res_dist: List[Tuple[str, int]],
    q_dist: List[Tuple[str, int]],
    top_space: List[Tuple[int, int, int, dict]],
    low_q_details: List[Tuple[str, float, Any]],
    path_mask: bool,
) -> str:
    """组装综合报告完整 HTML 字符串。"""
    # 统计卡片
    cards = "".join([
        _stat_card("缓存条目", total_cached, "#667eea"),
        _stat_card("有效视频", total_videos, "#43e97b"),
        _stat_card("重复分组", dup_group_count, "#fa709a"),
        _stat_card("冗余份数", redundant_count, "#f093fb"),
        _stat_card("总占用", total_size, "#4facfe", is_text=True),
        _stat_card("可节省", reclaimable, "#30cfd0", is_text=True),
        _stat_card("AI 标注", ai_tagged, "#a18cd1"),
        _stat_card("训练就绪", training_ready, "#43e97b"),
        _stat_card("低质素材", low_quality, "#ff9a9e"),
    ])

    # 磁盘空间 Top 表格行
    space_rows_html = ""
    if top_space:
        for gi, wasted, members, retain_info in top_space:
            space_rows_html += (
                "<tr>"
                f"<td>分组 {gi}</td>"
                f"<td>{members}</td>"
                f"<td>{_format_size(wasted)}</td>"
                f"<td class='path-cell'>{_xml_escape(_maybe_mask(retain_info['path'], path_mask))}</td>"
                "</tr>"
            )
    else:
        space_rows_html = '<tr><td colspan="4" class="empty-hint">未发现重复分组</td></tr>'

    # 低质素材明细行
    lowq_rows_html = ""
    if low_q_details:
        for path, qs, tags in low_q_details:
            tag_str = ", ".join(tags) if isinstance(tags, list) else (tags or "")
            lowq_rows_html += (
                "<tr>"
                f"<td>{qs:.2f}</td>"
                f"<td>{_xml_escape(tag_str)}</td>"
                f"<td class='path-cell'>{_xml_escape(_maybe_mask(path, path_mask))}</td>"
                "</tr>"
            )
    else:
        lowq_rows_html = '<tr><td colspan="3" class="empty-hint">暂无低质素材</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_xml_escape(project_name)} - 视频查重综合报告</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh; padding: 24px; color: #2d3748;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  .header {{
    background: rgba(255,255,255,0.95); border-radius: 18px; padding: 28px 36px;
    margin-bottom: 24px; box-shadow: 0 12px 40px rgba(0,0,0,0.18);
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px;
  }}
  .header h1 {{
    font-size: 26px; background: linear-gradient(90deg,#667eea,#764ba2);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
  }}
  .header .meta {{ font-size: 14px; color: #718096; line-height: 1.8; }}
  .header .meta b {{ color: #2d3748; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fill,minmax(180px,1fr)); gap: 16px; margin-bottom: 24px; }}
  .card {{
    background: #fff; border-radius: 14px; padding: 20px; text-align: center;
    box-shadow: 0 6px 20px rgba(0,0,0,0.08); transition: transform .2s;
    border-top: 4px solid #667eea;
  }}
  .card:hover {{ transform: translateY(-4px); }}
  .card .num {{ font-size: 30px; font-weight: 700; }}
  .card .lbl {{ font-size: 13px; color: #718096; margin-top: 6px; }}
  .section {{
    background: #fff; border-radius: 16px; padding: 24px 28px; margin-bottom: 24px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.08);
  }}
  .section h2 {{ font-size: 19px; margin-bottom: 18px; color: #4a5568; border-left: 5px solid #667eea; padding-left: 12px; }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 28px; }}
  @media (max-width: 820px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
  .bar-chart {{ display: flex; flex-direction: column; gap: 10px; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; }}
  .bar-label {{ width: 90px; font-size: 13px; color: #4a5568; text-align: right; flex-shrink: 0; }}
  .bar-track {{ flex: 1; background: #edf2f7; border-radius: 8px; height: 22px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 8px; transition: width .6s; }}
  .bar-value {{ width: 50px; font-size: 13px; font-weight: 600; color: #2d3748; }}
  .pie-wrap {{ display: flex; align-items: center; gap: 24px; flex-wrap: wrap; }}
  .pie {{ width: 160px; height: 160px; border-radius: 50%; flex-shrink: 0; box-shadow: 0 4px 14px rgba(0,0,0,0.12); }}
  .legend {{ list-style: none; font-size: 13px; }}
  .legend li {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
  .legend .dot {{ width: 12px; height: 12px; border-radius: 3px; flex-shrink: 0; }}
  .legend-label {{ color: #4a5568; min-width: 60px; }}
  .legend b {{ color: #2d3748; }}
  .legend-pct {{ color: #a0aec0; font-size: 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #edf2f7; }}
  th {{ background: #f7fafc; color: #4a5568; font-weight: 600; }}
  .path-cell {{ font-family: Consolas, monospace; font-size: 12px; color: #718096; word-break: break-all; }}
  .empty-hint {{ color: #a0aec0; text-align: center; padding: 20px; font-size: 13px; }}
  .footer {{ text-align: center; color: rgba(255,255,255,0.85); font-size: 13px; padding: 16px; }}
  .priority {{ display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; font-weight: 600; }}
  .p-high {{ background: #fed7d7; color: #c53030; }}
  .p-mid {{ background: #feebc8; color: #c05621; }}
  .p-low {{ background: #c6f6d5; color: #2f855a; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1>🎬 {_xml_escape(project_name)}</h1>
      <div class="meta">视频查重综合分析报告</div>
    </div>
    <div class="meta">
      <div>操作人员：<b>{_xml_escape(operator)}</b></div>
      <div>生成时间：<b>{gen_time}</b></div>
      <div>缓存版本：<b>{CACHE_VERSION}</b></div>
    </div>
  </div>

  <div class="cards">{cards}</div>

  <div class="section">
    <h2>📊 查重与磁盘空间分析</h2>
    <div class="grid2">
      <div>
        <h3 style="margin-bottom:12px;color:#4a5568;font-size:15px">重复分组规模（Top10）</h3>
        {_bar_chart_html(group_size_dist, "#fa709a")}
      </div>
      <div>
        <h3 style="margin-bottom:12px;color:#4a5568;font-size:15px">可节省空间分组（Top10）</h3>
        <table>
          <thead><tr><th>分组</th><th>成员数</th><th>可节省</th><th>保留文件</th></tr></thead>
          <tbody>{space_rows_html}</tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="section">
    <h2>🤖 AI 语义分析</h2>
    <div class="grid2">
      <div>
        <h3 style="margin-bottom:12px;color:#4a5568;font-size:15px">场景标签分布</h3>
        {_pie_chart_html(scene_dist)}
      </div>
      <div>
        <h3 style="margin-bottom:12px;color:#4a5568;font-size:15px">数据集用途分布</h3>
        {_pie_chart_html(purpose_dist)}
      </div>
    </div>
  </div>

  <div class="section">
    <h2>⏱️ 时长与分辨率分布</h2>
    <div class="grid2">
      <div>
        <h3 style="margin-bottom:12px;color:#4a5568;font-size:15px">时长分布</h3>
        {_bar_chart_html(dur_dist, "#4facfe")}
      </div>
      <div>
        <h3 style="margin-bottom:12px;color:#4a5568;font-size:15px">分辨率分布</h3>
        {_bar_chart_html(res_dist, "#43e97b")}
      </div>
    </div>
  </div>

  <div class="section">
    <h2>✅ 质量检测</h2>
    <div class="grid2">
      <div>
        <h3 style="margin-bottom:12px;color:#4a5568;font-size:15px">质量分分布</h3>
        {_bar_chart_html(q_dist, "#a18cd1")}
      </div>
      <div>
        <h3 style="margin-bottom:12px;color:#4a5568;font-size:15px">低质素材明细（Top15）</h3>
        <table>
          <thead><tr><th>质量分</th><th>场景标签</th><th>路径</th></tr></thead>
          <tbody>{lowq_rows_html}</tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="footer">
    本报告由 report_generator.py 自动生成 · 数据来自 video_hash_cache.json · 离线可打开
  </div>
</div>
</body>
</html>"""


def _stat_card(label: str, value: Any, color: str, is_text: bool = False) -> str:
    """生成单个统计卡片 HTML。"""
    display = value if is_text else f"{value}"
    return (
        f'<div class="card" style="border-top-color:{color}">'
        f'<div class="num" style="color:{color}">{_xml_escape(str(display))}</div>'
        f'<div class="lbl">{_xml_escape(label)}</div></div>'
    )


# ============================================================
# 模块 5：export_pdf_report PDF 导出
# ============================================================

class _HTMLTextExtractor(HTMLParser):
    """简易 HTML 文本提取器，提取标题/段落/列表/单元格文本块。"""

    BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th"}

    def __init__(self) -> None:
        super().__init__()
        self.blocks: List[Tuple[int, str]] = []
        self._buf: List[str] = []
        self._level: int = 0  # 0=正文，1-6=标题级别

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in self.BLOCK_TAGS:
            self._flush()
            self._level = int(tag[1]) if (len(tag) == 2 and tag[0] == "h") else 0
        elif tag == "br":
            self._buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self._buf.append(data.strip())

    def _flush(self) -> None:
        if self._buf:
            text = " ".join(self._buf).strip()
            if text:
                self.blocks.append((self._level, text))
            self._buf = []
            self._level = 0

    def close(self) -> None:
        super().close()
        self._flush()


def _export_pdf_reportlab(
    html_path: str, pdf_path: str, project_name: str, watermark: str
) -> bool:
    """使用 reportlab 将 HTML 报告转为 PDF（结构化文本渲染）。"""
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except (IOError, OSError) as e:
        log(f"[PDF] 读取 HTML 失败: {e}", force=True)
        return False

    extractor = _HTMLTextExtractor()
    extractor.feed(html_content)
    extractor.close()

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "RgBody", parent=styles["BodyText"], fontSize=9, leading=13, spaceAfter=4
    )
    heading_styles = {
        1: ParagraphStyle("RgH1", parent=styles["Heading1"], fontSize=18, spaceAfter=8),
        2: ParagraphStyle("RgH2", parent=styles["Heading2"], fontSize=14, spaceAfter=6),
        3: ParagraphStyle("RgH3", parent=styles["Heading3"], fontSize=12, spaceAfter=4),
    }

    story = []
    for level, text in extractor.blocks:
        style = heading_styles.get(level, body_style)
        # reportlab Paragraph 需转义 XML 特殊字符
        story.append(Paragraph(_xml_escape(text), style))
        story.append(Spacer(1, 1.5 * mm))

    def _on_page(canv: "_rl_canvas.Canvas", doc: Any) -> None:
        # 设置 PDF 元数据标题（仅首次）
        if project_name and not getattr(canv, "_rg_title_set", False):
            canv.setTitle(project_name)
            canv.setAuthor("report_generator")
            canv._rg_title_set = True  # type: ignore
        # 绘制水印（每页中心透明斜文字）
        if watermark:
            canv.saveState()
            canv.setFont("Helvetica", 42)
            canv.setFillColorRGB(0.75, 0.75, 0.75, alpha=0.25)
            canv.translate(A4[0] / 2, A4[1] / 2)
            canv.rotate(45)
            canv.drawCentredString(0, 0, watermark)
            canv.restoreState()

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=project_name or "视频查重报告",
    )
    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return True


def _export_pdf_weasyprint(
    html_path: str, pdf_path: str, project_name: str, watermark: str
) -> bool:
    """使用 weasyprint 将 HTML 原样转为 PDF（保真度高）。"""
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except (IOError, OSError) as e:
        log(f"[PDF] 读取 HTML 失败: {e}", force=True)
        return False

    # 注入水印 CSS 与固定水印层
    style_css = ""
    wm_layer = ""
    if watermark:
        style_css += (
            ".pdf-watermark{position:fixed;top:50%;left:50%;"
            "transform:translate(-50%,-50%) rotate(-45deg);font-size:72px;"
            "color:rgba(170,170,170,0.22);z-index:9999;pointer-events:none;"
            "font-family:sans-serif;}"
        )
        wm_layer = f'<div class="pdf-watermark">{_xml_escape(watermark)}</div>'
    # 注入标题元数据
    title_tag = ""
    if project_name and "<title>" not in html_content.lower():
        title_tag = f"<title>{_xml_escape(project_name)}</title>"

    if style_css or title_tag:
        inject_head = f"{title_tag}<style>{style_css}</style>"
        if "</head>" in html_content:
            html_content = html_content.replace("</head>", inject_head + "</head>", 1)
        else:
            html_content = inject_head + html_content
    if wm_layer:
        if "</body>" in html_content:
            html_content = html_content.replace("</body>", wm_layer + "</body>", 1)
        else:
            html_content += wm_layer

    base_url = os.path.dirname(os.path.abspath(html_path))
    _WHTML(string=html_content, base_url=base_url).write_pdf(pdf_path)
    return True


def export_pdf_report(
    html_path: str,
    pdf_path: str,
    project_name: str = "",
    watermark: str = "",
) -> bool:
    """
    基于 HTML 报告导出 PDF。

    - 优先使用 reportlab（如安装），降级使用 weasyprint，均缺失时给出 pip 安装提示
    - 支持水印文字（每页中心透明斜文字）
    - 支持项目名称作为 PDF 元数据标题

    Args:
        html_path: 源 HTML 文件路径
        pdf_path: 输出 PDF 路径
        project_name: PDF 元数据标题
        watermark: 水印文字

    Returns:
        是否成功生成
    """
    if not os.path.exists(html_path):
        log(f"[PDF] HTML 文件不存在: {html_path}", force=True)
        return False

    # 优先 reportlab
    if _REPORTLAB_OK:
        log("[PDF] 使用 reportlab 引擎导出...", force=True)
        try:
            if _export_pdf_reportlab(html_path, pdf_path, project_name, watermark):
                log(f"[PDF] 已生成 → {pdf_path}", force=True)
                return True
        except Exception as e:
            log(f"[PDF] reportlab 导出失败: {e}，尝试降级...", force=True)

    # 降级 weasyprint
    if _WEASYPRINT_OK:
        log("[PDF] 使用 weasyprint 引擎导出...", force=True)
        try:
            if _export_pdf_weasyprint(html_path, pdf_path, project_name, watermark):
                log(f"[PDF] 已生成 → {pdf_path}", force=True)
                return True
        except Exception as e:
            log(f"[PDF] weasyprint 导出失败: {e}", force=True)

    # 均缺失或失败
    print(_pdf_deps_hint())
    return False


# ============================================================
# 模块 6：generate_diff_report 数据对比报告
# ============================================================

def _group_sets(groups: List[dict]) -> List[frozenset]:
    """将分组转为路径集合列表（用于对比）。"""
    return [frozenset(_group_member_paths(g)) for g in groups]


def generate_diff_report(
    old_cache_path: str,
    new_cache_path: str,
    output_path: str,
) -> str:
    """
    对比两次扫描缓存，输出 Markdown 格式差异报告。

    对比维度：新增视频、已清理视频、素材变动（大小/修改时间变化）、新增重复分组。

    Args:
        old_cache_path: 旧缓存路径
        new_cache_path: 新缓存路径
        output_path: 输出 Markdown 路径

    Returns:
        生成的报告绝对路径
    """
    if not _check_deps():
        return ""
    old_cache = load_cache(old_cache_path)
    new_cache = load_cache(new_cache_path)

    old_entries = {k: v for k, v in old_cache.items() if not k.startswith("_") and isinstance(v, dict)}
    new_entries = {k: v for k, v in new_cache.items() if not k.startswith("_") and isinstance(v, dict)}

    old_paths = set(old_entries.keys())
    new_paths = set(new_entries.keys())

    added = sorted(new_paths - old_paths)
    removed = sorted(old_paths - new_paths)
    common = old_paths & new_paths

    # 素材变动：大小或修改时间发生变化
    changed = []
    for p in sorted(common):
        o, n = old_entries[p], new_entries[p]
        if o.get("size") != n.get("size") or abs(
            float(o.get("mtime", 0)) - float(n.get("mtime", 0))
        ) >= 1.0:
            changed.append(p)

    # 重复分组对比
    old_groups = _compute_duplicate_groups(old_cache)[0]
    new_groups = _compute_duplicate_groups(new_cache)[0]
    old_sets = _group_sets(old_groups)
    new_sets = _group_sets(new_groups)

    # 新增重复分组：新分组中存在成员路径组合在旧分组中未出现
    new_dup_groups = []
    for g in new_groups:
        gset = frozenset(_group_member_paths(g))
        # 若该分组的路径集合未被任一旧分组覆盖，视为新增重复
        if not any(gset <= old_s for old_s in old_sets):
            # 且至少包含 2 个成员才算重复
            if len(gset) >= 2:
                new_dup_groups.append(g)

    gen_time = time.strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = []
    lines.append("# 视频扫描数据对比报告")
    lines.append("")
    lines.append(f"- 生成时间：{gen_time}")
    lines.append(f"- 旧缓存：`{old_cache_path}`（{len(old_entries)} 条）")
    lines.append(f"- 新缓存：`{new_cache_path}`（{len(new_entries)} 条）")
    lines.append("")
    lines.append("## 概览统计")
    lines.append("")
    lines.append("| 指标 | 数量 |")
    lines.append("|------|------|")
    lines.append(f"| 新增视频 | {len(added)} |")
    lines.append(f"| 已清理视频 | {len(removed)} |")
    lines.append(f"| 素材变动 | {len(changed)} |")
    lines.append(f"| 新增重复分组 | {len(new_dup_groups)} |")
    lines.append("")

    # 新增视频
    lines.append("## 一、新增视频")
    lines.append("")
    if added:
        lines.append("| 序号 | 大小 | 时长(秒) | 路径 |")
        lines.append("|------|------|----------|------|")
        for i, p in enumerate(added, 1):
            e = new_entries[p]
            size = _format_size(int(e.get("size", 0) or 0))
            dur = f"{float(e.get('duration', 0) or 0):.1f}"
            lines.append(f"| {i} | {size} | {dur} | `{p}` |")
    else:
        lines.append("> 无新增视频")
    lines.append("")

    # 已清理视频
    lines.append("## 二、已清理视频")
    lines.append("")
    if removed:
        lines.append("| 序号 | 原大小 | 路径 |")
        lines.append("|------|--------|------|")
        for i, p in enumerate(removed, 1):
            e = old_entries[p]
            size = _format_size(int(e.get("size", 0) or 0))
            lines.append(f"| {i} | {size} | `{p}` |")
    else:
        lines.append("> 无已清理视频")
    lines.append("")

    # 素材变动
    lines.append("## 三、素材变动")
    lines.append("")
    if changed:
        lines.append("| 序号 | 原大小 | 新大小 | 路径 |")
        lines.append("|------|--------|--------|------|")
        for i, p in enumerate(changed, 1):
            o, n = old_entries[p], new_entries[p]
            osz = _format_size(int(o.get("size", 0) or 0))
            nsz = _format_size(int(n.get("size", 0) or 0))
            lines.append(f"| {i} | {osz} | {nsz} | `{p}` |")
    else:
        lines.append("> 无素材变动")
    lines.append("")

    # 新增重复分组
    lines.append("## 四、新增重复分组")
    lines.append("")
    if new_dup_groups:
        for i, g in enumerate(new_dup_groups, 1):
            members = g["members"]
            wasted = sum(info["size"] for idx, info in members if idx != g["retain_idx"])
            lines.append(f"### 分组 {i}（{len(members)} 个重复，可节省 {_format_size(wasted)}）")
            lines.append("")
            lines.append("| 角色 | 大小 | 路径 |")
            lines.append("|------|------|------|")
            for idx, info in members:
                role = "✅保留" if idx == g["retain_idx"] else "🗑冗余"
                lines.append(f"| {role} | {_format_size(info['size'])} | `{info['path']}` |")
            lines.append("")
    else:
        lines.append("> 无新增重复分组")
    lines.append("")

    _atomic_write(output_path, "\n".join(lines))
    log(f"[报告] 对比报告已生成 → {output_path}", force=True)
    return os.path.abspath(output_path)


# ============================================================
# 模块 7：generate_space_report 磁盘空间优化报告
# ============================================================

def generate_space_report(cache_path: str, output_path: str) -> str:
    """
    生成磁盘空间优化 Markdown 报告。

    - 统计各组重复占用容量，按可节省空间降序排序
    - 给出清理优先级建议（高/中/低）

    Args:
        cache_path: 缓存路径
        output_path: 输出 Markdown 路径

    Returns:
        生成的报告绝对路径
    """
    if not _check_deps():
        return ""
    cache = load_cache(cache_path)
    groups, mp4_files, _ = _compute_duplicate_groups(cache, threshold=0.85)

    # 计算每组可节省空间
    rows = []
    total_reclaimable = 0
    total_dup_size = 0
    for gi, g in enumerate(groups, 1):
        members = g["members"]
        retain_info = next((info for idx, info in members if idx == g["retain_idx"]), members[0][1])
        wasted = sum(info["size"] for idx, info in members if idx != g["retain_idx"])
        group_total = sum(info["size"] for _, info in members)
        total_reclaimable += wasted
        total_dup_size += group_total
        rows.append({
            "gi": gi, "members": len(members), "wasted": wasted,
            "group_total": group_total, "retain_path": retain_info["path"],
        })

    # 按可节省空间降序
    rows.sort(key=lambda x: x["wasted"], reverse=True)

    # 优先级阈值：>=1GB 高，>=100MB 中，其余低
    GB = 1024 ** 3
    MB = 1024 ** 2

    def _priority(wasted: int) -> Tuple[str, str]:
        if wasted >= GB:
            return ("p-high", "高")
        if wasted >= 100 * MB:
            return ("p-mid", "中")
        return ("p-low", "低")

    gen_time = time.strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = []
    lines.append("# 磁盘空间优化报告")
    lines.append("")
    lines.append(f"- 生成时间：{gen_time}")
    lines.append(f"- 数据来源：`{cache_path}`")
    lines.append(f"- 重复分组数：{len(groups)}")
    lines.append(f"- 重复视频总占用：**{_format_size(total_dup_size)}**")
    lines.append(f"- 可节省空间：**{_format_size(total_reclaimable)}**")
    lines.append("")
    lines.append("## 清理优先级建议（按可节省空间降序）")
    lines.append("")
    if rows:
        lines.append("| 优先级 | 分组 | 成员数 | 组内总占用 | 可节省 | 保留文件 |")
        lines.append("|--------|------|--------|-----------|--------|----------|")
        for r in rows:
            _, p_label = _priority(r["wasted"])
            lines.append(
                f"| {p_label} | 分组 {r['gi']} | {r['members']} | "
                f"{_format_size(r['group_total'])} | **{_format_size(r['wasted'])}** | "
                f"`{r['retain_path']}` |"
            )
        lines.append("")
        lines.append("## 清理建议")
        lines.append("")
        high = sum(1 for r in rows if r["wasted"] >= GB)
        mid = sum(1 for r in rows if 100 * MB <= r["wasted"] < GB)
        low = sum(1 for r in rows if r["wasted"] < 100 * MB)
        lines.append(f"- 🔴 **高优先级**（可节省 ≥1GB）：{high} 组，建议立即清理")
        lines.append(f"- 🟠 **中优先级**（可节省 100MB~1GB）：{mid} 组，建议近期清理")
        lines.append(f"- 🟢 **低优先级**（可节省 <100MB）：{low} 组，可按需清理")
        lines.append("")
        lines.append("> 清理前请使用 `find_mp4.py --gen-cleanup --backup-path <备份目录>` 生成安全清理脚本，")
        lines.append("> 并通过 `--gen-restore` 准备恢复脚本，避免误删。")
    else:
        lines.append("> 未发现重复分组，无需清理。")
    lines.append("")

    _atomic_write(output_path, "\n".join(lines))
    log(f"[报告] 空间优化报告已生成 → {output_path}", force=True)
    return os.path.abspath(output_path)


# ============================================================
# 模块 8：generate_quality_report AI 数据集质检报告
# ============================================================

def generate_quality_report(cache_path: str, output_path: str) -> str:
    """
    生成 AI 数据集质检 Markdown 报告。

    - 训练素材达标率
    - 场景均衡度
    - 低质素材明细
    - 补素材建议

    Args:
        cache_path: 缓存路径
        output_path: 输出 Markdown 路径

    Returns:
        生成的报告绝对路径
    """
    if not _check_deps():
        return ""
    cache = load_cache(cache_path)
    entries = {k: v for k, v in cache.items() if not k.startswith("_") and isinstance(v, dict)}
    total = len(entries)

    # 达标率：is_training_ready 或 quality_score >= 0.6
    ready_count = sum(
        1 for e in entries.values()
        if e.get("is_training_ready") or (
            e.get("quality_score") is not None and float(e.get("quality_score", 0)) >= 0.6
        )
    )
    ready_rate = (ready_count / total * 100) if total else 0.0

    # 场景均衡度
    scene_dist = _count_distribution(
        [e.get("scene_tags") for e in entries.values() if e.get("scene_tags")]
    )
    scene_total = sum(v for _, v in scene_dist) or 1
    max_scene_ratio = (max(v for _, v in scene_dist) / scene_total) if scene_dist else 0.0
    # 均衡度评分：越接近均匀越高（1 - 基尼简化指标）
    if scene_dist:
        n = len(scene_dist)
        uniform = 1.0 / n
        deviation = sum(abs(v / scene_total - uniform) for _, v in scene_dist) / 2
        balance_score = max(0.0, 1.0 - deviation)
    else:
        balance_score = 0.0

    # 低质素材明细
    low_q = []
    for path, e in entries.items():
        qs = e.get("quality_score")
        if qs is not None and float(qs) < 0.5:
            low_q.append((path, float(qs), e.get("scene_tags")))
    low_q.sort(key=lambda x: x[1])

    # 用途分布
    purpose_dist = _count_distribution(
        [e.get("dataset_purpose") for e in entries.values() if e.get("dataset_purpose")]
    )

    gen_time = time.strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = []
    lines.append("# AI 数据集质检报告")
    lines.append("")
    lines.append(f"- 生成时间：{gen_time}")
    lines.append(f"- 数据来源：`{cache_path}`")
    lines.append(f"- 素材总数：{total}")
    lines.append("")
    lines.append("## 一、训练素材达标率")
    lines.append("")
    lines.append(f"- 训练就绪素材：**{ready_count}** / {total}")
    lines.append(f"- 达标率：**{ready_rate:.1f}%**")
    if ready_rate >= 80:
        lines.append("- 评价：✅ 达标率优秀，素材质量整体满足训练要求")
    elif ready_rate >= 60:
        lines.append("- 评价：⚠️ 达标率一般，建议优化低质素材")
    else:
        lines.append("- 评价：❌ 达标率偏低，需重点补充高质量素材")
    lines.append("")

    lines.append("## 二、场景均衡度")
    lines.append("")
    lines.append(f"- 场景类别数：{len(scene_dist)}")
    lines.append(f"- 均衡度评分：**{balance_score:.2f}**（1.0 为完全均衡）")
    lines.append(f"- 最大场景占比：**{max_scene_ratio * 100:.1f}%**")
    lines.append("")
    if scene_dist:
        lines.append("| 场景标签 | 数量 | 占比 |")
        lines.append("|----------|------|------|")
        for label, cnt in scene_dist:
            lines.append(f"| {label} | {cnt} | {cnt / scene_total * 100:.1f}% |")
    lines.append("")

    lines.append("## 三、数据集用途分布")
    lines.append("")
    if purpose_dist:
        lines.append("| 用途 | 数量 | 占比 |")
        lines.append("|------|------|------|")
        pt = sum(v for _, v in purpose_dist) or 1
        for label, cnt in purpose_dist:
            lines.append(f"| {label} | {cnt} | {cnt / pt * 100:.1f}% |")
    else:
        lines.append("> 未标注数据集用途")
    lines.append("")

    lines.append("## 四、低质素材明细")
    lines.append("")
    lines.append(f"- 低质素材数：**{len(low_q)}**（质量分 < 0.5）")
    if low_q:
        lines.append("")
        lines.append("| 质量分 | 场景标签 | 路径 |")
        lines.append("|--------|----------|------|")
        for path, qs, tags in low_q[:50]:
            tag_str = ", ".join(tags) if isinstance(tags, list) else (tags or "")
            lines.append(f"| {qs:.2f} | {tag_str} | `{path}` |")
        if len(low_q) > 50:
            lines.append(f"\n> 仅显示前 50 条，共 {len(low_q)} 条低质素材")
    else:
        lines.append("> 无低质素材")
    lines.append("")

    lines.append("## 五、补素材建议")
    lines.append("")
    if scene_dist and max_scene_ratio > 0.5:
        dominant = scene_dist[0][0]
        lines.append(f"- ⚠️ 场景 **{dominant}** 占比过高（{max_scene_ratio * 100:.1f}%），"
                     f"建议补充其他场景素材以提升均衡度")
    if balance_score < 0.5 and len(scene_dist) > 0:
        lines.append("- ⚠️ 场景均衡度偏低，建议针对样本量最少的场景补充素材：")
        for label, cnt in scene_dist[-3:]:
            lines.append(f"  - **{label}**：当前仅 {cnt} 条")
    if ready_rate < 60:
        lines.append("- ❌ 达标率偏低，建议重新采集或增强低质素材（去模糊、补光等）")
    if len(low_q) > total * 0.2 and total > 0:
        lines.append(f"- ⚠️ 低质素材占比 {len(low_q) / total * 100:.1f}% 偏高，"
                     f"建议从数据集中剔除或单独标注")
    if not (scene_dist and max_scene_ratio > 0.5) and ready_rate >= 80 and balance_score >= 0.5:
        lines.append("- ✅ 数据集质量与均衡度良好，无需额外补素材")
    lines.append("")

    _atomic_write(output_path, "\n".join(lines))
    log(f"[报告] 质检报告已生成 → {output_path}", force=True)
    return os.path.abspath(output_path)


# ============================================================
# 模块 9：archive_reports 批量打包报告
# ============================================================

def archive_reports(
    reports_dir: str,
    output_zip: str = "",
    name_prefix: str = "",
) -> str:
    """
    打包所有报告为 zip 归档，自动按扫描时间命名。

    Args:
        reports_dir: 报告所在目录
        output_zip: 输出 zip 路径（为空时自动命名 reports_YYYYMMDD_HHMMSS.zip）
        name_prefix: 文件名前缀

    Returns:
        生成的 zip 文件绝对路径
    """
    if not os.path.isdir(reports_dir):
        log(f"[打包] 报告目录不存在: {reports_dir}", force=True)
        return ""

    # 自动按扫描时间命名
    if not output_zip:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        prefix = f"{name_prefix}_" if name_prefix else ""
        output_zip = os.path.join(reports_dir, f"{prefix}reports_{stamp}.zip")

    # 收集报告文件（html/md/csv/log/txt/json）
    report_exts = (".html", ".md", ".csv", ".log", ".txt", ".json")
    collected = []
    for name in sorted(os.listdir(reports_dir)):
        full = os.path.join(reports_dir, name)
        if not os.path.isfile(full):
            continue
        if name.lower().endswith(report_exts) and not name.endswith(".tmp"):
            collected.append((full, name))

    if not collected:
        log(f"[打包] 未发现可打包的报告文件: {reports_dir}", force=True)
        return ""

    # 原子写入：先写 .tmp 再 rename
    tmp_zip = output_zip + ".tmp"
    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for full, arcname in collected:
                zf.write(full, arcname)
        os.replace(tmp_zip, output_zip)
    except (IOError, OSError, zipfile.BadZipFile) as e:
        log(f"[打包] 打包失败: {e}", force=True)
        if os.path.exists(tmp_zip):
            try:
                os.remove(tmp_zip)
            except OSError:
                pass
        return ""

    log(f"[打包] 已打包 {len(collected)} 个文件 → {output_zip}", force=True)
    return os.path.abspath(output_zip)


# ============================================================
# 模块 10：命令行入口
# ============================================================

def _build_arg_parser() -> argparse.ArgumentParser:
    """构建 argparse 子命令解析器。"""
    parser = argparse.ArgumentParser(
        prog="report_generator",
        description="MP4 视频查重报告生成器（基于 find_mp4.py v2.5 缓存）",
    )
    sub = parser.add_subparsers(dest="command", help="可用子命令")

    # 全局可选参数（通过 parent 复用至各子命令）
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--path-mask", action="store_true", help="路径脱敏：隐藏路径中间层级")
    parent.add_argument("--project-name", default="", help="项目名称")
    parent.add_argument("--operator", default="", help="操作人员")

    # full-report
    p_full = sub.add_parser(
        "full-report", parents=[parent], help="生成综合汇总 HTML 报告"
    )
    p_full.add_argument("--cache", default=CACHE_FILE, help="缓存文件路径")
    p_full.add_argument("--output", default="full_report.html", help="输出 HTML 路径")

    # export-pdf
    p_pdf = sub.add_parser(
        "export-pdf", parents=[parent], help="基于 HTML 报告导出 PDF"
    )
    p_pdf.add_argument("--html", required=True, help="源 HTML 文件路径")
    p_pdf.add_argument("--pdf", required=True, help="输出 PDF 路径")
    p_pdf.add_argument("--watermark", default="", help="水印文字")

    # diff-report
    p_diff = sub.add_parser(
        "diff-report", parents=[parent], help="生成数据对比 Markdown 报告"
    )
    p_diff.add_argument("--old", required=True, help="旧缓存路径")
    p_diff.add_argument("--new", required=True, help="新缓存路径")
    p_diff.add_argument("--output", default="diff_report.md", help="输出 Markdown 路径")

    # space-report
    p_space = sub.add_parser(
        "space-report", parents=[parent], help="生成磁盘空间优化 Markdown 报告"
    )
    p_space.add_argument("--cache", default=CACHE_FILE, help="缓存文件路径")
    p_space.add_argument("--output", default="space_report.md", help="输出 Markdown 路径")

    # quality-report
    p_qual = sub.add_parser(
        "quality-report", parents=[parent], help="生成 AI 数据集质检 Markdown 报告"
    )
    p_qual.add_argument("--cache", default=CACHE_FILE, help="缓存文件路径")
    p_qual.add_argument("--output", default="quality_report.md", help="输出 Markdown 路径")

    # archive
    p_arch = sub.add_parser(
        "archive", parents=[parent], help="批量打包报告为 zip 归档"
    )
    p_arch.add_argument("--reports-dir", required=True, help="报告所在目录")
    p_arch.add_argument("--output-zip", default="", help="输出 zip 路径（默认自动命名）")
    p_arch.add_argument("--name-prefix", default="", help="文件名前缀")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """
    命令行入口，支持子命令：full-report / export-pdf / diff-report /
    space-report / quality-report / archive。

    Args:
        argv: 参数列表（None 时取 sys.argv）

    Returns:
        退出码（0=成功，1=失败）
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "full-report":
        out = generate_full_report(
            cache_path=args.cache,
            output_path=args.output,
            project_name=args.project_name,
            operator=args.operator,
            path_mask=args.path_mask,
        )
        return 0 if out else 1

    if args.command == "export-pdf":
        ok = export_pdf_report(
            html_path=args.html,
            pdf_path=args.pdf,
            project_name=args.project_name,
            watermark=args.watermark,
        )
        return 0 if ok else 1

    if args.command == "diff-report":
        out = generate_diff_report(
            old_cache_path=args.old,
            new_cache_path=args.new,
            output_path=args.output,
        )
        return 0 if out else 1

    if args.command == "space-report":
        out = generate_space_report(
            cache_path=args.cache,
            output_path=args.output,
        )
        return 0 if out else 1

    if args.command == "quality-report":
        out = generate_quality_report(
            cache_path=args.cache,
            output_path=args.output,
        )
        return 0 if out else 1

    if args.command == "archive":
        out = archive_reports(
            reports_dir=args.reports_dir,
            output_zip=args.output_zip,
            name_prefix=args.name_prefix,
        )
        return 0 if out else 1

    parser.print_help()
    return 0


# ============================================================
# 使用示例（命令行）
# ============================================================
# # 1. 生成综合汇总 HTML 报告
# python report_generator.py full-report --cache video_hash_cache.json \
#     --output full_report.html --project-name "车载监控数据集" --operator 张三 --path-mask
#
# # 2. 导出 PDF（基于 HTML，支持水印）
# python report_generator.py export-pdf --html full_report.html --pdf full_report.pdf \
#     --project-name "车载监控数据集" --watermark "内部资料"
#
# # 3. 两次扫描数据对比报告
# python report_generator.py diff-report --old video_hash_cache.json \
#     --new video_hash_cache_new.json --output diff_report.md
#
# # 4. 磁盘空间优化报告
# python report_generator.py space-report --cache video_hash_cache.json --output space_report.md
#
# # 5. AI 数据集质检报告
# python report_generator.py quality-report --cache video_hash_cache.json --output quality_report.md
#
# # 6. 批量打包报告（自动按时间命名）
# python report_generator.py archive --reports-dir ./reports --name-prefix batch1
#
# # 编程式调用示例：
# #     from report_generator import generate_full_report, export_pdf_report
# #     generate_full_report("video_hash_cache.json", "full_report.html",
# #                          project_name="测试", operator="李四", path_mask=True)
# #     export_pdf_report("full_report.html", "full_report.pdf",
# #                       project_name="测试", watermark="机密")


if __name__ == "__main__":
    sys.exit(main())
