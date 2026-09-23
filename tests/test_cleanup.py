# -*- coding: utf-8 -*-
"""安全清理流程测试（模块：video_dedup.cleanup）。

覆盖：计划校验 → 预览 → 确认执行（隔离区+SHA-256）→ 恢复 → 列出 → 过期清理。
全部使用 tmp_path 隔离，不会触碰真实素材。
"""
import json
import os
import shutil
import time
from argparse import Namespace

import pytest

from video_dedup.cleanup import (_run_execute_plan, _run_gen_restore,
                                 _run_list_operations, _run_purge_operations,
                                 _run_restore_operation, _run_validate_plan)

EXIT_OK = 0
EXIT_PARSE_ERROR = 2
EXIT_BAD_ARGS = 3


def make_plan(tmp_path, n=2):
    """生成 n 个待清理文件 + 对应清理计划，返回 (plan_path, sources)"""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    sources = []
    for i in range(n):
        f = work / f"dup_{i}.mp4"
        f.write_bytes(f"video-content-{i}".encode() * 100)
        sources.append(str(f))
    plan = {
        "schema_version": 2,
        "items": [{"source": s, "size": os.path.getsize(s),
                   "mtime": os.path.getmtime(s)} for s in sources],
    }
    plan_path = tmp_path / "cleanup_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return plan_path, sources


def ns(**kw):
    base = dict(plan_file="", confirm_cleanup=False, trash_dir="",
                older_than=30, confirm_purge=False, operation_file="",
                confirm_restore=False)
    base.update(kw)
    return Namespace(**base)


