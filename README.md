# MP4 视频相似度查重工具 v2.2

基于感知哈希（pHash + dHash）+ AI 语义分析的视频重复检测与数据集标注工具，支持 LSH 加速、增量扫描、缓存管理、多格式导出、安全清理脚本、场景聚类、训练集清单导出。

## 目录结构

```
distinguish/
├── find_mp4.py          # 主程序 v2.2
├── ai_semantic.py       # AI 语义分析模块（v2.2 新增，可选）
├── requirements.txt     # 基础依赖清单
├── requirements_ai.txt  # AI 扩展依赖清单（v2.2 新增）
├── install.bat          # Windows 一键安装脚本（区分基础/AI）
├── install_ai.bat       # AI 依赖一键安装脚本（v2.2 新增）
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

**方式三：安装 AI 扩展依赖（可选）**
```bash
pip install -r requirements_ai.txt
```
或使用：
```bat
install_ai.bat
```

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

## 删除功能风险警示

**默认模式为安全模式**，清理脚本将文件移动至临时目录而非永久删除。

- 默认：移动至 `%TEMP%\trash_*`（可恢复）
- `--hard-delete`：**永久删除**（不可恢复，谨慎使用）
- 脚本执行前必须手动输入 `CONFIRM` 才能继续
- 系统目录、桌面根目录默认受保护
- 建议先使用 `--dry-run` 预览结果
- v2.2 新增：清理脚本附带视频用途备注，清理监控训练集素材时做风险提示

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

## 更新日志

### v2.2 (最新)
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