# -*- coding: utf-8 -*-
"""相似度计算与阈值边界测试（模块：video_dedup.compare.compute_similarity）。"""
import imagehash
import pytest

from video_dedup.compare import _hash_list_similarity, compute_similarity

ZEROS = "0" * 16
ONES = "f" * 16


def h(hex_str):
    return [imagehash.hex_to_hash(hex_str)]


def sig(phash_hex=ZEROS, dhash_hex=ZEROS, duration=10.0, audio=None):
    return {"phash": h(phash_hex), "dhash": h(dhash_hex),
            "duration": duration, "audio": audio}


class TestComputeSimilarity:
    def test_identical_is_one(self):
        result = compute_similarity(sig(), sig())
        assert result["similarity"] == 1.0
        assert result["distance"] == 0.0

    def test_empty_dict_is_zero(self):
        assert compute_similarity({}, sig())["similarity"] == 0.0
        assert compute_similarity(None, sig())["similarity"] == 0.0

    def test_symmetry(self):
        a, b = sig(ZEROS), sig(ONES)
        assert compute_similarity(a, b)["similarity"] == \
               compute_similarity(b, a)["similarity"]

    def test_result_clamped(self):
        result = compute_similarity(sig(ZEROS), sig(ONES))
        assert 0.0 <= result["similarity"] <= 1.0
        assert abs(result["distance"] - (1 - result["similarity"])) < 1e-9

    def test_duration_prefilter_rejects(self):
        """时长差超过 20% → 直接判 0，不进哈希比对"""
        result = compute_similarity(sig(duration=10.0), sig(duration=30.0))
        assert result["similarity"] == 0.0

    def test_duration_close_passes(self):
        """时长差 10% 不影响画面相似度判定"""
        result = compute_similarity(sig(duration=10.0), sig(duration=11.0))
        assert result["similarity"] == 1.0

    def test_one_side_zero_duration_not_filtered(self):
        """一方时长未知（0）时不做时长预筛"""
        result = compute_similarity(sig(duration=10.0), sig(duration=0.0))
        assert result["similarity"] == 1.0

    def test_phash_weight_dominates(self):
        """phash 完全相同、dhash 完全不同时，权重决定融合结果"""
        weights = {"phash_weight": 1.0, "dhash_weight": 0.0}
        result = compute_similarity(sig(ZEROS, ZEROS), sig(ZEROS, ONES),
                                    weights=weights)
        assert result["similarity"] == 1.0
        weights = {"phash_weight": 0.0, "dhash_weight": 1.0}
        result = compute_similarity(sig(ZEROS, ZEROS), sig(ZEROS, ONES),
                                    weights=weights)
        assert result["similarity"] == 0.0


class TestAudioPenalty:
    def test_both_audio_dissimilar_penalized(self):
        far_audio_a, far_audio_b = h(ZEROS), h(ONES)
        result = compute_similarity(sig(audio=far_audio_a),
                                    sig(audio=far_audio_b))
        assert abs(result["similarity"] - 0.8) < 1e-9  # 1.0 * 0.8

    def test_one_sided_audio_not_penalized(self):
        """仅一方有音频（如静音）不扣分——画面一致即一致"""
        result = compute_similarity(sig(audio=h(ZEROS)), sig(audio=None))
        assert result["similarity"] == 1.0

    def test_similar_audio_not_penalized(self):
        result = compute_similarity(sig(audio=h(ZEROS)), sig(audio=h(ZEROS)))
        assert result["similarity"] == 1.0

    def test_audio_weight_configurable(self):
        weights = {"phash_weight": 0.7, "dhash_weight": 0.3, "audio_weight": 0.5}
        result = compute_similarity(sig(audio=h(ZEROS)), sig(audio=h(ONES)),
                                    weights=weights)
        assert abs(result["similarity"] - 0.5) < 1e-9


class TestHashListSimilarity:
    def test_empty_lists_zero(self):
        assert _hash_list_similarity([], []) == 0.0
        assert _hash_list_similarity([], h(ZEROS)) == 0.0

    def test_nearest_neighbor_matching(self):
        """最近邻匹配：每帧哈希取对对方列表的最小距离后求平均"""
        near = format(1, "016x")
        sim = _hash_list_similarity([h(ZEROS)[0], h(near)[0]], h(ZEROS))
        # h1=ZEROS 距离 0；h1=near 距离 1 → 平均 0.5/64
        assert abs(sim - (1 - 0.5 / 64)) < 1e-9

    def test_perfect_match_is_one(self):
        assert _hash_list_similarity(h(ZEROS), h(ZEROS)) == 1.0

    def test_opposite_is_zero(self):
        assert _hash_list_similarity(h(ZEROS), h(ONES)) == 0.0
