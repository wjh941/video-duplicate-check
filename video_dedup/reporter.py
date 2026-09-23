# -*- coding: utf-8 -*-
"""video_dedup — 由 find_mp4.py v2.8 按职责拆分生成的模块。"""
from __future__ import annotations

import argparse
import base64
import configparser
import csv
import fnmatch
import glob as _glob
import io
import json
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from collections import defaultdict
from concurrent.futures import (ThreadPoolExecutor, as_completed, wait,
                                FIRST_COMPLETED,
                                TimeoutError as FutureTimeout)
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
from PIL import Image
import imagehash

try:  # tqdm 可选
    from tqdm import tqdm as _tqdm
    TQDM_AVAILABLE = True
except ImportError:  # pragma: no cover
    TQDM_AVAILABLE = False

try:  # psutil 可选
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    PSUTIL_AVAILABLE = False

from .constants import *  # noqa: F401,F403
from .context import log, _APP_CTX, _get_memory_usage
from .utils import (_atomic_write_text, _atomic_write_lines,
                    _prefixed_path, _format_size, _normalize_path)
from .hasher import _video_quality_score



def export_csv(similar_pairs: list[dict], csv_path: str, threshold: float,
               semantic_data: dict = None, lite_csv: bool = False):
    """导出比对明细 CSV（v2.2 扩展语义标签列，v2.3 新增 lite_csv 轻量模式）"""
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            if lite_csv:
                # v2.3 新增：轻量化 CSV，仅路径和相似度
                header = ["视频A路径", "视频B路径", "相似度"]
            else:
                header = ["视频A路径", "视频B路径", "归一化距离", "相似度", "是否判定重复"]
                if semantic_data:
                    header.extend(["A场景标签", "A数据集用途", "B场景标签", "B数据集用途"])
            writer.writerow(header)
            for pair in similar_pairs:
                if lite_csv:
                    row = [pair["path_a"], pair["path_b"], f"{pair['similarity']:.4f}"]
                else:
                    is_dup = "是" if pair["similarity"] >= threshold else "否"
                    row = [
                        pair["path_a"], pair["path_b"],
                        f"{pair['distance']:.6f}", f"{pair['similarity']:.4f}", is_dup,
                    ]
                    if semantic_data:
                        sd_a = semantic_data.get(pair["path_a"], {})
                        sd_b = semantic_data.get(pair["path_b"], {})
                        scene_a = ", ".join(sd_a.get("scene_tags", [])[:3]) or "-"
                        purpose_a = sd_a.get("dataset_purpose", "-") or "-"
                        scene_b = ", ".join(sd_b.get("scene_tags", [])[:3]) or "-"
                        purpose_b = sd_b.get("dataset_purpose", "-") or "-"
                        row.extend([scene_a, purpose_a, scene_b, purpose_b])
                writer.writerow(row)
        log(f"[导出] CSV → {csv_path}")
    except (IOError, OSError) as e:
        log(f"[错误] CSV 导出失败: {e}")


def export_bad_paths(bad_videos: list[dict], output_dir: str):
    """v2.3 新增：单独输出损坏视频纯路径清单"""
    if not bad_videos:
        return
    bad_paths_file = os.path.join(output_dir, "bad_video_paths.txt")
    try:
        with open(bad_paths_file, "w", encoding="utf-8") as f:
            for bv in bad_videos:
                f.write(bv["path"] + "\n")
        log(f"[导出] 损坏路径清单 → {bad_paths_file}")
    except (IOError, OSError):
        pass


