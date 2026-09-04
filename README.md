# MP4 视频相似度查重工具 v2.6

基于感知哈希（pHash + dHash）+ AI 语义分析的视频重复检测与数据集标注工具，支持 LSH 加速、增量扫描、缓存管理、多格式导出、安全清理脚本、场景聚类、训练集清单导出、AI 自动分类、可视化看板、批量处理、增强报告系统。

## 核心特性

- **多格式视频扫描**：默认支持 MP4、MOV、MKV、AVI、WebM、M4V、FLV，可通过 `--ext` 自定义
- **LSH 加速**：位掩码均匀分桶，O(n log n) 复杂度，万级视频秒级查重
- **AI 语义分析**：CLIP 零样本场景识别、数据集用途判定、语义聚类
- **AI 自动分类**：K-Means 聚类自动归类素材，dry-run 安全预览
- **可视化看板**：Streamlit 交互式看板，8 大面板全方位可视化管理
- **增强报告系统**：综合 HTML 报告、PDF 导出、磁盘空间分析、质检报告
- **批量处理**：多目录批量扫描、缩略图集、硬链接替换、素材整理
- **安全清理**：回收站优先、永久删除二次确认、路径脱敏、保护清单
- **完整子命令架构**：20+ 子命令覆盖全场景，模块化解耦

## 适用场景

本项目适合需要整理、审核或长期维护视频素材库的个人和团队。它只读取视频并生成报告；删除、移动和硬链接操作默认通过预览脚本执行，建议先人工确认清理计划。

### 个人与家庭素材

- 手机、相机、无人机视频导入后的重复文件清理；
- 微信、网盘、聊天软件多次下载造成的副本整理；
- 旅行、婚礼、家庭录像按画面相似度筛选保留版本；
- NAS 或移动硬盘扩容前分析可释放空间。

推荐命令：

```bash
python find_mp4.py --dir D:\Videos --summary-json --format html
```

### 摄影、短视频与自媒体团队

- 相机原片、代理文件、剪辑导出文件的重复检测；
- MP4/MOV/MKV 混合素材库统一扫描；
- 按分辨率、时长、码率辅助选择更高质量版本；
- 发布前检查重复片头、重复成片和多次导出文件。

推荐先预览，不要直接永久删除：

```bash
python find_mp4.py --dir D:\素材库 --workers 4 --gen-cleanup --summary-json
```

### 教育、培训与会议录像

- 课程视频、直播录像、会议录屏去重；
- 识别不同编码、分辨率或文件名下的同一内容；
- 按时长过滤短片、片段和无效录屏；
- 生成可交给管理系统读取的 JSON 摘要。

```bash
python find_mp4.py --dir D:\课程 --duration-filter ">=300" --summary-json
```

### 数据集与 AI 训练素材

- 计算机视觉数据集的视频去重和清洗；
- CLIP 场景识别、语义标签和自动分类；
- 过滤低质量、模糊或暗光视频；
- 导出训练集清单、标签和数据集统计。

```bash
python find_mp4.py --dir D:\dataset --semantic --export-dataset --skip-low-quality
```

### 监控、行车记录仪与工业视频

- 多日期、多设备导出的视频批量去重；
- 按分辨率、时长和目录进行筛选；
- 对无法解码的文件生成独立故障清单；
- 适合离线处理敏感视频，避免上传云端。

### NAS、服务器与自动化任务

- 定时扫描新增视频并复用缓存；
- 用 `scan_summary.json` 接入看板、脚本或 CI；
- 检查解析失败数量和可释放空间；
- 多目录批量扫描和报告归档。

不建议直接用于：需要逐帧取证级结论、法律证据判定、实时流媒体检测、未经授权的他人视频处理。pHash 是相似度筛选工具，不等同于内容鉴定；重要删除操作必须人工复核。

## 项目结构

```
distinguish/
├── find_mp4.py            # 主程序 v2.6（核心查重+AI分析+子命令委托）
├── ai_semantic.py         # AI 语义分析+自动分类模块
├── dashboard.py           # Streamlit 可视化看板（v2.6 新增）
├── report_generator.py    # 增强报告系统（v2.6 新增）
├── batch_tools.py         # 批量处理工具集（v2.6 新增）
├── media_analyze.py       # 媒体分析工具集（v2.6 新增）
├── requirements.txt       # 基础依赖清单
├── requirements_ai.txt    # AI 扩展依赖清单
├── config.ini             # 配置文件模板
├── install.bat            # Windows 一键安装脚本
├── install_ai.bat         # AI 依赖一键安装脚本
├── dataset_labels.ini     # 自定义标签配置文件
├── test_demo.py           # 测试脚本
├── .gitignore             # Git 忽略规则
└── README.md              # 本文档
```

## 安装

