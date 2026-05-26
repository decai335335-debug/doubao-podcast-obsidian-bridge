# 开发日志（Development Log）

> 记录从需求到落地的完整迭代过程，包括技术决策、踩坑记录、性能优化。

---

## 需求背景

用户希望在 Obsidian 知识库中管理豆包 AI 生成的播客音频。核心痛点：
1. 豆包网页**没有批量导出**功能
2. 下载的音频默认名是乱码（如 `AI播客_123456.wav`），与原始 PDF 无关
3. 需要手动把音频关联到对应的 Markdown 笔记

**目标**：给一个豆包聊天 URL，全自动完成：扫描 → 下载 → 命名 → 压缩 → 绑定。

---

## 技术选型

### 为什么选 Playwright 而不是 Selenium？

| 维度 | Playwright | Selenium |
|------|-----------|----------|
| 下载事件监听 | 原生 `page.expect_download()` | 需借助第三方库或代理 |
| 异步 API | 原生支持 `async/await` | 需额外封装 |
| 虚拟列表处理 | 可直接执行 JS 操作 DOM | 同样可以，但 API 更繁琐 |
| 安装成本 | `pip install playwright` + `playwright install` | 需单独下载 WebDriver |

**决策**：Playwright 的 `expect_download` 上下文管理器可以精准捕获浏览器下载事件，这是 Selenium 难以比拟的。

### 为什么不用 HTTP 直接下载？

豆包播客的音频 URL 是**动态生成的 blob/stream**，没有固定直链。且需要：
- 登录态 Cookie
- JavaScript 渲染后的会话状态
- 虚拟列表滚动后才能触发音频加载

因此必须走**浏览器自动化**路径。

---

## 迭代时间线

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

## 性能数据

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

---

## 踩坑记录

| # | 问题 | 根因 | 解决方案 |
|---|------|------|---------|
| 1 | `networkidle` 超时 | 豆包持续心跳请求 | 改用 `domcontentloaded` |
| 2 | `input()` 报错 | Shell 不支持交互式输入 | 改用 `asyncio.sleep()` |
| 3 | 下载按钮定位错误 | 误点"下载电脑版" | 用 `data-plugin-identifier` 精确到播客卡片 |
| 4 | 元素不在 DOM 中 | 虚拟列表只渲染视口内 | TreeWalker + 滚动触发 |
| 5 | 文件名截断 | 正则排除了空格 | 用 `根据 xxx.pdf 的内容` 精确匹配 |
| 6 | 下载超时 | 单任务 300 秒不够 | 分 batch + 断点续传 |
| 7 | PermissionError | 浏览器未释放文件句柄 | `shutil.move()` 前重试 5 秒 |
| 8 | FFmpeg 编码错误 | Windows GBK 终端 | 忽略 stderr 编码错误 |
| 9 | 后台任务中断 | Worker heartbeat 过期 | 缩短 batch 大小 |
| 10 | 终端乱码 | GBK 不支持 emoji/中文 | 关键结果保存为 JSON 文件 |

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

## 未来优化方向

1. **单浏览器会话内完成扫描+下载**：目前扫描和下载分两次打开浏览器，可优化为一次会话内先扫描再下载，减少登录态加载时间。
2. **并发下载**：Playwright 支持多个 `page` 共享一个 `context`，理论上可同时操作多个标签页下载。但豆包可能有反并发限制。
3. **更智能的虚拟列表滚动**：目前使用固定步长（600px）滚动，可根据 `scrollHeight` 动态调整。
4. **Headless 模式**：当前使用 `headless=False`（有头模式），方便调试。稳定后可改为无头模式，节省资源。
5. **GUI 界面**：用 `tkinter` 或 `gradio` 做一个简单的图形界面，显示下载进度。

---

## 关键代码片段

### 虚拟列表滚动 + 元素定位
```python
async def scroll_up(page):
    await page.evaluate("""
        () => {
            for (const c of document.querySelectorAll('div')) {
                const style = window.getComputedStyle(c);
                if ((style.overflowY === 'auto' || style.overflowY === 'scroll') 
                    && c.scrollHeight > c.clientHeight + 200) {
                    c.scrollTop = Math.max(0, c.scrollTop - 600);
                }
            }
        }
    """)
```

### 断点续传检查
```python
existing = list(AUDIO_DIR.glob(f"{target_name}.*"))
if existing:
    return True  # 已存在，跳过
```

### 下载事件监听
```python
async with page.expect_download(timeout=120000) as download_info:
    await btn.click()
download = await download_info.value
```

---

## 文件位置

```
C:\Users\15403\Documents\Obsidian\申论真题\代码\
```

所有脚本、配置文件、调试输出均在此目录下。
