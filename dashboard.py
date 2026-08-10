# -*- coding: utf-8 -*-
"""
dashboard.py - MP4 视频查重工具可视化看板 (基于 Streamlit)
============================================================
对应 find_mp4.py v2.5，复用 find_mp4 / report_generator / media_analyze / ai_semantic 模块。

一键启动：
    streamlit run dashboard.py

功能面板：
    1. 总览大盘        5. 缓存管理面板
    2. 重复分组详情    6. 任务执行面板
    3. AI语义分析看板  7. 报告导出面板
    4. 时长&分辨率筛选 8. 语义检索面板
"""

# ============================================================
# 模块 0：依赖检测（仅使用标准库，缺失时给出 pip 安装提示）
# ============================================================
import sys
import importlib
import os


def _check_dep(install_name: str, import_name: str) -> bool:
    """检测单个第三方依赖是否可导入。"""
    try:
        importlib.import_module(import_name)
        return True
    except Exception:
        return False


# 必需依赖清单：(pip安装名, import名)
_REQUIRED_DEPS: list[tuple[str, str]] = [
    ("streamlit", "streamlit"),
    ("plotly", "plotly"),
    ("pandas", "pandas"),
    ("opencv-python", "cv2"),
]

_MISSING: list[str] = [name for name, imp in _REQUIRED_DEPS if not _check_dep(name, imp)]

if _MISSING:
    print("[dashboard] 缺少以下必需依赖，请先安装：")
    for _d in _MISSING:
        print(f"    pip install {_d}")
    print("或使用国内镜像加速：")
    print("    pip install " + " ".join(_MISSING) +
          " -i https://pypi.tunainghua.edu.cn/simple")
    # 缺失依赖时直接退出，避免后续 import 报错
    sys.exit(1)


# ============================================================
# 模块 1：第三方库导入
# ============================================================
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import cv2
import base64
import json
import time
import zipfile
import subprocess
import tempfile
import shutil
from typing import Optional, Any

# ============================================================
# 模块 2：复用项目模块导入（失败时给出清晰提示，不崩溃）
# ============================================================
_FIND_MP4_OK: bool = False
_REPORT_OK: bool = False
_MEDIA_OK: bool = False
_AI_OK: bool = False
_IMPORT_ERRORS: list[str] = []

try:
    from find_mp4 import (
        load_cache,
        save_cache,
        clean_invalid_cache,
        clean_expired_cache,
        merge_caches,
        _resolve_path,
        log,
        parse_duration_filter,
        match_duration,
        CACHE_FILE,
        SEMANTIC_META,
        AUDIT_LOG,
        BAD_VIDEO_LIST,
    )
    _FIND_MP4_OK = True
except Exception as _e:  # noqa: BLE001
    _IMPORT_ERRORS.append(f"find_mp4 模块导入失败: {_e}")
    # 兜底常量，保证后续代码可定义
    CACHE_FILE = "video_hash_cache.json"
    SEMANTIC_META = "video_semantic_meta.json"
    AUDIT_LOG = "cleanup_audit.log"
    BAD_VIDEO_LIST = "bad_video_list.txt"

try:
    from report_generator import (
        generate_full_report,
        generate_space_report,
        generate_quality_report,
        archive_reports,
        _compute_duplicate_groups,
    )
    _REPORT_OK = True
except Exception as _e:  # noqa: BLE001
    _IMPORT_ERRORS.append(f"report_generator 模块导入失败: {_e}")

try:
    from media_analyze import similar_search, analyze_space
    _MEDIA_OK = True
except Exception as _e:  # noqa: BLE001
    _IMPORT_ERRORS.append(f"media_analyze 模块导入失败: {_e}")

try:
    from ai_semantic import load_clip_model, semantic_analyze_video
    _AI_OK = True
except Exception as _e:  # noqa: BLE001
    _IMPORT_ERRORS.append(f"ai_semantic 模块导入失败: {_e}")


# ============================================================
# 模块 3：本地工具函数（带类型注解）
# ============================================================
def format_size(size_bytes: float) -> str:
    """将字节数格式化为易读字符串。"""
    size_bytes = float(size_bytes or 0)
    if size_bytes < 1024:
        return f"{int(size_bytes)} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.2f} KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.2f} MB"
    return f"{size_bytes / 1024 ** 3:.2f} GB"


def duration_bucket(dur: float) -> str:
    """时长分桶标签（与 report_generator 保持一致）。"""
    dur = float(dur or 0)
    if dur <= 0:
        return "未知"
    if dur < 30:
        return "<30秒"
    if dur < 60:
        return "30-60秒"
    if dur < 180:
        return "1-3分钟"
    if dur < 600:
        return "3-10分钟"
    return ">10分钟"


def resolution_tier(width: int, height: int) -> str:
    """分辨率分档标签（按短边判定）。"""
    w = int(width or 0)
    h = int(height or 0)
    min_dim = min(w, h) if w and h else 0
    if min_dim <= 0:
        return "未知"
    if min_dim >= 2160:
        return "4K+"
    if min_dim >= 1440:
        return "2K"
    if min_dim >= 1080:
        return "1080p"
    if min_dim >= 720:
        return "720p"
    return "SD"


