# 开发日志（Development Log）

> 记录从需求到落地的完整迭代过程，包括技术决策、踩坑记录、性能优化。

---

## 1. 项目起源

### 1.1 原始需求

用户在日常使用豆包 AI 的"根据文档生成播客"功能时，积累了大量由 PDF 生成的播客音频。这些音频散落在豆包网页中，存在以下痛点：

- **无批量导出**：豆包网页没有提供批量下载功能，每个播客需要手动点击下载
- **命名混乱**：下载后的默认文件名（如 `AI播客_123456.wav`）与原始 PDF 无关，无法在文件系统中识别内容
- **知识孤岛**：Obsidian 知识库中已有对应 PDF 的 Markdown 笔记，但播客音频与笔记之间没有关联

### 1.2 任务目标

给定一个豆包聊天记录 URL，全自动完成以下流程：

```
扫描页面所有播客 -> 按原始PDF名称下载 -> WAV压缩为MP3 -> 删除WAV -> 绑定到对应Markdown
```

### 1.3 约束条件

- 豆包是 SPA（单页应用），使用虚拟列表，只渲染视口内的 DOM 元素
- 需要登录态才能访问聊天记录
- 音频文件体积大（WAV 平均 120MB）
- 最终交付物必须嵌入 Obsidian 的 WikiLink 语法

---

## 2. 迭代时间线

### Iteration 1：基础探测（失败）

**尝试**：用 `FetchURL` 直接抓取页面内容。  
**结果**：失败。豆包是 SPA，`FetchURL` 只能拿到空壳 HTML，没有聊天内容。

**教训**：任何需要 JS 渲染的页面，FetchURL 都不可用。

---

### Iteration 2：Playwright 打开页面（部分成功）

**尝试**：`page.goto(URL, wait_until="networkidle")`  
**结果**：超时 30 秒。豆包页面有大量持续的心跳请求，`networkidle` 永远无法满足。

**修复**：改为 `wait_until="domcontentloaded"`，加载完成后通过 `asyncio.sleep()` 等待 SPA 渲染。

---

### Iteration 3：登录交互（失败 → 成功）

**尝试**：用 `input()` 暂停脚本，让用户在浏览器里登录后按回车继续。  
**结果**：`EOFError: EOF when reading a line`。Kimi Code CLI 的 Shell 环境不支持交互式输入。

**修复**：改为固定时长的 `asyncio.sleep(60)`，在日志中提示"请在 60 秒内完成登录"。

**进一步优化**：使用 `context.storage_state()` 保存登录态到 `doubao_state.json`，后续运行自动加载，无需重复登录。

---

### Iteration 4：下载按钮定位（踩大坑）

**尝试**：用 `button:has-text("下载")` 定位。  
**结果**：点击了右上角的**"下载电脑版"**按钮（豆包客户端下载），而非播客音频的下载按钮。

**根因**：页面上有多个含"下载"文字的元素，优先级策略错误。

**修复**：
1. 改为查找 `class*="download"` 的元素
2. 但发现更深层的问题：**虚拟列表**

---

### Iteration 5：虚拟列表（Virtual List）攻坚

**现象**：Playwright 的 `page.locator("text=xxx.pdf")` 返回 `count=0`，但截图中明明能看到这段文字。

**根因分析**：
- 豆包使用 CSS Transform 实现虚拟列表（`data-observe-row` + `transform: translate(...)`）
- 只有视口附近的元素会被渲染到 DOM 中
- 不在视口内的元素被**完全移除**（不是 `display: none`，是 DOM 中不存在）

**解决方案演进**：

| 方案 | 结果 |
|------|------|
| `page.evaluate` + `document.querySelectorAll` | 只能找到当前视口内的 |
| `window.scrollTo` | 对虚拟列表无效，因为滚动是容器级别的 |
| 遍历所有 `div` 并修改 `scrollTop` | ✅ 有效，触发 React 重新渲染 |
| `document.createTreeWalker` 遍历文本节点 | ✅ 配合滚动后，能精确找到目标 |

