# 🚀 自主运行指南（不用找 Kimi）

> 本文档告诉你：**什么情况下可以自己跑，什么情况下需要找 Kimi。**

---

## 一、先确认环境（只需做一次）

打开 Git Bash，依次执行：

```bash
# 1. 检查 Python
python --version
# 应该显示 Python 3.10+，如果报错，说明环境有问题

# 2. 检查 Playwright
python -c "import playwright; print(playwright.__version__)"
# 应该显示版本号，如 1.59.0

# 3. 检查 FFmpeg
ffmpeg -version | head -1
# 应该显示 ffmpeg 版本信息
```

如果以上都通过，环境 OK。

---

## 二、日常使用流程

### 情况 A：你已经下载过，想更新新增的几个播客

```bash
cd "C:/Users/15403/Documents/Obsidian/申论真题/代码/doubao-podcast-obsidian-bridge"

# 直接跑下载（会自动跳过已下载的）
python doubao_downloader.py "https://www.doubao.com/chat/你的URL" --all

# 然后跑后处理（压缩+绑定）
python post_process.py
```

### 情况 B：全新对话，从头开始

```bash
cd "C:/Users/15403/Documents/Obsidian/申论真题/代码/doubao-podcast-obsidian-bridge"

# 一键全流程
python doubao_full.py "https://www.doubao.com/chat/你的URL"
```

### 情况 C：只想下载某几个特定文件

```bash
python doubao_downloader.py "URL" "文件名1.pdf" "文件名2.pdf"
```

---

## 三、登录态过期了怎么办？

**症状**：浏览器打开后显示登录框/二维码，不是直接进聊天页面。

**解决**：自己刷新登录态，不需要找 Kimi！

```bash
cd "C:/Users/15403/Documents/Obsidian/申论真题/代码/doubao-podcast-obsidian-bridge"
python doubao_login.py
```

操作步骤：
1. 会打开一个浏览器窗口
2. 用豆包 App 扫码登录（或手机号登录）
3. 登录成功后，回到终端按 **回车**
4. 登录态自动保存，后续脚本可以直接使用

---

## 四、常见问题自己排查

### Q1：报错 `doubao_state.json` 找不到？
```bash
# 首次运行，先执行登录
python doubao_login.py
```

### Q2：下载特别慢，或者卡住不动？
这是豆包服务器问题，不是你电脑问题。按 `Ctrl+C` 中断，重新运行下载命令即可（会自动跳过已下载的）。

### Q3：报错 `Download.save_as: canceled`？
这是豆包端主动取消了下载（原因不明，可能是文件太大或服务器限制）。
**解决**：换个时间再试，或者手动在豆包网页上点击下载。

### Q4：绑定 Markdown 时报"找不到对应文件"？
说明你 Obsidian 库里没有和 PDF 同名的 `.md` 文件。
**解决**：先创建对应 Markdown 文件，或者检查文件名是否一致（注意空格）。

### Q5：终端显示乱码？
正常现象，不影响功能。关键结果会保存到 JSON 文件：
- `podcasts_list.json` — 扫描结果
- `embed_report.json` — 绑定结果

---

## 五、什么情况下**必须**找 Kimi？

| 现象 | 原因 | 需要 Kimi？ |
|------|------|------------|
| 登录态过期 | Cookie 到期 | ❌ 自己运行 `doubao_login.py` |
| 下载慢/卡住 | 网络问题 | ❌ 重试即可 |
| 找不到下载按钮 | 豆包改版，DOM 结构变了 | ✅ **必须找我** |
| 扫描不到播客 | 豆包改版，虚拟列表逻辑变了 | ✅ **必须找我** |
| 脚本报错看不懂 | Python/Playwright 问题 | ⚠️ 先发我报错信息 |

### 判断是否需要找 Kimi 的简单方法：

运行 `python doubao_scanner.py` 后，检查 `doubao_debug/scan_final.png`：
- 如果截图里**能看到播客卡片** → 自己重试下载即可
- 如果截图里**看不到播客**或**页面结构明显变了** → 找我

---

## 六、进阶：让 Windows 自动跑（可选）

如果你希望完全无人值守，可以创建一个 `.bat` 文件：

```batch
@echo off
cd /d "C:\Users\15403\Documents\Obsidian\申论真题\代码\doubao-podcast-obsidian-bridge"
python doubao_full.py "https://www.doubao.com/chat/你的URL"
pause
```

双击即可运行。

---

## 七、总结

```
日常自己跑：
    python doubao_full.py <URL>
    
登录过期：
    python doubao_login.py
    
页面改版/脚本失效：
    → 找 Kimi
```

**核心原则**：只要浏览器打开后能看到正常的豆包聊天页面，你就能自己跑。如果页面看起来跟之前不一样了，再找我。
