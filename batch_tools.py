#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MP4 视频查重批量工具集 batch_tools.py
=====================================
基于 find_mp4.py v2.5 的可复用函数，提供批量扫描、缩略图导出、重复备份、
硬链接替换、素材整理、相似片段提取等批处理能力，所有文件操作均支持 --dry-run 预览。

子命令：
    batch-scan          多目录批量扫描，合并缓存并汇总重复报告
    export-thumbnails   按重复分组批量导出首帧缩略图集
    backup-duplicates   批量备份待清理的重复视频
    replace-hardlinks   批量将重复视频替换为硬链接以节省空间
    organize            按 AI 用途/时长/分辨率整理素材
    extract-segments    提取两段视频高度相似的时间区间报告

依赖（与 find_mp4.py 同款基础依赖）：
    opencv-python  numpy  Pillow  imagehash
一键安装：
    pip install opencv-python numpy Pillow imagehash
    或运行项目根目录 install.bat
"""

import os
import sys
import json
import shutil
import argparse
import base64
from collections import defaultdict
from types import SimpleNamespace
from typing import Optional

# ============================================================
# 依赖检测 + 一键安装提示
# ============================================================
_MISSING: list[str] = []
try:
    import cv2  # noqa: F401
except ImportError:
    _MISSING.append("opencv-python")
try:
    import numpy as np  # noqa: F401
except ImportError:
    _MISSING.append("numpy")
try:
    from PIL import Image  # noqa: F401
except ImportError:
    _MISSING.append("Pillow")
try:
    import imagehash  # noqa: F401
except ImportError:
    _MISSING.append("imagehash")

if _MISSING:
    print("[batch_tools] 缺少依赖: " + ", ".join(_MISSING))
    print("[batch_tools] 请执行一键安装: pip install " + " ".join(_MISSING))
    print("[batch_tools] 或运行项目根目录 install.bat")
    sys.exit(3)

# ============================================================
# 复用 find_mp4.py 中的函数与常量
# ============================================================
try:
    import find_mp4
    from find_mp4 import (
        log,                  # 日志输出（双输出：控制台 + 日志文件）
        scan_mp4_files,       # 扫描视频文件
        load_cache,           # 加载哈希缓存
        merge_caches,         # 合并多个缓存
        _resolve_path,        # 解析路径为绝对路径
        CACHE_FILE,           # "video_hash_cache.json"
    )
except ImportError as _e:
    print("[batch_tools] 无法加载 find_mp4 模块，请确保 batch_tools.py 与 find_mp4.py 位于同一目录。")
    print(f"[batch_tools] 原始错误: {_e}")
    print("[batch_tools] find_mp4.py 是 MP4 视频查重工具 v2.5 主程序，为本工具提供基础扫描/缓存能力。")
    sys.exit(3)


def _is_cross_disk(path1: str, path2: str) -> bool:
    """
    检查两个路径是否位于不同的磁盘分区（v2.6 新增）。
    硬链接不支持跨磁盘分区，需在执行前预检。

    Args:
        path1: 第一个路径
        path2: 第二个路径

    Returns:
        True 表示跨盘，False 表示同盘
    """
    if os.name == 'nt':  # Windows
        drive1 = os.path.splitdrive(os.path.abspath(path1))[0].lower()
        drive2 = os.path.splitdrive(os.path.abspath(path2))[0].lower()
        return drive1 != drive2
    else:  # Unix/Linux：比较设备号
        try:
            st1 = os.stat(path1)
            st2 = os.stat(path2)
            return st1.st_dev != st2.st_dev
        except OSError:
            return True  # 无法确定时保守处理


def _cleanup_tmp_files(directory: str, pattern: str = ".__hlink_tmp_*"):
    """
    清理残留的临时文件（v2.6 新增）。
    在程序启动时调用，清理上次崩溃残留的临时硬链接文件。

    Args:
        directory: 要清理的目录
        pattern: 文件名匹配模式
    """
    import glob
    try:
        tmp_files = glob.glob(os.path.join(directory, pattern))
        for tmp in tmp_files:
            try:
                os.remove(tmp)
            except OSError:
                pass
    except Exception:
        pass

# 缩略图提取函数（若 find_mp4 提供则复用，否则自行实现）
_extract_thumbnail_b64 = getattr(find_mp4, "_extract_video_thumbnail_base64", None)

# 批量扫描汇总报告文件名
BATCH_REPORT_FILE = "batch_scan_report.txt"


# ============================================================
# 模块 1：batch-scan 多目录批量扫描
# ============================================================
def _make_scan_args(dir_path: str, recursive: bool) -> SimpleNamespace:
    """构造 find_mp4.scan_mp4_files 所需的轻量 args 对象"""
    return SimpleNamespace(
        dir=dir_path,
        no_recursive=not recursive,
        ext="mp4",
        exclude_folder="",
        exclude_size_lt="",
        exclude_size_gt="",
        audio_check=False,
    )


def _detect_name_size_duplicates(mp4_files: list[dict]) -> list[list[dict]]:
    """按 (文件名, 文件大小) 快速分组跨目录重复项（非内容级查重）"""
    buckets: dict = defaultdict(list)
    for f in mp4_files:
        buckets[(f["name"], f["size"])].append(f)
    return [members for members in buckets.values() if len(members) > 1]


def _write_batch_report(report_path: str, dir_list: list[str], per_dir_stats: list[tuple],
                        all_files: list[dict], dup_groups: list[list[dict]],
                        cache_summary: str) -> None:
    """写入 batch_scan_report.txt 汇总报告"""
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("MP4 视频批量扫描汇总报告\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"扫描目录数: {len(dir_list)}\n")
        f.write(f"视频文件总数: {len(all_files)}\n")
        f.write(f"缓存合并: {cache_summary}\n\n")

        f.write("【各目录扫描统计】\n")
        for i, (d, cnt, status) in enumerate(per_dir_stats, 1):
            f.write(f"  {i}. [{status}] {d}  ->  {cnt} 个文件\n")

        f.write("\n【跨目录重复提示（按文件名+大小分组）】\n")
        f.write("注：此为快速名称/大小级提示，精确内容查重请运行 find_mp4.py\n\n")
        if not dup_groups:
            f.write("  未发现名称/大小重复项\n")
        else:
            for gi, members in enumerate(dup_groups, 1):
                f.write(f"  组 {gi}（{len(members)} 个）:\n")
                for m in members:
                    f.write(f"    - {m['path']}  [{m.get('size_readable', '?')}]\n")
                f.write("\n")


def batch_scan(dir_list_file: str, output_dir: str, recursive: bool = True,
               dry_run: bool = False) -> int:
    """
    多目录批量扫描。
    :param dir_list_file: 文件夹路径列表 txt（一行一个，# 开头为注释）
    :param output_dir: 输出目录（合并缓存 + 汇总报告）
    :param recursive: 是否递归扫描子目录
    :param dry_run: 仅打印将要扫描的目录列表
    :return: 实际扫描的目录数
    """
    if not os.path.exists(dir_list_file):
        log(f"[batch-scan] 目录列表文件不存在: {dir_list_file}", force=True)
        return 0

    # 读取目录列表，跳过空行与 # 注释行
    with open(dir_list_file, "r", encoding="utf-8") as f:
        dir_list = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    log(f"[batch-scan] 共读取 {len(dir_list)} 个目录", force=True)

    if dry_run:
        log("[batch-scan] dry-run 模式：仅打印将要扫描的目录列表，不执行扫描", force=True)
        for i, d in enumerate(dir_list, 1):
            log(f"  {i}. {d}", force=True)
        return len(dir_list)

    os.makedirs(output_dir, exist_ok=True)
    all_files: list[dict] = []       # 汇总所有目录扫描结果
    per_dir_stats: list[tuple] = []  # 每个目录的统计 (目录, 文件数, 状态)
    cache_paths: list[str] = []      # 各目录下发现的缓存文件

    for idx, dir_path in enumerate(dir_list, 1):
        resolved = _resolve_path(dir_path)
        log(f"[batch-scan] ({idx}/{len(dir_list)}) 扫描: {resolved}", force=True)
        if not os.path.isdir(resolved):
            log(f"  [跳过] 不是有效目录: {resolved}", force=True)
            per_dir_stats.append((dir_path, 0, "目录无效"))
            continue
        try:
            scan_args = _make_scan_args(resolved, recursive)
            mp4_files = scan_mp4_files(scan_args)
        except Exception as e:
            log(f"  [错误] 扫描失败: {e}", force=True)
            per_dir_stats.append((dir_path, 0, f"扫描失败"))
            continue
        all_files.extend(mp4_files)
        per_dir_stats.append((dir_path, len(mp4_files), "OK"))
        # 收集该目录下的缓存文件用于合并
        local_cache = os.path.join(resolved, CACHE_FILE)
        if os.path.exists(local_cache):
            cache_paths.append(local_cache)

    # 统一合并缓存到 output_dir/video_hash_cache.json
    merged_cache_path = os.path.join(output_dir, CACHE_FILE)
    if cache_paths:
        try:
            merged_n, total_n = merge_caches(cache_paths, merged_cache_path)
            log(f"[batch-scan] 合并缓存完成: 新增 {merged_n} 条，合计 {total_n} 条 -> {merged_cache_path}", force=True)
            cache_summary = f"合并 {len(cache_paths)} 个缓存，新增 {merged_n} 条，合计 {total_n} 条"
        except Exception as e:
            log(f"[batch-scan] 缓存合并失败: {e}", force=True)
            cache_summary = f"缓存合并失败: {e}"
    else:
        log("[batch-scan] 未发现各目录下的缓存文件，跳过合并", force=True)
        cache_summary = "未发现可合并的缓存文件"

    # 基于文件名+大小快速汇总跨目录重复（精确内容查重请使用 find_mp4.py）
    dup_groups = _detect_name_size_duplicates(all_files)

    # 写入汇总报告
    report_path = os.path.join(output_dir, BATCH_REPORT_FILE)
    _write_batch_report(report_path, dir_list, per_dir_stats, all_files, dup_groups, cache_summary)
    log(f"[batch-scan] 汇总报告已生成: {report_path}", force=True)
    log(f"[batch-scan] 完成：共扫描 {len(all_files)} 个视频文件，发现 {len(dup_groups)} 组名称/大小重复", force=True)
    return len(dir_list)


# ============================================================
# 模块 2：export-thumbnails 批量导出分组缩略图集
# ============================================================
def _save_thumbnail(video_path: str, thumb_path: str, max_size: int) -> bool:
    """提取首帧并保存为 JPG。优先复用 find_mp4._extract_video_thumbnail_base64"""
    # 优先复用 find_mp4 中的 base64 实现（含 10% 跳过 + 缩放 + JPEG 编码逻辑）
    if _extract_thumbnail_b64 is not None:
        try:
            data_uri = _extract_thumbnail_b64(video_path, max_size)
            if data_uri and "," in data_uri:
                b64_data = data_uri.split(",", 1)[1]
                with open(thumb_path, "wb") as f:
                    f.write(base64.b64decode(b64_data))
                return True
            return False
        except Exception:
            return False
    # 自行实现：cv2 抽帧 -> 缩放 -> 写 JPG
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return False
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total * 0.1)))
        ret, frame = cap.read()
        if not ret or frame is None:
            return False
        h, w = frame.shape[:2]
        if w > max_size:
            scale = max_size / w
            frame = cv2.resize(frame, (max_size, int(h * scale)), interpolation=cv2.INTER_AREA)
        ok = cv2.imwrite(thumb_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return bool(ok)
    except Exception:
        return False
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def export_thumbnails(groups: list[dict], mp4_files: list[dict], output_dir: str,
                      max_size: int = 160) -> int:
    """
    批量导出分组缩略图集。
    每组生成独立文件夹 group_1, group_2, ...，提取首帧并缩放到 max_size 宽度，
    保存为 JPG，文件名为原视频名.jpg。
    :param groups: 重复分组列表，每项含 members=[(idx, info), ...] 与 retain_idx
    :param mp4_files: 全量视频信息列表
    :param output_dir: 缩略图输出目录
    :param max_size: 缩略图最大宽度（像素）
    :return: 成功导出的缩略图数量
    """
    os.makedirs(output_dir, exist_ok=True)
    exported = 0
    for gi, group in enumerate(groups, 1):
        group_dir = os.path.join(output_dir, f"group_{gi}")
        os.makedirs(group_dir, exist_ok=True)
        members = group.get("members", [])
        retain_idx = group.get("retain_idx")
        for idx, info in members:
            video_path = info["path"]
            thumb_name = os.path.splitext(info["name"])[0] + ".jpg"
            thumb_path = os.path.join(group_dir, thumb_name)
            mark = " [保留]" if idx == retain_idx else ""
            ok = _save_thumbnail(video_path, thumb_path, max_size)
            if ok:
                exported += 1
                log(f"  [缩略图] {thumb_path}{mark}", force=True)
            else:
                log(f"  [缩略图失败] {video_path}{mark}", force=True)
    log(f"[export-thumbnails] 共导出 {exported} 张缩略图到 {output_dir}", force=True)
    return exported


# ============================================================
# 模块 3：backup_duplicates 批量备份重复素材
# ============================================================
def _collect_non_retained(groups: list[dict], mp4_files: list[dict]) -> list[dict]:
    """收集所有分组中非保留项（待清理）的视频信息，去重"""
    targets: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        retain_idx = group.get("retain_idx")
        for idx, info in group.get("members", []):
            if idx == retain_idx:
                continue
            path = info["path"]
            if path in seen:
                continue
            seen.add(path)
            targets.append(info)
    return targets


def _unique_backup_path(backup_dir: str, name: str) -> str:
    """生成不冲突的备份路径，冲突时添加 _dup 后缀"""
    dst = os.path.join(backup_dir, name)
    if not os.path.exists(dst):
        return dst
    stem, ext = os.path.splitext(name)
    i = 1
    while True:
        candidate = os.path.join(backup_dir, f"{stem}_dup{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


def backup_duplicates(groups: list[dict], mp4_files: list[dict], backup_dir: str,
                      dry_run: bool = False) -> int:
    """
    批量备份重复素材（非保留项）到 backup_dir。
    保留原文件名，冲突时添加 _dup 后缀；使用 shutil.copy2 保留元数据。
    :param groups: 重复分组列表
    :param mp4_files: 全量视频信息列表
    :param backup_dir: 备份目录
    :param dry_run: 仅打印将要备份的文件清单
    :return: 备份的文件数
    """
    targets = _collect_non_retained(groups, mp4_files)
    log(f"[backup-duplicates] 待备份文件数: {len(targets)}", force=True)
    if dry_run:
        log("[backup-duplicates] dry-run 模式：仅打印将要备份的文件清单", force=True)
        for i, info in enumerate(targets, 1):
            log(f"  {i}. {info['path']}  ->  {os.path.join(backup_dir, info['name'])}", force=True)
        return len(targets)

    os.makedirs(backup_dir, exist_ok=True)
    backed_up = 0
    for info in targets:
        dst = _unique_backup_path(backup_dir, info["name"])
        try:
            shutil.copy2(info["path"], dst)
            backed_up += 1
            log(f"  [备份] {info['path']} -> {dst}", force=True)
        except Exception as e:
            log(f"  [备份失败] {info['path']}: {e}", force=True)
    log(f"[backup-duplicates] 完成：共备份 {backed_up} 个文件到 {backup_dir}", force=True)
    return backed_up


# ============================================================
# 模块 4：replace_with_hardlinks 批量替换重复视频为硬链接
# ============================================================
def replace_with_hardlinks(groups: list[dict], mp4_files: list[dict],
                           dry_run: bool = False) -> int:
    """
    批量将重复视频替换为指向保留项的硬链接。
    对每个分组的非保留项，删除原文件后创建指向保留项的硬链接，
    节省磁盘空间且保持文件路径不变。
    安全检查：硬链接创建失败时不删除原文件。
    :param groups: 重复分组列表
    :param mp4_files: 全量视频信息列表
    :param dry_run: 仅打印将要替换的文件清单
    :return: 替换的文件数
    """
    # 按保留项分组收集待替换项 plan = (retained_path, target_path)
    plan: list[tuple[str, str]] = []
    for group in groups:
        retain_idx = group.get("retain_idx")
        members = group.get("members", [])
        retained_path: Optional[str] = None
        for idx, info in members:
            if idx == retain_idx:
                retained_path = info["path"]
                break
        if not retained_path:
            continue
        for idx, info in members:
            if idx == retain_idx:
                continue
            # 跳过自身（保留项与待替换项路径相同时）
            if info["path"] == retained_path:
                continue
            plan.append((retained_path, info["path"]))

    log(f"[replace-hardlinks] 待替换为硬链接的文件数: {len(plan)}", force=True)
    # 【v2.6 新增】启动时清理上次崩溃残留的临时文件
    if plan:
        first_dir = os.path.dirname(plan[0][1])
        _cleanup_tmp_files(first_dir)
    if dry_run:
        log("[replace-hardlinks] dry-run 模式：仅打印将要替换的文件清单", force=True)
        # 【v2.6 新增】跨盘预检
        cross_disk = []
        for retained, target in plan:
            if _is_cross_disk(retained, target):
                cross_disk.append((retained, target))
        if cross_disk:
            log(f"  [警告] 检测到 {len(cross_disk)} 个跨磁盘分区文件，硬链接不支持跨盘：", force=True)
            for r, t in cross_disk[:5]:
                log(f"    {t} (盘 {os.path.splitdrive(t)[0]}) ← {r} (盘 {os.path.splitdrive(r)[0]})", force=True)
        for i, (retained, target) in enumerate(plan, 1):
            cross_mark = " [跨盘-跳过]" if _is_cross_disk(retained, target) else ""
            log(f"  {i}. {target}  ->  硬链接 -> {retained}{cross_mark}", force=True)
        return len(plan)

    replaced = 0
    skipped_cross_disk = 0
    for retained_path, target_path in plan:
        # 【v2.6 新增】跨盘预检：硬链接不支持跨磁盘分区
        if _is_cross_disk(retained_path, target_path):
            log(f"  [跳过-跨盘] {target_path} 与 {retained_path} 不在同一磁盘分区", force=True)
            skipped_cross_disk += 1
            continue
        # 安全策略：先在同目录创建临时硬链接，成功后删除原文件并重命名
        # 这样硬链接创建失败时原文件完全不受影响
        target_dir = os.path.dirname(target_path)
        tmp_link = os.path.join(target_dir, f".__hlink_tmp_{os.getpid()}_{replaced}")
        try:
            if os.path.exists(tmp_link):
                os.remove(tmp_link)
            os.link(retained_path, tmp_link)
        except OSError as e:
            log(f"  [硬链接失败] {target_path} -> {retained_path}: {e}", force=True)
            log(f"  [安全] 原文件未删除: {target_path}", force=True)
            continue
        # 临时硬链接创建成功，删除原文件并重命名临时链接为原文件名
        try:
            os.remove(target_path)
            os.rename(tmp_link, target_path)
            replaced += 1
            log(f"  [硬链接] {target_path} -> {retained_path}", force=True)
        except OSError as e:
            log(f"  [替换失败] {target_path}: {e}", force=True)
            # 清理临时链接，尽量保持现场
            try:
                if os.path.exists(tmp_link):
                    os.remove(tmp_link)
            except OSError:
                pass
    log(f"[replace-hardlinks] 完成：共替换 {replaced} 个文件为硬链接", force=True)
    return replaced


# ============================================================
# 模块 5：organize_materials 素材移动整理
# ============================================================
def _get_video_duration(video_path: str) -> Optional[float]:
    """通过 cv2 获取视频时长（秒）"""
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if fps and fps > 0 and frames and frames > 0:
            return frames / fps
        return None
    except Exception:
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def _get_video_height(video_path: str) -> Optional[int]:
    """通过 cv2 获取视频高度"""
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return h if h > 0 else None
    except Exception:
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def _classify_video(info: dict, organize_by: str, semantic_data: dict) -> str:
    """根据分类方式返回视频所属类别名"""
    path = info["path"]
    if organize_by == "ai_class":
        sd = semantic_data.get(path, {}) if semantic_data else {}
        return sd.get("dataset_purpose") or "未分类"
    elif organize_by == "duration":
        duration = _get_video_duration(path)
        if duration is None:
            return "未知时长"
        if duration < 60:
            return "小于1分钟"
        elif duration < 300:
            return "1-5分钟"
        elif duration < 1800:
            return "5-30分钟"
        else:
            return "大于30分钟"
    elif organize_by == "resolution":
        height = _get_video_height(path)
        if height is None or height <= 0:
            return "未知分辨率"
        if height < 720:
            return "小于720p"
        elif height < 1080:
            return "720p"
        elif height < 2160:
            return "1080p"
        else:
            return "4K"
    return "未分类"


def _safe_dir_name(name: str) -> str:
    """将分类名转换为安全的目录名（去除文件系统非法字符）"""
    safe = "".join(c for c in name if c not in '<>:"/\\|?*')
    return safe.strip() or "other"


def organize_materials(mp4_files: list[dict], semantic_data: dict, output_dir: str,
                       organize_by: str = "ai_class", dry_run: bool = False) -> int:
    """
    素材移动整理。按 organize_by 维度分类，每个分类生成独立子目录并移动视频文件。
    :param mp4_files: 待整理的视频信息列表
    :param semantic_data: AI 语义数据（ai_class 模式按 dataset_purpose 分组）
    :param output_dir: 整理输出目录
    :param organize_by: "ai_class" / "duration" / "resolution"
    :param dry_run: 仅打印将要移动的文件清单
    :return: 移动的文件数
    """
    valid_modes = {"ai_class", "duration", "resolution"}
    if organize_by not in valid_modes:
        log(f"[organize] 不支持的分类方式: {organize_by}（可选: {valid_modes}）", force=True)
        return 0

    # 计算每个文件所属分类
    plan: list[tuple[dict, str]] = []
    for info in mp4_files:
        category = _classify_video(info, organize_by, semantic_data)
        plan.append((info, category))

    log(f"[organize] 按 {organize_by} 整理，待处理文件数: {len(plan)}", force=True)
    if dry_run:
        log("[organize] dry-run 模式：仅打印将要移动的文件清单", force=True)
        for i, (info, category) in enumerate(plan, 1):
            dst_dir = os.path.join(output_dir, _safe_dir_name(category))
            log(f"  {i}. [{category}] {info['path']}  ->  {dst_dir}", force=True)
        return len(plan)

    os.makedirs(output_dir, exist_ok=True)
    moved = 0
    for info, category in plan:
        dst_dir = os.path.join(output_dir, _safe_dir_name(category))
        os.makedirs(dst_dir, exist_ok=True)
        dst_path = _unique_backup_path(dst_dir, info["name"])
        try:
            shutil.move(info["path"], dst_path)
            moved += 1
            log(f"  [移动] [{category}] {info['path']} -> {dst_path}", force=True)
        except Exception as e:
            log(f"  [移动失败] {info['path']}: {e}", force=True)
    log(f"[organize] 完成：共移动 {moved} 个文件到 {output_dir}", force=True)
    return moved


# ============================================================
# 模块 6：extract_similar_segments 重复画面片段提取
# ============================================================
def _sample_frames_with_hash(video_path: str, interval: float = 1.0) -> list[tuple[float, object]]:
    """按固定时间间隔抽帧并计算 pHash，返回 [(时间秒, imagehash), ...]"""
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
        fps = cap.get(cv2.CAP_PROP_FPS)
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if not fps or fps <= 0:
            return []
        duration = (total / fps) if total and total > 0 else 0
        results: list[tuple[float, object]] = []
        t = 0.0
        while True:
            if duration and t > duration:
                break
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret or frame is None:
                t += interval
                continue
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                pil_img = Image.fromarray(gray)
                h = imagehash.phash(pil_img, hash_size=8)
                results.append((t, h))
            except Exception:
                pass
            t += interval
        return results
    except Exception:
        return []
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def _phash_similarity(h1: object, h2: object) -> float:
    """由 pHash 汉明距离计算相似度 (0~1)，hash_size=8 对应 64 bit"""
    diff = h1 - h2
    total_bits = 64
    return 1.0 - (diff / total_bits)


def _merge_similar_intervals(matches: list[tuple], threshold: float) -> list[dict]:
    """将连续达到阈值的采样点合并为相似区间"""
    intervals: list[dict] = []
    cur: Optional[dict] = None
    for t1, t2, sim in matches:
        if sim >= threshold:
            if cur is None:
                cur = {"start_t1": t1, "end_t1": t1, "start_t2": t2, "end_t2": t2,
                       "min_sim": sim, "max_sim": sim, "count": 1}
            else:
                cur["end_t1"] = t1
                cur["end_t2"] = t2
                cur["min_sim"] = min(cur["min_sim"], sim)
                cur["max_sim"] = max(cur["max_sim"], sim)
                cur["count"] += 1
        else:
            if cur is not None:
                intervals.append(cur)
                cur = None
    if cur is not None:
        intervals.append(cur)
    return intervals


def _write_segments_report(output_path: str, video1: str, video2: str, threshold: float,
                           frames1: list, frames2: list, intervals: list[dict]) -> None:
    """写入 Markdown 格式相似片段报告"""

    def _fmt(sec: float) -> str:
        m = int(sec // 60)
        s = sec - m * 60
        return f"{m:02d}:{s:05.2f}"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# 视频相似片段报告\n\n")
        f.write(f"- 视频 A: `{video1}`\n")
        f.write(f"- 视频 B: `{video2}`\n")
        f.write(f"- 相似阈值: {threshold}\n")
        f.write(f"- 视频 A 采样帧数: {len(frames1)}\n")
        f.write(f"- 视频 B 采样帧数: {len(frames2)}\n")
        f.write(f"- 相似区间数: {len(intervals)}\n\n")
        f.write("## 相似区间\n\n")
        if not intervals:
            f.write("未发现达到阈值的相似区间。\n")
        else:
            f.write("| # | A 起始 | A 结束 | B 起始 | B 结束 | 最低相似度 | 最高相似度 | 采样点数 |\n")
            f.write("|---|--------|--------|--------|--------|-----------|-----------|---------|\n")
            for i, it in enumerate(intervals, 1):
                f.write(f"| {i} | {_fmt(it['start_t1'])} | {_fmt(it['end_t1'])} | "
                        f"{_fmt(it['start_t2'])} | {_fmt(it['end_t2'])} | "
                        f"{it['min_sim']:.4f} | {it['max_sim']:.4f} | {it['count']} |\n")


def extract_similar_segments(video1: str, video2: str, output_path: str,
                             threshold: float = 0.8) -> int:
    """
    重复画面片段提取。
    定位两段视频高度相似的时间区间，使用 cv2 抽帧比对感知哈希（pHash），
    输出 Markdown 格式时间戳报告（相似区间起止时间、相似度）。
    :param video1: 视频 A 路径
    :param video2: 视频 B 路径
    :param output_path: Markdown 报告输出路径
    :param threshold: 相似度阈值（0~1，默认 0.8）
    :return: 记录的相似区间数
    """
    log(f"[extract-segments] 比对: {video1}  vs  {video2}", force=True)
    log(f"[extract-segments] 采样间隔 1 秒，相似阈值 {threshold}", force=True)

    frames1 = _sample_frames_with_hash(video1, interval=1.0)
    frames2 = _sample_frames_with_hash(video2, interval=1.0)
    if not frames1 or not frames2:
        log("[extract-segments] 抽帧失败，无法比对", force=True)
        return 0

    # 对视频1的每个采样点，找到视频2中最佳匹配及其相似度
    matches: list[tuple[float, float, float]] = []  # (t1, best_t2, best_sim)
    for t1, h1 in frames1:
        best_sim = 0.0
        best_t2 = 0.0
        for t2, h2 in frames2:
            sim = _phash_similarity(h1, h2)
            if sim > best_sim:
                best_sim = sim
                best_t2 = t2
        matches.append((t1, best_t2, best_sim))

    # 将连续达到阈值的时间点合并为区间
    intervals = _merge_similar_intervals(matches, threshold)

    # 写入 Markdown 报告
    _write_segments_report(output_path, video1, video2, threshold, frames1, frames2, intervals)
    log(f"[extract-segments] 报告已生成: {output_path}（{len(intervals)} 个相似区间）", force=True)
    return len(intervals)


# ============================================================
# 模块 7：分组 JSON 加载辅助
# ============================================================
def _load_groups_json(groups_json: str) -> tuple[Optional[list], list]:
    """
    加载重复分组 JSON。
    支持两种格式：
      1) {"groups": [...], "mp4_files": [...]}
      2) [group, group, ...]（仅分组，members 内嵌视频信息）
    返回 (groups 或 None, mp4_files)
    """
    if not os.path.exists(groups_json):
        log(f"[batch_tools] 分组 JSON 文件不存在: {groups_json}", force=True)
        return None, []
    try:
        with open(groups_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"[batch_tools] 分组 JSON 解析失败: {e}", force=True)
        return None, []

    if isinstance(data, dict) and "groups" in data:
        return data["groups"], data.get("mp4_files", [])
    if isinstance(data, list):
        return data, []
    log("[batch_tools] 无法识别的分组 JSON 格式", force=True)
    return None, []


# ============================================================
# 模块 8：命令行入口
# ============================================================
def main() -> int:
    """命令行入口：argparse 子命令架构，每个子命令有独立 -h 帮助，全局支持 --dry-run"""
    parser = argparse.ArgumentParser(
        prog="batch_tools",
        description="MP4 视频查重批量工具集（基于 find_mp4.py v2.5）",
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # batch-scan 多目录批量扫描
    p_scan = subparsers.add_parser("batch-scan", help="多目录批量扫描，合并缓存并汇总重复报告")
    p_scan.add_argument("--dir-list", required=True, help="文件夹路径列表 txt（一行一个）")
    p_scan.add_argument("--output-dir", default="batch_output", help="输出目录（默认 batch_output）")
    p_scan.add_argument("--no-recursive", action="store_true", help="不递归扫描子目录")
    p_scan.add_argument("--dry-run", action="store_true", help="仅打印将要扫描的目录列表")

    # export-thumbnails 按分组导出缩略图
    p_thumb = subparsers.add_parser("export-thumbnails", help="按重复分组批量导出首帧缩略图集")
    p_thumb.add_argument("--groups-json", required=True, help="重复分组 JSON 文件路径")
    p_thumb.add_argument("--output-dir", default="thumbnails", help="缩略图输出目录")
    p_thumb.add_argument("--max-size", type=int, default=160, help="缩略图最大宽度（默认 160）")

    # backup-duplicates 批量备份重复
    p_backup = subparsers.add_parser("backup-duplicates", help="批量备份待清理的重复视频")
    p_backup.add_argument("--groups-json", required=True, help="重复分组 JSON 文件路径")
    p_backup.add_argument("--backup-dir", default="backup_duplicates", help="备份目录")
    p_backup.add_argument("--dry-run", action="store_true", help="仅打印将要备份的文件清单")

    # replace-hardlinks 批量硬链接替换
    p_link = subparsers.add_parser("replace-hardlinks", help="批量将重复视频替换为硬链接")
    p_link.add_argument("--groups-json", required=True, help="重复分组 JSON 文件路径")
    p_link.add_argument("--dry-run", action="store_true", help="仅打印将要替换的文件清单")

    # organize 素材整理
    p_org = subparsers.add_parser("organize", help="按 AI 用途/时长/分辨率整理素材")
    p_org.add_argument("--dir", required=True, help="待整理的视频目录")
    p_org.add_argument("--semantic-json", default="", help="AI 语义数据 JSON 路径（ai_class 模式使用）")
    p_org.add_argument("--output-dir", default="organized", help="整理输出目录")
    p_org.add_argument("--organize-by", default="ai_class",
                       choices=["ai_class", "duration", "resolution"], help="分类方式（默认 ai_class）")
    p_org.add_argument("--no-recursive", action="store_true", help="不递归扫描子目录")
    p_org.add_argument("--dry-run", action="store_true", help="仅打印将要移动的文件清单")

    # extract-segments 相似片段提取
    p_seg = subparsers.add_parser("extract-segments", help="提取两段视频高度相似的时间区间报告")
    p_seg.add_argument("--video1", required=True, help="视频 A 路径")
    p_seg.add_argument("--video2", required=True, help="视频 B 路径")
    p_seg.add_argument("--output", default="similar_segments.md", help="Markdown 报告输出路径")
    p_seg.add_argument("--threshold", type=float, default=0.8, help="相似度阈值（默认 0.8）")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "batch-scan":
        batch_scan(args.dir_list, args.output_dir,
                   recursive=not args.no_recursive, dry_run=args.dry_run)
    elif args.command == "export-thumbnails":
        groups, mp4_files = _load_groups_json(args.groups_json)
        if groups is None:
            return 1
        export_thumbnails(groups, mp4_files, args.output_dir, max_size=args.max_size)
    elif args.command == "backup-duplicates":
        groups, mp4_files = _load_groups_json(args.groups_json)
        if groups is None:
            return 1
        backup_duplicates(groups, mp4_files, args.backup_dir, dry_run=args.dry_run)
    elif args.command == "replace-hardlinks":
        groups, mp4_files = _load_groups_json(args.groups_json)
        if groups is None:
            return 1
        replace_with_hardlinks(groups, mp4_files, dry_run=args.dry_run)
    elif args.command == "organize":
        scan_args = _make_scan_args(_resolve_path(args.dir), not args.no_recursive)
        mp4_files = scan_mp4_files(scan_args)
        semantic_data: dict = {}
        if args.semantic_json and os.path.exists(args.semantic_json):
            try:
                with open(args.semantic_json, "r", encoding="utf-8") as f:
                    semantic_data = json.load(f)
            except Exception as e:
                log(f"[organize] 语义数据加载失败: {e}", force=True)
        organize_materials(mp4_files, semantic_data, args.output_dir,
                           organize_by=args.organize_by, dry_run=args.dry_run)
    elif args.command == "extract-segments":
        extract_similar_segments(args.video1, args.video2, args.output, threshold=args.threshold)
    return 0


# ============================================================
# 使用示例（仅供参考，不执行）
# ============================================================
# 1. 多目录批量扫描（dry-run 预览）
#    python batch_tools.py batch-scan --dir-list dirs.txt --output-dir batch_output --dry-run
#    python batch_tools.py batch-scan --dir-list dirs.txt --output-dir batch_output
#
# 2. 按重复分组导出缩略图（groups_json 由 find_mp4.py 导出）
#    python batch_tools.py export-thumbnails --groups-json duplicate_groups.json --output-dir thumbnails --max-size 200
#
# 3. 批量备份重复视频（dry-run 预览）
#    python batch_tools.py backup-duplicates --groups-json duplicate_groups.json --backup-dir backup --dry-run
#    python batch_tools.py backup-duplicates --groups-json duplicate_groups.json --backup-dir backup
#
# 4. 批量替换重复视频为硬链接（dry-run 预览）
#    python batch_tools.py replace-hardlinks --groups-json duplicate_groups.json --dry-run
#    python batch_tools.py replace-hardlinks --groups-json duplicate_groups.json
#
# 5. 素材整理
#    python batch_tools.py organize --dir D:\Videos --organize-by ai_class --semantic-json video_semantic_meta.json --output-dir organized
#    python batch_tools.py organize --dir D:\Videos --organize-by duration --output-dir organized_by_duration
#    python batch_tools.py organize --dir D:\Videos --organize-by resolution --output-dir organized_by_res --dry-run
#
# 6. 相似片段提取
#    python batch_tools.py extract-segments --video1 a.mp4 --video2 b.mp4 --output similar.md --threshold 0.85
#
# 说明：
#   - 所有文件操作子命令均支持 --dry-run 预览，不会修改/删除/移动任何文件。
#   - groups-json 推荐由 find_mp4.py 查重后导出，格式：{"groups":[...],"mp4_files":[...]}。
#   - find_mp4.py 主程序完成内容级精确查重，本工具在其结果上做批处理。
# ============================================================

if __name__ == "__main__":
    sys.exit(main())