### 1. 基础安装（纯哈希查重，无需 AI）

```bash
pip install -r requirements.txt
```

### 2. AI 扩展安装（语义分析、自动分类）

```bash
pip install -r requirements_ai.txt
```

### 3. 可视化看板安装（v2.6 新增）

```bash
pip install streamlit plotly pandas
# 国内镜像
pip install streamlit plotly pandas -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 4. PDF 导出安装（可选）

```bash
pip install reportlab
```

### 5. Excel 导出安装（可选）

```bash
pip install openpyxl
```

## 快速开始

### 1. 基础查重

```bash
# 最基础用法（默认扫描 MP4/MOV/MKV/AVI/WebM/M4V/FLV）
python find_mp4.py --dir D:\Videos

# 导出机器可读摘要，便于 CI/看板/自动化脚本读取
python find_mp4.py --dir D:\Videos --summary-json

# 只扫描指定格式
python find_mp4.py --dir D:\Videos --ext mp4,mov,mkv

# 快速粗筛
python find_mp4.py --dir D:\Videos --fast

# 高精度查重
python find_mp4.py --dir D:\Videos --threshold 0.85 --frames 15 --double-check
```

### 2. 启动可视化看板（v2.6 推荐）

```bash
# 方式一：直接启动
streamlit run dashboard.py

# 方式二：通过主程序启动
python find_mp4.py dashboard

# 浏览器访问 http://localhost:8501
```

看板包含 8 大面板：
- **总览大盘**：视频总量、重复分组、可释放空间、分辨率/时长饼图
- **重复分组详情**：表格+缩略图+相似度+批量勾选清理
- **AI 语义分析**：场景分布、用途占比、高质量/低质素材统计
- **时长&分辨率筛选**：直方图+交互式滑块筛选
- **缓存管理**：缓存大小、条目数、一键清理、合并上传
- **任务执行**：可视化配置参数，一键发起扫描/AI分析/分类
- **报告导出**：在线预览+一键下载 HTML/Excel/MD/压缩包
- **语义检索**：输入文字描述检索匹配视频

### 3. AI 自动分类（v2.5）

```bash
# 预览模式（默认，不修改文件）
python find_mp4.py auto-classify --dir D:\Videos

# 执行硬链接分类
python find_mp4.py auto-classify --dir D:\Videos --execute --link-mode

# 分类 + 筛选联动
python find_mp4.py auto-classify --dir D:\Videos --duration-filter ">=60" --min-res 1920
```

## v2.6 新增功能详解

### 可视化看板（dashboard.py）

基于 Streamlit 的交互式看板，一键启动后浏览器访问。

```bash
# 启动看板
streamlit run dashboard.py

# 通过主程序启动
python find_mp4.py dashboard
```

**功能面板**：
1. 总览大盘 - 全局统计 + 分辨率/时长饼图 + 磁盘占用柱状图
2. 重复分组详情 - 表格 + 缩略图 + 批量勾选清理
3. AI 语义分析 - 场景分布 + 用途占比 + 质量统计
4. 时长&分辨率筛选 - 直方图 + 交互式滑块
5. 缓存管理 - 清理无效/过期缓存 + 多文件合并
6. 任务执行 - 可视化参数配置 + 一键发起任务
7. 报告导出 - 在线预览 + 一键下载
8. 语义检索 - 文字描述检索视频

### 增强报告系统（report_generator.py）

```bash
# 生成综合汇总 HTML 报告
python find_mp4.py full-report --dir D:\Videos --project-name "项目A"

# HTML 转 PDF（需 reportlab）
python find_mp4.py export-pdf --html full_report.html --pdf 项目A.pdf --project-name "项目A"

# 磁盘空间优化报告
python find_mp4.py space-report --dir D:\Videos

# AI 数据集质检报告
python find_mp4.py quality-report --dir D:\Videos

# 批量打包所有报告为 zip
python find_mp4.py archive --dir D:\Videos
```

### 批量处理工具（batch_tools.py）

```bash
# 多目录批量扫描（txt 一行一个目录）
python find_mp4.py batch-scan --batch-dir-list dirs.txt

# 批量导出分组缩略图集
python find_mp4.py export-thumbnails --dir D:\Videos

# 批量备份重复素材
python find_mp4.py backup-duplicates --dir D:\Videos --backup-dir D:\backup

# 批量替换重复视频为硬链接（极致节省磁盘）
python find_mp4.py replace-hardlinks --dir D:\Videos

# 素材移动整理（按 AI 分类/时长/分辨率）
python find_mp4.py organize --dir D:\Videos --organize-by ai_class

# 重复画面片段提取（时间戳报告）
python find_mp4.py extract-segments --video1 v1.mp4 --video2 v2.mp4
```

### 媒体分析工具（media_analyze.py）

```bash
# 批量导出视频完整元数据到 Excel
python find_mp4.py media-info --dir D:\Videos

