# MP4 视频相似度查重工具 v2.5

基于感知哈希（pHash + dHash）+ AI 语义分析的视频重复检测与数据集标注工具，支持 LSH 加速、增量扫描、缓存管理、多格式导出、安全清理脚本、场景聚类、训练集清单导出、AI 自动分类。

## 目录结构

```
distinguish/
├── find_mp4.py          # 主程序 v2.5
├── ai_semantic.py       # AI 语义分析+自动分类模块（v2.5 增强）
├── requirements.txt     # 基础依赖清单
├── requirements_ai.txt  # AI 扩展依赖清单
├── config.ini           # 配置文件模板（v2.4 新增）
├── install.bat          # Windows 一键安装脚本（区分基础/AI）
├── install_ai.bat       # AI 依赖一键安装脚本
├── dataset_labels.ini   # 自定义标签配置文件（v2.3 新增）
├── .gitignore           # Git 忽略规则
├── README.md            # 本文档
└── (运行时生成的输出文件)
```

## 快速开始

### 1. 安装依赖

**方式一：一键安装（Windows）**
```bat
install.bat
```
选择 `[1]基础版` 仅安装查重依赖，选择 `[2]AI增强版` 额外安装 torch/CLIP/sklearn。

**方式二：手动安装基础依赖**
```bash
pip install -r requirements.txt
```

> 💡 **国内网络加速**：如遇下载缓慢，可临时使用国内 pip 镜像源：
> ```bash
> # 清华源（推荐）
> pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
> # 阿里源
> pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
> # 中科大源
> pip install -r requirements.txt -i https://pypi.mirrors.ustc.edu.cn/simple/
> ```
> 或将其写入全局配置一次性启用：
> ```bash
> pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
> ```

**方式三：安装 AI 扩展依赖（可选）**
```bash
pip install -r requirements_ai.txt
```
或使用：
```bat
install_ai.bat
```