**最终策略**：
```javascript
// 1. 遍历所有可滚动容器，向上滚动
for (const c of document.querySelectorAll('div')) {
    if (c.scrollHeight > c.clientHeight + 200) {
        c.scrollTop = Math.max(0, c.scrollTop - 600);
    }
}

// 2. 用 TreeWalker 查找目标 PDF 名称
const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
let node;
while (node = walker.nextNode()) {
    if (node.textContent.includes(searchText)) {
        // 3. 向上追溯到播客卡片
        let el = node.parentElement;
        for (let i = 0; i < 15; i++) {
            const pluginId = el.getAttribute('data-plugin-identifier');
            if (pluginId && pluginId.includes('receive-podcast-content')) {
                // 4. 点击卡片内的下载按钮
                el.querySelector('[class*="download"]').click();
            }
            el = el.parentElement;
        }
    }
}
```

---

### Iteration 6：精确绑定（关键设计）

**问题**：页面上有多个播客，如何确保点击的是"对应"的那个？

**方案**：以 **PDF 文件名** 为锚点。

每个播客卡片都有前置文本：
```
我将根据 xxx.pdf 的内容为你生成播客
```

绑定流程：
1. 输入目标 PDF 名称（如 `pm_deep_dive_sec01.pdf`）
2. 在浏览器内 TreeWalker 搜索该文本
3. 向上追溯到 `data-plugin-identifier="Symbol(receive-podcast-content)"`
4. 在该卡片内点击下载按钮

**为什么不用播客标题？**  
因为标题可能重复（如"任务管理"系列有多个），而 PDF 文件名是唯一的。

---

### Iteration 7：批量下载与超时

**问题**：52 个播客，单线程顺序下载，每次滚动查找很耗时。

**尝试1**：单任务下载全部 52 个，超时 300 秒。  
**结果**：只下载了约 24 个就超时了。

**尝试2**：后台任务，超时 1800 秒（30 分钟）。  
**结果**：因后台 worker heartbeat 过期，14 分钟后被中断。

**尝试3**：分 4 个 batch，每批 7 个，4 个后台任务并行。  
**结果**：大部分成功，但 batch 0 因 `PermissionError`（文件被浏览器占用）全部失败。

**修复**：在 `shutil.move()` 前增加重试循环：
```python
for retry in range(10):
    try:
        shutil.move(str(temp_path), str(final_audio))
        break
    except PermissionError:
        await asyncio.sleep(0.5)
```

**最终策略**：
- 分 batch 下载（每批 7 个）
- 断点续传：已存在的文件自动跳过
- 文件占用时等待 5 秒重试

---

### Iteration 8：文件名截断 Bug

**现象**：`多智能体协作调查：Agent 到底该怎么分工.pdf` 被命名为 `到底该怎么分工.wav`。

**根因**：正则表达式 `r'([^\/:*?"<>|\s]+\.pdf)'` 中 `\s` 排除了空格。PDF 名称中的 `Agent `（带空格）导致匹配被截断。

**修复**：扫描时改用更精确的正则：
```python
re.search(r'根据\s+(.+?\.pdf)\s+的内容', preview_text)
```

---

### Iteration 9：后处理（压缩 + 绑定）

**问题**：WAV 文件体积巨大（平均 100MB+），Obsidian 直接嵌入 WAV 不友好。

**方案**：FFmpeg 压缩为 MP3
```bash
ffmpeg -i input.wav -codec:a libmp3lame -q:a 2 output.mp3
```
- `-q:a 2`：VBR 质量等级 2，约 190kbps，体积约为 WAV 的 1/5

**编码陷阱**：  
Windows 终端默认 GBK 编码，FFmpeg 输出包含非 ASCII 字符时，`subprocess.run(capture_output=True)` 会抛出 `UnicodeDecodeError`。

**修复**：放弃捕获 stdout/stderr，或设置 `encoding='utf-8', errors='ignore'`。

---

### Iteration 10：Markdown 绑定格式

**用户指定格式**：
```markdown
> 🎧 **配套播客**（豆包 AI 生成）
> [[../附件/音频/文件名.mp3]]
```

