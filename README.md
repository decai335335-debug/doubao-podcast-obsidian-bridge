# 豆包播客全自动流水线

> Markdown / PDF → 豆包 AI 播客 → 下载 → 压缩 MP3 → 自动绑定 Obsidian 笔记

一句话：把豆包 AI "根据文档生成播客"的全流程串成一条自动化流水线，从 Markdown 到最终嵌入 Obsidian，全程无需人工干预。

---

## 1. 解决什么痛点

### Before（以前是这样的）

- 豆包网页**没有批量导出**播客的功能，52 个播客要逐个点击下载
- 下载后的音频默认名叫 `AI播客_123456.wav`，**和原始 PDF 完全无关**，文件系统里根本认不出内容
- Obsidian 里已有对应 PDF 的 Markdown 笔记，但播客音频和笔记之间**没有任何关联**，形成"有笔记无音频、有音频无笔记"的双轨割裂
- 想把 Markdown 生成 PDF 再上传豆包生成播客，需要**手动操作 4 个独立工具**，步骤繁琐且容易出错

### After（现在是这样的）

- 一条命令完成：Markdown → PDF → 上传 → 生成播客 → 保存地址
- 另一条命令完成：扫描 → 下载 → 按 PDF 同名重命名 → 压缩 MP3 → 自动嵌入 Markdown
- 全程断点续传，中断后不用从头开始
- 音频链接以 Obsidian WikiLink 语法嵌入，打开笔记直接播放

### 适合谁用

- **知识库管理者** —— 在 Obsidian 中管理大量文档，希望每份文档都有配套的 AI 播客
- **备考/复习用户** —— 把申论真题、技术文档转成 PDF，生成播客在路上听
- **内容创作者** —— 批量把文章/笔记生成播客，分发到不同平台

---

## 2. 核心功能

| 功能 | 解决什么问题 |
|------|-------------|
| **Markdown → PDF** | 不想手动打开编辑器导出 PDF，一键把 `.md` 转成排版精美的 PDF |
| **PDF 批量上传 + 生成播客** | 同一个聊天窗口内逐个上传 PDF，自动点击"生成播客"，等待完成后处理下一个，不用守在旁边 |
| **扫描页面所有播客** | 豆包虚拟列表只渲染视口内元素，脚本自动滚动并收集全部播客清单 |
| **按 PDF 名称精确下载** | 下载的音频默认名是乱码，脚本根据"我将根据 xxx.pdf 的内容为你生成播客"这段文字，精确定位到对应的播客卡片并下载 |
| **WAV 压缩为 MP3** | 豆包下载的是 WAV（平均 120MB），FFmpeg 压缩后约 25MB，节省 80% 空间 |
| **自动绑定 Obsidian** | 在对应 Markdown 文件开头插入 `[[附件/音频/xxx.mp3]]`，打开笔记即可播放 |
| **断点续传** | 52 个播客下载到第 30 个中断？重新运行自动跳过已下载的，不用从头来 |
| **交互式流水线入口** | 输入 `a` 生成播客，输入 `b` 下载播客，不用记一堆命令行参数 |
| **CF_HDROP 剪贴板支持** | 从文件资源管理器复制文件时直接读取 Windows 原生文件格式，长路径和 emoji 文件名不会截断 |
| **生成失败自动跳过** | 检测到"抱歉，暂时无法生成播客"等失败提示时，立即跳过当前文件，继续处理下一个 |
| **"未命名"文件过滤** | 自动跳过文件名包含"未命名"的 Markdown 文件，避免生成无意义的播客 |
| **一键扫描入剪贴板** | `scan_to_clipboard.py` 自动扫描指定目录下今天新增的 md 文件，直接写入剪贴板 |
| **批量上传+最后统一检测** | 点击"生成播客"后不逐个等待完成，快速上传全部 PDF，最后统一清点播客数量 |
| **页面自动滚动** | 每个 PDF 处理后自动滚动到底部，确保下一个"+"按钮可见，避免虚拟滚动导致卡住 |
| **上传/下载绑定记录** | 自动生成 `豆包播客代码上传与下载绑定记录.md`，记录每次操作的时间、文件和链接 |
| **精确路径映射** | `md_mapping.json` 保存 PDF → Markdown 完整路径映射，避免同名文件绑定到错误笔记 |

---

## 3. 安装方法

### 3.1 环境要求

- Windows 10/11
- Python >= 3.10
- Git Bash（推荐）或 PowerShell

