# -*- coding: utf-8 -*-
"""端到端集成测试：合成视频 → 进程内调用 main() → 校验产物与退出码。"""
import json
import sys
from argparse import Namespace

import pytest


def run_main(monkeypatch, argv):
    """以给定 argv 调用 video_dedup.cli.main()，捕获 SystemExit 退出码。"""
    from video_dedup.cli import main
    monkeypatch.setattr(sys, "argv", ["find_mp4.py"] + argv)
    with pytest.raises(SystemExit) as excinfo:
        main()
    return excinfo.value.code


class TestEndToEndScan:
    def test_duplicate_detected_group_and_exit_code(self, tmp_path, monkeypatch,
                                                    video_factory, capsys):
        """两个相同内容 + 一个不同内容 → 1 个重复分组，退出码 1"""
        vdir = tmp_path / "videos"
        video_factory("stripes_move", name=str(vdir / "dup_a.mp4"))
        video_factory("stripes_move", name=str(vdir / "dup_b.mp4"))
        video_factory("rings", name=str(vdir / "unique.mp4"))
        out = tmp_path / "out"

        code = run_main(monkeypatch, [
            "--dir", str(vdir), "--output-dir", str(out),
            "--frames", "4", "--format", "txt", "--no-cache", "--quiet"])

        assert code == 1, "发现重复分组时退出码必须为 1"
        assert (out / "similar_result.csv").exists()
        assert (out / "duplicate_groups.txt").exists()
        assert (out / "duplicate_paths.txt").exists()

        groups_txt = (out / "duplicate_groups.txt").read_text(encoding="utf-8")
        assert "dup_a.mp4" in groups_txt and "dup_b.mp4" in groups_txt
        assert "unique.mp4" not in groups_txt

    def test_clean_dir_exit_zero(self, tmp_path, monkeypatch, video_factory):
        """三个内容完全不同的视频 → 无重复，退出码 0"""
        vdir = tmp_path / "videos"
        video_factory("stripes_move", name=str(vdir / "a.mp4"))
        video_factory("rings", name=str(vdir / "b.mp4"))
        video_factory("checker", name=str(vdir / "c.mp4"))
        out = tmp_path / "out"

        code = run_main(monkeypatch, [
            "--dir", str(vdir), "--output-dir", str(out),
            "--frames", "4", "--format", "txt", "--no-cache", "--quiet"])
        assert code == 0

    def test_summary_json_schema(self, tmp_path, monkeypatch, video_factory):
        """--summary-json 导出稳定的机器可读摘要"""
        vdir = tmp_path / "videos"
        video_factory("stripes_move", name=str(vdir / "dup_a.mp4"))
        video_factory("stripes_move", name=str(vdir / "dup_b.mp4"))
        out = tmp_path / "out"

        code = run_main(monkeypatch, [
            "--dir", str(vdir), "--output-dir", str(out),
            "--frames", "4", "--no-cache", "--quiet", "--summary-json"])
        assert code == 1

        summary = json.loads((out / "scan_summary.json").read_text("utf-8"))
        for key in ("schema_version", "status", "total_videos", "hash_success",
                    "parse_failures", "duplicate_groups", "duplicate_videos",
                    "groups", "extensions", "quality_buckets"):
            assert key in summary, f"scan_summary.json 缺少字段 {key}"
        assert summary["status"] == "duplicates_found"
        assert summary["total_videos"] == 2
        assert summary["duplicate_groups"] == 1
        assert summary["parse_failures"] == 0

    def test_exit_code_2_on_corrupt_video(self, tmp_path, monkeypatch,
                                          video_factory):
        """无法解析的视频 → 退出码 2"""
        vdir = tmp_path / "videos"
        video_factory("stripes_move", name=str(vdir / "good.mp4"))
        (vdir / "corrupt.mp4").write_bytes(b"not-a-real-video" * 100)
        out = tmp_path / "out"

        code = run_main(monkeypatch, [
            "--dir", str(vdir), "--output-dir", str(out),
            "--frames", "4", "--no-cache", "--quiet"])
        assert code == 2
        assert (out / "bad_video_list.txt").exists()

    def test_conflicting_params_rejected(self, tmp_path, monkeypatch):
        """--fast 与 --double-check 互斥 → 退出码 3"""
        code = run_main(monkeypatch, [
            "--dir", str(tmp_path), "--fast", "--double-check"])
        assert code == 3