**实现逻辑**：
1. 遍历 `附件/音频/*.wav`
2. 用 `Path.stem` 作为关键词，在 Obsidian Vault 内 `rglob(f"{stem}.md")` 搜索对应文件
3. 检查文件开头是否已包含 `"配套播客"`，避免重复插入
4. 插入到文件最开头（`new_content = embed_block + old_content`）

---

### Iteration 11：一键整合

**问题**：用户需要运行 3 个脚本，步骤繁琐。

**方案**：`doubao_full.py` 包装器
```python
subprocess.run(["python", "doubao_scanner.py", url])
subprocess.run(["python", "doubao_downloader.py", url, "--all"])
subprocess.run(["python", "post_process.py"])
```

**为什么不真正整合到一个文件？**  
扫描、下载、后处理三个阶段的最佳实践不同：
- 扫描需要长时间滚动，浏览器保持打开
- 下载需要监听下载事件，需独占浏览器
- 后处理是纯本地文件操作，无需浏览器

分阶段执行更稳定，且便于调试和断点续传。

---

### Iteration 12：新电脑迁移兼容性修复（2026-05-27）

将工具链迁移到新电脑后，原以为"拷过来就能跑"，结果连续触发 5 个兼容性问题。

#### 12.1 FFmpeg GBK 编码崩溃

**现象**：`post_process.py` 执行 FFmpeg 时崩溃：`UnicodeDecodeError: 'gbk' codec can't decode byte 0xae`。

**根因**：新电脑 Windows 默认代码页 `cp936`（GBK），`subprocess.run(text=True)` 用 GBK 解码 ffmpeg 输出。ffmpeg 8.1.1 的输出中包含 UTF-8 特殊字符 `®`（字节 `0xae`），GBK 无法解码。

**修复**：显式声明编码
```python
subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
```

#### 12.2 Markdown 绑定失败 —— 文件名前缀丢失

**现象**：7 个 C++ 播客全部绑定失败。

**根因**：`doubao_scanner.py` 的正则 `[^\/:*?"<>|\s]+\.pdf` 排除了空格，导致 `2.5 指针与引用.pdf` 只匹配到 `指针与引用.pdf`。下载后的音频叫 `指针与引用.mp3`，但 Obsidian 中的 Markdown 叫 `2.5 指针与引用.md`，`post_process.py` 的精确匹配找不到。

**修复**：
1. 扫描器正则去掉 `\s` 限制：`[^\/:*?"<>|]+\.pdf`
2. `find_md_file()` 增加模糊匹配：stem 被包含在 md 文件名中即可匹配

#### 12.3 相对路径硬编码

**现象**：嵌入的链接是 `[[../附件/音频/xxx.mp3]]`，但 Markdown 在根目录，正确路径应为 `[[附件/音频/xxx.mp3]]`。

**修复**：`embed_podcast()` 改用 `os.path.relpath()` 动态计算
```python
rel_path = Path(os.path.relpath(mp3_path, md_path.parent)).as_posix()
```

#### 12.4 已有 MP3 被忽略

**现象**：第一次运行后 WAV 已删除，第二次运行时 `post_process.py` 显示"0 个 WAV"后直接退出，不绑定已有 MP3。

**修复**：`main()` 分两段：先处理 WAV（压缩+绑定），再处理已有 MP3（仅绑定）。

#### 12.5 终端输出乱码

**现象**：Git Bash 中所有中文输出为 `����`。

**说明**：Python 3.14 + Git Bash 的 stdout 编码不匹配。不影响文件操作，属显示问题。

---

### Iteration 13：PDF 上传 + 播客生成（新增功能，2026-05-27）

**需求**：用户已有 md2pdf 工具生成 PDF，需要在豆包网页中批量上传这些 PDF 并触发播客生成。流程为：在同一个聊天窗口内逐个上传 PDF → 点击"生成播客" → 等待播客生成完成 → 处理下一个 PDF。

#### 13.1 初始方案失败

**尝试**：上传 PDF 后，在输入框填入提示词并发送，让豆包根据提示词生成播客。  
**结果**：用户纠正——不需要输入提示词，上传 PDF 后页面会自动显示"生成播客"按钮，点击即可。

**教训**：必须先了解用户的实际操作流程，不能假设交互方式。

#### 13.2 "+" 按钮点击失败