# 磁盘占用分析报告
python find_mp4.py space-analyze --dir D:\Videos

# 素材标签管理
python find_mp4.py tag-manage add --video D:\v.mp4 --tag 精品素材
python find_mp4.py tag-manage remove --video D:\v.mp4 --tag 精品素材
python find_mp4.py tag-manage list
python find_mp4.py tag-manage export --output tags.txt

# 语义相似度检索（输入文字找视频）
python find_mp4.py similar-search "城市街道夜景" --dir D:\Videos --top-k 10

# 导出快照存档（缓存+报告打包 zip）
python find_mp4.py export-snapshot --output snapshot.zip

# 两次扫描对比报告
python find_mp4.py diff-scan --old-cache old.json --new-cache new.json
```

## 完整子命令列表

| 命令 | 说明 | 版本 |
|------|------|------|
| `scan` | 扫描视频（默认命令，可省略） | v1.0 |
| `clean-cache` | 清理无效缓存条目 | v1.0 |
| `merge-cache` | 合并多个缓存文件 | v1.0 |
| `verify-cache` | 校验缓存有效性 | v1.0 |
| `version` | 打印版本信息 | v1.0 |
| `semantic-analyze` | 仅执行 AI 内容语义分析 | v2.2 |
| `dataset-filter` | 按数据集用途筛选视频 | v2.2 |
| `cluster-scene` | 纯画面内容聚类分组 | v2.2 |
| `clear-semantic-cache` | 清理 AI 语义缓存 | v2.3 |
| `dataset-split` | 数据集分类拆分 | v2.3 |
| `duration-stat` | 时长统计子命令 | v2.4 |
| `reload-labels` | 热加载标签配置 | v2.4 |
| `test` | 内置核心逻辑测试 | v2.4 |
| `auto-classify` | AI 自动分类子命令 | v2.5 |
| **`dashboard`** | **启动 Streamlit 可视化看板** | **v2.6** |
| **`batch-scan`** | **多目录批量扫描** | **v2.6** |
| **`export-thumbnails`** | **批量导出分组缩略图集** | **v2.6** |
| **`backup-duplicates`** | **批量备份重复素材** | **v2.6** |
| **`replace-hardlinks`** | **批量替换重复视频为硬链接** | **v2.6** |
| **`organize`** | **素材移动整理** | **v2.6** |
| **`extract-segments`** | **重复画面片段提取** | **v2.6** |
| **`media-info`** | **批量导出视频元数据到 Excel** | **v2.6** |
| **`space-analyze`** | **磁盘占用分析报告** | **v2.6** |
| **`tag-manage`** | **素材标签管理** | **v2.6** |
| **`similar-search`** | **语义相似度检索** | **v2.6** |
| **`export-snapshot`** | **导出快照存档** | **v2.6** |
| **`diff-scan`** | **两次扫描对比报告** | **v2.6** |
| **`full-report`** | **生成综合汇总 HTML 报告** | **v2.6** |
| **`export-pdf`** | **HTML 报告转 PDF** | **v2.6** |
| **`diff-report`** | **数据对比报告** | **v2.6** |
| **`quality-report`** | **AI 数据集质检报告** | **v2.6** |
| **`archive`** | **批量打包所有报告为 zip** | **v2.6** |

## v2.6 新增命令行参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--report-name` | str | "" | 自定义所有报告前缀名称 |
| `--export-pdf` | flag | False | 扫描完成自动生成 PDF 完整报告 |
| `--batch-dir-list` | str | "" | 批量扫描文件夹 txt 路径文件 |
| `--tag` | str | "" | 扫描时过滤带指定标签素材 |
| `--similar-search` | str | "" | 语义检索指定画面视频 |
| `--summary-json` | flag | False | 导出机器可读扫描摘要 JSON（含重复等级、质量分和可释放空间） |

## 自动化集成与任务结果

使用 `--summary-json` 可将一次扫描结果作为稳定的任务结果文件导出：

```bash
python find_mp4.py --dir D:\Videos --summary-json --output-dir D:\Reports
```

外部程序只需读取 `D:\Reports\scan_summary.json`，即可获得扫描数量、失败数量、重复等级、可释放空间、文件格式分布和清理候选，不需要解析终端中文日志。`schema_version` 用于未来兼容升级；建议自动化程序先检查它再读取字段。摘要还包含 `extensions`（格式数量）、`quality_buckets`（重复等级统计）和 `status`（`clean`、`duplicates_found` 或 `completed_with_errors`），方便 NAS 看板和定时任务直接展示或触发告警。

## v2.6 新增输出文件