def export_groups_txt(groups: list[dict], mp4_files: list[dict], group_path: str,
                      threshold: float, min_sim: float = 0.0, semantic_data: dict = None):
    """导出分组报告 TXT（v2.2 扩展语义标签）"""
    try:
        filtered = []
        for g in groups:
            max_sim = max(g["similarities"].values()) if g["similarities"] else 1.0
            if max_sim >= min_sim:
                filtered.append(g)

        with open(group_path, "w", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write(f"  MP4 相似视频分组报告\n")
            f.write(f"  相似度阈值: {threshold:.0%}\n")
            f.write(f"  生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"  分组总数: {len(filtered)}\n")
            f.write("=" * 60 + "\n\n")

            if not filtered:
                f.write("未发现符合条件的相似视频分组。\n")
            else:
                for gi, group in enumerate(filtered, 1):
                    f.write(f"【第 {gi} 组】（共 {len(group['members'])} 个视频）\n")
                    f.write("-" * 50 + "\n")
                    # 统计该组的数据集用途
                    if semantic_data:
                        purposes = []
                        for idx, info in group["members"]:
                            sd = semantic_data.get(info["path"], {})
                            p = sd.get("dataset_purpose", "")
                            if p:
                                purposes.append(p)
                        if purposes:
                            from collections import Counter
                            pc = Counter(purposes)
                            f.write(f"  内容归类: {dict(pc)}\n")
                    for fi, (idx, info) in enumerate(group["members"]):
                        mark = " ★ 建议保留" if idx == group["retain_idx"] else " ✗ 建议清理"
                        f.write(f"  {fi + 1}. {info['name']}{mark}\n")
                        f.write(f"     路径: {info['path']}\n")
                        f.write(f"     大小: {info['size_readable']}\n")
                        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info["mtime"]))
                        f.write(f"     修改时间: {mtime}\n")
                        # v2.2 语义标签
                        if semantic_data:
                            sd = semantic_data.get(info["path"], {})
                            if sd.get("scene_tags"):
                                f.write(f"     场景: {', '.join(sd['scene_tags'][:3])}\n")
                            if sd.get("dataset_purpose"):
                                f.write(f"     用途: {sd['dataset_purpose']}\n")
                            if sd.get("quality_score", 0) > 0:
                                qs = sd["quality_score"]
                                quality_label = "✓适合训练" if sd.get("is_training_ready") else "✗质量不足"
                                f.write(f"     质量: {qs:.2f} ({quality_label})\n")
                        f.write("\n")
                    f.write("-" * 50 + "\n\n")
        log(f"[导出] 分组报告(TXT) → {group_path}")
    except (IOError, OSError) as e:
        log(f"[错误] TXT 导出失败: {e}")


def export_groups_md(groups: list[dict], mp4_files: list[dict], md_path: str,
                      threshold: float, min_sim: float = 0.0, semantic_data: dict = None):
    """导出分组报告 Markdown（v2.2 扩展语义标签）"""
    try:
        filtered = []
        for g in groups:
            max_sim = max(g["similarities"].values()) if g["similarities"] else 1.0
            if max_sim >= min_sim:
                filtered.append(g)

        total_savable = 0
        for g in filtered:
            for idx, info in g["members"]:
                if idx != g["retain_idx"]:
                    total_savable += info["size"]

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# MP4 相似视频分组报告\n\n")
            f.write(f"- **相似度阈值**: {threshold:.0%}\n")
            f.write(f"- **生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- **分组总数**: {len(filtered)}\n")
            f.write(f"- **可节省空间**: {_format_size(total_savable)}\n\n")

            if not filtered:
                f.write("> 未发现符合条件的相似视频分组。\n")
            else:
                for gi, group in enumerate(filtered, 1):
                    f.write(f"## 第 {gi} 组（{len(group['members'])} 个视频）\n\n")
                    # v2.2 内容归类统计
                    if semantic_data:
                        purposes = []
                        for idx, info in group["members"]:
                            sd = semantic_data.get(info["path"], {})
                            p = sd.get("dataset_purpose", "")
                            if p:
                                purposes.append(p)
                        if purposes:
                            from collections import Counter
                            pc = Counter(purposes)
                            f.write(f"> 内容归类: {dict(pc)}\n\n")
                    f.write("| # | 文件名 | 大小 | 修改时间 | 建议 |")
                    if semantic_data:
                        f.write(" 场景 | 用途 |")
                    f.write("\n")
                    f.write("|---|--------|------|----------|------|")
                    if semantic_data:
                        f.write("------|------|")
                    f.write("\n")
                    for fi, (idx, info) in enumerate(group["members"]):
                        mark = "✅ 保留" if idx == group["retain_idx"] else "❌ 清理"
                        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info["mtime"]))
                        line = f"| {fi + 1} | `{info['name']}` | {info['size_readable']} | {mtime} | {mark} |"
                        if semantic_data:
                            sd = semantic_data.get(info["path"], {})
                            scene = ", ".join(sd.get("scene_tags", [])[:2]) or "-"
                            purpose = sd.get("dataset_purpose", "-") or "-"
                            line += f" {scene} | {purpose} |"
                        f.write(line + "\n")
                    f.write("\n")
        log(f"[导出] 分组报告(MD) → {md_path}")
    except (IOError, OSError) as e:
        log(f"[错误] MD 导出失败: {e}")