**尝试**：用 `element.click()` 点击底部 "+" 按钮。  
**结果**：`element.click()` 返回成功，但菜单没有弹出。截图显示页面完全无变化。

**根因**：豆包的 "+" 按钮是 SVG 图标（无文字），且事件监听在 `pointerdown`/`pointerup` 而非标准 `click`。Playwright 的 `element.click()` 只派发 `click` 事件，没有触发真正的鼠标交互。

**修复**：改用真实鼠标序列：
```python
await page.mouse.move(cx, cy)
await asyncio.sleep(0.2)
await page.mouse.down()
await asyncio.sleep(0.1)
await page.mouse.up()
```

#### 13.3 选择器漂移问题

**现象**：第一个 PDF 上传成功后，第二个 PDF 的 `query_selector('#input-engine-container button')` 返回了完全不同的坐标（1151, 586 而不是 410, 676）。

**根因**：页面在生成播客后 DOM 结构可能变化，固定选择器不可靠。

**修复**：放弃固定选择器，每次上传前用 JS **实时几何探测**：
```javascript
const allBtns = document.querySelectorAll('button');
const candidates = [...allBtns].filter(btn => {
    const rect = btn.getBoundingClientRect();
    return rect.bottom > window.innerHeight * 0.88 &&
           rect.width > 20 && rect.height > 20 &&
           rect.width < 60 && rect.height < 60;
});
candidates.sort((a, b) => a.left - b.left); // 取最左侧的
```

#### 13.4 filechooser 捕获失败

**尝试**：先点击"上传文件或图片"，然后调用 `page.expect_file_chooser()`。  
**结果**：`expect_file_chooser` 超时，因为 filechooser 事件在监听开始之前就已经弹出了。

**修复**：`expect_file_chooser` 的上下文管理器必须**包含**触发 filechooser 的操作：
```python
async with page.expect_file_chooser(timeout=10000) as fc_info:
    await upload_button.click()  # 在上下文内触发
file_chooser = await fc_info.value
await file_chooser.set_files(str(pdf_path))
```

#### 13.5 "生成播客"误点左侧 sidebar

**现象**：`page.get_by_text("生成播客")` 匹配到了左侧历史对话列表中的"根据文档生成播客"，点击后进入了错误的聊天窗口。

**根因**：搜索范围是整个页面，包含了 sidebar。

**修复**：改为"以 PDF 为中心"的定位策略：
1. 找到最新上传的 PDF 文本节点
2. 向上追溯到消息卡片容器
3. 只在**该容器内部**查找"生成播客"按钮
4. 兜底策略：限制 `rect.left > 320` 排除 sidebar

#### 13.6 播客生成检测误报

**现象**：脚本"成功"了，但播客卡片是页面上已有的，不是新生成的。

**根因**：`wait_for_podcast` 从 0 开始计数，页面加载时就已有 3 个播客卡片，被误判为新生成。

**修复**：在点击"生成播客"前先记录**基准卡片数量**，只检测是否有**新增**的卡片：
```python
base_count = len(await page.query_selector_all('[data-plugin-identifier*="receive-podcast-content"]'))
# 等待过程中检测 current_count > base_count
```

#### 13.7 最终成果

- 在同一个聊天窗口内成功逐个上传 2 个 PDF
- 每个 PDF 上传后正确点击"生成播客"
- 播客生成完成后等待 8 秒再处理下一个
- 全程无需人工干预

---

## 3. 踩坑记录

