#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_process.py
下载完成后执行：wav -> mp3 压缩，并绑定到对应 Markdown 文件

用法:
    python post_process.py
"""

import json
import re
import subprocess
import sys
from pathlib import Path

VAULT = Path.home() / "Documents" / "Obsidian" / "申论真题"
AUDIO_DIR = VAULT / "附件" / "音频"


def wav_to_mp3(wav_path: Path) -> Path:
    """用 ffmpeg 将 wav 压缩为 mp3"""
    mp3_path = wav_path.with_suffix(".mp3")
    if mp3_path.exists():
        print(f"[跳过] MP3已存在: {mp3_path.name}")
        return mp3_path
    
    cmd = [
        "ffmpeg", "-y", "-i", str(wav_path),
        "-codec:a", "libmp3lame", "-q:a", "2",
        str(mp3_path)
    ]
    print(f"[压缩] {wav_path.name} -> {mp3_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[错误] ffmpeg失败: {result.stderr[:200]}")
        return None
    
    print(f"[完成] {mp3_path.name}")
    return mp3_path


def find_md_file(stem: str) -> Path | None:
    """在Obsidian库中查找对应的md文件"""
    for md in VAULT.rglob(f"{stem}.md"):
        return md
    return None


def embed_podcast(md_path: Path, mp3_name: str):
    """在Markdown文件开头插入播客链接"""
    content = md_path.read_text(encoding="utf-8")
    
    # 计算相对路径
    rel_path = Path("../附件/音频") / mp3_name
    
    embed_block = f"> 🎧 **配套播客**（豆包 AI 生成）\n> [[{rel_path.as_posix()}]]\n\n"
    
    # 检查是否已嵌入
    if "配套播客" in content and mp3_name in content:
        print(f"[跳过] 已嵌入: {md_path.name}")
        return
    
    # 插入到文件开头
    new_content = embed_block + content
    md_path.write_text(new_content, encoding="utf-8")
    print(f"[嵌入] {md_path.name}")


def main():
    wav_files = sorted(AUDIO_DIR.glob("*.wav"))
    print(f"[信息] 发现 {len(wav_files)} 个 WAV 文件待处理\n")
    
    for wav in wav_files:
        stem = wav.stem
        
        # 1. 压缩为 MP3
        mp3 = wav_to_mp3(wav)
        if not mp3:
            continue
        
        # 2. 查找对应 Markdown
        md = find_md_file(stem)
        if not md:
            print(f"[跳过] 找不到对应Markdown: {stem}.md")
            continue
        
        # 3. 嵌入播客链接
        embed_podcast(md, mp3.name)
        
        print()
    
    print("=" * 60)
    print("全部处理完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
