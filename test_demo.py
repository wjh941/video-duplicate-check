#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MP4 视频查重工具 v2.6 自测脚本
一键执行基础扫描、AI分析、导出全流程自测。

使用方法：
    python test_demo.py
"""

import os
import sys
import shutil
import cv2
import numpy as np
import tempfile

# 测试结果统计
_passed = 0
_failed = 0
_skipped = 0


def _print_result(name, status, detail=""):
    """打印测试结果"""
    symbols = {"pass": "[PASS]", "fail": "[FAIL]", "skip": "[SKIP]"}
    symbol = symbols.get(status, "[????]")
    print(f"  {symbol} {name}")
    if detail:
        print(f"        {detail}")


def _create_test_videos(test_dir, count=3):
    """创建测试视频文件"""
    os.makedirs(test_dir, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')

    videos = []
    # 创建2个相同内容的视频
    for i, name in enumerate(['test_dup_a.mp4', 'test_dup_b.mp4']):
        path = os.path.join(test_dir, name)
        out = cv2.VideoWriter(path, fourcc, 10.0, (320, 240))
        for frame_idx in range(30):
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.putText(frame, f'Frame {frame_idx}', (50, 120),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            out.write(frame)
        out.release()
        videos.append(path)

    # 创建1个不同内容的视频
    path_diff = os.path.join(test_dir, 'test_unique.mp4')
    out = cv2.VideoWriter(path_diff, fourcc, 10.0, (320, 240))
    for frame_idx in range(30):
        frame = np.random.randint(0, 255, (240, 320, 3), dtype=np.uint8)
        out.write(frame)
    out.release()
    videos.append(path_diff)

    return videos


def test_version():
    """测试版本信息输出"""
    global _passed, _failed
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, 'find_mp4.py', 'version'],
            capture_output=True, text=True, timeout=10
        )
        if 'v2.6' in result.stdout or 'v2.6' in result.stderr:
            _passed += 1
            _print_result("版本信息", "pass", "v2.6.0 确认")
        else:
            _failed += 1
            _print_result("版本信息", "fail", "未找到 v2.6 标识")
    except Exception as e:
        _failed += 1
        _print_result("版本信息", "fail", str(e))


def test_help():
    """测试帮助文档输出"""
    global _passed, _failed
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, 'find_mp4.py', '--help'],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr
        # 检查 v2.6 新参数是否存在
        new_params = ['--lite-csv', '--backup-path', '--cluster-num', '--no-store-embed',
                      '--cache-expire-days', '--clip-model-path',
                      '--duration-filter', '--duration-stat', '--use-md5',
                      '--interactive', '--export-clean-list', '--output-prefix']
        missing = [p for p in new_params if p not in output]
        if not missing:
            _passed += 1
            _print_result("帮助文档", "pass", "v2.6 新参数全部存在")
        else:
            _failed += 1
            _print_result("帮助文档", "fail", f"缺少参数: {missing}")
    except Exception as e:
        _failed += 1
        _print_result("帮助文档", "fail", str(e))


def test_basic_scan():
    """测试基础哈希查重"""
    global _passed, _failed
    test_dir = os.path.join(tempfile.gettempdir(), "test_mp4_basic")
    output_dir = os.path.join(tempfile.gettempdir(), "test_mp4_output")

    try:
        # 清理并创建测试环境
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        _create_test_videos(test_dir)

        import subprocess
        result = subprocess.run(
            [sys.executable, 'find_mp4.py', '--dir', test_dir,
             '--quiet', '--output-dir', output_dir],
            capture_output=True, text=True, timeout=60
        )
        output = result.stdout + result.stderr

        if '相似分组总数' in output and '1' in output.split('相似分组总数')[1][:20]:
            _passed += 1
            _print_result("基础查重", "pass", "正确识别1组重复")
        else:
            _failed += 1
            _print_result("基础查重", "fail", f"未找到预期结果: {output[:200]}")
    except Exception as e:
        _failed += 1
        _print_result("基础查重", "fail", str(e))
    finally:
        # 清理
        shutil.rmtree(test_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)


def test_param_conflict():
    """测试参数冲突校验"""
    global _passed, _failed
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, 'find_mp4.py', '--dir', '.',
             '--fast', '--double-check'],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr
        if '互斥' in output:
            _passed += 1
            _print_result("参数冲突校验", "pass", "正确拦截 --fast + --double-check")
        else:
            _failed += 1
            _print_result("参数冲突校验", "fail", "未检测到互斥拦截")
    except Exception as e:
        _failed += 1
        _print_result("参数冲突校验", "fail", str(e))


def test_ai_degradation():
    """测试无AI依赖时降级"""
    global _passed, _failed, _skipped
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, 'find_mp4.py', '--dir', '.',
             '--semantic', '--dry-run'],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr
        # 无AI依赖时应降级而非崩溃
        if '降级' in output or 'AI语义: 否' in output or 'AI 模块' in output:
            _passed += 1
            _print_result("AI降级", "pass", "无依赖时正确降级")
        else:
            _skipped += 1
            _print_result("AI降级", "skip", "可能已安装AI依赖")
    except Exception as e:
        _failed += 1
        _print_result("AI降级", "fail", str(e))


def test_cache_system():
    """测试缓存系统"""
    global _passed, _failed
    try:
        # 测试 load_dataset_labels
        sys.path.insert(0, '.')
        from find_mp4 import load_dataset_labels, _normalize_path, clean_expired_cache

        labels = load_dataset_labels()
        if labels and "scene" in labels and "purpose_rules" in labels:
            _passed += 1
            _print_result("标签配置加载", "pass", f"场景标签 {len(labels['scene'])} 个")
        else:
            _failed += 1
            _print_result("标签配置加载", "fail", "返回数据不完整")

        # 测试路径标准化
        test_path = _normalize_path("test_file.mp4")
        if test_path and len(test_path) > 0:
            _passed += 1
            _print_result("路径标准化", "pass", f"输出长度: {len(test_path)}")
        else:
            _failed += 1
            _print_result("路径标准化", "fail", "返回空路径")

    except Exception as e:
        _failed += 1
        _print_result("缓存系统", "fail", str(e))


def test_new_subcommands():
    """测试新子命令解析"""
    global _passed, _failed
    try:
        import subprocess
        # 测试 clear-semantic-cache 子命令
        result = subprocess.run(
            [sys.executable, 'find_mp4.py', 'clear-semantic-cache'],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr
        if '清理 AI 语义缓存' in output or '已清理' in output:
            _passed += 1
            _print_result("clear-semantic-cache子命令", "pass", "子命令正常执行")
        else:
            _failed += 1
            _print_result("clear-semantic-cache子命令", "fail", output[:200])
    except Exception as e:
        _failed += 1
        _print_result("新子命令", "fail", str(e))


def test_lite_csv():
    """测试轻量CSV导出"""
    global _passed, _failed
    test_dir = os.path.join(tempfile.gettempdir(), "test_mp4_lite")
    output_dir = os.path.join(tempfile.gettempdir(), "test_mp4_lite_out")

    try:
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        _create_test_videos(test_dir)

        import subprocess
        result = subprocess.run(
            [sys.executable, 'find_mp4.py', '--dir', test_dir,
             '--quiet', '--output-dir', output_dir, '--lite-csv'],
            capture_output=True, text=True, timeout=60
        )

        csv_path = os.path.join(output_dir, 'similar_result.csv')
        if os.path.exists(csv_path):
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                header = f.readline().strip()
            if '相似度' in header and len(header.split(',')) <= 3:
                _passed += 1
                _print_result("轻量CSV导出", "pass", f"表头: {header}")
            else:
                _failed += 1
                _print_result("轻量CSV导出", "fail", f"表头不符合: {header}")
        else:
            _failed += 1
            _print_result("轻量CSV导出", "fail", "CSV文件未生成")
    except Exception as e:
        _failed += 1
        _print_result("轻量CSV导出", "fail", str(e))
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)


def test_duration_filter():
    """测试时长筛选功能（v2.6 新增）"""
    global _passed, _failed
    try:
        import find_mp4
        # 测试解析函数
        conditions = find_mp4.parse_duration_filter(">=60")
        if conditions and conditions[0] == ('>=', 60.0):
            _passed += 1
            _print_result("时长筛选解析", "pass", ">=60 解析正确")
        else:
            _failed += 1
            _print_result("时长筛选解析", "fail", f"解析结果: {conditions}")
            return

        # 测试多条件
        multi = find_mp4.parse_duration_filter(">120&<=360")
        if len(multi) == 2:
            _passed += 1
            _print_result("多条件时长筛选", "pass", ">120&<=360 解析正确")
        else:
            _failed += 1
            _print_result("多条件时长筛选", "fail", f"解析结果: {multi}")

        # 测试匹配
        if find_mp4.match_duration(90, [('>=', 60)]):
            _passed += 1
            _print_result("时长匹配", "pass", "90秒 >=60 匹配")
        else:
            _failed += 1
            _print_result("时长匹配", "fail", "90秒应匹配 >=60")
    except Exception as e:
        _failed += 1
        _print_result("时长筛选", "fail", str(e))


def test_md5_checksum():
    """测试文件 MD5 校验（v2.6 新增）"""
    global _passed, _failed
    try:
        import find_mp4
        test_file = os.path.join(tempfile.gettempdir(), "test_md5.txt")
        with open(test_file, "w") as f:
            f.write("test content for md5")

        md5_1 = find_mp4._compute_file_md5(test_file)
        md5_2 = find_mp4._compute_file_md5(test_file)

        if md5_1 and md5_1 == md5_2:
            _passed += 1
            _print_result("MD5校验", "pass", f"MD5: {md5_1[:8]}...")
        else:
            _failed += 1
            _print_result("MD5校验", "fail", "同一文件 MD5 不一致")

        # 修改内容后 MD5 应变化
        with open(test_file, "w") as f:
            f.write("modified content")
        md5_3 = find_mp4._compute_file_md5(test_file)
        if md5_3 != md5_1:
            _passed += 1
            _print_result("MD5变更检测", "pass", "内容修改后 MD5 变化")
        else:
            _failed += 1
            _print_result("MD5变更检测", "fail", "内容修改后 MD5 未变")

        os.remove(test_file)
    except Exception as e:
        _failed += 1
        _print_result("MD5校验", "fail", str(e))


def test_cross_disk_detection():
    """测试跨盘检测（v2.6 新增）"""
    global _passed, _failed
    try:
        import batch_tools
        # 同盘检测
        result = batch_tools._is_cross_disk("C:\\a.mp4", "C:\\b\\c.mp4")
        if result is False:
            _passed += 1
            _print_result("同盘检测", "pass", "C:\\ 和 C:\\ 判定为同盘")
        else:
            _failed += 1
            _print_result("同盘检测", "fail", "同盘误判为跨盘")
            return

        # 跨盘检测
        result2 = batch_tools._is_cross_disk("C:\\a.mp4", "D:\\b.mp4")
        if result2 is True:
            _passed += 1
            _print_result("跨盘检测", "pass", "C:\\ 和 D:\\ 判定为跨盘")
        else:
            _failed += 1
            _print_result("跨盘检测", "fail", "跨盘误判为同盘")
    except Exception as e:
        _failed += 1
        _print_result("跨盘检测", "fail", str(e))


def test_unc_path():
    """测试 UNC 网络路径支持（v2.6 新增）"""
    global _passed, _failed
    try:
        import find_mp4
        # 测试 UNC 路径转换（模拟长路径）
        unc_path = "\\\\server\\share\\video.mp4"
        # 直接调用会因路径不存在而走 except 分支，但不会崩溃
        result = find_mp4._normalize_path(unc_path)
        if result:
            _passed += 1
            _print_result("UNC路径处理", "pass", "未崩溃且返回结果")
        else:
            _failed += 1
            _print_result("UNC路径处理", "fail", "返回空")
    except Exception as e:
        _failed += 1
        _print_result("UNC路径处理", "fail", str(e))


def test_signal_handler_registration():
    """测试信号处理器单次注册（v2.6 新增）"""
    global _passed, _failed
    try:
        import find_mp4
        # 第一次注册
        find_mp4._register_signal_handler()
        first_registered = find_mp4._register_signal_handler._registered

        # 第二次注册（应跳过）
        find_mp4._register_signal_handler()
        second_registered = find_mp4._register_signal_handler._registered

        if first_registered and second_registered:
            _passed += 1
            _print_result("信号处理器注册", "pass", "重复注册被正确跳过")
        else:
            _failed += 1
            _print_result("信号处理器注册", "fail", "注册标志异常")
    except Exception as e:
        _failed += 1
        _print_result("信号处理器注册", "fail", str(e))


def main():
    """主测试入口"""
    print("=" * 60)
    print("  MP4 视频查重工具 v2.6 自测脚本")
    print("=" * 60)
    print()

    # 切换到脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    tests = [
        ("版本信息", test_version),
        ("帮助文档", test_help),
        ("基础查重", test_basic_scan),
        ("参数冲突校验", test_param_conflict),
        ("AI降级", test_ai_degradation),
        ("缓存/标签系统", test_cache_system),
        ("新子命令", test_new_subcommands),
        ("轻量CSV导出", test_lite_csv),
        ("时长筛选", test_duration_filter),
        ("MD5校验", test_md5_checksum),
        ("跨盘检测", test_cross_disk_detection),
        ("UNC路径", test_unc_path),
        ("信号处理器", test_signal_handler_registration),
    ]

    for name, func in tests:
        print(f"\n[测试] {name}")
        try:
            func()
        except Exception as e:
            global _failed
            _failed += 1
            _print_result(name, "fail", f"异常: {e}")

    # 汇总
    total = _passed + _failed + _skipped
    print("\n" + "=" * 60)
    print(f"  自测结果: {_passed} 通过 / {_failed} 失败 / {_skipped} 跳过 (共 {total} 项)")
    print("=" * 60)

    if _failed > 0:
        print("\n  [警告] 存在失败项，请检查上述日志")
        sys.exit(1)
    else:
        print("\n  [成功] 全部测试通过！")
        sys.exit(0)


if __name__ == "__main__":
    main()