> ⚠️ **AI 依赖体积较大**（torch 单包约 2GB+），强烈建议配合上述国内镜像源使用；如需 GPU 版 torch，请参考 [PyTorch 官网](https://pytorch.org/) 选择对应 CUDA 版本安装命令。

### 2. 运行

```bash
# 最基础用法（纯哈希查重，无需 AI）
python find_mp4.py --dir D:\Videos

# 快速粗筛（200+视频推荐）
python find_mp4.py --dir D:\Videos --fast

# 高精度查重
python find_mp4.py --dir D:\Videos --threshold 0.85 --frames 15 --double-check

# 开启 AI 语义分析 + 数据集导出
python find_mp4.py --dir D:\Videos --semantic --export-dataset

# 增量扫描 + 缓存语义特征
python find_mp4.py --dir D:\Videos --semantic --incremental --embed-cache

# 仅 AI 语义分析（不做哈希查重）
python find_mp4.py semantic-analyze --dir D:\video

# 筛选自动驾驶数据集视频，生成训练清单
python find_mp4.py dataset-filter --purpose 自动驾驶 --dir D:\car_data

# 画面内容聚类（按场景分组，不依赖哈希）
python find_mp4.py cluster-scene --dir D:\camera

# 生成可视化 HTML 报告 + 清理脚本
python find_mp4.py --dir D:\Videos --format html --gen-cleanup

# 试运行（不写入任何文件）
python find_mp4.py --dir D:\Videos --dry-run
```

### 3. v2.5 AI 自动分类

```bash
# AI 自动分类（默认 dry-run 预览模式，不修改文件）
python find_mp4.py auto-classify --dir D:\Videos

# AI 自动分类并执行硬链接组织文件
python find_mp4.py auto-classify --dir D:\Videos --execute --link-mode

# AI 自动分类 + 时长筛选联动
python find_mp4.py auto-classify --dir D:\Videos --duration-filter ">=60"

# AI 自动分类 + 分辨率筛选 + 低质量过滤
python find_mp4.py auto-classify --dir D:\Videos --min-res 1920 --skip-low-quality

# 仅分类（扁平参数模式）
python find_mp4.py --dir D:\Videos --classify-only
python find_mp4.py --dir D:\Videos --classify-only --execute --link-mode
```

## 参数说明

### 基础参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--dir` | str | 必填 | 视频文件夹路径 |
| `--no-recursive` | flag | False | 不递归子目录 |
| `--threshold` | float | 0.7 | 相似度阈值（0~1，越高越严格） |
| `--frames` | int | 10 | 单视频抽取帧数 |
| `--workers` | int | 自动 | 线程数（默认 CPU×1.2） |
| `--no-cache` | flag | False | 禁用哈希缓存 |
| `--output-dir` | str | 脚本同级 | 自定义输出目录 |
| `--format` | txt/md/html | txt | 分组报告格式 |

### v2.1 增强参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `--ext` | str | 支持的视频后缀，逗号分隔（mp4,mov,avi...） |
| `--double-check` | flag | 二次校验模式，提升精度 |
| `--lsh-buckets` | int | LSH 分桶数（默认 32），加速比对 |
| `--mem-limit` | int | 内存限制 MB，超出强制落地缓存 |
| `--incremental` | flag | 增量模式，仅处理新增/修改视频 |
| `--keep-max-size` | flag | 保留体积最大视频（默认） |
| `--keep-latest` | flag | 保留最后修改视频 |
| `--keep-max-res` | flag | 保留最高分辨率视频 |
| `--keep-max-bitrate` | flag | 保留最高码率视频 |
| `--hard-delete` | flag | **危险！**生成永久删除脚本 |
| `--protect-folder` | str | 保护文件夹，逗号分隔，不允许删除 |
| `--audio-check` | flag | 音频辅助比对（需 FFmpeg） |
| `--exclude-folder` | str | 排除子文件夹名 |
| `--exclude-size-lt` | str | 过滤小于指定大小（如 100MB） |
| `--exclude-size-gt` | str | 过滤大于指定大小（如 50GB） |
| `--min-sim` | float | 仅导出高于此相似度的分组 |
| `--config` | str | 配置文件路径（INI 格式） |
| `--quiet` | flag | 静默模式，仅输出最终统计 |
| `--dry-run` | flag | 试运行，不写入任何缓存外文件 |
| `--fast` | flag | 快速粗筛模式 |
| `--check-only` | flag | 仅扫描+提取哈希，不比对 |
| `--export-hash` | flag | 导出全量哈希 JSON 备份 |
| `--gen-cleanup` | flag | 生成清理脚本 |

### v2.2 AI 语义分析参数（需安装 AI 扩展依赖）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--semantic` | flag | False | 开启 AI 视频内容语义分析 |
| `--purpose-filter` | str | "" | 仅保留指定用途的视频，逗号分隔（监控,自动驾驶,人像...） |
| `--cluster-semantic` | flag | False | 基于 CLIP 特征对视频内容聚类 |
| `--export-dataset` | flag | False | 导出 AI 训练集目录清单、标注文件 |
| `--scene-thresh` | float | 0.6 | 场景标签置信度阈值 |
| `--embed-cache` | flag | False | 持久化 CLIP 特征向量到缓存，重复扫描不重算 |
| `--no-semantic-cache` | flag | False | 关闭语义特征缓存 |
| `--semantic-workers` | int | 1 | AI 推理线程数 |

### v2.3 新增参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--clip-model-path` | str | "" | 自定义 CLIP 模型本地路径，离线/指定模型加载 |
| `--lite-csv` | flag | False | 轻量 CSV 导出，仅保留核心字段（路径/相似度/标签），适合大体量场景 |
| `--export-bad-paths` | flag | False | 单独导出损坏视频路径清单 `bad_video_paths.txt`，便于批量排查 |
| `--backup-path` | str | "" | 备份目录路径，清理时将文件移动至此目录而非系统临时回收站 |
| `--protect-file` | str | "" | 受保护文件清单路径（一行一个绝对路径），匹配项永不被清理 |
| `--cluster-num` | int | 自动 | 场景聚类分组数（不指定时按轮廓系数自动估计） |
| `--cache-expire-days` | int | 30 | 缓存过期天数，超期条目自动失效重算 |
| `--compress-cache` | flag | False | 写出时压缩缓存文件（gzip），减小磁盘占用 |
| `--no-store-embed` | flag | False | 不把 CLIP 特征向量写入缓存，仅存语义标签，节省空间 |

### v2.4 新增参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--duration-filter` | str | "" | 时长筛选条件，格式：>=60、<30、>120&<=360（单位秒） |
| `--duration-stat` | flag | False | 仅统计符合时长条件视频数量，不执行哈希查重 |
| `--duration-export` | flag | False | 导出符合时长条件视频路径清单到 txt |
| `--no-store-frames` | flag | False | 不将预览帧持久化存入缓存，降低内存占用 |
| `--min-res` | int | 0 | 筛选最小分辨率宽度，如 1920 |
| `--max-res` | int | 0 | 筛选最大分辨率宽度，如 3840 |
| `--gen-restore` | flag | False | 根据审计日志生成视频恢复 bat/sh 脚本 |
| `--export-clean-list` | flag | False | 单独输出仅待清理视频路径清单 |
| `--output-prefix` | str | "" | 自定义输出文件前缀，多批次扫描不覆盖报告 |
| `--path-mask` | flag | False | 审计日志隐藏路径中间层级，保护素材隐私 |
| `--cluster-thresh` | float | 0.5 | 语义聚类松紧阈值，默认 0.5 |
| `--skip-low-quality` | flag | False | 过滤 AI 判定低质量模糊暗光视频 |
| `--link-mode` | flag | False | 数据集拆分使用硬链接，不重复复制视频 |
| `--format xlsx` | flag | False | Excel 导出（需 openpyxl 可选依赖） |

### v2.5 新增 AI 自动分类参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `auto-classify` | 子命令 | - | AI 自动分类子命令（基于 CLIP 特征聚类） |
| `--classify-only` | flag | False | 仅执行 AI 自动分类，不查重 |
| `--classify-method` | str | kmeans | 分类算法（目前支持 kmeans） |
| `--n-clusters` | int | 0 | 目标聚类数 (0=自动估算) |
| `--execute` | flag | False | 执行文件操作（默认 dry-run 预览模式） |

### 子命令

| 命令 | 说明 |
|------|------|
| `scan` | 扫描视频（默认命令，可省略） |
| `clean-cache` | 清理无效缓存条目 |
| `merge-cache` | 合并多个缓存文件 |
| `verify-cache` | 校验缓存有效性 |
| `version` | 打印版本信息 |
| **`semantic-analyze`** | **仅执行 AI 内容语义分析，不做哈希查重（v2.2 新增）** |
| **`dataset-filter`** | **按数据集用途筛选视频，生成训练清单（v2.2 新增）** |
| **`cluster-scene`** | **纯画面内容聚类分组，不依赖文件哈希（v2.2 新增）** |
| **`clear-semantic-cache`** | **清理 AI 语义缓存（仅删语义/嵌入，保留哈希缓存）（v2.3 新增）** |
| **`dataset-split`** | **数据集分类拆分，按用途/场景将视频分组导出到独立子目录（v2.3 新增）** |
| **`duration-stat`** | **时长统计子命令（v2.4 新增）** |
| **`reload-labels`** | **热加载 dataset_labels.ini 标签配置（v2.4 新增）** |
| **`test`** | **内置核心逻辑自动化测试（v2.4 新增）** |
| **`auto-classify`** | **AI 自动分类子命令（v2.5 新增）** |

#### v2.3 子命令详解

**`clear-semantic-cache`** —— 选择性清理 AI 语义缓存
- 仅删除缓存中的语义标签与 CLIP 特征向量条目，**保留文件哈希缓存**，避免重复抽帧
- 适用于：标签库更新后需重新识别、嵌入模型升级、缓存膨胀需瘦身但不想丢失哈希加速
- 用法：`python find_mp4.py clear-semantic-cache --dir D:\Videos`
- 可配合 `--cache-expire-days 7` 仅清理 7 天前的过期条目，配合 `--dry-run` 预览

**`dataset-split`** —— 数据集分类拆分
- 读取已有语义元数据（`video_semantic_meta.json`），按「用途」或「场景」将视频分组
- 默认按用途拆分，每个用途生成一个子目录，并写入软链接/拷贝清单（不破坏原文件）
- 适用于：将混合数据集整理为可训练的目录结构，方便后续喂给训练框架
- 用法：
  ```bash
  # 按用途拆分（默认）
  python find_mp4.py dataset-split --dir D:\mixed_data --split-by purpose
  # 按场景拆分，并指定输出根目录
  python find_mp4.py dataset-split --dir D:\mixed_data --split-by scene --output-dir D:\split_out
  ```

## 输出文件

### 哈希查重输出（原有功能）

| 文件 | 说明 |
|------|------|
| `similar_result.csv` | 两两比对明细（含语义标签列） |
| `duplicate_groups.txt` | TXT 分组报告（含语义标签） |
| `duplicate_groups.md` | Markdown 分组报告（含语义标签） |
| `duplicate_groups.html` | HTML 可视化报告（含语义统计面板） |
| `duplicate_paths.txt` | 纯路径清单（批量操作用） |
| `bad_video_list.txt` | 损坏视频清单（含 AI 质量标签） |
| `video_hash_cache.json` | 哈希+语义缓存（加速后续运行） |
| `cleanup_duplicates.bat` | Windows 清理脚本（含用途备注） |
| `cleanup_duplicates.sh` | Linux 清理脚本（含用途备注） |
| `hash_export.json` | 全量哈希备份 |
| `run_log.txt` | 运行日志 |
| `cleanup_audit.log` | 清理操作审计日志（含用途备注） |

### v2.2 AI 新增输出（需开启 `--semantic` 相关参数）

| 文件 | 说明 |
|------|------|
| `video_semantic_meta.json` | 全量视频语义元数据总表 |
| `dataset_catalog.csv` | 数据集分类清单（路径/大小/场景/用途/置信度/质量分） |
| `train_sample_list.txt` | 筛选出可用于 AI 训练的视频路径清单 |
| `dataset_stats.md` | 数据集统计汇总（按用途分布、场景分布） |
| `scene_cluster.html` | 语义聚类可视化报告（交互折叠、搜索过滤） |

### v2.4 新增输出

| 文件 | 说明 |
|------|------|
| `clean_list.txt` | 仅待清理视频路径清单（不含保留素材） |
| `restore_duplicates.bat` | 视频恢复脚本（根据审计日志生成） |
| `duration_filter_list.txt` | 时长筛选视频路径清单 |

### v2.5 新增 AI 自动分类输出

| 文件 | 说明 |
|------|------|
| `auto_classify_report.json` | AI 自动分类报告（含聚类结果、类别名称、视频列表） |
| `_classify_output/` | 分类输出根目录（按类别名组织子目录） |

## AI 语义分析功能详解

### 自动识别能力

视频内容分析基于 OpenCLIP ViT-B/32 零样本模型，内置三大标签库：

**场景库**：室内楼道、室外街道、停车场、小区、办公室、教室、户外公园、车内、夜晚监控、夜晚暗光

**物体库**：行人、电动车、轿车、货车、监控设备、桌椅、绿植、猫狗、人脸

**行为库**：行走、跑动、静止、骑车

**数据集用途判定**（规则引擎）：
- `监控训练集`：高频行人 + 监控/楼道/道路场景
- `自动驾驶数据集`：车辆 + 路面 + 车流关键词匹配
- `人像素材`：人脸 + 人物 + 特写关键词匹配
- `影视素材`：电影 + 剧情 + 镜头关键词匹配
- `风景素材`：自然 + 山水 + 天空关键词匹配
- `游戏录屏`：游戏 + UI + 角色关键词匹配

### 视频质量评分

每个视频自动计算 4 个维度的质量分：
- **清晰度**：灰度方差评估
- **亮度**：平均灰度值适中度
- **主体占比**：边缘密度估计
- **抖动程度**：帧间差异分析

质量分 ≥ 0.4 且语义置信度 ≥ `--scene-thresh` 且已判定用途的视频标记为「适合训练」。

### 语义内容聚类（区别于哈希查重）

哈希查重检测的是**文件内容完全重复**；语义聚类是**画面内容近似但文件不同**的分组：
- 使用 CLIP 特征向量做 K-means 聚类
- 不同监控片段、画面都是街道行人，文件不重复但内容同类，会归为一类
- 生成 `scene_cluster.html` 可视化报告，支持搜索、折叠、展开

### 降级机制

| 环境情况 | 行为 |
|----------|------|
| 无 torch/CLIP | `--semantic` 等参数自动失效，仅运行原有哈希查重 |
| 有 torch 无 GPU | 自动切换 CPU 推理（速度较慢） |
| CPU 模式 | 自动降低推理批次，防止内存溢出 |
| 超大视频 | 分段推理，避免一次性加载卡死 |
| 可关闭缓存 | `--no-semantic-cache` 减小缓存文件体积 |

## 配置文件示例 (`duplicate_config.ini`)

```ini
[scan]
dir = D:\Videos
threshold = 0.75
frames = 10
format = html
fast = false
keep_latest = true

; v2.2 AI 配置
semantic = true
purpose_filter = 监控,自动驾驶
cluster_semantic = true
export_dataset = true
scene_thresh = 0.6
embed_cache = true
```

使用：`python find_mp4.py --config duplicate_config.ini`

## 自定义标签配置 (`dataset_labels.ini`)（v2.3 新增）

v2.3 起支持通过外置 INI 文件自定义 AI 语义识别的标签库，无需改代码即可适配垂直领域（医疗、工业、农业、安防等）。程序运行时若在脚本同级目录检测到 `dataset_labels.ini` 即自动加载并合并到内置标签库。

```ini
; 场景标签（每行一个，支持中文）
[scenes]
scene_1 = 病房
scene_2 = 手术室
scene_3 = 仓库货架
scene_4 = 工厂流水线

; 物体标签
[objects]
object_1 = 机床
object_2 = 托盘
object_3 = 安全帽

; 行为标签
[actions]
action_1 = 搬运
action_2 = 操作设备

; 数据集用途判定规则（关键词匹配，逗号分隔）
[purposes]
purpose_工业质检 = 工厂,流水线,机床,质检
purpose_仓储物流 = 仓库,托盘,搬运
purpose_医疗影像 = 病房,手术室,医疗

; 默认用途（当无规则命中时）
[default]
purpose = 通用素材
```

**说明：**
- 文件不存在时使用内置标签库，行为与 v2.2 完全一致
- 自定义标签会与内置标签**合并**（同名标签以自定义为准）
- 用途规则采用关键词命中判定，命中任一关键词即归类
- 修改后建议执行 `python find_mp4.py clear-semantic-cache --dir <目录>` 清理旧语义缓存以重新识别

## v2.5 AI 自动分类功能详解

### 功能概述

AI 自动分类功能基于 CLIP 视觉特征向量，通过 K-Means 聚类算法将视频自动分组，并根据每组的高频语义标签自动生成类别名称。适用于大批量素材的自动化整理，支持监控、自动驾驶、人像、影视等场景。

### 核心特性

1. **零样本自动归类**：无需训练数据集，扫描后直接生成结构化素材文件夹
2. **智能命名**：根据每个聚类中视频的场景/对象/动作标签自动生成类别名称
3. **安全预览**：默认 dry-run 模式，仅预览分类结果，不修改原文件
4. **文件组织**：支持硬链接（默认）、复制、移动三种方式组织分类文件
5. **筛选联动**：可与时长、分辨率、低质量过滤等条件联动，精准分类

### 使用场景

```bash
# 1. 预览模式（默认，不修改文件）
python find_mp4.py auto-classify --dir D:\Videos

# 2. 执行硬链接分类
python find_mp4.py auto-classify --dir D:\Videos --execute --link-mode

# 3. 分类 + 筛选联动
python find_mp4.py auto-classify --dir D:\Videos --duration-filter ">=60"
python find_mp4.py auto-classify --dir D:\Videos --min-res 1920 --skip-low-quality

# 4. 指定聚类数
python find_mp4.py auto-classify --dir D:\Videos --n-clusters 8
```

### 输出结构

分类完成后，输出目录结构如下：
```
_output_dir/
├── auto_classify_report.json    # 分类报告
├── 类别1_场景描述/
│   ├── video1.mp4 -> (硬链接原文件)
│   └── video2.mp4
├── 类别2_场景描述/
│   └── video3.mp4
└── ...
```

### 配置示例

在 `config.ini` 中预设分类参数：
```ini
[scan]
dir = D:\Videos
semantic = true
classify_only = true
link_mode = true
n_clusters = 8
skip_low_quality = true
min_res = 1080
```

## 场景示例

### 场景一：新手快速使用
```bash
python find_mp4.py --dir D:\Videos
```

### 场景二：200+ 视频快速粗筛
```bash
python find_mp4.py --dir D:\Videos --fast
```

### 场景三：高精度查重（重要素材）
```bash
python find_mp4.py --dir D:\Videos --threshold 0.85 --frames 15 --double-check --keep-max-res
```

### 场景四：定期增量扫描（日常自动化）
```bash
python find_mp4.py --dir D:\Videos --incremental --quiet --format html
```

### 场景五：安全清理重复视频
```bash
# 1. 先试运行
python find_mp4.py --dir D:\Videos --gen-cleanup --dry-run

# 2. 确认无误后生成清理脚本
python find_mp4.py --dir D:\Videos --gen-cleanup

# 3. 双击 cleanup_duplicates.bat 执行（需输入 CONFIRM）
```

### 场景六：监控素材批量扫描 + 导出训练集清单（v2.2）
```bash
# 开启 AI 语义分析，导出监控训练集清单
python find_mp4.py scan --dir D:\camera_video --semantic --export-dataset

# 结果：
# - dataset_catalog.csv：每个视频的场景标签、用途、质量分
# - train_sample_list.txt：可直接用于 AI 训练的视频路径清单
# - dataset_stats.md：数据集统计汇总
```

### 场景七：筛选自动驾驶视频做重复清理（v2.2）
```bash
# 只筛选自动驾驶数据集视频，生成训练列表
python find_mp4.py dataset-filter --purpose 自动驾驶 --dir D:\car_data

# 或配合哈希查重
python find_mp4.py scan --dir D:\car_data --purpose-filter 自动驾驶 --semantic
```

### 场景八：纯场景聚类，区分不同拍摄环境（v2.2）
```bash
# 画面内容聚类，把同类监控片段分组
python find_mp4.py scan --dir D:\camera --cluster-semantic

# 或使用子命令
python find_mp4.py cluster-scene --dir D:\camera
```

### 场景九：增量扫描 + 语义缓存复用（v2.2）
```bash
# 首次运行（会跑 AI 推理）
python find_mp4.py scan --dir D:\video --semantic --incremental --embed-cache

# 二次运行（缓存命中，无需重跑 AI）
python find_mp4.py scan --dir D:\video --semantic --incremental --embed-cache
```

### 场景十：自定义标签分类（v2.3）
适用：垂直领域（医疗/工业/农业）需要专属场景与用途标签。

```bash
# 1. 在脚本同级目录放置 dataset_labels.ini（参考上文配置示例）
# 2. 清理旧的语义缓存，避免旧标签干扰
python find_mp4.py clear-semantic-cache --dir D:\factory_video

# 3. 重新扫描，自动加载自定义标签库
python find_mp4.py scan --dir D:\factory_video --semantic --export-dataset

# 结果：dataset_catalog.csv 中会出现「工厂流水线/机床/工业质检」等自定义标签
```

### 场景十一：清理 AI 缓存但保留哈希缓存（v2.3）
适用：标签库更新或模型升级后只想重跑 AI，不想重新抽帧。

```bash
# 仅清理语义/嵌入缓存，保留 video_hash_cache.json 中的哈希
python find_mp4.py clear-semantic-cache --dir D:\Videos

# 先预览将清理多少条目（不实际删除）
python find_mp4.py clear-semantic-cache --dir D:\Videos --dry-run

# 仅清理 7 天前的过期语义缓存
python find_mp4.py clear-semantic-cache --dir D:\Videos --cache-expire-days 7

# 清理后重跑：哈希秒级命中，只重算 AI 部分
python find_mp4.py scan --dir D:\Videos --semantic --incremental --embed-cache
```

### 场景十二：数据集分类拆分（v2.3）
适用：把混合数据集按用途/场景整理成可直接训练的目录结构。

```bash
# 前置：先生成语义元数据
python find_mp4.py scan --dir D:\mixed_data --semantic --export-dataset

# 按用途拆分（默认），每个用途一个子目录
python find_mp4.py dataset-split --dir D:\mixed_data --split-by purpose

# 按场景拆分，并指定输出根目录与分组数
python find_mp4.py dataset-split --dir D:\mixed_data --split-by scene --output-dir D:\split_out --cluster-num 8

# 结果：D:\split_out\监控训练集\、D:\split_out\自动驾驶\、... 各含对应视频清单/链接
```

### 场景十三：备份模式清理（v2.3）
适用：清理重复视频时希望统一备份到指定目录，而非散落在系统临时目录。

```bash
# 1. 准备受保护文件清单（一行一个绝对路径）
#    protected.txt 内容示例：
#      D:\Videos\keep\source.mp4
#      D:\Videos\archive\2024.mp4

# 2. 生成清理脚本：移动到 D:\Backup\duplicates，并保护清单内文件
python find_mp4.py --dir D:\Videos --gen-cleanup \
    --backup-path D:\Backup\duplicates \
    --protect-file protected.txt

# 3. 执行清理脚本（需输入 CONFIRM）
#    cleanup_duplicates.bat
```

### 场景十四：轻量 CSV 导出（v2.3）
适用：视频数量极大（万级以上），只需核心字段做后续脚本处理，减小导出体积。

```bash
# 轻量 CSV：仅路径/相似度/标签，跳过冗余元数据列
python find_mp4.py --dir D:\huge_library --lite-csv --fast

# 同时压缩缓存、不存嵌入向量，进一步节省磁盘
python find_mp4.py --dir D:\huge_library --lite-csv --compress-cache --no-store-embed

# 单独导出损坏视频路径清单，便于运维批量排查
python find_mp4.py --dir D:\huge_library --export-bad-paths --check-only
```

### 场景十五：AI 自动分类整理素材（v2.5）
适用：将大量混合素材自动按内容类别整理成结构化文件夹。

```bash
# 1. 预览模式（默认，不修改文件，仅预览分类结果）
python find_mp4.py auto-classify --dir D:\Videos

# 2. 执行硬链接分类（不复制文件，节省磁盘）
python find_mp4.py auto-classify --dir D:\Videos --execute --link-mode

# 3. 分类 + 筛选联动（仅对高清长视频分类）
python find_mp4.py auto-classify --dir D:\Videos --min-res 1920 --duration-filter ">=60"

# 4. 分类 + 低质量过滤（排除模糊暗光视频）
python find_mp4.py auto-classify --dir D:\Videos --skip-low-quality --execute

# 5. 自定义聚类数
python find_mp4.py auto-classify --dir D:\Videos --n-clusters 10 --execute

# 结果：D:\Videos\_classify_output\ 下按类别名生成子目录，包含对应视频硬链接
```

## 删除功能风险警示

**默认模式为安全模式**，清理脚本将文件移动至临时目录而非永久删除。

- 默认：移动至 `%TEMP%\trash_*`（可恢复）
- `--hard-delete`：**永久删除**（不可恢复，谨慎使用）
- 脚本执行前必须手动输入 `CONFIRM` 才能继续
- 系统目录、桌面根目录默认受保护
- 建议先使用 `--dry-run` 预览结果
- v2.2 新增：清理脚本附带视频用途备注，清理监控训练集素材时做风险提示
- v2.3 新增：`--backup-path` 统一备份目录、`--protect-file` 受保护文件清单，双重保险防止误删重要素材

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 无重复 |
| 1 | 存在重复分组 |
| 2 | 视频解析失败 |
| 3 | 参数错误 |

## 技术栈

**基础依赖（必需）：**
- **Python 3.8+**
- **OpenCV** - 视频帧提取
- **imagehash** - 感知哈希（pHash + dHash）
- **NumPy** - 数值计算
- **Pillow** - 图像处理

**可选增强：**
- **tqdm** - 进度条显示
- **psutil** - 内存监控
- **FFmpeg** - 音频分析、兜底解码

**v2.2 AI 扩展依赖（可选）：**
- **torch ≥ 2.0.0** - 深度学习推理
- **open-clip-torch ≥ 2.20.0** - CLIP 视觉-语言模型
- **transformers ≥ 4.30.0** - 模型基础设施
- **scikit-learn ≥ 1.3.0** - KMeans 聚类

## 自测说明（安装后验证）

安装完成后，按以下步骤快速验证功能是否正常（任选对应环境的命令执行）：

**1. 版本与基础环境自测**
```bash
# 打印版本信息，确认程序可运行
python find_mp4.py version

# 期望输出：MP4 视频相似度查重工具 v2.3
```

**2. 基础哈希查重自测（无需 AI 依赖）**
```bash
# 准备 2~3 个测试视频放入 D:\test_video（可放一对重复视频）
python find_mp4.py --dir D:\test_video --dry-run --format txt

# 期望：生成 duplicate_groups.txt，且 run_log.txt 中无 ERROR
# 退出码：0=无重复，1=存在重复分组
```

**3. 缓存与子命令自测**
```bash
# 校验缓存写入与读取
python find_mp4.py --dir D:\test_video --check-only
python find_mp4.py verify-cache --dir D:\test_video

# v2.3 子命令可用性自测（应能正常执行，无报错）
python find_mp4.py clear-semantic-cache --dir D:\test_video --dry-run
python find_mp4.py dataset-split --dir D:\test_video --dry-run
```

**4. AI 语义功能自测（需已安装 AI 扩展依赖）**
```bash
# 首次运行会下载 CLIP 模型（约 350MB），需联网
python find_mp4.py scan --dir D:\test_video --semantic --export-dataset

# 期望：生成 video_semantic_meta.json、dataset_catalog.csv
# 无 torch/CLIP 时该命令应自动降级为纯哈希查重并给出提示，而非崩溃
```

**5. 自定义标签自测（v2.3）**
```bash
# 在脚本同级目录放置 dataset_labels.ini 后
python find_mp4.py scan --dir D:\test_video --semantic

# 期望：dataset_catalog.csv 的场景/用途列出现 dataset_labels.ini 中定义的标签
```

**常见问题排查：**
- `ModuleNotFoundError: No module named 'cv2'` → 未装基础依赖，执行 `pip install -r requirements.txt`
- AI 命令报 `ModuleNotFoundError: No module named 'torch'` → 未装 AI 依赖，或可忽略（会自动降级）
- 清理脚本执行无反应 → 确认是否输入 `CONFIRM` 并回车
- 长路径报错 → v2.3 已统一长路径处理，如仍报错请检查路径是否超过 260 字符并启用长路径支持

## 更新日志

### v2.3 (最新)

**P0 BUG 修复**
- 🐛 缓存分块自动合并读取：修复大缓存分块写入后读取时未自动合并导致的条目丢失
- 🐛 长路径统一：统一 Windows 长路径（`\\?\` 前缀）处理，避免 >260 字符路径解析失败
- 🐛 AI 语义字段清理：修复缓存中残留旧版语义字段导致导出报告列错位
- 🐛 参数冲突校验：`--hard-delete` 与 `--backup-path`、`--keep-*` 互斥参数现在启动时即报错而非静默忽略
- 🐛 权限跳过：无读权限目录现在跳过并记录日志，而非中断整个扫描
- 🐛 FFmpeg 警告：FFmpeg stderr 警告不再被误判为损坏视频
- 🐛 超大视频分段抽帧：超长视频分段抽帧时帧索引越界已修复

**P1 功能增强**
- 🆕 `dataset_labels.ini` 外置标签配置，支持自定义场景/物体/行为/用途标签库
- 🆕 子命令 `clear-semantic-cache`：选择性清理 AI 语义缓存，保留哈希缓存
- 🆕 子命令 `dataset-split`：按用途/场景拆分数据集到独立子目录
- 🆕 `--clip-model-path`：自定义 CLIP 模型本地路径，支持离线/指定模型
- 🆕 `--lite-csv`：轻量 CSV 导出，仅核心字段，适合大体量场景
- 🆕 `--export-bad-paths`：单独导出损坏视频路径清单
- 🆕 `--backup-path`：清理时统一备份到指定目录
- 🆕 `--protect-file`：受保护文件清单，匹配项永不被清理
- 🆕 `--cluster-num`：手动指定场景聚类分组数
- 🆕 `--cache-expire-days`：缓存过期天数，超期自动失效
- 🆕 `--compress-cache`：gzip 压缩缓存文件，减小磁盘占用
- 🆕 `--no-store-embed`：不存储特征向量到缓存，仅存语义标签

**P2 性能优化**
- ⚡ 帧复用：哈希抽帧与 AI 推理共享同一批帧，避免重复解码
- ⚡ 断点续扫：扫描中断后可从上次进度继续，无需重头扫描
- ⚡ 缓存过期清理：启动时自动清理超期缓存条目，避免缓存膨胀
- ⚡ AI 线程自适应：根据 GPU/CPU 与内存占用动态调整 AI 推理线程数

### v2.2
- 🆕 AI 语义内容分析模块（CLIP ViT-B/32 零样本模型）
- 🆕 场景/物体/行为标签自动识别
- 🆕 数据集用途自动判定（监控训练集/自动驾驶/人像/影视/风景/游戏）
- 🆕 视频质量评分（清晰度/亮度/主体占比/抖动）
- 🆕 语义内容聚类（区别于文件哈希查重）
- 🆕 训练集清单导出（catalog CSV + 路径清单 + 统计报告）
- 🆕 子命令：`semantic-analyze`、`dataset-filter`、`cluster-scene`
- 🆕 缓存语义标签和 CLIP 特征向量，增量扫描复用
- 🆕 所有导出报告扩展语义标签列/面板
- 🆕 AI 依赖可选安装，无依赖环境自动降级
- 🔧 新增 `requirements_ai.txt`、`install_ai.bat`
- 🔧 更新 `install.bat` 区分基础/AI 安装模式

### v2.1
- 新增子命令架构（scan/clean-cache/merge-cache/verify-cache/version）
- 多维度保留策略（最新/最高分辨率/最大码率）
- LSH 分桶加速比对
- 增量扫描模式
- .duplicateignore 文件过滤
- 音频辅助比对
- 配置文件支持
- 安全清理脚本（默认移动至回收站）
- 损坏视频分类清单
- 审计日志

### v2.0
- 基础哈希查重功能（pHash + dHash 融合）
- 多线程哈希提取
- CSV/TXT/MD/HTML 多格式导出
- 连通图分组算法
- 缓存管理（版本化）

## License

MIT