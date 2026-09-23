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
from .context import log, repo_dir
from .utils import (_atomic_write_text, _atomic_write_lines,
                    _prefixed_path, _format_size, _normalize_path,
                    _resolve_path, _compute_file_sha256)



def generate_cleanup_script(
    groups: list[dict], mp4_files: list[dict],
    output_dir: str, hard_delete: bool = False,
    protect_folders: set = None,
    backup_path: str = "", protect_file: str = "",
    semantic_data: dict = None, path_mask: bool = False,
):
    """
    生成清理脚本（v2.3 增强：备份模式 + 批量保护目录 + 文件占用检测 + 用途备注）。
    v2.4 修复：保护目录使用完整分隔符精准匹配，杜绝 video 误保护 video123；
    新增 --path-mask 路径脱敏，审计日志隐藏中间层级保护素材隐私。
    默认：移动至回收站/垃圾桶（安全模式）。
    --hard-delete：永久删除。
    --backup-path：清理前先复制视频到备份目录。
    --protect-file：从文件批量导入保护目录。
    """
    protect_folders = protect_folders or set()
    protect_folders.add(os.path.expanduser("~/Desktop"))
    protect_folders.add(os.path.expanduser("~/"))

    # v2.3 新增：从文件批量导入保护目录
    if protect_file:
        try:
            with open(protect_file, "r", encoding="utf-8") as pf:
                for line in pf:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        protect_folders.add(_normalize_path(line))
            log(f"  [清理] 从 {protect_file} 导入保护目录")
        except (IOError, OSError):
            pass

    # 【改造修复 v2.4】保护目录使用完整分隔符精准匹配
    # 旧方案 startswith 会导致 video 误匹配 video123，新增 os.sep 边界检查
    def is_protected(path: str) -> bool:
        path_lower = path.lower().rstrip(os.sep)
        for pf in protect_folders:
            pf_norm = _normalize_path(pf).lower().rstrip(os.sep)
            if not pf_norm:
                continue
            # 精确匹配：路径等于保护目录，或以 保护目录+分隔符 开头
            if path_lower == pf_norm or path_lower.startswith(pf_norm + os.sep):
                return True
        return False

    # 【改造 v2.4】路径脱敏：隐藏路径中间层级，仅保留首尾两级
    def _mask_path(p: str) -> str:
        if not path_mask or not p:
            return p
        parts = p.replace("\\", "/").split("/")
        if len(parts) <= 4:
            return p
        return parts[0] + "//.../" + "/".join(parts[-2:])

    # v2.3 新增：获取用途备注
    def get_purpose_note(path: str) -> str:
        if semantic_data:
            sd = semantic_data.get(path, {})
            purpose = sd.get("dataset_purpose", "")
            if purpose:
                return f" [用途: {purpose}]"
        return ""

    # 写出结构化清理计划：先审阅/备份，再执行脚本，便于恢复和自动化。
    plan_path = os.path.join(output_dir, CLEANUP_PLAN_FILE)
    plan_rows = []
    for gi, group in enumerate(groups, 1):
        for idx, info in group.get("members", []):
            if idx == group.get("retain_idx") or is_protected(info["path"]):
                continue
            plan_rows.append({
                "group": gi, "source": info["path"],
                "size": int(info.get("size", 0) or 0),
                "retained": mp4_files[group["retain_idx"]]["path"],
                "action": "delete" if hard_delete else "move_to_trash",
            })
    plan = {"schema_version": 1, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "items": plan_rows, "count": len(plan_rows),
            "total_bytes": sum(row["size"] for row in plan_rows)}
    _atomic_write_text(plan_path, json.dumps(plan, ensure_ascii=False, indent=2))
    log(f"[导出] 清理计划 → {plan_path} ({len(plan_rows)} 个)")

    # Windows BAT
    bat_path = os.path.join(output_dir, CLEANUP_SCRIPT_WIN)
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write("@echo off\r\n")
        f.write("chcp 65001 >nul\r\n")
        f.write(f"REM 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\r\n")
        mode_str = '永久删除 (危险!)' if hard_delete else '安全模式（移动至回收站）'
        f.write(f"REM 模式: {mode_str}\r\n")
        if backup_path:
            f.write(f"REM 备份目录: {backup_path}\r\n")
        f.write("echo.\r\n")
        f.write("echo ============================================\r\n")
        f.write("echo   MP4 重复视频清理脚本\r\n")
        f.write("echo ============================================\r\n")
        f.write("echo.\r\n")
        f.write("echo 以下文件将被处理：\r\n")
        f.write("echo.\r\n")

        to_delete = []
        for gi, group in enumerate(groups, 1):
            retain_name = mp4_files[group["retain_idx"]]["name"]
            f.write(f"REM --- 第 {gi} 组 (保留: {retain_name}) ---\r\n")
            for idx, info in group["members"]:
                if idx != group["retain_idx"]:
                    path = info["path"]
                    if is_protected(path):
                        f.write(f"REM [已保护] {_mask_path(path)}\r\n")
                        continue
                    note = get_purpose_note(path)
                    # 【改造 v2.4】echo 显示行使用脱敏路径，实际操作行保留真实路径
                    f.write(f"echo   {_mask_path(path)}{note}\r\n")
                    to_delete.append(path)
            f.write("\r\n")

        f.write("echo.\r\n")
        f.write("set /p CONFIRM=输入 CONFIRM 执行操作: \r\n")
        f.write('if /i not "%CONFIRM%"=="CONFIRM" (\r\n')
        f.write('    echo 已取消\r\n')
        f.write('    pause\r\n')
        f.write('    exit /b 0\r\n')
        f.write(")\r\n\r\n")

        # v2.3 新增：备份模式
        if backup_path:
            f.write(f'if not exist "{backup_path}" mkdir "{backup_path}"\r\n')
            f.write("echo 正在备份文件...\r\n")
            for path in to_delete:
                fname = os.path.basename(path)
                f.write(f'copy "{path}" "{backup_path}\\{fname}" >nul 2>&1\r\n')
            f.write("echo 备份完成，开始清理...\r\n\r\n")

        for path in to_delete:
            if hard_delete:
                f.write(f'del /f /q "{path}" 2>nul\r\n')
                # v2.3 新增：文件占用检测
                f.write(f'if exist "{path}" echo [警告] 文件被占用无法删除: {path}\r\n')
            else:
                f.write(f'move "{path}" "%TEMP%\\trash_%RANDOM%_" 2>nul\r\n')
                f.write(f'if exist "{path}" echo [警告] 文件被占用无法移动: {path}\r\n')

        f.write("\r\necho.\r\n")
        f.write("echo 清理完成！\r\n")
        f.write("pause\r\n")

    # Linux SH
    sh_path = os.path.join(output_dir, CLEANUP_SCRIPT_LINUX)
    with open(sh_path, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# 模式: {'永久删除 (危险!)' if hard_delete else '安全模式（移动至 ~/.trash）'}\n")
        if backup_path:
            f.write(f"# 备份目录: {backup_path}\n")
        f.write("\n")
        f.write('echo "==========================================="\n')
        f.write('echo "  MP4 重复视频清理脚本"\n')
        f.write('echo "==========================================="\n')
        f.write("echo.\n")

        to_delete = []
        for gi, group in enumerate(groups, 1):
            f.write(f'# --- 第 {gi} 组 ---\n')
            for idx, info in group["members"]:
                if idx != group["retain_idx"]:
                    path = info["path"]
                    if is_protected(path):
                        f.write(f"# [已保护] {_mask_path(path)}\n")
                        continue
                    # 【改造 v2.4】echo 显示行使用脱敏路径
                    f.write(f'echo "  {_mask_path(path)}"\n')
                    to_delete.append(path)
            f.write("\n")

        f.write('read -p "输入 CONFIRM 执行操作: " CONFIRM\n')
        f.write('if [ "$CONFIRM" != "CONFIRM" ]; then\n')
        f.write('    echo "已取消"\n')
        f.write('    exit 0\n')
        f.write("fi\n\n")

        # v2.3 新增：备份模式
        if backup_path:
            f.write(f'mkdir -p "{backup_path}"\n')
            f.write('echo "正在备份文件..."\n')
            for path in to_delete:
                fname = os.path.basename(path)
                f.write(f'cp "{path}" "{backup_path}/{fname}" 2>/dev/null\n')
            f.write('echo "备份完成，开始清理..."\n\n')

        for path in to_delete:
            if hard_delete:
                f.write(f'rm -f "{path}" 2>/dev/null\n')
            else:
                f.write(f'mv "{path}" ~/.trash/ 2>/dev/null\n')

        f.write('\necho "清理完成！"\n')

    log(f"[导出] 清理脚本 → {bat_path} / {sh_path}")


def export_audit_log(output_dir: str, groups: list[dict], mp4_files: list[dict],
                     hard_delete: bool, semantic_data: dict = None,
                     path_mask: bool = False):
    """导出清理操作审计日志（v2.2 扩展语义备注，v2.3 按日期分割文件，v2.4 路径脱敏）"""
    try:
        # v2.3 新增：审计日志按日期分割，避免单文件无限膨胀
        date_str = time.strftime("%Y%m")
        audit_name = AUDIT_LOG.replace(".log", f"_{date_str}.log")
        audit_path = os.path.join(output_dir, audit_name)

        # 【改造 v2.4】路径脱敏处理
        def _mask(p: str) -> str:
            if not path_mask or not p:
                return p
            parts = p.replace("\\", "/").split("/")
            if len(parts) <= 4:
                return p
            return parts[0] + "//.../" + "/".join(parts[-2:])

        # 【改造 v2.4】原子写入审计日志
        buf = io.StringIO()
        buf.write(f"\n{'=' * 60}\n")
        buf.write(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        buf.write(f"模式: {'永久删除' if hard_delete else '安全移动'}\n")
        buf.write(f"分组数: {len(groups)}\n")
        for gi, group in enumerate(groups, 1):
            buf.write(f"\n第{gi}组:\n")
            retain_info = mp4_files[group['retain_idx']]
            buf.write(f"  保留: {_mask(retain_info['path'])}")
            if semantic_data:
                sd = semantic_data.get(retain_info["path"], {})
                if sd.get("dataset_purpose"):
                    buf.write(f" ({sd['dataset_purpose']})")
            buf.write("\n")
            for idx, info in group["members"]:
                if idx != group["retain_idx"]:
                    buf.write(f"  清理: {_mask(info['path'])} ({info['size_readable']})")
                    if semantic_data:
                        sd = semantic_data.get(info["path"], {})
                        if sd.get("dataset_purpose"):
                            buf.write(f" [{sd['dataset_purpose']}]")
                    buf.write("\n")
        # 追加模式：先读后写（原子化追加）
        existing = ""
        if os.path.exists(audit_path):
            try:
                with open(audit_path, "r", encoding="utf-8") as fr:
                    existing = fr.read()
            except (IOError, OSError):
                pass
        _atomic_write_text(audit_path, existing + buf.getvalue())
        log(f"[导出] 审计日志 → {audit_path}")
    except (IOError, OSError) as e:
        log(f"[错误] 审计日志导出失败: {e}")




def _run_execute_plan(args):
    """安全执行清理计划：默认预览，确认后移动到隔离目录，不永久删除。"""
    plan_file = os.path.abspath(os.path.expanduser(args.plan_file))
    try:
        with open(plan_file, "r", encoding="utf-8") as f:
            plan = json.load(f)
        items = plan.get("items", [])
        if not isinstance(items, list):
            raise ValueError("items 不是列表")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"错误: 清理计划无效: {exc}")
        return EXIT_BAD_ARGS
    ready = []
    for item in items:
        if not isinstance(item, dict) or not item.get("source"):
            continue
        source = os.path.abspath(os.path.expanduser(item["source"]))
        try:
            stat = os.stat(source)
            if int(item.get("size", stat.st_size)) == stat.st_size:
                ready.append((source, item))
        except (OSError, ValueError, TypeError):
            continue
    print(f"清理计划: {plan_file}")
    print(f"可执行项目: {len(ready)}/{len(items)}")
    if not getattr(args, "confirm_cleanup", False):
        print("预览模式：未移动文件。执行时添加 --confirm-cleanup。")
        return EXIT_OK
    operation_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    trash_dir = os.path.join(os.path.dirname(plan_file), "trash", operation_id)
    os.makedirs(trash_dir, exist_ok=True)
    moved = 0
    log_path = os.path.join(trash_dir, "operation.json")
    operations = []
    for source, item in ready:
        target = os.path.join(trash_dir, f"{moved:05d}_{os.path.basename(source)}")
        try:
            shutil.move(source, target)
            operations.append({"source": source, "target": target, "size": item.get("size", 0),
                               "mtime": item.get("mtime"), "sha256": _compute_file_sha256(target),
                               "status": "moved"})
            moved += 1
        except (OSError, shutil.Error) as exc:
            operations.append({"source": source, "target": target, "error": str(exc)})
    _atomic_write_text(log_path, json.dumps({"schema_version": 2, "operation_id": operation_id,
                                                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                                "plan_file": plan_file, "operations": operations},
                                               ensure_ascii=False, indent=2))
    print(f"操作编号: {operation_id}")
    print(f"已安全移动: {moved} 个")
    print(f"恢复记录: {log_path}")
    return EXIT_OK if moved == len(ready) else EXIT_PARSE_ERROR


def _run_purge_operations(args):
    """统计并可选清理过期隔离操作，默认只预览。"""
    root = os.path.abspath(os.path.expanduser(args.trash_dir))
    cutoff = time.time() - max(0, args.older_than) * 86400
    candidates, total_bytes = [], 0
    if not os.path.isdir(root):
        print(f"隔离区不存在: {root}")
        return EXIT_BAD_ARGS
    for current, _, files in os.walk(root):
        if "operation.json" not in files:
            continue
        operation_file = os.path.join(current, "operation.json")
        try:
            if os.path.getmtime(operation_file) > cutoff:
                continue
            with open(operation_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            operations = data.get("operations", [])
            size = sum(int(x.get("size", 0) or 0) for x in operations if not x.get("error"))
            candidates.append((current, size))
            total_bytes += size
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    print(f"隔离区: {root}")
    print(f"过期阈值: {args.older_than} 天 | 候选操作: {len(candidates)} | 空间: {_format_size(total_bytes)}")
    if not args.confirm_purge:
        print("预览模式：未删除。执行时添加 --confirm-purge。")
        return EXIT_OK
    removed = 0
    for path, _ in candidates:
        try:
            shutil.rmtree(path)
            removed += 1
        except OSError as exc:
            print(f"[警告] 删除失败: {path}: {exc}")
    print(f"已删除过期操作: {removed} 个")
    return EXIT_OK if removed == len(candidates) else EXIT_PARSE_ERROR


def _run_list_operations(args):
    """列出隔离区中的清理操作，便于选择恢复记录。"""
    root = os.path.abspath(os.path.expanduser(args.trash_dir))
    if not os.path.isdir(root):
        print(f"隔离区不存在: {root}")
        return EXIT_BAD_ARGS
    rows = []
    for current, _, files in os.walk(root):
        if "operation.json" not in files:
            continue
        path = os.path.join(current, "operation.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            operations = data.get("operations", [])
            moved = [item for item in operations if item.get("status") == "moved" or not item.get("error")]
            total_bytes = sum(int(item.get("size", 0) or 0) for item in moved)
            rows.append((data.get("operation_id", os.path.basename(current)),
                         data.get("created_at", "-"), len(moved), total_bytes, path))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    rows.sort(reverse=True)
    print(f"隔离区: {root}")
    print(f"操作数: {len(rows)}")
    for operation_id, created_at, count, total_bytes, path in rows:
        print(f"{operation_id} | {created_at} | {count} 个 | {_format_size(total_bytes)} | {path}")
    return EXIT_OK


def _run_restore_operation(args):
    """从 operation.json 恢复安全移动的文件，默认仅预览。"""
    operation_file = os.path.abspath(os.path.expanduser(args.operation_file))
    try:
        with open(operation_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        operations = data.get("operations", [])
        if not isinstance(operations, list):
            raise ValueError("operations 不是列表")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"错误: 恢复记录无效: {exc}")
        return EXIT_BAD_ARGS
    ready, conflicts = [], []
    for item in operations:
        source, target = item.get("source"), item.get("target")
        if not source or not target or item.get("error"):
            continue
        if os.path.exists(target) and not os.path.exists(source):
            expected_size = item.get("size")
            try:
                if expected_size is not None and os.path.getsize(target) != int(expected_size):
                    conflicts.append(target)
                    continue
                expected_sha256 = item.get("sha256")
                if expected_sha256 and _compute_file_sha256(target) != expected_sha256:
                    conflicts.append(target)
                    continue
            except (OSError, ValueError, TypeError):
                conflicts.append(target)
                continue
            ready.append((source, target))
        elif os.path.exists(source):
            conflicts.append(source)
    print(f"恢复记录: {operation_file}")
    print(f"可恢复: {len(ready)} | 冲突/已存在: {len(conflicts)}")
    if not getattr(args, "confirm_restore", False):
        print("预览模式：未恢复文件。执行时添加 --confirm-restore。")
        return EXIT_OK
    restored = 0
    for source, target in ready:
        try:
            os.makedirs(os.path.dirname(source) or ".", exist_ok=True)
            shutil.move(target, source)
            restored += 1
        except (OSError, shutil.Error) as exc:
            print(f"[警告] 恢复失败: {target} -> {source}: {exc}")
    print(f"已恢复: {restored} 个")
    return EXIT_OK if restored == len(ready) and not conflicts else EXIT_PARSE_ERROR


def _run_validate_plan(args):
    """校验清理计划中的文件是否仍存在且大小未变化，不执行任何文件操作。"""
    plan_file = os.path.abspath(os.path.expanduser(args.plan_file))
    try:
        with open(plan_file, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"错误: 无法读取清理计划: {exc}")
        return EXIT_BAD_ARGS
    items = plan.get("items") if isinstance(plan, dict) else None
    if not isinstance(items, list):
        print("错误: 清理计划格式无效，缺少 items 列表")
        return EXIT_BAD_ARGS
    missing, changed, ready = [], [], []
    for item in items:
        path = item.get("source", "") if isinstance(item, dict) else ""
        expected_size = item.get("size") if isinstance(item, dict) else None
        try:
            stat = os.stat(path)
            if expected_size is not None and int(expected_size) != stat.st_size:
                changed.append(path)
            else:
                ready.append(path)
        except (OSError, ValueError, TypeError):
            missing.append(path)
    print(f"清理计划: {plan_file}")
    print(f"总项目: {len(items)} | 可执行: {len(ready)} | 已变化: {len(changed)} | 不存在: {len(missing)}")
    if changed:
        print("[警告] 文件大小已变化，建议重新扫描后再清理。")
    if missing:
        print("[警告] 部分文件已不存在，已从可执行列表排除。")
    return EXIT_OK if not changed and not missing else EXIT_PARSE_ERROR




def _run_gen_restore(args):
    """根据审计日志生成恢复脚本（v2.4 新增）"""
    script_dir = repo_dir()
    output_dir = _resolve_path(args.output_dir) if args.output_dir else script_dir
    os.makedirs(output_dir, exist_ok=True)

    # 查找最新审计日志
    import glob as glob_mod
    audit_files = sorted(glob_mod.glob(os.path.join(output_dir, "cleanup_audit_*.log")))
    if not audit_files:
        audit_files = [os.path.join(output_dir, AUDIT_LOG)]
    if not os.path.exists(audit_files[-1]):
        log("错误: 未找到审计日志文件", force=True)
        return

    audit_path = audit_files[-1]
    log(f"读取审计日志: {audit_path}", force=True)

    restore_entries = []
    try:
        with open(audit_path, "r", encoding="utf-8") as f:
            for line in f:
                # 解析移动/删除记录
                if "移动" in line or "删除" in line or "→" in line or "→" in line:
                    parts = line.strip().split("\t")
                    if len(parts) >= 2:
                        restore_entries.append(parts)
    except (IOError, OSError) as e:
        log(f"错误: 读取审计日志失败: {e}", force=True)
        return

    if not restore_entries:
        log("审计日志中未找到可恢复的记录", force=True)
        return

    # 生成恢复脚本
    restore_bat = os.path.join(output_dir, "restore_duplicates.bat")
    try:
        with open(restore_bat, "w", encoding="utf-8") as f:
            f.write("@echo off\r\nchcp 65001 >nul\r\n")
            f.write(f"REM 恢复脚本生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\r\n")
            f.write("echo 正在恢复视频文件...\r\n")
            for entry in restore_entries:
                if len(entry) >= 2:
                    src = entry[0].strip()
                    dst = entry[1].strip() if len(entry) > 1 else ""
                    if dst:
                        f.write(f'move "{dst}" "{src}" 2>nul\r\n')
            f.write("echo 恢复完成！\r\npause\r\n")
        log(f"[导出] 恢复脚本 → {restore_bat}", force=True)
    except (IOError, OSError) as e:
        log(f"错误: 生成恢复脚本失败: {e}")


# 【v2.6 新增】子命令委托函数：将新增子命令转发到独立模块执行
