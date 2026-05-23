# 豆包播客自动化工具链

> 全自动下载豆包 AI 播客 → 按原始 PDF 名称重命名 → 压缩为 MP3 → 绑定到 Obsidian Markdown

---

## 目录

- [项目概述](#项目概述)
- [技术栈与工具链](#技术栈与工具链)
- [依赖库](#依赖库)
- [文件结构](#文件结构)
- [使用方法](#使用方法)
- [设计原理](#设计原理)
- [已知限制](#已知限制)
- [故障排查](#故障排查)

---

## 项目概述

本项目解决的核心问题：**豆包 AI 生成的播客音频无法批量导出，且默认文件名与原始 PDF 无关**，导致用户在 Obsidian 知识库中难以管理和关联。

通过浏览器自动化（Playwright）+ 本地文件处理脚本，实现：
1. 自动扫描豆包聊天记录中的所有播客
2. 根据原始 PDF 名称精确定位并下载对应播客
3. 将 WAV 压缩为 MP3（节省约 80% 空间）
4. 自动在对应 Markdown 文件开头插入音频链接（`[[../附件/音频/xxx.mp3]]`）

---

## 技术栈与工具链

| 层级 | 技术/工具 | 版本要求 | 用途 |
|------|----------|---------|------|
| **浏览器自动化** | Playwright (Python) | >= 1.59 | 控制 Chromium 浏览器，操作豆包网页 |
| **浏览器内核** | Chromium | Playwright 自带 | 渲染豆包 SPA，执行 JavaScript |
| **音频处理** | FFmpeg | >= 8.1 | WAV → MP3 压缩（libmp3lame 编码器）|
| **编程语言** | Python | >= 3.10 | 主控脚本，异步 I/O 编排 |
| **操作系统** | Windows 10/11 | - | 路径处理、PowerShell 剪贴板交互 |
| **Shell** | Git Bash (MSYS2) | - | 执行命令行操作 |
| **知识库** | Obsidian | - | Markdown 笔记存储与 WikiLink 渲染 |

---

## 依赖库

### Python 包

```bash
pip install playwright
playwright install chromium
```

- **playwright**：微软出品的浏览器自动化库，支持异步 API、下载事件监听、storage_state 持久化
- **asyncio**：Python 原生异步 I/O，用于并发编排浏览器操作和文件处理

### 系统依赖

- **FFmpeg**：必须已安装并在 `PATH` 中可调用
  ```bash
  # 验证安装
  ffmpeg -version
  ```

---

## 文件结构

```
代码/
├── README.md                      # 本文件
├── DEV_LOG.md                     # 开发日志与设计决策
├── doubao_full.py                 # 【入口】一键完整流程
├── doubao_scanner.py              # 阶段1：扫描页面所有播客
├── doubao_downloader.py           # 阶段2：精确下载（支持断点续传）
├── post_process.py                # 阶段3：压缩MP3 + 删除WAV + 绑定Markdown
├── podcasts_list.json             # 扫描结果（52个播客的PDF名、标题、时长）
├── doubao_state.json              # 登录态持久化（Cookie + LocalStorage）
├── md_check.json                  # Markdown对应关系检查报告
├── embed_report.json              # 绑定结果报告
└── doubao_debug/                  # 调试输出目录
    ├── probe_*.png                # 页面截图
    ├── probe_*.html               # 页面DOM快照
    └── scan_final.png             # 最终扫描截图
```

---

## 使用方法

### 方式一：一键完整流程（推荐）

```bash
cd 代码
python doubao_full.py https://www.doubao.com/chat/xxxxxx
```

自动执行：扫描 → 下载 → 压缩 → 删除WAV → 绑定Markdown

### 方式二：分步执行（适合调试）

#### 步骤1：扫描页面

```bash
python doubao_scanner.py https://www.doubao.com/chat/xxxxxx
```

- 打开浏览器，加载登录态
- 持续向上滚动虚拟列表，收集所有播客
- 输出 `podcasts_list.json`
- 显示：共发现 N 个播客

#### 步骤2：下载全部

```bash
python doubao_downloader.py https://www.doubao.com/chat/xxxxxx --all
```

- 从 `podcasts_list.json` 读取列表
- 逐个滚动定位 → 点击下载 → 重命名为 PDF 同名
- 断点续传：已下载的会自动跳过

#### 步骤3：后处理

```bash
python post_process.py
```

- 遍历 `附件/音频/*.wav`
- FFmpeg 压缩为 MP3（`-q:a 2`，约 1/5 体积）
- 删除 WAV
- 在对应 Markdown 开头插入播客链接

### 方式三：指定文件名下载

```bash
python doubao_downloader.py <URL> "文件名1.pdf" "文件名2.pdf"
```

### 方式四：批量文件下载

```bash
# 创建 batch.txt，每行一个PDF名称
python doubao_downloader.py <URL> --batch batch.txt
```

---

## 设计原理

### 1. 两段式架构

```
浏览器层（Playwright）          本地层（Python）
    │                              │
    ├─ 打开豆包 SPA               ├─ 监听下载事件
    ├─ 滚动虚拟列表               ├─ 重命名文件
    ├─ 点击下载按钮               ├─ FFmpeg 压缩
    └─ 捕获下载事件               └─ 修改 Markdown
```

**为什么不用直接 HTTP 下载？**
- 豆包播客 URL 是动态生成的 blob/stream，无固定直链
- 需要登录态 + JavaScript 渲染后才能获取

### 2. 虚拟列表（Virtual List）处理

豆包使用 CSS Transform 实现的虚拟列表（`data-observe-row` + `transform: translate(...)`），**只渲染视口附近的 DOM 节点**。

**解决方案**：
- 通过 JavaScript `document.createTreeWalker()` 遍历所有文本节点
- 若未找到目标，向上滚动滚动容器（`scrollTop -= 600`）触发 React 重新渲染
- 循环最多 100 次，直到找到或到达顶部

### 3. 精确绑定策略

**问题**：页面上有多个播客，如何确保下载的是"对应"的？

**方案**：以 **PDF 文件名** 为锚点
- 每个播客卡片上方都有文字：`"我将根据 xxx.pdf 的内容为你生成播客"`
- 在浏览器内使用 TreeWalker 找到包含该 PDF 名称的文本节点
- 向上追溯 DOM 树，直到找到 `data-plugin-identifier="Symbol(receive-podcast-content)"` 的播客卡片
- 在该卡片内部点击下载按钮

### 4. 登录态持久化

- Playwright 的 `context.storage_state()` 保存 Cookie + LocalStorage
- 文件：`doubao_state.json`
- 下次启动时自动加载，无需重复扫码

### 5. 断点续传

下载前检查目标文件是否已存在：
```python
existing = list(AUDIO_DIR.glob(f"{target_name}.*"))
if existing:
    return True  # 跳过
```

### 6. 编码兼容

Windows 终端默认使用 GBK 编码，导致中文输出乱码。
- 所有文件操作使用 UTF-8 编码
- 终端输出尽量使用 ASCII 字符（实际运行时仍可能有乱码）
- 关键结果保存为 JSON 文件，避免依赖终端显示

---

## 已知限制

| 限制 | 说明 |
|------|------|
| 虚拟列表不稳定性 | 极个别情况下，向上滚动后目标播客仍未渲染到 DOM 中 |
| 下载取消 | 某些播客在豆包端会被主动取消下载（原因不明）|
| Markdown 缺失 | 如果 Obsidian 库中没有对应的 `.md` 文件，无法绑定 |
| 单浏览器实例 | 多个后台任务同时操作可能因 storage_state 写入冲突而失败 |
| 终端乱码 | Windows Git Bash 对中文字符支持不完善 |

---

## 故障排查

### Q：浏览器打开后显示登录框
A：正常。如果是首次运行，请在浏览器内完成扫码/手机号登录。登录态会自动保存到 `doubao_state.json`，下次无需重复登录。

### Q：下载按钮找不到
A：豆包页面结构可能更新。运行探测模式查看当前 DOM：
```bash
python doubao_scanner.py <URL>
```
检查 `doubao_debug/probe_*.png` 截图。

### Q：下载文件被占用（PermissionError）
A：浏览器可能还未释放文件句柄。脚本已内置重试逻辑（最多等 5 秒）。如仍失败，稍后手动移动文件即可。

### Q：FFmpeg 报错
A：确认 FFmpeg 已安装并在 PATH 中：
```bash
ffmpeg -version
```

---

## 作者

- 开发：Kimi Code CLI（自动化编排）
- 需求与设计：用户 @deck
