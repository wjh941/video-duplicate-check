# -*- coding: utf-8 -*-
"""报告导出 / 清理脚本生成 / 关键帧检测 / 忽略规则 测试。"""
import json
import os

import pytest

from video_dedup.cleanup import generate_cleanup_script
from video_dedup.grouper import build_groups
from video_dedup.hasher import _detect_keyframes
from video_dedup.reporter import (export_bad_paths, export_clean_list,
                                  export_groups_html, export_groups_md,
                                  export_groups_txt, export_hash_backup)
from video_dedup.scanner import _match_ignore_rule


@pytest.fixture()
def scan_fixture(tmp_path, video_factory):
    """两个重复视频 + 一个独立视频 → 构建分组与哈希数据"""
    vdir = tmp_path / "videos"
    va = video_factory("stripes_move", name=str(vdir / "dup_a.mp4"))
    vb = video_factory("stripes_move", name=str(vdir / "dup_b.mp4"))
    vc = video_factory("rings", name=str(vdir / "unique.mp4"))
    from video_dedup.hasher import _extract_hashes_single
    files, hashes = [], {}
    for i, p in enumerate((va, vb, vc)):
        h, err = _extract_hashes_single(str(p), num_frames=4)
        assert err is None
        files.append({"name": p.name, "path": str(p),
                      "size": p.stat().st_size, "mtime": p.stat().st_mtime,
                      "size_readable": f"{p.stat().st_size} B"})
        hashes[i] = h
    pairs = []
    from video_dedup.compare import find_similar_pairs
    pairs = find_similar_pairs(hashes, files, threshold=0.7)
    groups = build_groups(pairs, files, hashes)
    return tmp_path, files, hashes, groups


class TestReporterExports:
    def test_txt_md_html_exports(self, scan_fixture):
        tmp_path, files, hashes, groups = scan_fixture
        assert len(groups) == 1, "重复对应构建出 1 个分组"
        out = str(tmp_path / "out")
        os.makedirs(out, exist_ok=True)
        export_groups_txt(groups, files, os.path.join(out, "g.txt"), 0.7, 0.0, {})
        export_groups_md(groups, files, os.path.join(out, "g.md"), 0.7, 0.0, {})
        export_groups_html(groups, files, os.path.join(out, "g.html"),
                           0.7, 0.0, {}, video_hashes=hashes)
        txt = open(os.path.join(out, "g.txt"), encoding="utf-8").read()
        md = open(os.path.join(out, "g.md"), encoding="utf-8").read()
        html = open(os.path.join(out, "g.html"), encoding="utf-8").read()
        assert "dup_a.mp4" in txt and "保留" in txt
        assert "dup_b.mp4" in md
        assert "<html" in html.lower() and "dup_a.mp4" in html

    def test_clean_list_and_bad_paths(self, scan_fixture, tmp_path,
                                      video_factory):
        tmp_path, files, hashes, groups = scan_fixture
        out = str(tmp_path / "out2")
        os.makedirs(out, exist_ok=True)
        export_clean_list(groups, files, os.path.join(out, "clean_list.txt"))
        export_bad_paths([{"name": "bad.mp4", "path": "X:/bad.mp4",
                           "error": "read_failed"}], out)
        clean = open(os.path.join(out, "clean_list.txt"),
                     encoding="utf-8").read()
        bad = open(os.path.join(out, "bad_video_paths.txt"),
                   encoding="utf-8").read()
        # 清理清单只含被清理者（保留者排除）
        assert "dup_b.mp4" in clean or "dup_a.mp4" in clean
        assert "bad.mp4" in bad

    def test_hash_backup_roundtrip_content(self, scan_fixture):
        tmp_path, files, hashes, groups = scan_fixture
        out = str(tmp_path / "out3")
        os.makedirs(out, exist_ok=True)
        export_hash_backup(hashes, files, os.path.join(out, "hash.json"))
        data = json.loads(open(os.path.join(out, "hash.json"),
                               encoding="utf-8").read())
        assert "0" in data or "phash" in str(data)[:200]