def extract_thumbnail_base64(video_path: str, max_size: int = 200) -> str:
    """使用 cv2 提取视频首帧并转为 base64 data URI，失败返回空串。"""
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return ""
        # 跳过开头 10%，取相对稳定帧
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total * 0.1)))
        ret, frame = cap.read()
        if not ret or frame is None:
            return ""
        h, w = frame.shape[:2]
        if w > max_size:
            scale = max_size / w
            frame = cv2.resize(frame, (max_size, int(h * scale)),
                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ok:
            return ""
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return ""
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def append_log(msg: str) -> None:
    """向会话操作日志追加一条带时间戳的记录。"""
    if "log_lines" not in st.session_state:
        st.session_state.log_lines = []
    stamp = time.strftime("%H:%M:%S")
    st.session_state.log_lines.append(f"[{stamp}] {msg}")
    # 仅保留最近 500 条，避免无限增长
    if len(st.session_state.log_lines) > 500:
        st.session_state.log_lines = st.session_state.log_lines[-500:]
    # 同步写入 find_mp4 运行日志（如可用）
    if _FIND_MP4_OK:
        try:
            log(msg, force=True)
        except Exception:
            pass


def safe_load_cache(cache_path: str) -> Optional[dict]:
    """安全加载缓存，支持 .gz 压缩，失败返回 None。"""
    if not cache_path:
        return None
    real_path = cache_path
    if not os.path.exists(real_path):
        gz_path = cache_path + ".gz"
        if os.path.exists(gz_path):
            real_path = gz_path
        else:
            return None
    try:
        if _FIND_MP4_OK:
            return load_cache(real_path)
        # 兜底：直接读取 JSON
        if real_path.endswith(".gz"):
            import gzip
            with gzip.open(real_path, "rt", encoding="utf-8") as f:
                return json.load(f)
        with open(real_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        append_log(f"缓存加载失败: {e}")
        return None


def compute_duplicate_groups_safe(cache: dict, threshold: float = 0.85) -> tuple:
    """安全计算重复分组，复用 report_generator 逻辑，失败返回空结构。"""
    if _REPORT_OK:
        try:
            return _compute_duplicate_groups(cache, threshold)
        except Exception as e:
            append_log(f"重复分组计算失败: {e}")
            return [], [], {}
    return [], [], {}


def get_cache_entries(cache: dict) -> dict:
    """提取缓存中有效条目（剔除 _ 开头的元字段）。"""
    if not isinstance(cache, dict):
        return {}
    return {k: v for k, v in cache.items()
            if not k.startswith("_") and isinstance(v, dict)}


def count_corrupted(entries: dict) -> int:
    """统计损坏/无法解析的视频数量（无 phash 视为损坏）。"""
    return sum(1 for v in entries.values() if not v.get("phash"))


def count_ai_classified(entries: dict) -> int:
    """统计已完成 AI 分类的视频数量（有 scene_tags 或 dataset_purpose）。"""
    return sum(1 for v in entries.values()
               if v.get("scene_tags") or v.get("dataset_purpose"))


def stat_card(label: str, value: Any, color: str = "#667eea") -> None:
    """渲染一个统计卡片（使用 metric 组件）。"""
    st.metric(label=label, value=str(value))


def filter_by_global_query(paths: list[str], query: str) -> list[str]:
    """按全局搜索关键字过滤路径列表（大小写不敏感子串匹配）。"""
    if not query:
        return paths
    q = query.strip().lower()
    if not q:
        return paths
    return [p for p in paths if q in p.lower()]


# ============================================================
# 模块 4：页面配置（宽屏布局、中文标题、favicon）
# ============================================================
st.set_page_config(
    page_title="MP4 视频查重可视化看板",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# 模块 5：主题切换
# ============================================================
def apply_theme(theme: str) -> None:
    """根据主题选择注入基础 CSS（明/暗）。"""
    if theme == "暗色":
        st.markdown(
            """
            <style>
            .stApp { background-color: #0e1117; color: #fafafa; }
            .stSidebar { background-color: #161b22; }
            </style>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <style>
            .stApp { background-color: #ffffff; color: #1f1f1f; }
            </style>
            """,
            unsafe_allow_html=True,
        )


# ============================================================
# 模块 6：面板渲染函数
# ============================================================
def render_overview(cache: dict, cache_path: str, global_query: str) -> None:
    """面板 1：总览大盘。"""
    st.header("📊 总览大盘")
    entries = get_cache_entries(cache)
    if not entries:
        st.info("当前缓存为空，请先在「任务执行面板」发起扫描。")
        return

    # ---------- 顶部统计卡片 ----------
    total_videos = len(entries)
    groups, _mp4_files, _vh = compute_duplicate_groups_safe(cache, 0.85)
    dup_group_count = len(groups)
    corrupted = count_corrupted(entries)
    ai_count = count_ai_classified(entries)

    # 可释放磁盘空间 = 各重复分组(总占用 - 保留最大)
    reclaimable = 0
    for g in groups:
        sizes = [info["size"] for _idx, info in g.get("members", [])]
        if sizes:
            reclaimable += sum(sizes) - max(sizes)

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        stat_card("视频总量", total_videos)
    with c2:
        stat_card("重复分组数", dup_group_count)
    with c3:
        stat_card("可释放磁盘空间", format_size(reclaimable))
    with c4:
        stat_card("损坏文件数", corrupted)
    with c5:
        stat_card("AI分类数量", ai_count)

    st.divider()

    # ---------- 分辨率分布饼图 ----------
    res_counter: dict[str, int] = {}
    dur_counter: dict[str, int] = {}
    dir_counter: dict[str, int] = {}
    for path, v in entries.items():
        res_counter[resolution_tier(v.get("width", 0), v.get("height", 0))] = (
            res_counter.get(resolution_tier(v.get("width", 0), v.get("height", 0)), 0) + 1
        )
        dur_counter[duration_bucket(v.get("duration", 0))] = (
            dur_counter.get(duration_bucket(v.get("duration", 0)), 0) + 1
        )
        d = os.path.dirname(path) or "(根目录)"
        dir_counter[d] = dir_counter.get(d, 0) + int(v.get("size", 0) or 0)

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("分辨率分布")
        try:
            df_res = pd.DataFrame(
                [{"分辨率": k, "数量": v} for k, v in res_counter.items()]
            )
            if df_res.empty:
                st.caption("暂无分辨率数据")
            else:
                fig = px.pie(df_res, names="分辨率", values="数量",
                             hole=0.4, color_discrete_sequence=px.colors.qualitative.Set2)
                st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.warning(f"分辨率图表渲染失败：{e}")

    with col_b:
        st.subheader("时长区间分布")
        try:
            df_dur = pd.DataFrame(
                [{"时长区间": k, "数量": v} for k, v in dur_counter.items()]
            )
            if df_dur.empty:
                st.caption("暂无时长数据")
            else:
                fig2 = px.pie(df_dur, names="时长区间", values="数量",
                              hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
                st.plotly_chart(fig2, use_container_width=True)
        except Exception as e:
            st.warning(f"时长图表渲染失败：{e}")

    st.divider()
    # ---------- 磁盘占用柱状图（按目录，Top 15） ----------
    st.subheader("磁盘占用（按目录 Top 15）")
    try:
        df_dir = pd.DataFrame(
            [{"目录": k, "占用": v} for k, v in dir_counter.items()]
        )
        df_dir["占用GB"] = df_dir["占用"] / (1024 ** 3)
        df_dir = df_dir.sort_values("占用GB", ascending=False).head(15)
        if df_dir.empty:
            st.caption("暂无磁盘占用数据")
        else:
            # 缩短过长的目录名便于展示
            df_dir["目录展示"] = df_dir["目录"].apply(
                lambda x: (x[:40] + "...") if len(x) > 42 else x
            )
            fig3 = px.bar(df_dir, x="目录展示", y="占用GB",
                          color="占用GB", color_continuous_scale="Blues",
                          labels={"目录展示": "目录", "占用GB": "占用 (GB)"})
            fig3.update_layout(xaxis_tickangle=-30)
            st.plotly_chart(fig3, use_container_width=True)
    except Exception as e:
        st.warning(f"磁盘占用图表渲染失败：{e}")

    # ---------- 全局搜索结果 ----------
    if global_query:
        st.divider()
        st.subheader(f"🔍 全局搜索：{global_query}")
        matched = filter_by_global_query(list(entries.keys()), global_query)
        st.caption(f"匹配 {len(matched)} 个视频")
        for p in matched[:100]:
            st.text(p)


def render_duplicate_groups(cache: dict, cache_path: str, global_query: str) -> None:
    """面板 2：重复分组详情。"""
    st.header("🗂️ 重复分组详情")
    if not _REPORT_OK:
        st.error("report_generator 模块未加载，无法计算重复分组。")
        return

    threshold = st.slider("相似度阈值", 0.5, 1.0, 0.85, 0.01, key="dup_threshold")
    groups, mp4_files, _vh = compute_duplicate_groups_safe(cache, threshold)
    if not groups:
        st.info("未发现重复分组，可调整阈值后重试。")
        return

    st.caption(f"共 {len(groups)} 组重复，{sum(len(g['members']) for g in groups)} 个视频。")

    # 初始化清理标记集合
    if "cleanup_marks" not in st.session_state:
        st.session_state.cleanup_marks = set()

    show_thumb = st.checkbox("显示首帧缩略图（加载较慢）", value=False)
    global_query_local = global_query

    # 遍历每组
    for gi, g in enumerate(groups, 1):
        members = g.get("members", [])
        retain_idx = g.get("retain_idx", -1)
        # 全局搜索过滤
        if global_query_local:
            members = [(idx, info) for idx, info in members
                       if global_query_local.lower() in info.get("path", "").lower()]
            if not members:
                continue

        with st.expander(f"第 {gi} 组 · {len(members)} 个视频", expanded=(gi == 1)):
            rows = []
            for idx, info in members:
                is_retain = (idx == retain_idx)
                rows.append({
                    "保留": "✅ 建议保留" if is_retain else "",
                    "文件名": info.get("name", os.path.basename(info.get("path", ""))),
                    "路径": info.get("path", ""),
                    "大小": format_size(info.get("size", 0)),
                    "建议保留": is_retain,
                })
            df = pd.DataFrame(rows)
            # 高亮建议保留行
            st.dataframe(df[["保留", "文件名", "路径", "大小"]], use_container_width=True,
                         hide_index=True)

            # 缩略图预览
            if show_thumb:
                tcols = st.columns(min(len(members), 4))
                for ci, (idx, info) in enumerate(members[:4]):
                    with tcols[ci]:
                        st.caption(info.get("name", ""))
                        data_uri = extract_thumbnail_base64(info.get("path", ""))
                        if data_uri:
                            st.markdown(
                                f'<img src="{data_uri}" style="width:100%;'
                                f'border:{"3px solid #2ecc71" if idx==retain_idx else "1px solid #ccc"};'
                                f'border-radius:6px;">',
                                unsafe_allow_html=True,
                            )
                        else:
                            st.caption("(无法提取缩略图)")

            # 批量勾选清理（仅标记）
            st.markdown("**勾选需清理的视频（仅标记，不实际删除）：**")
            for idx, info in members:
                if idx == retain_idx:
                    continue
                key = f"mark_{gi}_{idx}"
                prev = info.get("path", "") in st.session_state.cleanup_marks
                checked = st.checkbox(
                    f"清理：{info.get('name', '')}", value=prev, key=key
                )
                if checked:
                    st.session_state.cleanup_marks.add(info.get("path", ""))
                else:
                    st.session_state.cleanup_marks.discard(info.get("path", ""))

    st.divider()
    st.markdown(f"当前已标记清理 **{len(st.session_state.cleanup_marks)}** 个视频（仅标记）。")
    c1, c2 = st.columns(2)
    if c1.button("📥 导出清理清单（txt）", key="export_clean_list"):
        if not st.session_state.cleanup_marks:
            st.warning("未勾选任何视频。")
        else:
            out_path = os.path.join(os.path.dirname(cache_path) or ".",
                                    "dashboard_clean_list.txt")
            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(sorted(st.session_state.cleanup_marks)))
                append_log(f"已导出清理清单: {out_path}")
                st.success(f"已导出: {out_path}（未删除任何文件）")
            except Exception as e:
                st.error(f"导出失败: {e}")
    if c2.button("🧹 清空标记", key="clear_marks"):
        st.session_state.cleanup_marks.clear()
        st.rerun()


def render_ai_semantic(cache: dict, cache_path: str) -> None:
    """面板 3：AI 语义分析看板。"""
    st.header("🤖 AI 语义分析看板")
    entries = get_cache_entries(cache)
    if not entries:
        st.info("缓存为空，请先扫描并执行语义分析。")
        return

    # ---------- 场景分布柱状图 ----------
    st.subheader("场景标签分布")
    scene_counter: dict[str, int] = {}
    purpose_counter: dict[str, int] = {}
    high_q = 0
    low_q_list: list[tuple[str, float]] = []
    for path, v in entries.items():
        tags = v.get("scene_tags") or []
        if isinstance(tags, list):
            for t in tags:
                t = str(t).strip()
                if t:
                    scene_counter[t] = scene_counter.get(t, 0) + 1
        elif isinstance(tags, str) and tags:
            scene_counter[tags] = scene_counter.get(tags, 0) + 1
        # 数据集用途
        purp = v.get("dataset_purpose")
        if purp:
            purpose_counter[str(purp)] = purpose_counter.get(str(purp), 0) + 1
        # 质量分
        qs = v.get("quality_score")
        if qs is not None:
            try:
                qf = float(qs)
                if qf >= 0.7:
                    high_q += 1
                if qf < 0.4:
                    low_q_list.append((path, qf))
            except Exception:
                pass

    try:
        df_scene = pd.DataFrame(
            [{"场景": k, "数量": v} for k, v in scene_counter.items()]
        ).sort_values("数量", ascending=True)
        if df_scene.empty:
            st.caption("暂无场景标签数据（请先运行语义分析）")
        else:
            fig = px.bar(df_scene, x="数量", y="场景", orientation="h",
                         color="数量", color_continuous_scale="Viridis")
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.warning(f"场景图表渲染失败：{e}")

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("数据集用途占比")
        try:
            df_p = pd.DataFrame(
                [{"用途": k, "数量": v} for k, v in purpose_counter.items()]
            )
            if df_p.empty:
                st.caption("暂无数据集用途标注")
            else:
                fig2 = px.pie(df_p, names="用途", values="数量",
                              color_discrete_sequence=px.colors.qualitative.Set3)
                st.plotly_chart(fig2, use_container_width=True)
        except Exception as e:
            st.warning(f"用途图表渲染失败：{e}")

    with col_b:
        st.subheader("高质量训练素材统计")
        st.metric("quality_score ≥ 0.7", high_q)
        st.caption(f"占已评分视频的 "
                   f"{(high_q / max(1, sum(1 for v in entries.values() if v.get('quality_score') is not None)) * 100):.1f}%")

    st.divider()
    # ---------- 低质视频清单 ----------
    st.subheader(f"低质视频清单（quality_score < 0.4，共 {len(low_q_list)} 个）")
    if low_q_list:
        df_low = pd.DataFrame(
            [{"路径": p, "质量分": round(s, 3)} for p, s in low_q_list]
        )
        st.dataframe(df_low, use_container_width=True, hide_index=True)
    else:
        st.caption("暂无低质视频。")


def render_duration_resolution_filter(cache: dict) -> None:
    """面板 4：时长 & 分辨率筛选看板。"""
    st.header("⏱️ 时长 & 分辨率筛选看板")
    entries = get_cache_entries(cache)
    if not entries:
        st.info("缓存为空，无法筛选。")
        return

    # 收集时长与分辨率数据
    durations = [float(v.get("duration", 0) or 0) for v in entries.values()]
    widths = [int(v.get("width", 0) or 0) for v in entries.values()]
    heights = [int(v.get("height", 0) or 0) for v in entries.values()]
    short_sides = [min(w, h) if w and h else 0 for w, h in zip(widths, heights)]

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("时长分布直方图")
        try:
            df_d = pd.DataFrame({"时长(秒)": [d for d in durations if d > 0]})
            if df_d.empty:
                st.caption("暂无时长数据")
            else:
                fig = px.histogram(df_d, x="时长(秒)", nbins=20,
                                   color_discrete_sequence=["#667eea"])
                st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.warning(f"时长直方图渲染失败：{e}")

    with col_b:
        st.subheader("分辨率（短边）分布直方图")
        try:
            df_r = pd.DataFrame({"短边像素": [s for s in short_sides if s > 0]})
            if df_r.empty:
                st.caption("暂无分辨率数据")
            else:
                fig2 = px.histogram(df_r, x="短边像素", nbins=20,
                                    color_discrete_sequence=["#2ecc71"])
                st.plotly_chart(fig2, use_container_width=True)
        except Exception as e:
            st.warning(f"分辨率直方图渲染失败：{e}")

    st.divider()
    # ---------- 交互式滑块筛选 ----------
    st.subheader("交互式筛选")
    max_dur = max(durations) if durations else 0
    max_res = max(short_sides) if short_sides else 0

    dur_range = st.slider(
        "时长区间（秒）", 0.0, max(max_dur, 1.0),
        (0.0, max_dur), 1.0, key="dur_slider"
    )
    res_range = st.slider(
        "短边像素区间", 0, max(max_res, 1),
        (0, max_res), 10, key="res_slider"
    )

    matched = []
    for path, v in entries.items():
        dur = float(v.get("duration", 0) or 0)
        w = int(v.get("width", 0) or 0)
        h = int(v.get("height", 0) or 0)
        ss = min(w, h) if w and h else 0
        if dur_range[0] <= dur <= dur_range[1] and res_range[0] <= ss <= res_range[1]:
            matched.append({
                "路径": path,
                "时长(秒)": round(dur, 1),
                "分辨率": f"{w}x{h}",
                "大小": format_size(v.get("size", 0)),
            })

    st.caption(f"匹配 {len(matched)} 个视频")
    if matched:
        st.dataframe(pd.DataFrame(matched), use_container_width=True, hide_index=True)


def render_cache_management(cache: dict, cache_path: str) -> None:
    """面板 5：缓存管理面板。"""
    st.header("🗄️ 缓存管理面板")

    if not _FIND_MP4_OK:
        st.error("find_mp4 模块未加载，缓存管理功能不可用。")
        return

    # ---------- 缓存基本信息 ----------
    real_path = cache_path
    cache_size = 0
    if os.path.exists(cache_path):
        real_path = cache_path
    elif os.path.exists(cache_path + ".gz"):
        real_path = cache_path + ".gz"
    if os.path.exists(real_path):
        cache_size = os.path.getsize(real_path)

    entries = get_cache_entries(cache)
    c1, c2, c3 = st.columns(3)
    c1.metric("缓存文件大小", format_size(cache_size))
    c2.metric("缓存条目数", len(entries))
    c3.metric("缓存文件路径", os.path.basename(real_path))

    st.caption(f"完整路径：{real_path}")

    st.divider()
    # ---------- 清理无效缓存（二次确认） ----------
    st.subheader("清理无效缓存")
    st.warning("此操作将删除缓存中指向已失效文件的条目（修改缓存文件）。")
    if "confirm_clean_invalid" not in st.session_state:
        st.session_state.confirm_clean_invalid = False

    if st.button("🧹 清理无效缓存", key="btn_clean_invalid"):
        st.session_state.confirm_clean_invalid = True

    if st.session_state.confirm_clean_invalid:
        st.error("⚠️ 确认要清理无效缓存吗？此操作不可撤销。")
        cc1, cc2 = st.columns(2)
        if cc1.button("✅ 确认清理", type="primary", key="confirm_clean_yes"):
            try:
                removed, kept = clean_invalid_cache(cache_path)
                append_log(f"清理无效缓存：删除 {removed} 条，保留 {kept} 条")
                st.success(f"已清理 {removed} 条无效缓存，保留 {kept} 条。")
            except Exception as e:
                st.error(f"清理失败：{e}")
                append_log(f"清理无效缓存失败: {e}")
            st.session_state.confirm_clean_invalid = False
            st.rerun()
        if cc2.button("❌ 取消", key="confirm_clean_no"):
            st.session_state.confirm_clean_invalid = False
            st.rerun()

    st.divider()
    # ---------- 清理过期缓存 ----------
    st.subheader("清理过期缓存")
    expire_days = st.number_input("过期天数（清理超过 N 天未修改的缓存）",
                                  min_value=1, value=30, step=1, key="expire_days")
    if "confirm_clean_expired" not in st.session_state:
        st.session_state.confirm_clean_expired = False
    if st.button("🧹 清理过期缓存", key="btn_clean_expired"):
        st.session_state.confirm_clean_expired = True
    if st.session_state.confirm_clean_expired:
        st.error(f"⚠️ 确认清理超过 {expire_days} 天未修改的缓存？")
        ee1, ee2 = st.columns(2)
        if ee1.button("✅ 确认清理", type="primary", key="confirm_exp_yes"):
            try:
                removed = clean_expired_cache(cache_path, int(expire_days))
                append_log(f"清理过期缓存：删除 {removed} 条")
                st.success(f"已清理 {removed} 条过期缓存。")
            except Exception as e:
                st.error(f"清理失败：{e}")
            st.session_state.confirm_clean_expired = False
            st.rerun()
        if ee2.button("❌ 取消", key="confirm_exp_no"):
            st.session_state.confirm_clean_expired = False
            st.rerun()

    st.divider()
    # ---------- 缓存合并上传 ----------
    st.subheader("缓存合并上传")
    uploaded = st.file_uploader(
        "上传多个缓存文件（JSON）进行合并",
        accept_multiple_files=True,
        type=["json"],
        key="cache_upload",
    )
    if uploaded and st.button("🔀 合并并保存", key="btn_merge"):
        if "confirm_merge" not in st.session_state:
            st.session_state.confirm_merge = False
        st.session_state.confirm_merge = True
    if st.session_state.get("confirm_merge") and uploaded:
        st.warning(f"将合并 {len(uploaded)} 个缓存文件，确认？")
        mm1, mm2 = st.columns(2)
        if mm1.button("✅ 确认合并", type="primary", key="confirm_merge_yes"):
            tmp_paths = []
            try:
                for f in uploaded:
                    tmp = tempfile.NamedTemporaryFile(
                        delete=False, suffix=".json", prefix="upload_"
                    )
                    tmp.write(f.read())
                    tmp.close()
                    tmp_paths.append(tmp.name)
                out_path = os.path.join(
                    os.path.dirname(cache_path) or ".", "merged_cache.json"
                )
                total, kept = merge_caches(tmp_paths, out_path)
                append_log(f"合并缓存完成：新增 {total} 条，合计 {kept} 条 → {out_path}")
                st.success(f"合并完成：新增 {total} 条，合计 {kept} 条。")
                st.code(f"输出文件：{out_path}")
            except Exception as e:
                st.error(f"合并失败：{e}")
                append_log(f"缓存合并失败: {e}")
            finally:
                for tp in tmp_paths:
                    try:
                        os.unlink(tp)
                    except Exception:
                        pass
            st.session_state.confirm_merge = False
            st.rerun()
        if mm2.button("❌ 取消", key="confirm_merge_no"):
            st.session_state.confirm_merge = False
            st.rerun()


def _build_python_cmd(subcommand: str, params: dict) -> list[str]:
    """构建调用 find_mp4.py 的命令列表。"""
    py = sys.executable or "python"
    cmd = [py, "find_mp4.py"]
    if subcommand:
        cmd.append(subcommand)
    if params.get("dir"):
        cmd += ["--dir", str(params["dir"])]
    if "threshold" in params:
        cmd += ["--threshold", str(params["threshold"])]
    if "frames" in params:
        cmd += ["--frames", str(params["frames"])]
    if "workers" in params:
        cmd += ["--workers", str(params["workers"])]
    if params.get("recursive") is False:
        cmd.append("--no-recursive")
    if params.get("semantic"):
        cmd.append("--semantic")
    if params.get("duration_filter"):
        cmd += ["--duration-filter", str(params["duration_filter"])]
    return cmd


def _stream_subprocess(cmd: list[str]) -> int:
    """以 Popen 实时流式输出命令 stdout/stderr 到界面，返回退出码。"""
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
    except Exception as e:
        st.error(f"启动命令失败：{e}")
        return -1

    out_box = st.empty()
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line.rstrip())
        with out_box.container(height=400):
            st.code("\n".join(lines[-200:]), language="text")
    proc.wait()
    return proc.returncode


def render_task_execution(cache_path: str) -> None:
    """面板 6：任务执行面板。"""
    st.header("⚙️ 任务执行面板")
    st.caption("通过 subprocess 调用 find_mp4.py 执行任务，实时显示输出。")

    # ---------- 参数表单 ----------
    with st.form("task_form"):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            scan_dir = st.text_input("扫描目录", value="", key="task_dir")
        with col2:
            threshold = st.slider("相似度阈值", 0.5, 1.0, 0.7, 0.01, key="task_threshold")
        with col3:
            frames = st.number_input("采样帧数", 1, 50, 10, key="task_frames")
        with col4:
            workers = st.number_input("线程数 (0=自动)", 0, 64, 0, key="task_workers")
        col5, col6 = st.columns(2)
        with col5:
            recursive = st.checkbox("递归扫描子目录", value=True, key="task_recursive")
        with col6:
            use_ai = st.checkbox("开启 AI 语义分析", value=False, key="task_ai")
        duration_filter = st.text_input("时长筛选条件（可选，如 >=60&<=360）",
                                        value="", key="task_durfilt")
        st.form_submit_button("保存参数（无需提交即可使用下方按钮）")

    base_params = {
        "dir": scan_dir,
        "threshold": threshold,
        "frames": int(frames),
        "workers": int(workers),
        "recursive": recursive,
        "semantic": use_ai,
        "duration_filter": duration_filter,
    }

    st.divider()
    b1, b2, b3, b4 = st.columns(4)
    run_scan = b1.button("🔍 发起扫描", key="run_scan")
    run_semantic = b2.button("🤖 发起语义分析", key="run_semantic")
    run_classify = b3.button("🏷️ 发起自动分类", key="run_classify")
    run_durstat = b4.button("⏱️ 发起时长统计", key="run_durstat")

    if not (run_scan or run_semantic or run_classify or run_durstat):
        st.caption("点击上方按钮执行对应任务，输出将实时显示在下方。")
        return

    if run_scan:
        cmd = _build_python_cmd("scan", base_params)
        st.code(" ".join(cmd), language="bash")
        append_log("发起扫描任务")
        rc = _stream_subprocess(cmd)
        append_log(f"扫描任务结束，退出码 {rc}")
    elif run_semantic:
        cmd = _build_python_cmd("semantic-analyze", base_params)
        st.code(" ".join(cmd), language="bash")
        append_log("发起语义分析任务")
        rc = _stream_subprocess(cmd)
        append_log(f"语义分析任务结束，退出码 {rc}")
    elif run_classify:
        cmd = _build_python_cmd("auto-classify", base_params)
        st.code(" ".join(cmd), language="bash")
        append_log("发起自动分类任务")
        rc = _stream_subprocess(cmd)
        append_log(f"自动分类任务结束，退出码 {rc}")
    elif run_durstat:
        params = dict(base_params)
        if not duration_filter:
            params["duration_filter"] = ">=0"
        cmd = _build_python_cmd("duration-stat", params)
        st.code(" ".join(cmd), language="bash")
        append_log("发起时长统计任务")
        rc = _stream_subprocess(cmd)
        append_log(f"时长统计任务结束，退出码 {rc}")


def render_report_export(cache_path: str) -> None:
    """面板 7：报告导出面板。"""
    st.header("📄 报告导出面板")

    if not _REPORT_OK:
        st.error("report_generator 模块未加载，报告生成功能不可用。")
        return

    out_dir = os.path.dirname(cache_path) or "."
    project_name = st.text_input("项目名称", value="", key="rep_project")
    operator = st.text_input("操作人员", value="", key="rep_operator")
    path_mask = st.checkbox("路径脱敏", value=False, key="rep_mask")

    st.divider()
    # ---------- 在线预览 full_report.html ----------
    st.subheader("在线预览综合报告")
    full_html_path = os.path.join(out_dir, "full_report.html")
    if os.path.exists(full_html_path):
        try:
            with open(full_html_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            # 限制大小避免卡顿
            if len(html_content) > 5_000_000:
                st.warning("报告过大，建议下载后查看。")
            else:
                try:
                    st.components.v1.html(html_content, height=800, scrolling=True)
                except Exception:
                    st.caption("iframe 预览不可用，请使用下方下载按钮。")
        except Exception as e:
            st.warning(f"读取报告失败：{e}")
    else:
        st.caption("尚未生成 full_report.html，请点击下方按钮生成。")

    st.divider()
    # ---------- 一键生成并下载 ----------
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("生成综合报告(HTML)", key="gen_full"):
        try:
            out_path = os.path.join(out_dir, "full_report.html")
            ret = generate_full_report(cache_path, out_path, project_name, operator, path_mask)
            append_log(f"生成综合报告: {ret}")
            st.success(f"已生成: {ret}")
        except Exception as e:
            st.error(f"生成失败：{e}")

    if c2.button("生成空间报告(MD)", key="gen_space"):
        try:
            out_path = os.path.join(out_dir, "space_report.md")
            ret = generate_space_report(cache_path, out_path)
            append_log(f"生成空间报告: {ret}")
            st.success(f"已生成: {ret}")
        except Exception as e:
            st.error(f"生成失败：{e}")

    if c3.button("生成质检报告(MD)", key="gen_quality"):
        try:
            out_path = os.path.join(out_dir, "quality_report.md")
            ret = generate_quality_report(cache_path, out_path)
            append_log(f"生成质检报告: {ret}")
            st.success(f"已生成: {ret}")
        except Exception as e:
            st.error(f"生成失败：{e}")

    if c4.button("📦 打包所有报告为 zip", key="gen_zip"):
        if "confirm_zip" not in st.session_state:
            st.session_state.confirm_zip = False
        st.session_state.confirm_zip = True

    if st.session_state.get("confirm_zip"):
        st.warning("确认打包报告目录下所有报告？")
        z1, z2 = st.columns(2)
        if z1.button("✅ 确认打包", type="primary", key="confirm_zip_yes"):
            try:
                ret = archive_reports(out_dir, "", "dashboard")
                append_log(f"打包报告: {ret}")
                st.success(f"已打包: {ret}")
            except Exception as e:
                st.error(f"打包失败：{e}")
            st.session_state.confirm_zip = False
            st.rerun()
        if z2.button("❌ 取消", key="confirm_zip_no"):
            st.session_state.confirm_zip = False
            st.rerun()

    st.divider()
    # ---------- 下载已生成的报告 ----------
    st.subheader("下载报告")
    report_files = []
    for name in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []:
        if name.lower().endswith((".html", ".md", ".csv", ".xlsx")):
            report_files.append(os.path.join(out_dir, name))
    if not report_files:
        st.caption("暂无可下载的报告文件。")
    for rf in report_files:
        try:
            with open(rf, "rb") as f:
                data = f.read()
            st.download_button(
                label=f"下载 {os.path.basename(rf)}",
                data=data,
                file_name=os.path.basename(rf),
                key=f"dl_{rf}",
            )
        except Exception as e:
            st.caption(f"读取 {rf} 失败：{e}")


def render_semantic_search(cache_path: str) -> None:
    """面板 8：语义检索面板。"""
    st.header("🔎 语义检索面板")
    if not _MEDIA_OK:
        st.error("media_analyze 模块未加载，语义检索不可用。")
        return

    st.caption("输入文字描述，检索画面匹配的视频（优先 CLIP 语义，降级关键词匹配）。")
    query = st.text_input(
        "检索描述（如：夜晚街道行人监控）", value="", key="ss_query"
    )
    top_k = st.slider("返回结果数 Top-K", 1, 50, 10, key="ss_topk")

    if st.button("🔍 开始检索", key="ss_btn") and query.strip():
        try:
            results = similar_search(query.strip(), cache_path, top_k=int(top_k), dry_run=True)
            append_log(f"语义检索: \"{query.strip()}\" 命中 {len(results)} 条")
        except Exception as e:
            st.error(f"检索失败：{e}")
            append_log(f"语义检索失败: {e}")
            results = []

        if not results:
            st.info("未检索到匹配视频，请尝试更换关键词或确认已运行语义分析。")
            return

        rows = []
        for rank, (path, score) in enumerate(results, 1):
            rows.append({
                "排名": rank,
                "路径": path,
                "文件名": os.path.basename(path),
                "相似度": round(float(score), 4),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # 展示前若干缩略图
        st.subheader("Top 匹配缩略图预览")
        tcols = st.columns(min(len(results), 4))
        for ci, (path, _score) in enumerate(results[:4]):
            with tcols[ci]:
                st.caption(os.path.basename(path))
                data_uri = extract_thumbnail_base64(path)
                if data_uri:
                    st.markdown(
                        f'<img src="{data_uri}" style="width:100%;border-radius:6px;">',
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("(无缩略图)")


def render_operation_log() -> None:
    """渲染操作日志实时输出区。"""
    st.subheader("📜 操作日志")
    lines = st.session_state.get("log_lines", [])
    if not lines:
        st.caption("暂无日志。")
        return
    with st.container(height=220):
        st.code("\n".join(lines), language="text")
    if st.button("🧹 清空日志", key="clear_log"):
        st.session_state.log_lines = []
        st.rerun()


# ============================================================
# 模块 7：主程序入口
# ============================================================
def main() -> None:
    """看板主入口：侧边栏导航 + 主内容区调度。"""
    # 导入错误提示（顶层）
    if _IMPORT_ERRORS:
        for err in _IMPORT_ERRORS:
            st.error(err)
        st.info("部分功能将不可用。请确认在项目根目录运行本看板。")

    # ---------- 侧边栏 ----------
    with st.sidebar:
        st.title("🎬 MP4 视频查重看板")
        st.caption("find_mp4.py v2.5 配套可视化")

        # 主题切换
        theme = st.selectbox("主题", ["明色", "暗色"], key="theme_select")
        apply_theme(theme)

        st.divider()
        # 缓存路径配置
        cache_path = st.text_input(
            "缓存文件路径", value=CACHE_FILE, key="cache_path_input"
        )
        if _FIND_MP4_OK:
            cache_path = _resolve_path(cache_path)

        # 全局路径搜索
        global_query = st.text_input("🔍 全局路径搜索", value="", key="global_search")

        st.divider()
        # 导航菜单
        page = st.radio(
            "功能导航",
            [
                "📊 总览大盘",
                "🗂️ 重复分组详情",
                "🤖 AI语义分析看板",
                "⏱️ 时长&分辨率筛选",
                "🗄️ 缓存管理面板",
                "⚙️ 任务执行面板",
                "📄 报告导出面板",
                "🔎 语义检索面板",
            ],
            key="nav_radio",
        )
        st.divider()
        if st.button("🔄 刷新缓存", key="refresh_cache"):
            st.cache_data.clear()
            append_log("手动刷新缓存")
            st.rerun()

    # ---------- 加载缓存 ----------
    cache = safe_load_cache(cache_path)
    if cache is None:
        st.warning(f"未找到缓存文件：{cache_path}。请先在「任务执行面板」发起扫描，或检查缓存路径。")
        # 仍渲染任务执行面板以便用户启动扫描
        if page == "⚙️ 任务执行面板":
            render_task_execution(cache_path)
        render_operation_log()
        return

    # ---------- 主内容区调度 ----------
    if page == "📊 总览大盘":
        render_overview(cache, cache_path, global_query)
    elif page == "🗂️ 重复分组详情":
        render_duplicate_groups(cache, cache_path, global_query)
    elif page == "🤖 AI语义分析看板":
        render_ai_semantic(cache, cache_path)
    elif page == "⏱️ 时长&分辨率筛选":
        render_duration_resolution_filter(cache)
    elif page == "🗄️ 缓存管理面板":
        render_cache_management(cache, cache_path)
    elif page == "⚙️ 任务执行面板":
        render_task_execution(cache_path)
    elif page == "📄 报告导出面板":
        render_report_export(cache_path)
    elif page == "🔎 语义检索面板":
        render_semantic_search(cache_path)

    # ---------- 操作日志区（始终显示在底部） ----------
    st.divider()
    render_operation_log()


# ============================================================
# 使用示例
# ============================================================
# 1. 安装依赖：
#       pip install streamlit plotly pandas opencv-python
#       （AI 语义分析另需：pip install torch open_clip_torch imagehash）
#
# 2. 在项目根目录启动看板：
#       streamlit run dashboard.py
#
# 3. 在侧边栏「缓存文件路径」填入 video_hash_cache.json（默认值即可）。
#
# 4. 若暂无缓存，进入「任务执行面板」填写扫描目录后点击「发起扫描」。
#
# 5. 扫描完成后回到「总览大盘」查看统计图表，重复分组、语义分析等面板同步可用。
#
# 6. 报告导出：在「报告导出面板」一键生成 HTML/Markdown/zip 归档。
#
# 7. 语义检索：在「语义检索面板」输入描述文字，检索匹配视频。
#
# 注意：本看板所有「删除/清理」类操作均需二次确认；「重复分组详情」中的
#       清理勾选仅标记不实际删除文件。
# ============================================================

if __name__ == "__main__":
    main()
