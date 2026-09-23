# -*- coding: utf-8 -*-
"""分组与保留策略测试（模块：video_dedup.grouper）。"""
from video_dedup.grouper import (_get_keep_strategy, _select_retain,
                                 _split_group_min_sim, build_groups)


def mk_pair(a, b, sim):
    return {"idx_a": a, "idx_b": b, "similarity": sim}


def mk_files(n):
    return [{"name": f"v{i}.mp4", "path": f"/x/v{i}.mp4",
             "size": 100 + i, "mtime": 1000.0 + i} for i in range(n)]


class TestSplitGroupMinSim:
    def test_chain_with_weak_bridge_is_split(self):
        """A≈B≈C 但 A≉C：floor=0.8 时弱边 (0,2) 两端不得同组"""
        sim = {(0, 1): 0.9, (1, 2): 0.85, (0, 2): 0.5}
        groups = _split_group_min_sim([0, 1, 2], sim, 0.8)
        assert not any(0 in g and 2 in g for g in groups)
        for g in groups:
            for i, a in enumerate(g):
                for b in g[i + 1:]:
                    assert sim.get((min(a, b), max(a, b)), 0.0) >= 0.8

    def test_all_strong_edges_not_split(self):
        sim = {(0, 1): 0.95, (1, 2): 0.92, (0, 2): 0.9}
        groups = _split_group_min_sim([0, 1, 2], sim, 0.8)
        assert len(groups) == 1 and sorted(groups[0]) == [0, 1, 2]

    def test_all_weak_pairs_all_split(self):
        sim = {(0, 1): 0.3, (1, 2): 0.3, (0, 2): 0.3}
        groups = _split_group_min_sim([0, 1, 2], sim, 0.8)
        assert all(len(g) < 2 for g in groups)

    def test_single_member_no_group(self):
        assert _split_group_min_sim([7], {}, 0.8) == []


class TestBuildGroups:
    def test_pair_becomes_single_group(self):
        files = mk_files(3)
        pairs = [mk_pair(0, 1, 0.92)]
        groups = build_groups(pairs, files, {}, keep_strategy="max-size")
        assert len(groups) == 1
        members = [idx for idx, _ in groups[0]["members"]]
        assert sorted(members) == [0, 1]
        assert groups[0]["retain_idx"] == 1  # size 更大者保留

    def test_disconnected_pairs_form_separate_groups(self):
        files = mk_files(4)
        pairs = [mk_pair(0, 1, 0.9), mk_pair(2, 3, 0.88)]
        groups = build_groups(pairs, files, {})
        assert len(groups) == 2

    def test_no_pairs_no_groups(self):
        assert build_groups([], mk_files(3), {}) == []

    def test_min_group_sim_splits_transitive_chain(self):
        files = mk_files(3)
        pairs = [mk_pair(0, 1, 0.9), mk_pair(1, 2, 0.85), mk_pair(0, 2, 0.5)]
        groups = build_groups(pairs, files, {}, min_group_sim=0.8)
        for g in groups:
            for (a, b), sim in g["similarities"].items():
                assert sim >= 0.8

    def test_sorted_by_group_size_desc(self):
        files = mk_files(5)
        pairs = [mk_pair(0, 1, 0.9), mk_pair(2, 3, 0.9), mk_pair(3, 4, 0.85)]
        groups = build_groups(pairs, files, {})
        sizes = [len(g["members"]) for g in groups]
        assert sizes == sorted(sizes, reverse=True)


class TestSelectRetain:
    def _members(self, idxs, files):
        return [(i, files[i]) for i in idxs]

    def test_max_size_default(self):
        files = mk_files(3)  # sizes 100,101,102
        assert _select_retain(self._members([0, 1, 2], files), {}, "max-size") == 2

    def test_latest_mtime(self):
        files = mk_files(3)  # mtimes 1000,1001,1002
        assert _select_retain(self._members([0, 1, 2], files), {}, "latest") == 2

    def test_max_res_from_hashes(self):
        files = mk_files(2)
        hashes = {0: {"width": 1920, "height": 1080}, 1: {"width": 640, "height": 480}}
        assert _select_retain(self._members([0, 1], files), hashes, "max-res") == 0

    def test_max_bitrate_prefers_higher(self):
        files = mk_files(2)  # size 100,101
        hashes = {0: {"duration": 10}, 1: {"duration": 100}}  # 码率 10 vs ~1
        assert _select_retain(self._members([0, 1], files), hashes, "max-bitrate") == 0


class TestKeepStrategy:
    def test_flags_to_strategy(self, ):
        from argparse import Namespace
        assert _get_keep_strategy(Namespace()) == "max-size"
        assert _get_keep_strategy(Namespace(keep_latest=True)) == "latest"
        assert _get_keep_strategy(Namespace(keep_max_res=True)) == "max-res"
        assert _get_keep_strategy(Namespace(keep_max_bitrate=True)) == "max-bitrate"
