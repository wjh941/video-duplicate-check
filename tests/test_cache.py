# -*- coding: utf-8 -*-
"""哈希缓存管理测试（模块：video_dedup.cache）。"""
import json
import os

import pytest

from video_dedup.cache import (clean_expired_cache, clean_invalid_cache,
                               is_cache_valid, load_cache, merge_caches,
                               save_cache)


def make_entry(path, size=10, mtime=1000.0, md5="abc"):
    return {"path": path, "size": size, "mtime": mtime, "md5": md5,
            "phash": ["0" * 16], "dhash": ["0" * 16],
            "duration": 12.5, "width": 320, "height": 240,
            "cached_at": "2026-01-01 00:00:00"}


class TestSaveLoad:
    def test_roundtrip(self, tmp_path):
        cache_path = str(tmp_path / "cache.json")
        cache = {"_version": "2.6",
                 "C:/v/a.mp4": make_entry("C:/v/a.mp4")}
        save_cache(cache_path, cache)
        loaded = load_cache(cache_path)
        assert loaded.get("_version") == "2.6"
        entry = loaded["C:/v/a.mp4"]
        assert entry["size"] == 10 and entry["duration"] == 12.5
        assert entry["phash"] == ["0" * 16]

    def test_frames_pil_stripped_from_disk(self, tmp_path):
        """frames_pil 等大对象不得写入磁盘缓存"""
        cache_path = str(tmp_path / "cache.json")
        entry = make_entry("x")
        entry["frames_pil"] = ["<PIL Image>"]
        save_cache(cache_path, {"x.mp4": entry})
        on_disk = json.loads(open(cache_path, encoding="utf-8").read())
        assert "frames_pil" not in on_disk["x.mp4"]

    def test_gzip_compress_roundtrip(self, tmp_path):
        cache_path = str(tmp_path / "cache.json")
        save_cache(cache_path, {"a": make_entry("a")}, compress=True)
        assert os.path.exists(cache_path + ".gz")
        loaded = load_cache(cache_path + ".gz")
        assert loaded["a"]["size"] == 10

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        cache_path = str(tmp_path / "cache.json")
        save_cache(cache_path, {"a": make_entry("a")})
        assert not os.path.exists(cache_path + ".tmp")

    def test_load_missing_returns_empty(self, tmp_path):
        loaded = load_cache(str(tmp_path / "nope.json"))
        assert isinstance(loaded, dict)


class TestCacheValidity:
    def test_same_size_mtime_valid(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"x" * 20)
        entry = make_entry(str(f), size=20, mtime=f.stat().st_mtime)
        assert is_cache_valid(entry, {"path": str(f), "size": 20,
                                      "mtime": f.stat().st_mtime}) is True

    def test_size_change_invalidates(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"x" * 20)
        entry = make_entry(str(f), size=20, mtime=f.stat().st_mtime)
        assert is_cache_valid(entry, {"path": str(f), "size": 21,
                                      "mtime": f.stat().st_mtime}) is False


class TestCleanAndMerge:
    def test_clean_invalid_removes_missing_files(self, tmp_path):
        cache_path = str(tmp_path / "cache.json")
        ghost = str(tmp_path / "ghost.mp4")
        save_cache(cache_path, {ghost: make_entry(ghost)})
        removed, remaining = clean_invalid_cache(cache_path)
        assert removed == 1 and remaining == 0

    def test_clean_invalid_keeps_existing_files(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"content")
        cache_path = str(tmp_path / "cache.json")
        # 缓存以文件路径为键；size/mtime 与磁盘一致 → 保留
        save_cache(cache_path, {str(f): make_entry(str(f),
                                                   size=f.stat().st_size,
                                                   mtime=f.stat().st_mtime)})
        removed, remaining = clean_invalid_cache(cache_path)
        assert removed == 0 and remaining == 1

    def test_clean_expired_by_age(self, tmp_path):
        old_file = tmp_path / "old.mp4"
        old_file.write_bytes(b"old")
        fresh_file = tmp_path / "fresh.mp4"
        fresh_file.write_bytes(b"fresh")
        past = __import__("time").time() - 60 * 86400  # 60 天前
        os.utime(old_file, (past, past))
        cache_path = str(tmp_path / "cache.json")
        save_cache(cache_path, {
            str(old_file): make_entry(str(old_file)),
            str(fresh_file): make_entry(str(fresh_file)),
        })
        removed = clean_expired_cache(cache_path, expire_days=30)
        assert removed == 1
        loaded = load_cache(cache_path)
        assert str(old_file) not in loaded
        assert str(fresh_file) in loaded

    def test_merge_two_caches(self, tmp_path):
        c1 = str(tmp_path / "c1.json")
        c2 = str(tmp_path / "c2.json")
        out = str(tmp_path / "merged.json")
        save_cache(c1, {"C:/v/a.mp4": make_entry("C:/v/a.mp4")})
        save_cache(c2, {"C:/v/b.mp4": make_entry("C:/v/b.mp4")})
        total, count = merge_caches([c1, c2], out)
        assert count == 2
        merged = load_cache(out)
        assert "C:/v/a.mp4" in merged and "C:/v/b.mp4" in merged
