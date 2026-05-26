#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_process.py
下载完成后执行：wav -> mp3 压缩，并绑定到对应 Markdown 文件

用法:
    python post_process.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

VAULT = Path.home() / "Documents" / "Obsidian" / "申论真题"
AUDIO_DIR = VAULT / "附件" / "音频"


# 自动定位 ffmpeg（支持 scoop 等自定义安装路径）
FFMPEG_PATH = shutil.which("ffmpeg")
if not FFMPEG_PATH:
    # 尝试常见路径
    for candidate in [
        Path.home() / "scoop" / "shims" / "ffmpeg.exe",
        Path("C:/") / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path("C:/") / "Program Files" / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]:
        if candidate.exists():
            FFMPEG_PATH = str(candidate)
            break


def wav_to_mp3(wav_path: Path) -> Path:
    """用 ffmpeg 将 wav 压缩为 mp3"""
    mp3_path = wav_path.with_suffix(".mp3")
    if mp3_path.exists():
        print(f"[跳过] MP3已存在: {mp3_path.name}")
        return mp3_path
    
    if not FFMPEG_PATH:
        print("[错误] 找不到 ffmpeg，请先安装 ffmpeg 并确保它在 PATH 中")
        print("       推荐: scoop install ffmpeg")
        return None
    
    cmd = [
        FFMPEG_PATH, "-y", "-i", str(wav_path),
        "-codec:a", "libmp3lame", "-q:a", "2",
        str(mp3_path)
    ]
    print(f"[压缩] {wav_path.name} -> {mp3_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
    if result.returncode != 0:
        print(f"[错误] ffmpeg失败: {result.stderr[:200]}")
        return None
    
    print(f"[完成] {mp3_path.name}")
    return mp3_path


def find_md_file(stem: str) -> Path | None:
    """在Obsidian库中查找对应的md文件，支持模糊匹配"""
    # 1. 精确匹配
    for md in VAULT.rglob(f"{stem}.md"):
        return md
    
    # 2. 模糊匹配：stem 被包含在 md 文件名中
    # 例如 stem="指针与引用" 能匹配到 "2.5 指针与引用.md"
    candidates = []
    for md in VAULT.rglob("*.md"):
        if stem in md.stem:
            candidates.append(md)
    
    if candidates:
        if len(candidates) == 1:
            return candidates[0]
        # 多个候选时优先匹配 stem 在末尾的（如 "2.5 指针与引用"）
        for md in candidates:
            if md.stem.endswith(stem):
                return md
        return candidates[0]
    
    return None


def embed_podcast(md_path: Path, mp3_name: str):
    """在Markdown文件开头插入播客链接"""
    content = md_path.read_text(encoding="utf-8")
    
    # 动态计算相对路径
    mp3_path = AUDIO_DIR / mp3_name
    rel_path = Path(os.path.relpath(mp3_path, md_path.parent)).as_posix()
    
    embed_block = f"> 🎧 **配套播客**（豆包 AI 生成）\n> [[{rel_path}]]\n\n"
    
    # 检查是否已嵌入
    if "配套播客" in content and mp3_name in content:
        print(f"[跳过] 已嵌入: {md_path.name}")
        return
    
    # 插入到文件开头
    new_content = embed_block + content
    md_path.write_text(new_content, encoding="utf-8")
    print(f"[嵌入] {md_path.name}")


def process_mp3(mp3_path: Path):
    """处理单个MP3：查找对应Markdown并嵌入"""
    stem = mp3_path.stem
    
    md = find_md_file(stem)
    if not md:
        print(f"[跳过] 找不到对应Markdown: {stem}.md")
        return False
    
    embed_podcast(md, mp3_path.name)
    return True


def main():
    # 1. 处理 WAV 文件（压缩 + 绑定）
    wav_files = sorted(AUDIO_DIR.glob("*.wav"))
    if wav_files:
        print(f"[信息] 发现 {len(wav_files)} 个 WAV 文件待处理\n")
        for wav in wav_files:
            mp3 = wav_to_mp3(wav)
            if mp3:
                process_mp3(mp3)
            print()
    
    # 2. 处理已有 MP3 文件（仅绑定，跳过已绑定的）
    mp3_files = sorted(AUDIO_DIR.glob("*.mp3"))
    # 过滤掉已经处理过的（避免重复输出）
    mp3_to_bind = [m for m in mp3_files if not (m.with_suffix('.wav')).exists()]
    if mp3_to_bind:
        print(f"[信息] 发现 {len(mp3_to_bind)} 个已有 MP3 待绑定\n")
        for mp3 in mp3_to_bind:
            process_mp3(mp3)
            print()
    
    if not wav_files and not mp3_to_bind:
        print("[信息] 没有需要处理的音频文件")
    
    print("=" * 60)
    print("全部处理完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