| # | 问题 | 根因 | 解决方案 | 涉及版本 |
|---|------|------|---------|---------|
| 1 | `networkidle` 超时 | 豆包持续心跳请求 | 改用 `domcontentloaded` | v1.0 |
| 2 | `input()` 报错 | Shell 不支持交互式输入 | 改用 `asyncio.sleep()` | v1.0 |
| 3 | 下载按钮定位错误 | 误点"下载电脑版" | 用 `data-plugin-identifier` 精确到播客卡片 | v1.0 |
| 4 | 元素不在 DOM 中 | 虚拟列表只渲染视口内 | TreeWalker + 滚动触发 | v1.0 |
| 5 | 文件名截断 | 正则排除了空格 | 用 `根据 xxx.pdf 的内容` 精确匹配 | v1.0 |
| 6 | 下载超时 | 单任务 300 秒不够 | 分 batch + 断点续传 | v1.0 |
| 7 | PermissionError | 浏览器未释放文件句柄 | `shutil.move()` 前重试 5 秒 | v1.0 |
| 8 | FFmpeg 编码错误 | Windows GBK 终端 | 忽略 stderr 编码错误 | v1.0 |
| 9 | 后台任务中断 | Worker heartbeat 过期 | 缩短 batch 大小 | v1.0 |
| 10 | 终端乱码 | GBK 不支持 emoji/中文 | 关键结果保存为 JSON 文件 | v1.0 |
| 11 | GBK 编码崩溃 | 新电脑默认 cp936 | `encoding='utf-8', errors='ignore'` | v1.1 |
| 12 | 文件名前缀丢失 | 正则 `\s` 截断 | 去掉 `\s` 限制 + 模糊匹配 | v1.1 |
| 13 | 相对路径硬编码 | `../附件/音频` 写死 | `os.path.relpath()` 动态计算 | v1.1 |
| 14 | 已有 MP3 被忽略 | 只处理 WAV | 分两段：处理 WAV + 处理已有 MP3 | v1.1 |
| 15 | "+" 按钮点击失败 | `element.click()` 不触发菜单 | `mouse.move/down/up` 真实鼠标序列 | v2.0 |
| 16 | 选择器漂移 | DOM 变化导致坐标错误 | 每次实时几何探测 | v2.0 |
| 17 | filechooser 超时 | 监听在触发之后 | `expect_file_chooser` 包含触发操作 | v2.0 |
| 18 | "生成播客"误点 sidebar | 搜索范围是整个页面 | "以 PDF 为中心"的定位策略 | v2.0 |
| 19 | 播客检测误报 | 从 0 计数，已有卡片被误认 | 记录基准值，检测新增 | v2.0 |
| 20 | 剪贴板多行粘贴 | `input()` 只读一行 | `tkinter` 读取 Windows 剪贴板 | v2.0 |
| 21 | PDF 混入旧文件 | `glob("*.pdf")` 收集全部 | 根据传入的 Markdown 文件名匹配对应 PDF | v2.0 |

---

## 4. 设计决策

### 4.1 为什么选 Playwright 而不是 Selenium？

| 维度 | Playwright | Selenium |
|------|-----------|----------|
| 下载事件监听 | 原生 `page.expect_download()` | 需借助第三方库或代理 |
| 异步 API | 原生支持 `async/await` | 需额外封装 |
| 虚拟列表处理 | 可直接执行 JS 操作 DOM | 同样可以，但 API 更繁琐 |
| 安装成本 | `pip install playwright` + `playwright install` | 需单独下载 WebDriver |

**决策**：Playwright 的 `expect_download` 和 `expect_file_chooser` 上下文管理器可以精准捕获浏览器事件，这是 Selenium 难以比拟的。

### 4.2 为什么不用 HTTP 直接下载？

豆包播客的音频 URL 是**动态生成的 blob/stream**，没有固定直链。且需要：
- 登录态 Cookie
- JavaScript 渲染后的会话状态
- 虚拟列表滚动后才能触发音频加载

因此必须走**浏览器自动化**路径。

### 4.3 为什么分阶段执行而不是一个文件？

扫描、下载、后处理三个阶段的最佳实践不同：
- 扫描需要长时间滚动，浏览器保持打开
- 下载需要监听下载事件，需独占浏览器
- 后处理是纯本地文件操作，无需浏览器

分阶段执行更稳定，且便于调试和断点续传。

### 4.4 为什么以 PDF 文件名为锚点？

每个播客卡片上方都有固定文本：
```
我将根据 xxx.pdf 的内容为你生成播客
```

PDF 文件名在整个页面中唯一，比标题或 class 选择器更稳定。

---

## 5. 实际测试数据

### 5.1 下载阶段