### 3.2 安装依赖

```bash
# 1. 安装 Playwright
pip install playwright
python -m playwright install chromium

# 2. 安装 Markdown 解析库（md2pdf 需要）
pip install markdown

# 3. 安装 FFmpeg（压缩音频用）
# 下载地址：https://ffmpeg.org/download.html
# 安装后确保 ffmpeg 在 PATH 中
ffmpeg -version
```

### 3.3 首次登录豆包

运行任意脚本时，如果浏览器显示登录框：
1. 在浏览器内完成**扫码登录**或**手机号登录**
2. 登录态会自动保存到 `doubao_state.json`
3. 下次运行无需重复登录

---

## 4. 使用方法

### 场景一：从 Markdown 生成播客（模式 a）

**什么时候用**：你有一堆 Markdown 笔记，想让豆包 AI 根据它们生成播客

```bash
python doubao_pipeline.py
# 输入 a
```

1. 在资源管理器中选中多个 Markdown 文件，按 **Ctrl + Shift + C** 复制路径
2. 程序自动检测到剪贴板中的路径，按 **y** 确认
3. 程序自动：Markdown → PDF → 打开浏览器 → 上传 → 点击"生成播客" → 等待完成
4. 所有播客生成完毕后，聊天地址自动保存到 `pipeline_state.json`

### 场景二：下载已生成的播客（模式 b）

**什么时候用**：播客已经生成完了，需要批量下载并绑定到 Obsidian

```bash
python doubao_pipeline.py
# 输入 b
```

1. 程序自动读取上次保存的聊天地址
2. 直接按 **回车** 确认，或输入 `n` 换其他地址
3. 选择 `[1] 一键完整流程`
4. 自动执行：扫描 → 下载 → 压缩 MP3 → 删除 WAV → 绑定 Markdown

### 场景三：断点续传

**什么时候用**：下载到一半断网/断电了，重新运行不想从头来

```bash
python doubao_pipeline.py
# 输入 b → 确认地址 → 选择 [1] 一键完整流程
```

下载脚本会自动检查文件是否已存在，已下载的自动跳过。

### 场景四：仅处理指定 PDF

**什么时候用**：只需要上传某几个特定的 PDF

```bash
python doubao_uploader.py "C:\path\to\file1.pdf" "C:\path\to\file2.pdf"
```

### 场景五：指定文件名下载

**什么时候用**：只需要下载某几个播客，不想全量扫描

```bash
python doubao_downloader.py <URL> "文件名1.pdf" "文件名2.pdf"
```

---

## 5. 技术栈

### 5.1 核心技术

| 层级 | 技术 | 用途 |
|------|------|------|
| 浏览器自动化 | Playwright (Python async API) | 控制 Chromium，操作豆包 SPA |
| 浏览器内核 | Chromium（Playwright 自带） | 渲染豆包页面，执行 JavaScript |
| Markdown 解析 | Python `markdown` 库 | md2pdf 的 Markdown → HTML 转换 |
| 音频处理 | FFmpeg（libmp3lame） | WAV → MP3 压缩 |
| 编程语言 | Python 3.10+ | 异步 I/O 编排 |

### 5.2 依赖库

```bash
pip install playwright markdown
python -m playwright install chromium
```

- **playwright**：微软出品，原生支持 `expect_download` 和 `expect_file_chooser`
- **markdown**：标准的 Markdown → HTML 解析器，支持表格、代码块等扩展

---

## 6. 文件结构

```
doubao-podcast-obsidian-bridge/
├── README.md                     # 本文件
├── DEV_LOG.md                    # 开发日志与踩坑记录
├── 豆包播客自动化下载与Obsidian绑定_任务总结.md   # 项目复盘
│
├── doubao_pipeline.py            # 【推荐入口】交互式流水线 (a生成 / b下载)
├── doubao_full.py                # 一键完整流程（扫描→下载→压缩→绑定）
├── scan_to_clipboard.py          # 自动扫描 md 文件并写入剪贴板
│
├── md2pdf.py                     # Markdown → PDF 转换（基于 Chromium 渲染）
├── doubao_uploader.py            # PDF 上传 + 点击"生成播客" + 等待完成
├── doubao_scanner.py             # 扫描页面所有播客（处理虚拟列表）
├── doubao_downloader.py          # 精确下载（按 PDF 名称定位 + 断点续传）
├── post_process.py               # FFmpeg 压缩 + 删除 WAV + 绑定 Markdown
│
├── pipeline_state.json           # 流水线状态（聊天URL、PDF列表、时间）
├── upload_progress.json          # 上传断点续传记录
├── md_mapping.json               # PDF → Markdown 精确路径映射
├── podcasts_list.json            # 扫描结果（播客清单）
├── doubao_state.json             # 登录态持久化（Cookie + LocalStorage）
├── paths.txt                     # 路径列表文件（供 scan_to_clipboard 生成）
│
├── doubao_debug/                 # 调试输出目录
│   ├── after_plus_click_*.png    # 点击"+"按钮后的截图
│   ├── upload_complete_*.png     # 上传完成截图
│   └── *.html                    # 页面 DOM 快照
│
└── 豆包播客代码上传与下载绑定记录.md   # 自动生成的操作记录（Obsidian 中查看）
```

