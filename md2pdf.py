#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Markdown to PDF 转换工具
用法:
    python md2pdf.py <文件1.md> [文件2.md] ... [-o 输出目录]
    python md2pdf.py *.md
    python md2pdf.py              # 交互模式
"""

import argparse
import glob
import os
import sys
from pathlib import Path

# ============ 依赖检查 ============
def check_dependencies():
    missing = []
    try:
        import markdown
    except ImportError:
        missing.append("markdown")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        missing.append("playwright")
    
    if missing:
        print("❌ 缺少依赖，请先安装:")
        print(f"   pip install {' '.join(missing)}")
        print("   python -m playwright install chromium")
        print("\n或双击运行 install.bat")
        sys.exit(1)


check_dependencies()

import markdown
from playwright.sync_api import sync_playwright


# ============ CSS 样式 ============
CSS_STYLE = """
@page {
    size: A4;
    margin: 20mm 18mm 20mm 18mm;
}

* {
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, "Noto Sans",
                 "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
                 "WenQuanYi Micro Hei", sans-serif;
    font-size: 10.5pt;
    line-height: 1.75;
    color: #333;
    max-width: 100%;
    word-wrap: break-word;
}

/* 标题 */
h1, h2, h3, h4, h5, h6 {
    font-weight: 600;
    line-height: 1.35;
    margin-top: 1.6em;
    margin-bottom: 0.6em;
    color: #222;
    page-break-after: avoid;
}