| 指标 | 数值 |
|------|------|
| 总播客数 | 52 个 |
| 成功下载 | 51 个（98%）|
| 单文件平均下载时间 | 约 15-30 秒（含滚动定位）|
| 全部下载耗时 | 约 30-40 分钟（分 4 批并行）|
| WAV 平均体积 | 约 120MB |
| MP3 平均体积 | 约 25MB |
| 压缩率 | 约 79% |
| 绑定成功率 | 50/52（2 个因无对应 MD 文件失败）|

### 5.2 上传生成阶段

| 指标 | 数值 |
|------|------|
| 单次上传 PDF | 2 个 |
| "+" 按钮点击成功率 | 100%（第 3 次迭代后）|
| 文件上传成功率 | 100% |
| "生成播客"点击准确率 | 100%（以 PDF 为中心定位后）|
| 播客生成检测准确率 | 100%（基准值策略后）|
| PDF 间等待时间 | 1 秒（从 8 秒优化） |

---

## 6. 文件位置

```
D:\GitHubDownloads\doubao-podcast-obsidian-bridge\
├── README.md                      # 项目说明
├── DEV_LOG.md                     # 本文件
├── 豆包播客自动化下载与Obsidian绑定_任务总结.md   # 项目复盘
├── doubao_pipeline.py             # 【推荐入口】交互式流水线
├── doubao_full.py                 # 一键完整流程
├── scan_to_clipboard.py           # 自动扫描 md → 剪贴板
├── md2pdf.py                      # Markdown → PDF
├── doubao_uploader.py             # PDF 上传 + 播客生成
├── doubao_scanner.py              # 扫描页面播客
├── doubao_downloader.py           # 精确下载
├── post_process.py                # 压缩 + 绑定
├── pipeline_state.json            # 流水线状态
├── upload_progress.json           # 上传断点续传
├── podcasts_list.json             # 扫描结果
├── doubao_state.json              # 登录态
├── paths.txt                      # 路径列表
└── doubao_debug/                  # 调试截图
```

---

## 7. 新增迭代（2026-05-28）

### Iteration 14：剪贴板长路径截断问题

**问题**：从文件资源管理器复制多个文件后，脚本通过 `tkinter.clipboard_get()` 读取路径，包含 `🤖`、`🎮` 等 emoji 的长路径被截断，导致大量文件找不到。

**根因**：`tkinter` 的剪贴板读取使用 Tcl 字符串处理，对 Unicode 代理对（surrogate pair）计算长度错误。Windows 资源管理器 `Ctrl+C` 复制文件时，剪贴板里有 `CF_HDROP`（原生文件格式）和 `CF_UNICODETEXT`（文本格式）两种数据，文本格式在 emoji 路径上不可靠。

**修复**：
1. 新增 `_get_paths_from_clipboard_hdrop()`，使用 `ctypes` 直接调用 Windows API 读取 `CF_HDROP` 格式
2. 读取优先级：`CF_HDROP` > `pyperclip` > `win32clipboard` > `tkinter`
3. `CF_HDROP` 是 Windows 原生文件列表格式，路径完整，不受 emoji 影响

### Iteration 15：播客生成失败导致卡死

**问题**：豆包偶尔返回"抱歉，暂时无法生成播客"，但脚本继续等待播客卡片出现，直到 600 秒超时。

**修复**：在 `wait_for_podcast()` 轮询循环中，每 3 秒检测一次页面 body 文本，匹配失败关键词（"抱歉，暂时无法生成播客"、"服务繁忙"、"生成失败"等），一旦匹配立即返回失败，主流程自动跳过该文件处理下一个。

### Iteration 16："未命名"文件过滤

**问题**：Obsidian 自动创建的 `未命名.md`、`未命名 6.md` 等文件被误传入流水线，生成无意义播客。

**修复**：在 `doubao_pipeline.py` 和 `scan_to_clipboard.py` 的文件验证环节，增加 `if "未命名" in p.stem: skip` 过滤。

### Iteration 17：PDF 间隔等待优化

**问题**：处理完一个 PDF 后等待 8 秒再处理下一个，批量处理时浪费时间。

**修复**：实测 1 秒足够页面稳定，将 `await asyncio.sleep(8)` 改为 `await asyncio.sleep(1)`。

### Iteration 18：scan_to_clipboard 工具