---

## 7. 常见问题

### Q：浏览器打开后显示登录框

**A**：正常。首次运行请在浏览器内完成扫码/手机号登录，登录态会自动保存到 `doubao_state.json`。下次运行自动加载。

### Q：粘贴多个 Markdown 路径，程序只识别到一个

**A**：这是 Git Bash 的输入限制。请使用**剪贴板方式**：在资源管理器中选中文件 → `Ctrl + Shift + C` 复制路径 → 程序自动检测剪贴板内容。

### Q：上传时提示 "filechooser 流程失败: Target page has been closed"

**A**：md2pdf 和 uploader 都使用了 Playwright，两个浏览器实例可能冲突。脚本已在两者之间加入 2 秒等待。如果仍失败，请手动关闭所有 Chrome 进程后重试。

### Q："生成播客"按钮点击到了左侧历史对话

**A**：已修复。脚本现在采用"以 PDF 为中心"的定位策略，只在最新上传的 PDF 卡片内部查找"生成播客"按钮，完全排除 sidebar 区域。

### Q：pdf_output 文件夹里有旧的 PDF，程序把它们也传上去了

**A**：已修复。程序现在只收集**本次传入的 Markdown 对应的 PDF**（根据文件名匹配），不会混入旧文件。

### Q：下载文件被占用（PermissionError）

**A**：浏览器可能还未释放文件句柄。`doubao_downloader.py` 已内置重试逻辑（最多等 5 秒）。如仍失败，稍后手动移动文件即可。

### Q：FFmpeg 报错 `UnicodeDecodeError`

**A**：Windows 默认 GBK 编码导致。`post_process.py` 已内置修复（`encoding='utf-8', errors='ignore'`），无需操作。

### Q：绑定 Markdown 时提示"找不到对应 Markdown"

**A**：检查 Obsidian 库中的 Markdown 文件名是否与音频文件名一致。脚本已支持**模糊匹配**（音频名被包含在 Markdown 文件名中即可匹配，如 `指针与引用.mp3` 可匹配 `2.5 指针与引用.md`）。

### Q：嵌入的音频链接在 Obsidian 中打不开

**A**：检查链接路径。`post_process.py` 已改用动态相对路径计算（根据 Markdown 文件实际位置），旧版本硬编码的 `../附件/音频` 可能导致路径错误。重新运行 `post_process.py` 即可自动修正。

---

## 8. 更新日志

| 日期 | 版本 | 更新内容 |
|------|------|---------|
| 2026-05-23 | v1.0 | 初始版本：扫描 → 下载 → 压缩 → 绑定 |
| 2026-05-27 | v2.0 | 新增 PDF 上传 + 播客生成功能，创建交互式流水线入口 `doubao_pipeline.py` |
| 2026-05-28 | v2.1 | **剪贴板增强**：支持 CF_HDROP 原生文件格式；**失败自动跳过**：检测到"无法生成播客"立即跳过；**"未命名"过滤**；**scan_to_clipboard**；**等待优化**：PDF 间隔从 8 秒降至 1 秒 |
| 2026-05-28 | v2.2 | **批量上传模式**：去掉逐个轮询等待，快速上传全部 PDF 后统一检测；**页面自动滚动**：三层滚动策略确保"+"按钮可见；**上传记录**：批量写入 markdown 记录文件；**精确绑定**：`md_mapping.json` 避免同名文件冲突；**超时优化**：600 秒 → 120 秒 |

---

## 作者

- 开发：Kimi Code CLI（自动化编排）
- 需求与设计：用户 @deck
