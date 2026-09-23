# -*- coding: utf-8 -*-
"""目录扫描与时长过滤测试（模块：video_dedup.scanner）。"""
from argparse import Namespace

import pytest

from video_dedup.scanner import (apply_duration_resolution_filter,
                                 match_duration, parse_duration_filter,
                                 scan_mp4_files)


class TestParseDurationFilter:
    def test_single_condition(self):
        assert parse_duration_filter(">=60") == [(">=", 60.0)]

    def test_multi_condition_and(self):
        conds = parse_duration_filter(">120&<=360")
        assert conds == [(">", 120.0), ("<=", 360.0)]

    def test_lt(self):
        assert parse_duration_filter("<30") == [("<", 30.0)]

    def test_empty_string(self):
        assert parse_duration_filter("") == []

    def test_invalid_raises_or_empty(self):
        try:
            result = parse_duration_filter("abc")
            assert result == []
        except (ValueError, IndexError):
            pass  # 抛错也算合理行为


class TestMatchDuration:
    def test_ge_boundary(self):
        assert match_duration(60.0, [(">=", 60.0)]) is True
        assert match_duration(59.99, [(">=", 60.0)]) is False

    def test_lt(self):
        assert match_duration(29.0, [("<", 30.0)]) is True
        assert match_duration(30.0, [("<", 30.0)]) is False

    def test_and_combination(self):
        conds = [(">", 120.0), ("<=", 360.0)]
        assert match_duration(200.0, conds) is True
        assert match_duration(100.0, conds) is False
        assert match_duration(400.0, conds) is False


class TestScanMp4Files:
    def _args(self, **kw):
        base = dict(dir=None, ext="mp4,mov,mkv", no_recursive=False,
                    exclude_folder="", exclude_size_lt="", exclude_size_gt="",
                    audio_check=False)
        base.update(kw)
        return Namespace(**base)

    def test_discovers_videos_only(self, tmp_path, video_factory):
        video_factory("stripes_move", name="a.mp4")
        video_factory("gradient", name="b.mov")
        (tmp_path / "notes.txt").write_text("not a video")
        found = scan_mp4_files(self._args(dir=str(tmp_path)))
        names = {f["name"] for f in found}
        assert names == {"a.mp4", "b.mov"}
        for f in found:
            assert {"name", "path", "size", "mtime", "size_readable"} <= set(f)

    def test_duplicateignore_rule_excluded(self, tmp_path, video_factory):
        video_factory("stripes_move", name="keep.mp4")
        video_factory("gradient", name="skip_me.mp4")
        (tmp_path / ".duplicateignore").write_text("skip_me.mp4\n",
                                                   encoding="utf-8")
        found = scan_mp4_files(self._args(dir=str(tmp_path)))
        assert {f["name"] for f in found} == {"keep.mp4"}

    def test_no_recursive_only_top_level(self, tmp_path, video_factory):
        video_factory("stripes_move", name="top.mp4")
        video_factory("gradient", name="nested.mp4", )  # 位于 tmp_path 根
        sub = tmp_path / "sub"
        sub.mkdir(exist_ok=True)
        import shutil
        shutil.move(str(tmp_path / "nested.mp4"), str(sub / "nested.mp4"))
        found = scan_mp4_files(self._args(dir=str(tmp_path), no_recursive=True))
        assert {f["name"] for f in found} == {"top.mp4"}

    def test_zero_byte_skipped(self, tmp_path):
        (tmp_path / "empty.mp4").write_bytes(b"")
        found = scan_mp4_files(self._args(dir=str(tmp_path)))
        assert found == []

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            scan_mp4_files(self._args(dir=str(tmp_path / "ghost")))

    def test_custom_ext_filter(self, tmp_path, video_factory):
        video_factory("stripes_move", name="a.mp4")
        video_factory("gradient", name="b.mkv")
        found = scan_mp4_files(self._args(dir=str(tmp_path), ext="mkv"))
        assert {f["name"] for f in found} == {"b.mkv"}


class TestApplyDurationResolutionFilter:
    def test_duration_filter_removes_outliers(self, tmp_path, video_factory):
        fast = video_factory("stripes_move", name="fast.mp4", n=6, fps=12)   # ~0.5s
        slow = video_factory("gradient", name="slow.mp4", n=60, fps=12)     # ~5s
        files = [{"name": "fast.mp4", "path": str(fast),
                  "size": fast.stat().st_size, "mtime": fast.stat().st_mtime},
                 {"name": "slow.mp4", "path": str(slow),
                  "size": slow.stat().st_size, "mtime": slow.stat().st_mtime}]
        args = Namespace(duration_filter=">=3", min_res=0, max_res=0)
        filtered, stats = apply_duration_resolution_filter(
            files, args, cache_path="")
        names = {f["name"] for f in filtered}
        assert names == {"slow.mp4"}
        assert stats["total_before"] == 2 and stats["total_after"] == 1

    def test_no_filters_noop(self, tmp_path, video_factory):
        v = video_factory("stripes_move", name="a.mp4")
        files = [{"name": "a.mp4", "path": str(v), "size": 1, "mtime": 1}]
        args = Namespace(duration_filter="", min_res=0, max_res=0)
        filtered, stats = apply_duration_resolution_filter(files, args, "")
        assert filtered == files