**新增**：`scan_to_clipboard.py` 自动扫描指定目录下今天 12:00 后新增的 md 文件，将完整路径直接写入 Windows 剪贴板。用户无需手动复制路径，运行一个命令即可。

---

## 8. 重大重构（2026-05-28 晚）

### Iteration 19：pipeline 直接控制浏览器生命周期

**问题**：`doubao_pipeline.py` 的 `[a]生成播客` 模式通过 `subprocess.run([python, doubao_uploader.py, ...])` 调用上传器。子进程是"黑盒"，pipeline 无法干预浏览器行为（如页面滚动）。

**重构**：
1. `doubao_pipeline.py` 直接 `from doubao_uploader import upload_pdf, click_generate_podcast, wait_for_podcast, ensure_chat_open`
2. 新增 `async def run_generate_flow(pdf_files)` 函数，pipeline 自己管理 `async_playwright()` 上下文
3. 新增 `async def scroll_to_bottom(page)`，采用三层滚动策略：
   - 循环滚动 `body` / `documentElement`，检测 `scrollTop` 是否稳定
   - 找到所有 `overflow-y: auto/scroll` 的容器并滚到底
   - 模拟 `End` 键兜底

**效果**：pipeline 可以直接在每个 PDF 之间插入滚动、等待等自定义逻辑，不再受限于子进程接口。

---

### Iteration 20：虚拟滚动导致播客检测卡住

**问题**：处理到第 5 个 PDF 时，`wait_for_podcast()` 卡住 120 秒超时。日志显示基准数是 4，但 `current_count` 始终不大于 4。

**根因**：豆包页面使用虚拟滚动，当播客数量超过 4 个后，新播客生成的同时，最旧的一个被从 DOM 中卸载。DOM 中始终只保留 4 个卡片：
```
生成前 DOM: [播客1, 播客2, 播客3, 播客4]  → count=4
生成后 DOM: [播客2, 播客3, 播客4, 播客5]  → count=4
```
`current_count (4)` 不大于 `base_count (4)`，轮询永远不满足退出条件。

**用户验证**：手动滑动页面后，程序立即继续。证明虚拟滚动在用户交互后才加载新卡片。

**方案演进**：

| 方案 | 结果 |
|------|------|
| 在 `wait_for_podcast` 轮询中加入 `window.scrollTo` | 仍卡住，因为虚拟滚动不是 body 滚动 |
| 传入 `expected_base_count` 参数避免 DOM 计数 | 基准数对了，但 `current_count` 仍不大于它 |
| **彻底去掉逐个轮询，改为批量上传+最后统一检测** | ✅ 解决 |

**最终方案**：
1. `run_generate_flow()` 中不再调用 `wait_for_podcast()` 逐个等待
2. 点击"生成播客"后，固定等待 10 秒（给豆包时间开始生成），然后 `scroll_to_bottom()`
3. 直接处理下一个 PDF
4. 全部上传完成后，统一等待 30 秒，然后一次性获取播客卡片总数

**效果**：27 个 PDF 批量上传，每个间隔约 18 秒，全程无卡顿。最后统一检测时，播客卡片总数与上传数量一致。

---

### Iteration 21：上传/下载绑定记录

**需求**：用户希望有一个持续更新的 markdown 文件，记录每次上传了哪些 PDF、下载绑定了哪些 MP3，方便追溯。

**实现**：
1. 记录文件：`C:/Users/.../申论真题/总报告/豆包播客代码上传与下载绑定记录.md`
2. `doubao_pipeline.py` 中：所有 PDF 上传完成后，调用 `write_batch_upload_records()` 一次性批量写入
3. `post_process.py` 中：每次 MP3 绑定到 Markdown 成功后，调用 `append_download_bind_record()` 追加记录

**格式**：
```markdown
### [批量上传] 2026-05-28 23:10:33
- **聊天链接**: https://www.doubao.com/chat/...
- **文件数量**: 13 个
- **文件列表**:
  - `xxx.pdf`
  - `yyy.pdf`
- **状态**: ✅ 全部上传成功
```

---

### Iteration 22：Markdown 路径精确映射（解决同名冲突）

