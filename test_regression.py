#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_regression.py — 核心回归测试（v2.8 新增，v2.6.1/v2.7/v2.8 修复项的安全网）

无 pytest 依赖，直接运行：python test_regression.py
覆盖 4 个高危回归点（都是实战中真出过问题的）：
    1. 错标注入检测   —— label_verify 必须抓到放入异类文件夹的 wrong_1（v2.6.1 核心功能）
    2. 场景预设参数   —— surveillance 预设必须正确改写 find_mp4/label_verify 参数（v2.8）
    3. 分组最低相似度 —— _split_group_min_sim 必须拆开 A≈B≈C 却 A≉C 的传递链（v2.7 修复）
    4. 复核结果导出   —— 判定到最终标签的映射必须正确（v2.8 复核工作台数据出口）

退出码：0=全部通过；1=存在失败。测试数据在临时目录生成并自动清理。
"""

import csv
import os
import shutil
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

PY = sys.executable or "python"
RESULTS = []


def check(name):
    """装饰器：登记测试用例"""
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


# ---------- 合成数据生成（与人工回归使用的内容一致：纹理圆环，move/static/blink 三类） ----------
def make_dataset(root: str, n_move: int = 3, n_static: int = 3, n_blink: int = 2,
                 with_wrong: bool = True) -> None:
    import cv2
    import numpy as np
    W, H, FPS, N = 320, 240, 12, 24

    def texture(shift_x=0, shift_y=0, blink=False, i=0):
        img = np.zeros((H, W), np.uint8)
        for k in range(6):
            cx = (40 + k * 48 + shift_x) % W
            cy = (60 + (k * 37) % 120 + shift_y) % H
            cv2.circle(img, (int(cx), int(cy)), 14 + k * 3, 60 + k * 30, 2)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        img += ((xx / 9 + yy / 7).astype(np.uint8) % 2) * 18
        if blink and i % 6 == 3:
            img = np.clip(img.astype(np.int16) + 110, 0, 255).astype(np.uint8)
        return img

    def write(path, kind):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
        for i in range(N):
            sx, sy = (i * 5, i * 3) if kind == "move" else (0, 0)
            img = texture(sx, sy, blink=(kind == "blink"), i=i)
            vw.write(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
        vw.release()

    for n in range(1, n_move + 1):
        write(os.path.join(root, "move", "move_%d.mp4" % n), "move")
    for n in range(1, n_static + 1):
        write(os.path.join(root, "static", "static_%d.mp4" % n), "static")
    for n in range(1, n_blink + 1):
        write(os.path.join(root, "blink", "blink_%d.mp4" % n), "blink")
    if with_wrong:
        write(os.path.join(root, "move", "wrong_1.mp4"), "static")


# ---------- 用例 1：错标注入必须被抓到 ----------
@check("1. 错标注入检测：wrong_1 放入 move 组必须被标记")
def test_wrong_label_detected():
    import label_verify
    tmp = tempfile.mkdtemp(prefix="lv_reg_")
    try:
        data = os.path.join(tmp, "data")
        make_dataset(data)
        out_dir = os.path.join(tmp, "out")
        argv = ["label_verify.py", "--dir", data,
                "--output-dir", out_dir, "--suspect-threshold", "0.5"]
        old = sys.argv
        sys.argv = argv
        try:
            rc = label_verify.main()
        finally:
            sys.argv = old
        assert rc == 1, "退出码应为 1（发现疑似），实际 %s" % rc
        csv_path = os.path.join(out_dir, "label_verify_suspects.csv")
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        suspects = [r for r in rows if (r.get("文件路径") or "").strip()]
        assert any("wrong_1" in r["文件路径"] for r in suspects), \
            "wrong_1 必须出现在嫌疑清单，实际: %s" % [r["文件路径"] for r in suspects]
        w = next(r for r in suspects if "wrong_1" in r["文件路径"])
        assert w.get("建议标签") == "static", \
            "wrong_1 的建议标签应为 static，实际 %s" % w.get("建议标签")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 用例 2：surveillance 预设参数生效 ----------
@check("2. 场景预设：surveillance 必须正确改写两个工具的默认参数")
def test_preset_applied():
    import find_mp4 as fm
    import label_verify as lv
    old = sys.argv
    try:
        sys.argv = ["find_mp4.py", "--dir", "X", "--preset", "surveillance"]
        args = fm._apply_preset(fm.parse_args())
        assert args.threshold == 0.85, "threshold 应为 0.85，实际 %s" % args.threshold
        assert args.group_min_sim == 0.8, "group-min-sim 应为 0.8，实际 %s" % args.group_min_sim
        assert args.frames == 6, "frames 应为 6，实际 %s" % args.frames
        sys.argv = ["label_verify.py", "--dir", "X", "--preset", "surveillance"]
        largs = lv._apply_preset(lv.parse_args())
        assert largs.motion_hash is True, "surveillance 应自动启用运动前景哈希"
        assert largs.suspect_threshold == 0.55, "嫌疑阈值应为 0.55，实际 %s" % largs.suspect_threshold
        # 显式指定的参数优先于预设
        sys.argv = ["find_mp4.py", "--dir", "X", "--preset", "surveillance", "--threshold", "0.6"]
        args2 = fm._apply_preset(fm.parse_args())
        assert args2.threshold == 0.6, "显式参数必须优先于预设"
    finally:
        sys.argv = old


# ---------- 用例 3：分组最低相似度约束必须拆分传递链 ----------
@check("3. 分组约束：A≈B≈C 但 A≉C 的链必须被拆分")
def test_group_min_sim_split():
    import find_mp4 as fm
    # 构造链：0-1 强边 0.9，1-2 强边 0.85，0-2 弱边 0.5 → floor=0.8 必须不能三人同组
    sim = {(0, 1): 0.9, (1, 2): 0.85, (0, 2): 0.5}
    groups = fm._split_group_min_sim([0, 1, 2], sim, 0.8)
    for g in groups:
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                assert sim.get((min(a, b), max(a, b)), 0.0) >= 0.8, \
                    "组 %s 内存在低于 floor 的点对" % g
    # 核心不变量：弱边 (0,2) 的两端绝不能同时出现在任何一组
    assert not any(0 in g and 2 in g for g in groups), \
        "弱点对 (0,2) 仍被分在同组: %s" % groups
    # 契约：被剔除的落单成员不再成组（build_groups 视为非重复）
    # 全强边时不拆
    strong = {(0, 1): 0.95, (1, 2): 0.92, (0, 2): 0.9}
    groups2 = fm._split_group_min_sim([0, 1, 2], strong, 0.8)
    assert len(groups2) == 1 and sorted(groups2[0]) == [0, 1, 2], "全强边不应拆分"


# ---------- 用例 4：复核结果导出映射正确 ----------
@check("4. 复核导出：ok/wrong/manual/skip 到最终标签的映射必须正确")
def test_review_export():
    import review_tools
    verdicts = {
        "a.mp4": {"orig": "move", "verdict": "ok", "suggested": "static", "manual": ""},
        "b.mp4": {"orig": "move", "verdict": "wrong", "suggested": "static", "manual": ""},
        "c.mp4": {"orig": "move", "verdict": "manual", "suggested": "static", "manual": "dog_come"},
        "d.mp4": {"orig": "move", "verdict": "skip", "suggested": "static", "manual": ""},
    }
    tmp = tempfile.mkdtemp(prefix="rv_reg_")
    try:
        out = os.path.join(tmp, "review_results.csv")
        n = review_tools.export_review_results(verdicts, out)
        assert n == 4, "应写入 4 行，实际 %s" % n
        with open(out, "r", encoding="utf-8-sig") as f:
            rows = {r["文件路径"]: r for r in csv.DictReader(f)}
        assert rows["a.mp4"]["最终标签"] == "move", "ok 应保留原标签"
        assert rows["b.mp4"]["最终标签"] == "static", "wrong 应改为建议标签"
        assert rows["c.mp4"]["最终标签"] == "dog_come", "manual 应采用人工标签"
        assert rows["d.mp4"]["最终标签"] == "", "skip 最终标签应为空"
        md = os.path.join(tmp, "review_summary.md")
        review_tools.export_review_report(verdicts, md, 4)
        text = open(md, encoding="utf-8").read()
        assert "复核结果" in text and "人工指定新标签" in text, "MD 摘要应含判定统计"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=" * 62)
    print("  核心回归测试（4 用例）")
    print("=" * 62)
    failed = []
    for name, fn in RESULTS:
        try:
            fn()
            print("  [PASS] %s" % name)
        except SystemExit as exc:
            failed.append(name)
            print("  [FAIL] %s\n         SystemExit: %s" % (name, exc.code))
    print("=" * 62)
    print("  结果: %d 通过 / %d 失败" % (len(RESULTS) - len(failed), len(failed)))
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())