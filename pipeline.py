#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline.py — 数据集一键体检（v2.8 新增）

一条命令串起三步，产出一份《数据集体检报告》：
    步骤1 查重        find_mp4.py --summary-json（按预设自动加 --group-min-sim 等）
    步骤2 标注验证    label_verify.py（自动识别预标注 + 可选运动前景哈希/CLIP）
    步骤3 汇总体检    读两步的机器可读结果，给出 A/B/C 体检等级与建议行动清单

用法：
    python find_mp4.py pipeline --dir D:\cam_data
    python find_mp4.py pipeline --dir D:\cam_data --preset surveillance --label-regex "cam01_(.+?)-(?:pos|neg)" --purpose train
    python pipeline.py --dir D:\cam_data --no-dedup   # 只跑标注验证+体检

输出（--output-dir，默认 --dir 下 _healthcheck）：
    dedup\            查重报告与 scan_summary.json
    label_verify\     标注验证三件套 + label_verify_summary.json
    数据集体检报告.md  汇总结论（等级 + 建议行动）

退出码：0=体检完成；2=两个分析步骤都失败；3=参数/目录错误
"""

import argparse
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def log(msg: str = ""):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(msg)


def parse_args():
    ap = argparse.ArgumentParser(description="数据集一键体检：查重 + 标注验证 + 汇总报告")
    ap.add_argument("--dir", required=True, help="视频根目录")
    ap.add_argument("--output-dir", default="", help="体检输出目录（默认 --dir 下 _healthcheck）")
    ap.add_argument("--label-regex", default="",
                    help=r'标签提取正则（第1捕获组为标签），如 "cam01_(.+?)-(?:pos|neg)"')
    ap.add_argument("--purpose", default="train",
                    choices=["general", "train", "detection", "retrieval", "archive"],
                    help="数据用途（影响标注验证的参考建议，默认 train）")
    ap.add_argument("--preset", default="surveillance",
                    choices=["general", "surveillance", "footage"],
                    help="场景预设（默认 surveillance：固定机位，自动启用运动前景哈希与分组约束）")
    ap.add_argument("--use-clip", action="store_true", help="标注验证启用 CLIP（含 neg 验证）")
    ap.add_argument("--no-motion-hash", action="store_true", help="禁用运动前景哈希（surveillance 预设默认开启）")
    ap.add_argument("--no-dedup", action="store_true", help="跳过查重步骤，只做标注验证+体检")
    ap.add_argument("--fast", action="store_true", help="查重用快速模式（5 帧/高阈值）")
    ap.add_argument("--ext", default="mp4,mov,mkv,avi,webm,m4v,flv", help="视频后缀")
    ap.add_argument("--no-recursive", action="store_true", help="不递归子目录")
    return ap.parse_args()


def _run_step(name: str, cmd: list) -> int:
    log("")
    log("=" * 62)
    log("  [体检步骤] %s" % name)
    log("=" * 62)
    log("  命令: %s" % " ".join(cmd))
    try:
        rc = subprocess.call(cmd)
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        log("  [失败] %s" % exc)
        return 99
    log("  [步骤完成] %s 退出码 %s" % (name, rc))
    return rc


def _grade(n_videos: int, suspects: int, mixed_groups: int, dup_groups: int) -> tuple:
    """体检评级：(等级, 一句话结论)。A=干净 B=可用需清洗 C=需重点复核"""
    ratio = suspects / max(1, n_videos)
    if suspects == 0 and mixed_groups == 0:
        return "A", "标注与查重结果均干净，可直接使用。"
    if ratio < 0.10 and mixed_groups <= max(1, dup_groups // 4):
        return "B", "整体可用：存在少量疑似错标或混合标注分组，建议按清单复核后使用。"
    return "C", "需要重点清洗：疑似错标比例较高或查重虚警明显，建议先复核再使用。"


def build_report(args, out_dir: str, dedup_summary: dict, lv_summary: dict,
                 dedup_rc: int, lv_rc: int) -> str:
    """汇总体检报告 MD，返回报告路径"""
    import label_verify  # 复用用途建议与判定文案

    n_videos = 0
    dup_groups = dup_videos = mixed = reclaim_mb = 0
    if dedup_summary:
        n_videos = dedup_summary.get("total_videos", 0)
        dup_groups = dedup_summary.get("duplicate_groups", 0)
        dup_videos = dedup_summary.get("duplicate_videos", 0)
        reclaim_mb = round(dedup_summary.get("reclaimable_bytes", 0) / 1048576)
        mixed = sum(1 for g in dedup_summary.get("groups", []) if g.get("mixed_labels"))
    suspects = lv_summary.get("suspects", 0) if lv_summary else 0
    intra = lv_summary.get("global_intra") if lv_summary else None
    inter = lv_summary.get("global_inter") if lv_summary else None
    separation = lv_summary.get("separation") if lv_summary else None
    static_ratio = lv_summary.get("static_ratio", 0) if lv_summary else 0

    grade, conclusion = _grade(n_videos, suspects, mixed, dup_groups)
    L = [
        "# 数据集体检报告", "",
        "- 体检时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "- 数据目录：%s" % args.dir,
        "- 场景预设：%s ｜ 数据用途：%s" % (args.preset, args.purpose),
        "- 查重步骤：%s ｜ 标注验证：%s" % (
            "跳过" if args.no_dedup else ("完成" if dedup_summary else "失败"),
            "完成" if lv_summary else "失败"),
        "", "## 总览", "",
        "| 项目 | 结果 |", "|---|---|",
        "| 视频总数 | %d |" % n_videos,
    ]
    if not args.no_dedup:
        L += [
            "| 查重分组 | %d 组 / 涉及 %d 个（可释放约 %d MB） |" % (dup_groups, dup_videos, reclaim_mb),
            "| ⚠ 混合标注分组 | %d 个（同组混有 pos/neg 或多行为，固定机位场景多为虚警，清理前须人工复核） |" % mixed,
        ]
    if lv_summary:
        L += [
            "| 标注一致性 | 组内 %.3f / 组间 %.3f / 分离度 %+.3f |" % (intra, inter, separation),
            "| 静止机位占比 | %.0f%% |" % (static_ratio * 100),
        ]
    L += ["| 疑似错标 | **%d** 个（清单见 label_verify\\label_verify_suspects.csv） |" % suspects]
    L += ["", "## 体检结论：%s" % grade, "", conclusion, ""]

    L += ["## 建议行动", ""]
    actions = []
    if suspects:
        actions.append("1. **复核 %d 个疑似错标**：打开看板 `find_mp4.py dashboard` → 『🔍 嫌疑复核』面板，"
                       "加载 `label_verify\\label_verify_suspects.csv`，并排播放逐项判定；"
                       "复核结果可直接导出为修正标签 CSV。" % suspects)
    elif lv_summary:
        actions.append("1. 标注一致性良好，无需复核。")
    if mixed:
        actions.append("2. **%d 个混合标注分组先别删**：固定机位背景下整段画面哈希会把\"同场景\"判成\"同内容\"，"
                       "这些组多半是虚警；确认需求可用 `--motion-hash` 特征或人工抽看。" % mixed)
    if static_ratio >= 0.4:
        actions.append("3. **固定机位场景**（静止占比 %.0f%%）：整段画面哈希区分度有限，"
                       "查重/验证请保持 `--preset surveillance`（自动启用运动前景哈希与分组约束）。" % (static_ratio * 100))
    if lv_summary:
        purpose_tips = label_verify._purpose_guidance(args.purpose, {
            "labels": [], "label_stats": {}, "suspects": suspects,
            "global_intra": intra, "global_inter": inter, "separation": separation,
            "static_ratio": static_ratio, "silhouette": None}, args)
        for i, t in enumerate(purpose_tips, len(actions) + 1):
            actions.append("%d. %s" % (i, t))
    if not args.no_dedup and not dedup_summary:
        actions.append("查重步骤失败，请单独运行 find_mp4.py 查看报错。")
    if not lv_summary:
        actions.append("标注验证失败（可能未识别到预标注），请单独运行 label_verify.py 查看报错。")
    L += actions
    L += ["", "---", "", "*本报告由 pipeline 自动生成；判定仅供清洗参考，执行任何删除前请人工复核。*"]

    report_path = os.path.join(out_dir, "数据集体检报告.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return report_path


def main():
    args = parse_args()
    t0 = time.time()
    root = os.path.abspath(os.path.expanduser(args.dir))
    if not os.path.isdir(root):
        log("[错误] 目录不存在: %s" % root)
        return 3
    out_dir = os.path.abspath(args.output_dir) if args.output_dir else os.path.join(root, "_healthcheck")
    dedup_dir = os.path.join(out_dir, "dedup")
    lv_dir = os.path.join(out_dir, "label_verify")
    os.makedirs(dedup_dir, exist_ok=True)
    os.makedirs(lv_dir, exist_ok=True)
    py = sys.executable or "python"
    find_mp4 = os.path.join(SCRIPT_DIR, "find_mp4.py")
    label_verify_py = os.path.join(SCRIPT_DIR, "label_verify.py")

    log("╔" + "═" * 60 + "╗")
    log("║%s║" % "数据集一键体检 pipeline".center(46))
    log("╚" + "═" * 60 + "╝")
    log("  目录: %s ｜ 预设: %s ｜ 用途: %s" % (root, args.preset, args.purpose))

    # ---- 步骤1 查重 ----
    dedup_rc = 0
    dedup_summary = None
    if not args.no_dedup:
        cmd = [py, find_mp4, "--dir", root, "--output-dir", dedup_dir,
               "--summary-json", "--preset", args.preset, "--ext", args.ext]
        if args.fast:
            cmd.append("--fast")
        if not args.no_recursive:
            pass  # find_mp4 默认递归
        else:
            cmd.append("--no-recursive")
        dedup_rc = _run_step("查重", cmd)
        sj = os.path.join(dedup_dir, "scan_summary.json")
        if os.path.exists(sj):
            try:
                with open(sj, "r", encoding="utf-8") as f:
                    dedup_summary = json.load(f)
            except Exception as exc:
                log("  [警告] scan_summary.json 解析失败: %s" % exc)

    # ---- 步骤2 标注验证 ----
    cmd = [py, label_verify_py, "--dir", root, "--output-dir", lv_dir,
           "--purpose", args.purpose, "--preset", args.preset, "--summary-json"]
    if args.label_regex:
        cmd += ["--label-regex", args.label_regex]
    if args.use_clip:
        cmd += ["--use-clip", "--verify-neg"]
    if args.no_motion_hash:
        cmd.append("--no-motion-hash")
    if args.no_recursive:
        cmd.append("--no-recursive")
    lv_rc = _run_step("标注验证", cmd)
    lv_summary = None
    sj = os.path.join(lv_dir, "label_verify_summary.json")
    if os.path.exists(sj):
        try:
            with open(sj, "r", encoding="utf-8") as f:
                lv_summary = json.load(f)
        except Exception as exc:
            log("  [警告] label_verify_summary.json 解析失败: %s" % exc)

    # ---- 步骤3 汇总 ----
    log("")
    log("=" * 62)
    log("  [体检步骤] 汇总体检报告")
    log("=" * 62)
    sys.path.insert(0, SCRIPT_DIR)
    try:
        report_path = build_report(args, out_dir, dedup_summary, lv_summary, dedup_rc, lv_rc)
    except Exception as exc:
        log("  [失败] 报告生成失败: %s" % exc)
        return 2 if (dedup_rc not in (0, 1) and lv_rc not in (0, 1)) else 0
    log("")
    log("  体检完成（耗时 %.1fs）。报告: %s" % (time.time() - t0, report_path))
    if lv_summary:
        log("  疑似错标 %d 个 ｜ 静止机位 %.0f%% ｜ 分离度 %+.3f"
            % (lv_summary.get("suspects", 0), lv_summary.get("static_ratio", 0) * 100,
               lv_summary.get("separation", 0)))
    if dedup_summary:
        log("  查重 %d 组（混合标注 %d 组）"
            % (dedup_summary.get("duplicate_groups", 0),
               sum(1 for g in dedup_summary.get("groups", []) if g.get("mixed_labels"))))
    both_failed = dedup_rc not in (0, 1) and lv_rc not in (0, 1)
    return 2 if both_failed else 0


if __name__ == "__main__":
    sys.exit(main())