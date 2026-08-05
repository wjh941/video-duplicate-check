# MP4 视频相似度查重工具 v2.1

基于感知哈希（pHash + dHash）的视频重复检测工具，支持快速预筛、多线程提取、LSH 加速、多种导出格式、安全清理脚本。

## 目录结构

```
distinguish/
├── find_mp4.py          # 主程序
├── requirements.txt     # 依赖清单
├── install.bat          # Windows 一键安装脚本
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

**方式二：手动安装**
```bash
pip install -r requirements.txt
```

**可选依赖（增强功能）：**
- `tqdm` - 进度条显示
- `psutil` - 内存监控
- `ffmpeg` - 音频辅助比对、兜底解码

### 2. 运行

```bash
# 最基础用法
python find_mp4.py --dir D:\Videos

# 快速粗筛（200+视频推荐）
python find_mp4.py --dir D:\Videos --fast

# 高精度查重
python find_mp4.py --dir D:\Videos --threshold 0.85 --frames 15 --double-check

# 生成可视化 HTML 报告 + 清理脚本
python find_mp4.py --dir D:\Videos --format html --gen-cleanup

# 增量扫描（仅处理新增/修改视频）
python find_mp4.py --dir D:\Videos --incremental

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

### v2.1 新增参数

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

### 子命令

| 命令 | 说明 |
|------|------|
| `scan` | 扫描视频（默认命令，可省略） |
| `clean-cache` | 清理无效缓存条目 |
| `merge-cache` | 合并多个缓存文件 |
| `verify-cache` | 校验缓存有效性 |
| `version` | 打印版本信息 |

## 输出文件

| 文件 | 说明 |
|------|------|
| `similar_result.csv` | 两两比对明细（Excel 可打开） |
| `duplicate_groups.txt` | TXT 分组报告 |
| `duplicate_groups.md` | Markdown 分组报告 |
| `duplicate_groups.html` | HTML 可视化报告 |
| `duplicate_paths.txt` | 纯路径清单（批量操作用） |
| `bad_video_list.txt` | 损坏视频清单（按故障类型分类） |
| `video_hash_cache.json` | 哈希缓存（加速后续运行） |
| `cleanup_duplicates.bat` | Windows 清理脚本（需手动确认） |
| `cleanup_duplicates.sh` | Linux 清理脚本 |
| `hash_export.json` | 全量哈希备份 |
| `run_log.txt` | 运行日志 |
| `cleanup_audit.log` | 清理操作审计日志 |

## 配置文件示例 (`duplicate_config.ini`)

```ini
[scan]
dir = D:\Videos
threshold = 0.75
frames = 10
format = html
fast = false
keep_latest = true
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

## 删除功能风险警示

**默认模式为安全模式**，清理脚本将文件移动至临时目录而非永久删除。

- 默认：移动至 `%TEMP%\trash_*`（可恢复）
- `--hard-delete`：**永久删除**（不可恢复，谨慎使用）
- 脚本执行前必须手动输入 `CONFIRM` 才能继续
- 系统目录、桌面根目录默认受保护
- 建议先使用 `--dry-run` 预览结果

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 无重复 |
| 1 | 存在重复分组 |
| 2 | 视频解析失败 |
| 3 | 参数错误 |

## 技术栈

- **Python 3.8+**
- **OpenCV** - 视频帧提取
- **imagehash** - 感知哈希（pHash + dHash）
- **NumPy** - 数值计算
- **Pillow** - 图像处理
- **tqdm** - 进度条（可选）
- **psutil** - 内存监控（可选）
- **FFmpeg** - 音频分析（可选）

## License

MIT