class TestValidatePlan:
    def test_intact_plan_ok(self, tmp_path, capsys):
        plan_path, _ = make_plan(tmp_path)
        rc = _run_validate_plan(ns(plan_file=str(plan_path)))
        assert rc == EXIT_OK
        assert "可执行: 2" in capsys.readouterr().out

    def test_missing_file_flagged(self, tmp_path, capsys):
        plan_path, sources = make_plan(tmp_path)
        os.remove(sources[0])  # 删掉一个文件
        rc = _run_validate_plan(ns(plan_file=str(plan_path)))
        assert rc == EXIT_PARSE_ERROR
        assert "不存在" in capsys.readouterr().out

    def test_changed_size_flagged(self, tmp_path, capsys):
        plan_path, sources = make_plan(tmp_path)
        with open(sources[0], "ab") as f:  # 追加字节 → 大小变化
            f.write(b"extra")
        rc = _run_validate_plan(ns(plan_file=str(plan_path)))
        assert rc == EXIT_PARSE_ERROR
        assert "已变化" in capsys.readouterr().out

    def test_invalid_json_rejected(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert _run_validate_plan(ns(plan_file=str(bad))) == EXIT_BAD_ARGS

    def test_plan_without_items_rejected(self, tmp_path):
        bad = tmp_path / "noitems.json"
        bad.write_text(json.dumps({"foo": 1}), encoding="utf-8")
        assert _run_validate_plan(ns(plan_file=str(bad))) == EXIT_BAD_ARGS


class TestExecutePlan:
    def test_preview_moves_nothing(self, tmp_path, capsys):
        plan_path, sources = make_plan(tmp_path)
        rc = _run_execute_plan(ns(plan_file=str(plan_path),
                                  confirm_cleanup=False))
        assert rc == EXIT_OK
        assert all(os.path.exists(s) for s in sources)
        assert "预览模式" in capsys.readouterr().out

    def test_confirm_moves_to_quarantine_with_sha256(self, tmp_path):
        plan_path, sources = make_plan(tmp_path)
        rc = _run_execute_plan(ns(plan_file=str(plan_path),
                                  confirm_cleanup=True))
        assert rc == EXIT_OK
        assert not any(os.path.exists(s) for s in sources), "源文件必须被移走"
        trash_root = tmp_path / "trash"
        ops = list(trash_root.iterdir())
        assert len(ops) == 1, "应创建一个带操作ID的隔离目录"
        op_file = ops[0] / "operation.json"
        data = json.loads(op_file.read_text(encoding="utf-8"))
        assert data["schema_version"] == 2
        moved = [o for o in data["operations"] if o.get("status") == "moved"]
        assert len(moved) == 2
        import hashlib
        for o in moved:
            assert os.path.exists(o["target"])
            digest = hashlib.sha256(open(o["target"], "rb").read()).hexdigest()
            assert o["sha256"] == digest, "记录的 SHA-256 必须与实际内容一致"
            assert o["size"] > 0

    def test_size_mismatch_item_skipped(self, tmp_path):
        plan_path, sources = make_plan(tmp_path, n=1)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["items"][0]["size"] = 1  # 与实际大小不符
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        rc = _run_execute_plan(ns(plan_file=str(plan_path),
                                  confirm_cleanup=True))
        assert rc == EXIT_OK  # ready 为空 → moved==len(ready)==0
        assert os.path.exists(sources[0]), "大小不匹配的文件不得移动"

    def test_invalid_plan_file_rejected(self, tmp_path):
        assert _run_execute_plan(
            ns(plan_file=str(tmp_path / "nope.json"))) == EXIT_BAD_ARGS


class TestRestoreOperation:
    def _execute_first(self, tmp_path):
        plan_path, sources = make_plan(tmp_path)
        rc = _run_execute_plan(ns(plan_file=str(plan_path),
                                  confirm_cleanup=True))
        assert rc == EXIT_OK
        op_file = next((tmp_path / "trash").iterdir()) / "operation.json"
        return op_file, sources

    def test_preview_restores_nothing(self, tmp_path, capsys):
        op_file, sources = self._execute_first(tmp_path)
        rc = _run_restore_operation(ns(operation_file=str(op_file),
                                       confirm_restore=False))
        assert rc == EXIT_OK
        assert not any(os.path.exists(s) for s in sources)
        assert "预览模式" in capsys.readouterr().out

    def test_confirm_restores_content(self, tmp_path):
        op_file, sources = self._execute_first(tmp_path)
        original = {s: open(s, "rb").read() if os.path.exists(s) else None
                    for s in sources}
        saved = json.loads(op_file.read_text(encoding="utf-8"))
        blobs = {o["source"]: open(o["target"], "rb").read()
                 for o in saved["operations"]}
        rc = _run_restore_operation(ns(operation_file=str(op_file),
                                       confirm_restore=True))
        assert rc == EXIT_OK
        for s in sources:
            assert os.path.exists(s), "恢复后源路径应重新存在"
            assert open(s, "rb").read() == blobs[s], "内容必须与隔离区一致"

    def test_tampered_target_conflicts(self, tmp_path, capsys):
        op_file, sources = self._execute_first(tmp_path)
        saved = json.loads(op_file.read_text(encoding="utf-8"))
        # 篡改隔离区内容 → SHA-256 不匹配 → 冲突
        target = saved["operations"][0]["target"]
        with open(target, "ab") as f:
            f.write(b"tampered")
        rc = _run_restore_operation(ns(operation_file=str(op_file),
                                       confirm_restore=True))
        assert rc == EXIT_PARSE_ERROR
        out = capsys.readouterr().out
        assert "冲突" in out or "可恢复: 1 |" in out


class TestListAndPurge:
    def test_list_operations(self, tmp_path, capsys):
        plan_path, _ = make_plan(tmp_path)
        _run_execute_plan(ns(plan_file=str(plan_path), confirm_cleanup=True))
        capsys.readouterr()
        rc = _run_list_operations(ns(trash_dir=str(tmp_path / "trash")))
        out = capsys.readouterr().out
        assert rc == EXIT_OK
        assert "操作数: 1" in out
        assert "operation.json" in out

    def test_purge_preview_keeps_recent(self, tmp_path, capsys):
        plan_path, _ = make_plan(tmp_path)
        _run_execute_plan(ns(plan_file=str(plan_path), confirm_cleanup=True))
        capsys.readouterr()
        rc = _run_purge_operations(ns(trash_dir=str(tmp_path / "trash"),
                                      older_than=30, confirm_purge=False))
        assert rc == EXIT_OK
        assert "预览模式" in capsys.readouterr().out
        assert len(list((tmp_path / "trash").iterdir())) == 1

    def test_purge_confirm_removes_expired(self, tmp_path):
        plan_path, _ = make_plan(tmp_path)
        _run_execute_plan(ns(plan_file=str(plan_path), confirm_cleanup=True))
        trash = tmp_path / "trash"
        op_dir = list(trash.iterdir())[0]
        past = time.time() - 40 * 86400  # 40 天前
        os.utime(op_dir / "operation.json", (past, past))
        rc = _run_purge_operations(ns(trash_dir=str(trash), older_than=30,
                                      confirm_purge=True))
        assert rc == EXIT_OK
        assert list(trash.iterdir()) == [], "过期操作应被永久删除"


class TestGenRestore:
    def test_generates_restore_script_from_tsv_audit(self, tmp_path):
        """gen-restore 读取 TSV 审计记录（含「移动/删除」标记）→ 生成恢复 .bat"""
        date_str = time.strftime("%Y%m")
        audit = tmp_path / f"cleanup_audit_{date_str}.log"
        src1, dst1 = str(tmp_path / "a.mp4"), str(tmp_path / "trash" / "a.mp4")
        src2, dst2 = str(tmp_path / "c.mp4"), str(tmp_path / "trash" / "c.mp4")
        audit.write_text(
            f"{src1}\t{dst1}\t移动\n{src2}\t{dst2}\t移动\n",
            encoding="utf-8")
        rc = _run_gen_restore(ns(output_dir=str(tmp_path)))
        assert rc in (EXIT_OK, None)
        bat = tmp_path / "restore_duplicates.bat"
        assert bat.exists(), "应生成 restore_duplicates.bat"
        content = bat.read_text(encoding="utf-8")
        assert f'move "{dst1}" "{src1}"' in content
        assert f'move "{dst2}" "{src2}"' in content

    def test_no_restorable_records_no_script(self, tmp_path):
        audit = tmp_path / f"cleanup_audit_{time.strftime('%Y%m')}.log"
        audit.write_text("没有可解析的记录\n", encoding="utf-8")
        rc = _run_gen_restore(ns(output_dir=str(tmp_path)))
        assert rc in (EXIT_OK, None)
        assert not (tmp_path / "restore_duplicates.bat").exists()