| 文件 | 说明 |
|------|------|
| `full_report.html` | 综合汇总 HTML 报告（离线可打开） |
| `full_report.pdf` | PDF 完整报告（需 reportlab） |
| `space_optimization_report.md` | 磁盘空间优化报告 |
| `quality_report.md` | AI 数据集质检报告 |
| `diff_report.md` | 数据对比报告 |
| `batch_scan_report.txt` | 批量扫描汇总报告 |
| `media_info.xlsx` | 视频完整元数据 Excel |
| `video_tags.json` | 素材标签存储文件 |
| `auto_classify_report.json` | AI 自动分类报告 |
| `snapshot_YYYYMMDD_HHMMSS.zip` | 快照存档 zip |
| `reports_YYYYMMDD_HHMMSS.zip` | 报告打包 zip |

## 场景示例

### 场景一：可视化看板全流程管理（v2.6 推荐）

```bash
# 1. 启动看板
streamlit run dashboard.py

# 2. 在看板中配置参数，一键发起扫描
# 3. 查看总览大盘，了解重复情况
# 4. 在重复分组详情中勾选清理项
# 5. 导出综合报告存档
```

### 场景二：批量扫描多个目录

```bash
# 1. 准备目录列表文件 dirs.txt
# D:\Videos\camera1
# D:\Videos\camera2
# D:\Videos\backup

# 2. 批量扫描
python find_mp4.py batch-scan --batch-dir-list dirs.txt

# 3. 查看汇总报告
type batch_scan_report.txt
```

### 场景三：语义检索视频素材

```bash
# 检索城市街道夜景视频
python find_mp4.py similar-search "城市街道夜景" --dir D:\Videos --top-k 10

# 检索人物特写
python find_mp4.py similar-search "人物面部特写" --dir D:\Videos

# 检索室内会议
python find_mp4.py similar-search "室内会议场景" --dir D:\Videos
```

### 场景四：生成完整项目报告

```bash
# 1. 生成综合 HTML 报告
python find_mp4.py full-report --dir D:\Videos --project-name "2026年Q1素材审计"

# 2. 转 PDF（带水印）
python find_mp4.py export-pdf --input full_report.html --watermark "机密"

# 3. 磁盘空间分析
python find_mp4.py space-analyze --dir D:\Videos

# 4. AI 质检报告
python find_mp4.py quality-report --dir D:\Videos

# 5. 打包所有报告
python find_mp4.py archive --dir D:\Videos
```

### 场景五：素材整理与硬链接优化

```bash
# 1. AI 自动分类（预览）
python find_mp4.py auto-classify --dir D:\Videos

# 2. 执行硬链接分类
python find_mp4.py auto-classify --dir D:\Videos --execute --link-mode

# 3. 替换重复视频为硬链接（极致节省磁盘）
python find_mp4.py replace-hardlinks --dir D:\Videos

# 4. 按时长整理素材
python find_mp4.py organize --dir D:\Videos --organize-by duration
```

### 场景六：素材标签管理

```bash
# 标记精品素材
python find_mp4.py tag-manage add --video D:\v1.mp4 --tag 精品素材

# 标记待删素材
python find_mp4.py tag-manage add --video D:\v2.mp4 --tag 待删

# 查看所有标签
python find_mp4.py tag-manage list

# 导出标签清单
python find_mp4.py tag-manage export --output tags.txt

# 扫描时过滤带标签素材
python find_mp4.py --dir D:\Videos --tag 精品素材
```

## 配置文件

`config.ini` 支持全部参数预配置，命令行参数优先级更高。

```ini
[scan]
dir = D:\Videos
threshold = 0.7
frames = 10
semantic = true

[dashboard]
theme = light
port = 8501
watermark = 
default_format = html

[weights]
phash_weight = 0.7
dhash_weight = 0.3
audio_weight = 0.8
```

## 常见问题

### Q: 启动看板报错 No module named 'streamlit'

```bash
pip install streamlit plotly pandas
```

### Q: PDF 导出报错 No module named 'reportlab'

```bash
pip install reportlab
```

### Q: AI 自动分类报错 No module named 'torch'

```bash
pip install -r requirements_ai.txt
```

### Q: Excel 导出报错 No module named 'openpyxl'

```bash
pip install openpyxl
```

## 版本历史

- **v2.6** - 可视化看板、增强报告系统、批量处理工具、媒体分析工具
- **v2.5** - AI 自动分类、AppContext 全局状态管理
- **v2.4** - 时长筛选统计、分辨率过滤、Excel导出、HTML缩略图、硬链接拆分
- **v2.3** - 自定义CLIP模型、轻量CSV、备份路径、保护清单、压缩缓存
- **v2.2** - AI 语义分析、场景聚类、数据集自动标注
- **v1.0** - 基础哈希查重、LSH加速、缓存管理、多格式导出