h1 { font-size: 20pt; border-bottom: 2px solid #e8e8e8; padding-bottom: 0.3em; }
h2 { font-size: 16pt; border-bottom: 1px solid #e8e8e8; padding-bottom: 0.25em; }
h3 { font-size: 13pt; }
h4 { font-size: 11pt; }
h5 { font-size: 10.5pt; }
h6 { font-size: 10pt; color: #555; }

/* 段落和文字 */
p {
    margin: 0.8em 0;
    text-align: justify;
}

a {
    color: #0366d6;
    text-decoration: none;
}

strong { font-weight: 600; color: #222; }
em { font-style: italic; }
del { text-decoration: line-through; color: #888; }

/* 列表 */
ul, ol {
    padding-left: 2em;
    margin: 0.6em 0;
}

li {
    margin: 0.25em 0;
}

li > ul, li > ol {
    margin: 0.2em 0;
}

/* 代码 */
code {
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo,
                 "Courier New", "PingFang SC", "Microsoft YaHei", monospace;
    background: #f6f8fa;
    padding: 0.15em 0.4em;
    border-radius: 3px;
    font-size: 0.92em;
    color: #c7254e;
}

pre {
    background: #f6f8fa;
    padding: 1em;
    border-radius: 6px;
    overflow-x: auto;
    line-height: 1.5;
    margin: 1em 0;
    page-break-inside: avoid;
}

pre code {
    background: transparent;
    padding: 0;
    border-radius: 0;
    font-size: 0.88em;
    color: #333;
}

/* 表格 */
table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 0.95em;
    page-break-inside: avoid;
}

th, td {
    border: 1px solid #dfe2e5;
    padding: 0.5em 0.8em;
    text-align: left;
}

th {
    background: #f6f8fa;
    font-weight: 600;
}

tr:nth-child(even) {
    background: #fafbfc;
}

/* 引用块 */
blockquote {
    margin: 1em 0;
    padding: 0.5em 1em;
    border-left: 4px solid #dfe2e5;
    color: #555;
    background: #fafbfc;
    font-style: italic;
}

blockquote p {
    margin: 0.3em 0;
}

/* 分割线 */
hr {
    border: none;
    border-top: 1px solid #e1e4e8;
    margin: 1.5em 0;
}

/* 图片 */
img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 1em auto;
    page-break-inside: avoid;
}

/* 任务列表 */
input[type="checkbox"] {
    margin-right: 0.4em;
}

/* 打印优化 */
@media print {
    body { font-size: 10pt; }
    pre { white-space: pre-wrap; word-wrap: break-word; }
}
"""


def md_to_pdf(md_path: Path, output_dir: Path) -> bool:
    """将单个 Markdown 文件转换为 PDF"""
    md_path = md_path.resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 读取 Markdown
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
    except Exception as e:
        print(f"   ❌ 读取失败: {e}")
        return False
    
    # Markdown → HTML
    html_body = markdown.markdown(
        md_content,
        extensions=[
            "tables",
            "fenced_code",
            "toc",
            "nl2br",
        ],
    )
    
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{md_path.stem}</title>
<style>{CSS_STYLE}</style>
</head>
<body>
{html_body}
</body>
</html>"""
    
    # 临时 HTML 必须和原 md 同目录，这样相对路径图片才能正确加载
    temp_html = md_path.parent / f"._temp_{md_path.stem}.html"
    output_pdf = output_dir / f"{md_path.stem}.pdf"
    
    try:
        with open(temp_html, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        # Playwright 渲染 PDF
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(temp_html.as_uri())
            page.wait_for_load_state("networkidle")
            
            page.pdf(
                path=str(output_pdf),
                format="A4",
                margin={"top": "18mm", "right": "16mm", "bottom": "18mm", "left": "16mm"},
                print_background=True,
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=(
                    '<div style="font-size:9px; color:#999; width:100%; text-align:center; padding:0 20mm;">'
                    '<span class="pageNumber"></span> / <span class="totalPages"></span>'
                    "</div>"
                ),
            )
            browser.close()
        
        print(f"   ✅ {output_pdf.name}")
        return True
        
    except Exception as e:
        print(f"   ❌ 转换失败: {e}")
        return False
        
    finally:
        if temp_html.exists():
            try:
                temp_html.unlink()
            except Exception:
                pass


def parse_file_list(inputs):
    """解析文件列表，支持通配符"""
    files = []
    for item in inputs:
        item = item.strip().strip('"').strip("'")
        if not item:
            continue
        if "*" in item or "?" in item:
            matched = glob.glob(item)
            files.extend(matched)
        else:
            files.append(item)
    return files


def main():
    parser = argparse.ArgumentParser(
        description="Markdown 转 PDF 工具 — 基于 Chromium 渲染",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python md2pdf.py note.md
  python md2pdf.py doc/*.md -o D:/Output
  python md2pdf.py file1.md file2.md -o ./PDF
  python md2pdf.py              # 交互模式
        """,
    )
    parser.add_argument("files", nargs="*", help="Markdown 文件路径（支持多个、通配符）")
    parser.add_argument("-o", "--output", default=r"D:\Users\md2pdf", help="PDF 输出目录")
    args = parser.parse_args()
    
    output_dir = Path(args.output)
    file_list = args.files
    
    # 无参数 → 交互模式
    if not file_list:
        print("=" * 50)
        print("  📄 Markdown → PDF 转换工具")
        print("=" * 50)
        print(f"\n默认输出目录: {output_dir}")
        print("\n请输入 Markdown 文件路径")
        print("（支持多个，用空格分隔；支持 *.md 通配符）")
        print("可拖拽文件到窗口，然后按回车:\n")
        
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(0)
            
        if not user_input:
            print("未输入文件，退出。")
            sys.exit(0)
        file_list = user_input.split()
    
    # 解析通配符
    file_list = parse_file_list(file_list)
    
    if not file_list:
        print("未找到任何文件。")
        sys.exit(1)
    
    # 过滤有效文件
    valid_files = []
    for f in file_list:
        p = Path(f)
        if not p.exists():
            print(f"⚠️  跳过（不存在）: {f}")
            continue
        if p.suffix.lower() not in (".md", ".markdown"):
            print(f"⚠️  跳过（非 Markdown）: {f}")
            continue
        valid_files.append(p)
    
    if not valid_files:
        print("没有可转换的 Markdown 文件。")
        sys.exit(1)
    
    print(f"\n📁 输出目录: {output_dir}")
    print(f"📄 待转换: {len(valid_files)} 个文件\n")
    
    success = 0
    for idx, md_path in enumerate(valid_files, 1):
        print(f"[{idx}/{len(valid_files)}] {md_path.name}")
        if md_to_pdf(md_path, output_dir):
            success += 1
    
    print(f"\n{'=' * 50}")
    print(f"  完成: {success}/{len(valid_files)} 个文件成功转换")
    print(f"  输出: {output_dir}")
    print(f"{'=' * 50}")
    
    if success > 0 and len(valid_files) > 1:
        try:
            os.startfile(str(output_dir))
        except Exception:
            pass


if __name__ == "__main__":
    main()