class TestCleanupScriptGeneration:
    def test_generates_plan_scripts_and_audit(self, scan_fixture, monkeypatch):
        tmp_path, files, hashes, groups = scan_fixture
        # generate_cleanup_script 默认保护 ~ 与 ~/Desktop；
        # pytest 的 tmp_path 位于用户目录下会被连带保护，这里屏蔽该默认值，
        # 仅验证计划/脚本的生成逻辑本身。
        import video_dedup.cleanup as cleanup_mod
        monkeypatch.setattr(cleanup_mod.os.path, "expanduser", lambda p: p)
        out = tmp_path / "out4"
        out.mkdir()
        generate_cleanup_script(groups, files, str(out), hard_delete=False)
        plan = json.loads((out / "cleanup_plan.json").read_text("utf-8"))
        assert isinstance(plan.get("items"), list)
        assert len(plan["items"]) == 1, "每组只清理 1 个重复者（保留者除外）"
        item = plan["items"][0]
        assert {"source", "size"} <= set(item)
        assert (out / "cleanup_duplicates.bat").exists()
        assert (out / "cleanup_duplicates.sh").exists()
        # 清理计划针对的是非保留者，且保留者不得出现在计划里
        retain_idx = groups[0]["retain_idx"]
        to_clean_idx = next(idx for idx, _ in groups[0]["members"]
                            if idx != retain_idx)
        assert item["source"] == files[to_clean_idx]["path"]
        bat = (out / "cleanup_duplicates.bat").read_text("utf-8")
        assert files[to_clean_idx]["name"] in bat, "脚本应包含待清理文件"

    def test_home_dir_auto_protected(self, scan_fixture):
        """默认自动保护用户主目录：其下所有文件不得进入清理计划"""
        tmp_path, files, hashes, groups = scan_fixture
        out = tmp_path / "out6"
        out.mkdir()
        generate_cleanup_script(groups, files, str(out), hard_delete=False)
        plan = json.loads((out / "cleanup_plan.json").read_text("utf-8"))
        assert plan["items"] == [], "用户目录下的测试文件必须被默认保护排除"

    def test_protect_folder_excluded_from_cleanup(self, scan_fixture,
                                                  monkeypatch):
        tmp_path, files, hashes, groups = scan_fixture
        import video_dedup.cleanup as cleanup_mod
        monkeypatch.setattr(cleanup_mod.os.path, "expanduser", lambda p: p)
        protected_dir = str(tmp_path / "videos")
        out = tmp_path / "out5"
        out.mkdir()
        generate_cleanup_script(groups, files, str(out), hard_delete=False,
                                protect_folders={protected_dir})
        plan = json.loads((out / "cleanup_plan.json").read_text("utf-8"))
        assert plan["items"] == [], "受保护目录下的文件必须被排除"


class TestDetectKeyframes:
    def test_scene_cut_detected(self, tmp_path):
        """前半静止、后半换场景 → 至少检测出一个切点"""
        import cv2
        import numpy as np
        path = tmp_path / "cut.mp4"
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             12, (320, 240))
        for i in range(12):
            vw.write(np.full((240, 320, 3), 30, np.uint8))
        for i in range(12):
            vw.write(np.full((240, 320, 3), 220, np.uint8))
        vw.release()
        cap = cv2.VideoCapture(str(path))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        keys = _detect_keyframes(cap, total, threshold=30.0)
        cap.release()
        assert len(keys) >= 2  # 首帧 + 至少一个切点

    def test_no_cut_returns_empty_or_first(self, tmp_path, video_factory):
        import cv2
        path = video_factory("stripes_move")
        cap = cv2.VideoCapture(str(path))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        keys = _detect_keyframes(cap, total, threshold=30.0)
        cap.release()
        assert all(isinstance(k, int) and 0 <= k < total for k in keys)


class TestIgnoreRules:
    def test_exact_filename_rule(self, tmp_path):
        from pathlib import Path
        assert _match_ignore_rule(Path("X:/dir/skip_me.mp4"), ["skip_me.mp4"])
        assert not _match_ignore_rule(Path("X:/dir/keep.mp4"), ["skip_me.mp4"])

    def test_wildcard_rule(self, tmp_path):
        from pathlib import Path
        assert _match_ignore_rule(Path("X:/dir/raw_001.mp4"), ["raw_*"])
        assert not _match_ignore_rule(Path("X:/dir/edit_001.mp4"), ["raw_*"])

    def test_empty_rules_never_match(self, tmp_path):
        from pathlib import Path
        assert not _match_ignore_rule(Path("X:/dir/a.mp4"), [])