def _extract_video_thumbnail_base64(video_path: str, max_size: int = 160) -> str:
    """
    提取视频首帧并转为 base64 缩略图（v2.4 新增 HTML 缩略图预览）。
    返回 data URI 字符串，失败返回空字符串。
    """
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return ""
        # 跳过开头10%，取相对稳定帧
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total * 0.1)))
        ret, frame = cap.read()
        if not ret or frame is None:
            return ""
        # 缩放到 max_size 宽度
        h, w = frame.shape[:2]
        if w > max_size:
            scale = max_size / w
            frame = cv2.resize(frame, (max_size, int(h * scale)),
                               interpolation=cv2.INTER_AREA)
        # 转 JPEG base64
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ok:
            return ""
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return ""
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def export_groups_html(groups: list[dict], mp4_files: list[dict], html_path: str,
                        threshold: float, min_sim: float = 0.0, semantic_data: dict = None,
                        video_hashes: dict = None):
    """
    导出 HTML 可视化报告（v2.2 扩展语义标签，v2.4 新增首帧缩略图预览）。
    #【改造注释】重复分组 HTML 报告内嵌视频首帧 base64 缩略图，直观对比相似画面。
    """
    try:
        filtered = []
        for g in groups:
            max_sim = max(g["similarities"].values()) if g["similarities"] else 1.0
            if max_sim >= min_sim:
                filtered.append(g)

        total_savable = 0
        for g in filtered:
            for idx, info in g["members"]:
                if idx != g["retain_idx"]:
                    total_savable += info["size"]

        # v2.2 语义统计
        semantic_summary = {}
        if semantic_data:
            for path, sd in semantic_data.items():
                p = sd.get("dataset_purpose", "未分类")
                semantic_summary[p] = semantic_summary.get(p, 0) + 1

        html_parts = [
            "<!DOCTYPE html>\n<html lang='zh-CN'>\n<head>\n",
            "<meta charset='UTF-8'>\n",
            "<title>MP4 相似视频报告</title>\n",
            "<style>",
            "body{font-family:sans-serif;margin:20px;background:#f5f5f5}",
            "h1{color:#333}h2{color:#555;margin-top:30px}",
            ".group{background:#fff;border-radius:8px;padding:15px;margin:10px 0;",
            "box-shadow:0 2px 4px rgba(0,0,0,.1)}",
            ".video{display:flex;align-items:center;padding:8px;border-bottom:1px solid #eee}",
            ".video:last-child{border-bottom:none}",
            ".badge{padding:2px 8px;border-radius:4px;font-size:12px;margin-left:10px}",
            ".retain{background:#4CAF50;color:#fff}",
            ".clean{background:#f44336;color:#fff}",
            ".stats{background:#fff;padding:15px;border-radius:8px;margin:15px 0}",
            ".summary{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}",
            ".summary div{background:#f8f8f8;padding:10px;border-radius:4px;text-align:center}",
            ".semantic-tag{background:#2196F3;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:5px}",
            ".purpose-tag{background:#FF9800;color:white;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:5px}",
            ".thumb{width:80px;height:60px;object-fit:cover;border-radius:4px;margin-right:10px;border:1px solid #ddd}",
            ".thumb-placeholder{width:80px;height:60px;background:#eee;border-radius:4px;margin-right:10px;display:flex;align-items:center;justify-content:center;color:#999;font-size:11px}",
            "</style>\n</head>\n<body>\n",
            f"<h1>MP4 相似视频分组报告</h1>\n",
            f"<div class='stats'><div class='summary'>",
            f"<div><h3>{len(mp4_files)}</h3><p>总视频数</p></div>",
            f"<div><h3>{len(filtered)}</h3><p>相似分组</p></div>",
            f"<div><h3>{_format_size(total_savable)}</h3><p>可节省空间</p></div>",
            f"<div><h3>{threshold:.0%}</h3><p>相似度阈值</p></div>",
            f"</div></div>\n",
        ]

        # v2.2 语义统计面板
        if semantic_summary:
            html_parts.append("<div class='stats'><h3>📊 AI 语义统计</h3>")
            html_parts.append("<div class='summary'>")
            for purpose, count in sorted(semantic_summary.items(), key=lambda x: -x[1]):
                html_parts.append(f"<div><h3>{count}</h3><p>{purpose}</p></div>")
            html_parts.append("</div></div>\n")

        # 【改造 v2.4】缩略图缓存，避免同组重复提取
        thumb_cache = {}

        for gi, group in enumerate(filtered, 1):
            html_parts.append(f"<div class='group'><h2>第 {gi} 组</h2>\n")
            # 【v2.7 新增】混合标注告警徽标（v2.9 修复：成员渲染循环此前被错误嵌在本条件内）
            if group.get("mixed_labels"):
                html_parts.append(
                    f"<p style='color:#c00;font-size:13px'>⚠ 混合标注分组：包含多个不同标注"
                    f"（{', '.join(group.get('labels', []))}），疑似固定机位场景误聚，清理前请人工复核。</p>\n")
            for fi, (idx, info) in enumerate(group["members"]):
                mark = "retain" if idx == group["retain_idx"] else "clean"
                text = "保留" if idx == group["retain_idx"] else "清理"
                extra_tags = ""
                if semantic_data:
                    sd = semantic_data.get(info["path"], {})
                    if sd.get("scene_tags"):
                        extra_tags += "".join(
                            f"<span class='semantic-tag'>{t}</span>"
                            for t in sd["scene_tags"][:2]
                        )
                    if sd.get("dataset_purpose"):
                        extra_tags += f"<span class='purpose-tag'>{sd['dataset_purpose']}</span>"

                # 【改造 v2.4】提取首帧缩略图（带缓存）
                thumb_html = ""
                path = info["path"]
                if path not in thumb_cache:
                    thumb_cache[path] = _extract_video_thumbnail_base64(path)
                thumb_b64 = thumb_cache[path]
                if thumb_b64:
                    thumb_html = f"<img class='thumb' src='{thumb_b64}' alt='缩略图'>"
                else:
                    thumb_html = "<div class='thumb-placeholder'>无预览</div>"

                html_parts.append(
                    f"<div class='video'>"
                    f"{thumb_html}"
                    f"<span>{fi + 1}. <b>{info['name']}</b></span>"
                    f"<span style='margin-left:auto'>{info['size_readable']}</span>"
                    f"<span class='badge {mark}'>{text}</span>"
                    f"{extra_tags}"
                    f"</div>\n"
                )
            html_parts.append("</div>\n")

        html_parts.append("</body>\n</html>")

        _atomic_write_text(html_path, "".join(html_parts))
        log(f"[导出] HTML 报告 → {html_path}")
    except (IOError, OSError) as e:
        log(f"[错误] HTML 导出失败: {e}")


