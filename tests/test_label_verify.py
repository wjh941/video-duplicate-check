# -*- coding: utf-8 -*-
"""预标注一致性验证测试（顶层模块 label_verify.py：标注识别 + 判定逻辑）。"""
import pytest

import label_verify as lv


class TestValidLabel:
    @pytest.mark.parametrize("token,expected", [
        ("dog_come", True),
        ("行走", True),
        ("closeup-dog", True),
        ("20240501", False),   # 纯数字不算标签
        ("x", False),          # 单字符太短
        ("", False),
        ("  ", False),
    ])
    def test_valid_label(self, token, expected):
        assert lv._valid_label(token) is expected


class TestAssignLabels:
    def _videos(self, tmp_path, rel_paths):
        out = []
        for rel in rel_paths:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\x00")
            out.append({"path": str(p), "name": p.name,
                        "size": p.stat().st_size})
        return out

    def test_folder_mode(self, tmp_path):
        videos = self._videos(tmp_path, ["move/a.mp4", "move/b.mp4",
                                         "static/c.mp4"])
        label_map, used = lv.assign_labels(videos, "folder", str(tmp_path), "")
        assert used == "folder"
        assert label_map[str(tmp_path / "move" / "a.mp4")] == "move"
        assert label_map[str(tmp_path / "static" / "c.mp4")] == "static"

    def test_prefix_mode(self, tmp_path):
        videos = self._videos(tmp_path, ["cam01_closeup-pos-01.mp4",
                                         "cam01_closeup-pos-02.mp4"])
        label_map, used = lv.assign_labels(videos, "prefix", str(tmp_path), "")
        assert used == "prefix"
        assert label_map[str(tmp_path / "cam01_closeup-pos-01.mp4")] == "cam01"

    def test_regex_mode_capture_group(self, tmp_path):
        videos = self._videos(tmp_path, [
            "20260909-cam01_dog_come-pos-daytime-276.mp4",
            "20260909-cam01_static-neg-night-9.mp4"])
        label_map, used = lv.assign_labels(
            videos, "regex", str(tmp_path), "",
            label_regex=r"cam01_(.+?)-(?:pos|neg)")
        assert used == "regex"
        assert label_map[str(tmp_path / "20260909-cam01_dog_come-pos-daytime-276.mp4")] == "dog_come"
        assert label_map[str(tmp_path / "20260909-cam01_static-neg-night-9.mp4")] == "static"

    def test_regex_without_match_raises(self, tmp_path):
        videos = self._videos(tmp_path, ["nomatch.mp4"])
        with pytest.raises(ValueError):
            lv.assign_labels(videos, "regex", str(tmp_path), "",
                             label_regex=r"zzz_(.+)")
        with pytest.raises(ValueError):
            lv.assign_labels(videos, "regex", str(tmp_path), "",
                             label_regex=r"(nomatch.mp4)?")  # 空捕获组

    def test_csv_mode(self, tmp_path):
        videos = self._videos(tmp_path, ["a.mp4", "b.mp4"])
        csv_path = tmp_path / "labels.csv"
        csv_path.write_text("filename,label\na.mp4,dog\nb.mp4,cat\n",
                            encoding="utf-8-sig")
        label_map, used = lv.assign_labels(videos, "csv", str(tmp_path),
                                           str(csv_path))
        assert label_map[str(tmp_path / "a.mp4")] == "dog"
        assert label_map[str(tmp_path / "b.mp4")] == "cat"

    def test_csv_missing_columns_raises(self, tmp_path):
        videos = self._videos(tmp_path, ["a.mp4"])
        csv_path = tmp_path / "bad.csv"
        csv_path.write_text("file,cls\na.mp4,dog\n", encoding="utf-8-sig")
        with pytest.raises(ValueError):
            lv.assign_labels(videos, "csv", str(tmp_path), str(csv_path))

    def test_auto_prefers_folder(self, tmp_path):
        videos = self._videos(tmp_path, ["move/run_01.mp4", "move/run_02.mp4",
                                         "static/idle_01.mp4"])
        label_map, used = lv.assign_labels(videos, "auto", str(tmp_path), "")
        assert used == "folder(auto)"
        assert label_map[str(tmp_path / "move" / "run_01.mp4")] == "move"

    def test_unknown_mode_raises(self, tmp_path):
        videos = self._videos(tmp_path, ["a.mp4"])
        with pytest.raises(ValueError):
            lv.assign_labels(videos, "bogus", str(tmp_path), "")


class TestVerdict:
    def test_insufficient_samples(self):
        stats = {"count": 1, "intra_mean": 1.0}
        assert "样本不足" in lv.verdict_of(stats, {"global_inter": 0.5})

    def test_consistent(self):
        stats = {"count": 3, "intra_mean": 0.95}
        assert lv.verdict_of(stats, {"global_inter": 0.5}).startswith("一致")

    def test_suspicious(self):
        stats = {"count": 3, "intra_mean": 0.3}
        assert lv.verdict_of(stats, {"global_inter": 0.5}).startswith("可疑")


class TestHashSimilarity:
    def _sig(self, hex_str="0" * 16, motion=0.0):
        import imagehash
        h = [imagehash.hex_to_hash(hex_str)]
        return {"phash": h, "dhash": h, "fg_phash": h, "fg_dhash": h,
                "motion_mean": motion}

    def test_identical_signature_is_one(self):
        assert lv.hash_similarity(self._sig(), self._sig()) == 1.0

    def test_symmetric(self):
        a, b = self._sig("0" * 16), self._sig("f" * 16)
        assert lv.hash_similarity(a, b) == lv.hash_similarity(b, a)

    def test_motion_similarity_log_scale(self):
        a = {"motion_mean": 0.0}
        b = {"motion_mean": 0.0}
        assert lv.motion_similarity(a, b, scale=5.0) == 1.0
        far = {"motion_mean": 1000.0}
        assert lv.motion_similarity(a, far, scale=5.0) == 0.0
