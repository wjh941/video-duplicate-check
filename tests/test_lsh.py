# -*- coding: utf-8 -*-
"""LSH 分桶逻辑测试（模块：video_dedup.compare._build_lsh_buckets + find_similar_pairs）。"""
import imagehash
import pytest

from video_dedup.compare import _build_lsh_buckets, find_similar_pairs

BANDS = 4
BITS = 64


def make_h(hex_str: str) -> list:
    return [imagehash.hex_to_hash(hex_str)]


def hd(hex_str: str, duration: float = 10.0) -> dict:
    return {"phash": make_h(hex_str), "dhash": make_h(hex_str),
            "duration": duration, "width": 320, "height": 240}


ZEROS = "0" * 16


class TestBuildLshBuckets:
    def test_identical_hashes_share_every_band(self):
        buckets = _build_lsh_buckets({0: hd(ZEROS), 1: hd(ZEROS)}, 32)
        for band in range(BANDS):
            key = (band, int(ZEROS, 16) % 32)  # 各 band 值都是 0
            assert 0 in buckets[key] and 1 in buckets[key]

    def test_one_bit_difference_still_candidate(self):
        """低位 1 bit 之差：band0 不同，但其余 3 个 band 完全一致 → 候选对"""
        one_bit = format(1, "016x")
        buckets = _build_lsh_buckets({0: hd(ZEROS), 1: hd(one_bit)}, 32)
        shared = 0
        for key, members in buckets.items():
            if 0 in members and 1 in members:
                shared += 1
        assert shared == BANDS - 1  # band1..band3 相同

    def test_bucket_keys_are_band_value_tuples(self):
        buckets = _build_lsh_buckets({0: hd(ZEROS)}, 32)
        non_empty = [k for k, v in buckets.items() if v]
        assert all(isinstance(k, tuple) and len(k) == 2 for k in non_empty)
        assert all(k[0] in range(BANDS) for k in non_empty)

    def test_four_bands_per_video(self):
        video_hashes = {i: hd(format(i, "016x")) for i in range(6)}
        buckets = _build_lsh_buckets(video_hashes, 32)
        seen = {}
        for key, members in buckets.items():
            for m in members:
                seen.setdefault(m, set()).add(key[0])
        assert all(len(bands) == BANDS for bands in seen.values())

    def test_empty_hash_goes_to_empty_bucket(self):
        buckets = _build_lsh_buckets({0: {"phash": [], "dhash": []}}, 32)
        assert buckets["__empty__"] == [0]

    def test_invalid_hex_goes_to_empty_bucket(self):
        buckets = _build_lsh_buckets({0: {"phash": ["not-hex!"]}}, 32)
        assert buckets["__empty__"] == [0]

    def test_num_buckets_minimum_two(self):
        """num_buckets 为 0/None 时按默认 32，且不会出现 0 取模崩溃"""
        buckets = _build_lsh_buckets({0: hd(ZEROS), 1: hd(ZEROS)}, 0)
        assert any(0 in v and 1 in v for v in buckets.values())


class TestFindSimilarPairs:
    def test_duplicate_pair_found_small_n(self):
        """n<50 时即使指定 lsh_buckets 也走全量比对，重复对必须被发现"""
        video_hashes = {0: hd(ZEROS), 1: hd(ZEROS), 2: hd("f" * 16)}
        mp4_files = [{"name": f"v{i}.mp4", "path": f"/x/v{i}.mp4"} for i in range(3)]
        pairs = find_similar_pairs(video_hashes, mp4_files, threshold=0.9,
                                   lsh_buckets=32)
        found = {(p["idx_a"], p["idx_b"]) for p in pairs}
        assert (0, 1) in found
        assert all((1, 2) != f and (0, 2) != f for f in found)

    def test_threshold_boundary_exact(self):
        """汉明距离 1/64 → 相似度 0.984375：阈值 0.99 排除、0.98 保留"""
        one_bit = format(1, "016x")
        video_hashes = {0: hd(ZEROS), 1: hd(one_bit)}
        mp4_files = [{"name": "a.mp4", "path": "/x/a.mp4"},
                     {"name": "b.mp4", "path": "/x/b.mp4"}]
        high = find_similar_pairs(video_hashes, mp4_files, threshold=0.99)
        low = find_similar_pairs(video_hashes, mp4_files, threshold=0.98)
        assert high == []
        assert len(low) == 1
        assert abs(low[0]["similarity"] - (1 - 1 / 64)) < 1e-9

    def test_lsh_recall_on_dedup_pair_large_n(self):
        """n>50 触发 LSH：完全相同的哈希对绝不能因分桶而漏掉"""
        video_hashes = {0: hd(ZEROS), 1: hd(ZEROS)}
        for i in range(2, 60):
            video_hashes[i] = hd(format(i * 0x9E3779B97F4A7C15 & (2**64 - 1),
                                        "016x"))
        mp4_files = [{"name": f"v{i}.mp4", "path": f"/x/v{i}.mp4"}
                     for i in range(60)]
        pairs = find_similar_pairs(video_hashes, mp4_files, threshold=0.95,
                                   lsh_buckets=32)
        assert any({p["idx_a"], p["idx_b"]} == {0, 1} for p in pairs)

    def test_duration_prefilter_skips_pair(self):
        """时长差 >20% 的对直接不参与比对"""
        video_hashes = {0: hd(ZEROS, duration=10.0),
                        1: hd(ZEROS, duration=30.0)}
        mp4_files = [{"name": "a.mp4", "path": "/x/a.mp4"},
                     {"name": "b.mp4", "path": "/x/b.mp4"}]
        pairs = find_similar_pairs(video_hashes, mp4_files, threshold=0.5)
        assert pairs == []

    def test_pair_result_fields(self):
        video_hashes = {0: hd(ZEROS), 1: hd(ZEROS)}
        mp4_files = [{"name": "a.mp4", "path": "/x/a.mp4"},
                     {"name": "b.mp4", "path": "/x/b.mp4"}]
        pairs = find_similar_pairs(video_hashes, mp4_files, threshold=0.9)
        p = pairs[0]
        assert {"idx_a", "idx_b", "name_a", "name_b", "path_a", "path_b",
                "similarity", "distance"} <= set(p)
        assert p["name_a"] == "a.mp4" and p["path_b"] == "/x/b.mp4"
        assert p["similarity"] == 1.0 and p["distance"] == 0.0

    def test_skip_pairs_honored(self):
        video_hashes = {0: hd(ZEROS), 1: hd(ZEROS)}
        mp4_files = [{"name": "a.mp4", "path": "/x/a.mp4"},
                     {"name": "b.mp4", "path": "/x/b.mp4"}]
        pairs = find_similar_pairs(video_hashes, mp4_files, threshold=0.9,
                                   skip_pairs={(0, 1)})
        assert pairs == []