def export_groups_xlsx(similar_pairs: list[dict], groups: list[dict],
                       mp4_files: list[dict], xlsx_path: str,
                       threshold: float, min_sim: float = 0.0,
                       semantic_data: dict = None):
    """
    导出 Excel 格式重复报告（v2.4 新增）。
    #【改造注释】依赖 openpyxl 可选库，缺失时给出精准 pip 安装命令。
    包含两个工作表：比对明细、分组汇总。
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        log("[错误] Excel 导出需要 openpyxl 库，请安装：")
        log("  pip install openpyxl")
        log("  或使用国内镜像: pip install openpyxl -i https://pypi.tuna.tsinghua.edu.cn/simple")
        return

    try:
        wb = Workbook()

        # 工作表1：比对明细
        ws1 = wb.active
        ws1.title = "比对明细"
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        headers = ["视频A", "视频B", "相似度", "距离", "是否重复"]
        if semantic_data:
            headers.extend(["A用途", "B用途"])
        ws1.append(headers)
        for col in range(1, len(headers) + 1):
            cell = ws1.cell(row=1, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for pair in similar_pairs:
            is_dup = "是" if pair["similarity"] >= threshold else "否"
            row = [pair["name_a"], pair["name_b"],
                   round(pair["similarity"], 4), round(pair["distance"], 6), is_dup]
            if semantic_data:
                sd_a = semantic_data.get(pair["path_a"], {})
                sd_b = semantic_data.get(pair["path_b"], {})
                row.append(sd_a.get("dataset_purpose", "-"))
                row.append(sd_b.get("dataset_purpose", "-"))
            ws1.append(row)

        # 工作表2：分组汇总
        ws2 = wb.create_sheet("分组汇总")
        headers2 = ["分组", "视频名", "路径", "大小", "建议", "相似度"]
        if semantic_data:
            headers2.extend(["场景", "用途"])
        ws2.append(headers2)
        for col in range(1, len(headers2) + 1):
            cell = ws2.cell(row=1, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        retain_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        clean_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

        for gi, group in enumerate(groups, 1):
            max_sim = max(group["similarities"].values()) if group["similarities"] else 1.0
            if max_sim < min_sim:
                continue
            for idx, info in group["members"]:
                is_retain = idx == group["retain_idx"]
                mark = "保留" if is_retain else "清理"
                row = [f"第{gi}组", info["name"], info["path"],
                       info["size_readable"], mark, f"{max_sim:.2%}"]
                if semantic_data:
                    sd = semantic_data.get(info["path"], {})
                    row.append(", ".join(sd.get("scene_tags", [])[:2]) or "-")
                    row.append(sd.get("dataset_purpose", "-") or "-")
                ws2.append(row)
                # 着色
                fill = retain_fill if is_retain else clean_fill
                for col in range(1, len(headers2) + 1):
                    ws2.cell(row=ws2.max_row, column=col).fill = fill

        # 调整列宽
        for ws in [ws1, ws2]:
            for col_cells in ws.columns:
                max_len = max(len(str(c.value or "")) for c in col_cells)
                ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 60)

        wb.save(xlsx_path)
        log(f"[导出] Excel 报告 → {xlsx_path}")
    except Exception as e:
        log(f"[错误] Excel 导出失败: {e}")


def export_paths_list(groups: list[dict], mp4_files: list[dict], paths_path: str):
    """导出纯路径清单"""
    try:
        buf = io.StringIO()
        for gi, group in enumerate(groups, 1):
            buf.write(f"# 第 {gi} 组\n")
            for idx, info in group["members"]:
                buf.write(f"{info['path']}\n")
            buf.write("\n")
        _atomic_write_text(paths_path, buf.getvalue())
        log(f"[导出] 路径清单 → {paths_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 路径清单导出失败: {e}")


def export_clean_list(groups: list[dict], mp4_files: list[dict],
                      clean_list_path: str, path_mask: bool = False):
    """
    导出仅待清理视频路径清单（v2.4 新增）。
    不含保留素材，仅列出建议清理的视频路径，便于外部脚本批量处理。
    """
    try:
        # 【改造 v2.4】路径脱敏处理
        def _mask(p: str) -> str:
            if not path_mask or not p:
                return p
            parts = p.replace("\\", "/").split("/")
            if len(parts) <= 4:
                return p
            return parts[0] + "//.../" + "/".join(parts[-2:])

        buf = io.StringIO()
        buf.write(f"# 待清理视频路径清单\n")
        buf.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        total_clean = 0
        for gi, group in enumerate(groups, 1):
            retain_name = mp4_files[group["retain_idx"]]["name"]
            buf.write(f"# 第{gi}组 (保留: {retain_name})\n")
            for idx, info in group["members"]:
                if idx != group["retain_idx"]:
                    buf.write(f"{_mask(info['path'])}\n")
                    total_clean += 1
            buf.write("\n")
        buf.write(f"# 共 {total_clean} 个待清理视频\n")
        _atomic_write_text(clean_list_path, buf.getvalue())
        log(f"[导出] 清理清单 → {clean_list_path} ({total_clean} 个)")
    except (IOError, OSError) as e:
        log(f"[错误] 清理清单导出失败: {e}")


def export_bad_videos(bad_videos: list[dict], bad_path: str):
    """导出损坏清单（带故障分类）"""
    try:
        error_labels = {
            ERR_READ_FAILED: "视频打开失败",
            ERR_ZERO_FRAMES: "帧数为0",
            ERR_TIMEOUT: "提取超时",
            ERR_DECODE_ERROR: "解码异常",
            ERR_PERMISSION: "权限不足",
            ERR_DISK_ERROR: "磁盘读取错误",
        }
        counts = {}
        for bv in bad_videos:
            t = bv["error_type"]
            counts[t] = counts.get(t, 0) + 1

        with open(bad_path, "w", encoding="utf-8") as f:
            f.write(f"# 损坏视频清单（共 {len(bad_videos)} 个）\n")
            f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            for err_type, count in sorted(counts.items()):
                label = error_labels.get(err_type, err_type)
                f.write(f"## {label}: {count} 个\n\n")
            for bv in bad_videos:
                label = error_labels.get(bv["error_type"], bv["error_type"])
                f.write(f"- [{label}] {bv['path']}\n")
        log(f"[导出] 损坏清单 → {bad_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 损坏清单导出失败: {e}")


def export_hash_backup(video_hashes: dict, mp4_files: list[dict], export_path: str):
    """导出全量哈希备份"""
    try:
        backup = {}
        for idx, hash_dict in video_hashes.items():
            if idx < len(mp4_files):
                backup[mp4_files[idx]["path"]] = {
                    "name": mp4_files[idx]["name"],
                    "size": mp4_files[idx]["size"],
                    "phash": [str(h) for h in hash_dict.get("phash", [])],
                    "dhash": [str(h) for h in hash_dict.get("dhash", [])],
                    "duration": hash_dict.get("duration", 0),
                    "width": hash_dict.get("width", 0),
                    "height": hash_dict.get("height", 0),
                    "fps": hash_dict.get("fps", 0),
                }
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
        log(f"[导出] 哈希备份 → {export_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 哈希备份导出失败: {e}")




def _similarity_level(similarity: float) -> str:
    """将相似度转换为便于人工和自动化使用的等级。"""
    if similarity >= 0.98:
        return "exact_or_reencoded"
    if similarity >= 0.85:
        return "highly_similar"
    if similarity >= 0.70:
        return "possibly_similar"
    return "weak_match"


def _summary_extensions(mp4_files: list[dict]) -> dict:
    counts = {}
    for info in mp4_files:
        suffix = os.path.splitext(info.get("name", ""))[1].lower() or "[unknown]"
        counts[suffix] = counts.get(suffix, 0) + 1
    return dict(sorted(counts.items()))


def _summary_quality_buckets(group_rows: list[dict]) -> dict:
    counts = {"exact_or_reencoded": 0, "highly_similar": 0,
              "possibly_similar": 0, "weak_match": 0}
    for row in group_rows:
        level = row.get("level", "weak_match")
        counts[level] = counts.get(level, 0) + 1
    return counts


def _summary_exit_status(groups: list[dict], bad_videos: list[dict]) -> str:
    if groups:
        return "duplicates_found"
    if bad_videos:
        return "completed_with_errors"
    return "clean"


def export_summary_json(path: str, mp4_files: list[dict], video_hashes: dict,
                        bad_videos: list[dict], groups: list[dict],
                        semantic_results: dict = None):
    """导出稳定的机器可读摘要，供 CI、看板和外部清理工具使用。"""
    duplicate_bytes = 0
    group_rows = []
    for number, group in enumerate(groups, 1):
        members = []
        for idx, info in group.get("members", []):
            retained = idx == group.get("retain_idx")
            if not retained:
                duplicate_bytes += int(info.get("size", 0) or 0)
            members.append({
                "index": idx, "name": info.get("name", ""),
                "path": info.get("path", ""),
                "size": int(info.get("size", 0) or 0),
                "quality_score": _video_quality_score(info, video_hashes.get(idx)),
                "retained": retained,
            })
        similarities = group.get("similarities", {})
        max_similarity = max(similarities.values()) if similarities else 1.0
        # v2.6.1 修复：similarities 的键是 (idx_a, idx_b) 元组，直接 json.dumps
        # 会抛 "keys must be str, int, float, bool or None, not tuple"（真实数据集上必现）
        similarities_json = {f"{k[0]}|{k[1]}" if isinstance(k, tuple) else str(k): v
                             for k, v in similarities.items()}
        group_rows.append({"group": number, "members": members,
                           "max_similarity": max_similarity,
                           "level": _similarity_level(max_similarity),
                           "labels": group.get("labels", []),
                           "mixed_labels": bool(group.get("mixed_labels", False)),
                           "similarities": similarities_json})
    summary = {
        "schema_version": 1,
        "status": _summary_exit_status(groups, bad_videos),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total_videos": len(mp4_files),
        "hash_success": len(video_hashes),
        "parse_failures": len(bad_videos),
        "duplicate_groups": len(groups),
        "duplicate_videos": sum(len(g.get("members", [])) for g in groups),
        "reclaimable_bytes": duplicate_bytes,
        "bad_videos": bad_videos,
        "groups": group_rows,
        "semantic_analyzed": len(semantic_results or {}),
        "quality_scoring": "metadata_resolution_bitrate_v1",
        "extensions": _summary_extensions(mp4_files),
        "quality_buckets": _summary_quality_buckets(group_rows),
    }
    _atomic_write_text(path, json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    log(f"[导出] 扫描摘要 JSON → {path}")


def print_summary(
    mp4_files: list[dict],
    video_hashes: dict,
    bad_videos: list[dict],
    groups: list[dict],
    semantic_results: dict = None,
):
    """打印统计汇总（v2.2 扩展语义统计）"""
    total = len(mp4_files)
    success = len(video_hashes)
    failed = len(bad_videos)
    group_count = len(groups)
    dup_total = sum(len(g["members"]) for g in groups)

    log("")
    log("=" * 60, force=True)
    log("  统计汇总", force=True)
    log("=" * 60, force=True)
    log(f"  扫描视频总数:     {total}", force=True)
    log(f"  哈希成功数量:     {success}", force=True)
    log(f"  解析失败数量:     {failed}", force=True)
    log(f"  相似分组总数:     {group_count}", force=True)
    log(f"  涉及重复视频数:   {dup_total}", force=True)
    mem = _get_memory_usage()
    if mem:
        log(f"  峰值内存占用:     {mem}", force=True)

    # v2.2 AI 语义统计
    if semantic_results:
        log("", force=True)
        log("  📊 AI 语义分析统计:", force=True)
        total_analyzed = len(semantic_results)
        log(f"    已分析视频:     {total_analyzed}", force=True)
        from collections import Counter
        purpose_counts = Counter()
        scene_counts = Counter()
        train_ready = 0
        for idx, sd in semantic_results.items():
            p = sd.get("dataset_purpose", "未分类")
            purpose_counts[p] += 1
            for s in sd.get("scene_tags", [])[:2]:
                scene_counts[s] += 1
            if sd.get("is_training_ready"):
                train_ready += 1
        for purpose, count in purpose_counts.most_common():
            log(f"    {purpose}: {count} 个", force=True)
        if scene_counts:
            log("    高频场景:", force=True)
            for scene, count in scene_counts.most_common(5):
                log(f"      {scene}: {count} 个", force=True)
        log(f"    适合训练:       {train_ready} 个", force=True)

    log("=" * 60, force=True)

    if bad_videos:
        log("\n  损坏视频清单：", force=True)
        error_labels = {
            ERR_READ_FAILED: "视频打开失败",
            ERR_ZERO_FRAMES: "帧数为0",
            ERR_TIMEOUT: "提取超时",
            ERR_DECODE_ERROR: "解码异常",
            ERR_PERMISSION: "权限不足",
            ERR_DISK_ERROR: "磁盘读取错误",
            ERR_LOW_QUALITY: "画面质量低",
        }
        for bv in bad_videos:
            label = error_labels.get(bv["error_type"], bv["error_type"])
            log(f"    ✗ [{label}] {bv['path']}", force=True)

    if groups:
        log("\n  相似分组概览：", force=True)
        for gi, group in enumerate(groups, 1):
            names = [info["name"] for _, info in group["members"]]
            log(f"    第{gi}组: {', '.join(names)}", force=True)


# ============================================================
# 模块 9：全局异常处理与主入口
# ============================================================
