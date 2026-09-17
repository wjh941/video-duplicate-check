#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_tools.py — 嫌疑项复核工作台的纯逻辑模块（v2.8 新增）

为 dashboard 复核面板与 pipeline 提供无 UI 依赖的函数：
    load_suspects_csv()      读取 label_verify_suspects.csv
    scan_video_labels()      按正则扫描目录，返回 {路径: 标签}
    find_representative()    为某标签找一个对照视频
    export_review_results()  导出复核结果 CSV（可直接用作 --labels-csv 的修正版）
    export_review_report()   导出复核摘要 MD

复核判定取值：ok=标注正确 / wrong=改为建议标签 / manual=人工指定新标签 / skip=跳过
"""

import csv
import os
import re
import time


def load_suspects_csv(csv_path: str) -> list:
    """读取嫌疑清单。返回 [{path,label,suggested,score,reason}]，空文件返回 []"""
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            p = (row.get("文件路径") or "").strip()
            if not p:
                continue
            rows.append({
                "path": p,
                "label": (row.get("当前标签") or "").strip(),
                "suggested": (row.get("建议标签") or "").strip(),
                "score": (row.get("组内平均相似度") or "").strip(),
                "reason": (row.get("原因") or "").strip(),
            })
    return rows


def scan_video_labels(video_dir: str, label_regex: str = "", recursive: bool = True) -> dict:
    """扫描目录视频并按正则提取标签（第1捕获组）。返回 {绝对路径: 标签}"""
    exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}
    mapping = {}
    pat = re.compile(label_regex) if label_regex else None
    if recursive:
        walker = os.walk(video_dir)
    else:
        try:
            walker = [(video_dir, [], os.listdir(video_dir))]
        except OSError:
            return mapping
    for dirpath, _dirs, files in walker:
        for fn in files:
            if os.path.splitext(fn)[1].lower() not in exts:
                continue
            full = os.path.join(dirpath, fn)
            label = ""
            if pat is not None:
                m = pat.search(os.path.splitext(fn)[0])
                if m and m.group(1):
                    label = m.group(1).strip()
            elif os.path.dirname(os.path.abspath(full)) != os.path.abspath(video_dir):
                label = os.path.basename(os.path.dirname(os.path.abspath(full)))
            mapping[os.path.abspath(full)] = label
    return mapping


def find_representative(video_dir: str, label: str, label_regex: str = "",
                        exclude: str = "") -> str:
    """为标签找一个对照视频（第一个匹配且不是 exclude 的）。返回路径或空串"""
    if not label:
        return ""
    mapping = scan_video_labels(video_dir, label_regex, recursive=True)
    for p, lbl in sorted(mapping.items()):
        if lbl == label and os.path.abspath(p) != os.path.abspath(exclude):
            return p
    return ""


VERDICT_TEXT = {"ok": "标注正确", "wrong": "改为建议标签",
                "manual": "人工指定新标签", "skip": "跳过"}


def export_review_results(verdicts: dict, out_path: str) -> int:
    """
    导出复核结果 CSV。verdicts: {path: {"orig":原标签, "verdict":判定,
    "suggested":建议标签, "manual":人工标签}}
    列：文件路径/原标签/复核判定/建议标签/最终标签/复核时间
    最终标签可直接修正：wrong→suggested，manual→manual，ok→orig
    返回写入行数。
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["文件路径", "原标签", "复核判定", "建议标签", "最终标签", "复核时间"])
        for p in sorted(verdicts.keys()):
            v = verdicts[p]
            vd = v.get("verdict", "skip")
            if vd == "wrong":
                final = v.get("suggested", "")
            elif vd == "manual":
                final = v.get("manual", "")
            elif vd == "ok":
                final = v.get("orig", "")
            else:
                final = ""
            w.writerow([p, v.get("orig", ""), VERDICT_TEXT.get(vd, vd),
                        v.get("suggested", ""), final, now])
            n += 1
    return n


def export_review_report(verdicts: dict, out_path: str, suspects_total: int = 0) -> str:
    """导出复核摘要 MD。返回写入的字符数"""
    cnt = {"ok": 0, "wrong": 0, "manual": 0, "skip": 0}
    for v in verdicts.values():
        cnt[v.get("verdict", "skip")] = cnt.get(v.get("verdict", "skip"), 0) + 1
    lines = [
        "# 嫌疑项复核结果", "",
        "- 复核时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "- 嫌疑总数：%d ｜ 已复核：%d ｜ 跳过：%d" % (
            suspects_total, cnt["ok"] + cnt["wrong"] + cnt["manual"], cnt["skip"]),
        "- 判定标注正确：%d ｜ 改为建议标签：%d ｜ 人工指定新标签：%d" % (
            cnt["ok"], cnt["wrong"], cnt["manual"]),
        "", "## 明细", "",
        "| 文件 | 原标签 | 判定 | 最终标签 |", "|---|---|---|---|",
    ]
    for p in sorted(verdicts.keys()):
        v = verdicts[p]
        vd = v.get("verdict", "skip")
        final = (v.get("suggested", "") if vd == "wrong"
                 else v.get("manual", "") if vd == "manual"
                 else v.get("orig", "") if vd == "ok" else "—")
        lines.append("| %s | %s | %s | %s |" % (
            os.path.basename(p), v.get("orig", ""), VERDICT_TEXT.get(vd, vd), final))
    text = "\n".join(lines)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return len(text)


if __name__ == "__main__":
    print("review_tools 是库模块，由 dashboard/大 panel 调用")