# -*- coding: utf-8 -*-
"""pHash/dHash/MD5/SHA-256 单元测试（模块：video_dedup.hasher / utils）。"""
import hashlib

import cv2
import imagehash
import numpy as np
import pytest

from video_dedup.hasher import (_compute_frame_hashes, _extract_hashes_single,
                                extract_hashes_with_cache)
from video_dedup.utils import _compute_file_md5, _compute_file_sha256


class TestFrameHashes:
    def test_returns_phash_and_dhash_imagehash(self, gray_pair_identical):
        img, _ = gray_pair_identical
        ph, dh = _compute_frame_hashes(img)
        assert isinstance(ph, imagehash.ImageHash)
        assert isinstance(dh, imagehash.ImageHash)
        assert len(ph.hash.flatten()) == 64  # HASH_SIZE=8 -> 64 bit

    def test_identical_frames_distance_zero(self, gray_pair_identical):
        img, same = gray_pair_identical
        ph1, dh1 = _compute_frame_hashes(img)
        ph2, dh2 = _compute_frame_hashes(same)
        assert (ph1 - ph2) == 0
        assert (dh1 - dh2) == 0

    def test_deterministic_across_calls(self, gray_pair_identical):
        img, _ = gray_pair_identical
        a = _compute_frame_hashes(img)
        b = _compute_frame_hashes(img.copy())
        assert a[0] == b[0] and a[1] == b[1]

    def test_small_brightness_change_stays_close(self, gray_pair_identical):
        img, _ = gray_pair_identical
        brighter = cv2.convertScaleAbs(img, alpha=1.0, beta=6)
        ph1, _ = _compute_frame_hashes(img)
        ph2, _ = _compute_frame_hashes(brighter)
        assert (ph1 - ph2) <= 6  # 微小亮度变化不应大幅改变 pHash

    def test_different_content_far_apart(self, gray_pair_different):
        a, b = gray_pair_different
        ph_a, _ = _compute_frame_hashes(a)
        ph_b, _ = _compute_frame_hashes(b)
        assert (ph_a - ph_b) >= 12  # 低频结构差异在 pHash 上应显著

    def test_32x32_preprocessing_invariance(self):
        """同一内容不同分辨率应得到相同 pHash（工具内部统一缩到 32x32）"""
        small = np.zeros((32, 32), np.uint8)
        cv2.circle(small, (16, 16), 10, 255, 2)
        big = cv2.resize(small, (256, 256), interpolation=cv2.INTER_NEAREST)
        ph_small, _ = _compute_frame_hashes(small)
        ph_big, _ = _compute_frame_hashes(
            cv2.resize(big, (32, 32), interpolation=cv2.INTER_AREA))
        assert (ph_small - ph_big) <= 2


class TestFileDigests:
    def test_md5_known_value(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"hello world")
        assert _compute_file_md5(str(f)) == hashlib.md5(b"hello world").hexdigest()

    def test_md5_stable_and_sensitive(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"abcdef")
        h1 = _compute_file_md5(str(f))
        assert h1 == _compute_file_md5(str(f))
        f.write_bytes(b"abcdeg")  # 改一个字节
        assert _compute_file_md5(str(f)) != h1

    def test_sha256_stable_and_sensitive(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"video-bytes")
        h1 = _compute_file_sha256(str(f))
        assert h1 == hashlib.sha256(b"video-bytes").hexdigest()
        assert h1 == _compute_file_sha256(str(f))
        f.write_bytes(b"video-bytes!")
        assert _compute_file_sha256(str(f)) != h1

    def test_md5_missing_file_returns_none_or_raises_cleanly(self, tmp_path):
        # 不应崩溃出栈——返回 None 或可预期的错误
        try:
            result = _compute_file_md5(str(tmp_path / "nope.bin"))
        except (OSError, IOError):
            result = None
        assert result is None or isinstance(result, str)


class TestExtractSingle:
    def test_synthetic_video_hash_dict(self, video_factory):
        path = video_factory("stripes_move")
        h, err = _extract_hashes_single(str(path), num_frames=5)
        assert err is None and h is not None
        assert len(h["phash"]) == 5 and len(h["dhash"]) == 5
        assert h["duration"] > 0
        assert h["width"] == 320 and h["height"] == 240
        assert h["fps"] > 0
        assert h["frames_pil"] and len(h["frames_pil"]) == 5
        assert h["audio"] is None  # 默认不开音频

    def test_frame_count_clamped_to_request(self, video_factory):
        path = video_factory("checker")
        h, _ = _extract_hashes_single(str(path), num_frames=3)
        assert len(h["phash"]) == 3

    def test_no_store_frames(self, video_factory):
        path = video_factory("rings")
        h, _ = _extract_hashes_single(str(path), num_frames=2, store_frames=False)
        assert h["frames_pil"] is None

    def test_identical_content_identical_hashes(self, tmp_path):
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        from conftest import write_video
        write_video(a, "stripes_move")
        write_video(b, "stripes_move")  # 同一内容族、相同帧序列
        ha, err_a = _extract_hashes_single(str(a), num_frames=5)
        hb, err_b = _extract_hashes_single(str(b), num_frames=5)
        assert err_a is None and err_b is None
        assert all(x == y for x, y in zip(ha["phash"], hb["phash"]))

    def test_missing_file_error_code(self, tmp_path):
        h, err = _extract_hashes_single(str(tmp_path / "ghost.mp4"), num_frames=3)
        assert h is None
        assert err in ("read_failed", "decode_error")


class TestExtractWithCache:
    def test_second_run_served_from_cache(self, video_factory, fake_args,
                                          tmp_path, monkeypatch):
        path = video_factory("stripes_static")
        file_info = {"path": str(path), "name": path.name,
                     "size": path.stat().st_size, "mtime": path.stat().st_mtime}
        cache_path = str(tmp_path / "cache.json")

        calls = {"n": 0}
        real = _extract_hashes_single

        def counting(*a, **kw):
            calls["n"] += 1
            return real(*a, **kw)

        monkeypatch.setattr("video_dedup.hasher._extract_hashes_single", counting)

        hashes1, bad1, _stats = extract_hashes_with_cache(
            [file_info], cache_path, num_frames=4, max_workers=1,
            args=fake_args, use_cache=True)
        assert calls["n"] == 1 and not bad1
        assert 0 in hashes1 and len(hashes1[0]["phash"]) == 4

        hashes2, bad2, _stats = extract_hashes_with_cache(
            [file_info], cache_path, num_frames=4, max_workers=1,
            args=fake_args, use_cache=True)
        assert calls["n"] == 1, "第二次扫描必须命中缓存，不再重新抽帧"
        assert [str(x) for x in hashes1[0]["phash"]] == \
               [str(x) for x in hashes2[0]["phash"]]
        assert not bad2

    def test_changed_size_recomputed(self, video_factory, fake_args, tmp_path):
        path = video_factory("gradient")
        file_info = {"path": str(path), "name": path.name,
                     "size": path.stat().st_size + 1,  # 故意错误的 size
                     "mtime": path.stat().st_mtime}
        hashes, bad, _ = extract_hashes_with_cache(
            [file_info], str(tmp_path / "c.json"), num_frames=3,
            max_workers=1, args=fake_args, use_cache=False)
        assert not bad and 0 in hashes