**问题**：Obsidian 知识库中不同文件夹可能有同名 `.md` 文件（如 `A/plan.md` 和 `B/plan.md`）。`post_process.py` 的 `rglob(f"{stem}.md")` 会找到第一个匹配，可能绑定到错误的文件。

**实现**：
1. `doubao_pipeline.py` 在 `[a]生成播客` 时，保存 `md_mapping.json`：
   ```json
   {
     "plan.pdf": "C:/Users/.../A/plan.md",
     "plan_1.pdf": "C:/Users/.../B/plan.md"
   }
   ```
2. `post_process.py` 的 `find_md_file()` 优先读取 `md_mapping.json`，用完整路径直接定位
3. 没有映射时才回退到 `rglob` 模糊匹配

**效果**：同名文件在不同文件夹时，绑定准确率从"可能错误"提升到 100%。

---

### Iteration 23：Windows 终端 GBK 编码崩溃

**问题**：`post_process.py` 处理包含 emoji 的文件名（如 `🎮 从零写...mp3`）时，`print()` 抛出 `UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f3ae'`。

**根因**：Windows 默认终端代码页是 `cp936`（GBK），不支持 Unicode emoji 字符。当 Python 的 `sys.stdout` 用 GBK 编码输出 emoji 时直接崩溃。

**修复**：
```python
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
```

将 stdout/stderr 重新包装为 UTF-8 编码，`errors='replace'` 会把无法编码的字符替换成 `?`，不会崩溃。

---

### Iteration 24：绑定记录格式重构

**问题**：每次绑定成功就追加一个独立的 `### [下载绑定]` 条目，记录文件迅速膨胀成几十个小块，难以阅读。

**重构**：
1. `embed_podcast()` 中不再调用 `append_download_bind_record()` 追加独立条目
2. `main()` 中收集所有成功绑定的 stems 到 `bound_stems` 列表
3. 全部绑定完成后，统一调用 `mark_upload_records_as_bound(bound_stems)`
4. 在记录文件的最近一次 `[批量上传]` 块中，给对应文件行内添加 `✅ 绑定成功`

**效果**：
```markdown
### [批量上传] 2026-05-28 23:52:36
- **文件列表**:
  - [[xxx]] ✅ 绑定成功
  - [[yyy]] ✅ 绑定成功
```

所有状态一目了然，不再有一堆分散的小块。

---

## 9. 踩坑记录（补充）

| # | 问题 | 根因 | 解决方案 | 涉及版本 |
|---|------|------|---------|---------|
| 22 | 播客检测卡住 | 虚拟滚动卸载旧卡片，DOM 总数不变 | 去掉逐个轮询，批量上传+最后统一检测 | v2.2 |
| 23 | 上传记录卡顿 | 逐个 `open(..., 'a')` 写文件 | 收集到列表，最后一次性写入 | v2.2 |
| 24 | 同名 MD 绑定错误 | `rglob` 找到第一个匹配 | `md_mapping.json` 精确路径映射 | v2.2 |
| 25 | 600 秒超时太长 | 播客实际 10-20 秒生成 | 改为 120 秒 | v2.2 |
| 26 | GBK 编码崩溃 | Windows 终端默认 cp936 | stdout 重包装为 UTF-8 | v2.3 |
| 27 | 记录文件膨胀 | 逐个追加 `[下载绑定]` 小块 | 统一在批量上传块内标记 | v2.3 |

---

## 10. 未来优化方向

1. **单浏览器会话内完成扫描+下载**：目前扫描和下载分两次打开浏览器，可优化为一次会话内先扫描再下载，减少登录态加载时间。
2. **并发下载**：Playwright 支持多个 `page` 共享一个 `context`，理论上可同时操作多个标签页下载。但豆包可能有反并发限制。
3. **更智能的虚拟列表滚动**：目前使用固定步长（600px）滚动，可根据 `scrollHeight` 动态调整。
4. **Headless 模式**：当前使用 `headless=False`（有头模式），方便调试。稳定后可改为无头模式，节省资源。
5. **GUI 界面**：用 `tkinter` 或 `gradio` 做一个简单的图形界面，显示下载进度。
