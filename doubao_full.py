#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doubao_full.py
一键完整流程：扫描 -> 下载 -> 压缩MP3 -> 删除WAV -> 绑定Markdown

用法:
    python doubao_full.py <豆包URL>
    例: python doubao_full.py https://www.doubao.com/chat/38427010004394498
"""

import subprocess
import sys
from pathlib import Path


def run_step(name, cmd):
    print(f"\n{'='*60}")
    print(f"【{name}】")
    print(f"{'='*60}")
    result = subprocess.run(cmd, shell=False)
    if result.returncode != 0:
        print(f"[警告] {name} 可能出现问题，继续下一步...")
    return result.returncode


def main():
    if len(sys.argv) >= 2:
        url = sys.argv[1]
    else:
        url = input("请输入豆包URL (例如 https://www.doubao.com/chat/xxx): ").strip()
        if not url:
            print("错误: 未提供URL，程序退出")
            sys.exit(1)
    script_dir = Path(__file__).parent
    
    # Step 1: 扫描
    run_step("1/4 扫描播客列表", ["python", str(script_dir / "doubao_scanner.py"), url])
    
    # Step 2: 下载全部
    run_step("2/4 下载全部播客", ["python", str(script_dir / "doubao_downloader.py"), url, "--all"])
    
    # Step 3: 压缩MP3 + 删除WAV + 绑定Markdown
    run_step("3/4 压缩MP3并绑定Markdown", ["python", str(script_dir / "post_process.py")])
    
    # Step 4: 清理WAV（确保删除）
    run_step("4/4 清理WAV文件", ["python", "-c", 
        "from pathlib import Path; [f.unlink() for f in (Path.home() / 'Documents/Obsidian/申论真题/附件/音频').glob('*.wav')]; print('WAV清理完成')"
    ])
    
    print(f"\n{'='*60}")
    print("全部完成！请检查:")
    print("  - MP3: 附件/音频/")
    print("  - Markdown绑定: 对应的 .md 文件开头")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
