#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_process.py
下载完成后执行：wav -> mp3 压缩，并绑定到对应 Markdown 文件

用法:
    python post_process.py
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 修复 Windows 终端 GBK 编码导致 emoji 输出崩溃
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

VAULT = Path.home() / "Documents" / "Obsidian" / "申论真题"
AUDIO_DIR = VAULT / "附件" / "音频"

# 记录文件（与 pipeline 共用同一个）
RECORD_FILE = Path.home() / "Documents" / "Obsidian" / "申论真题" / "总报告" / "豆包播客代码上传与下载绑定记录.md"


def _ensure_record_file():
    """确保记录文件存在"""
    if not RECORD_FILE.exists():
        RECORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        header = "# 豆包播客上传与下载绑定记录\n\n---\n\n"
        with open(RECORD_FILE, "w", encoding="utf-8") as f:
            f.write(header)


def _load_chat_url():
    """从 pipeline_state.json 读取聊天链接"""
    state_file = Path(__file__).parent / "pipeline_state.json"
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("chat_url", "")
        except Exception:
            pass
    return ""


def append_download_bind_record(filename: str, chat_url: str = ""):
    """追加一条下载绑定成功记录"""
    _ensure_record_file()
    import time
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    url_line = f"- **聊天链接**: [{chat_url}]({chat_url})\n" if chat_url else ""
    entry = (
        f"### [下载绑定] {now}\n\n"
        f"- **文件名**: `{filename}`\n"
        f"{url_line}"
        f"- **状态**: ✅ 绑定成功\n\n"
        f"---\n\n"
    )
    with open(RECORD_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    print(f"  [已记录] {RECORD_FILE.name}")


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


def _load_md_mapping():
    """读取 pipeline 保存的 md_mapping.json（pdf_name -> md_full_path）"""
    mapping_file = Path(__file__).parent / "md_mapping.json"
    if mapping_file.exists():
        try:
            with open(mapping_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def find_md_file(stem: str) -> Path | None:
    """在Obsidian库中查找对应的md文件，支持模糊匹配"""
    # 0. 优先从 md_mapping.json 精确匹配（避免同名文件在不同文件夹冲突）
    mapping = _load_md_mapping()
    for pdf_name, md_path in mapping.items():
        if Path(md_path).stem == stem:
            p = Path(md_path)
            if p.exists():
                return p
    
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


def embed_podcast(md_path: Path, mp3_name: str, chat_url: str = ""):
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
    # 记录绑定成功
    append_download_bind_record(mp3_name, chat_url)


def process_mp3(mp3_path: Path, chat_url: str = ""):
    """处理单个MP3：查找对应Markdown并嵌入"""
    stem = mp3_path.stem
    
    md = find_md_file(stem)
    if not md:
        print(f"[跳过] 找不到对应Markdown: {stem}.md")
        return False
    
    embed_podcast(md, mp3_path.name, chat_url)
    return True


def load_last_upload_batch() -> list:
    """从上传记录文件中读取最近一次批量上传的文件名列表（Markdown stem）"""
    if not RECORD_FILE.exists():
        return []
    
    content = RECORD_FILE.read_text(encoding="utf-8")
    # 按 "### [批量上传]" 分割，取最后一个块
    parts = content.split("### [批量上传]")
    if len(parts) < 2:
        return []
    
    last_block = parts[-1]
    # 提取文件列表（格式：  - [[文件名]]）
    files = []
    for line in last_block.splitlines():
        match = re.search(r'^\s+-\s+\[\[(.+?)\]\]', line)
        if match:
            files.append(match.group(1))
    
    return files


def main():
    import argparse
    parser = argparse.ArgumentParser(description="WAV → MP3 压缩 + 绑定 Markdown")
    parser.add_argument("--bind-existing", action="store_true",
                        help="同时处理已有 MP3（默认只处理本次上传记录中的文件）")
    parser.add_argument("--all-wav", action="store_true",
                        help="处理 AUDIO_DIR 下全部 WAV（忽略记录文件）")
    args = parser.parse_args()

    # 读取当前聊天链接
    chat_url = _load_chat_url()
    if chat_url:
        print(f"[信息] 当前聊天链接: {chat_url}\n")
    
    # 优先从上传记录读取本次要绑定的文件列表
    target_stems = load_last_upload_batch()
    
    if target_stems and not args.all_wav:
        print(f"[信息] 从上传记录读取到 {len(target_stems)} 个待绑定文件\n")
        for stem in target_stems:
            wav = AUDIO_DIR / f"{stem}.wav"
            mp3 = AUDIO_DIR / f"{stem}.mp3"
            if wav.exists():
                result = wav_to_mp3(wav)
                if result:
                    process_mp3(result, chat_url)
                print()
            elif mp3.exists():
                process_mp3(mp3, chat_url)
                print()
            else:
                print(f"[跳过] 找不到音频: {stem}.wav/.mp3\n")
    else:
        # 回退：遍历 AUDIO_DIR 下全部 WAV（兼容旧模式）
        wav_files = sorted(AUDIO_DIR.glob("*.wav"))
        if wav_files:
            print(f"[信息] 发现 {len(wav_files)} 个 WAV 文件待处理\n")
            for wav in wav_files:
                mp3 = wav_to_mp3(wav)
                if mp3:
                    process_mp3(mp3, chat_url)
                print()
        
        # 处理已有 MP3（仅当 --bind-existing 时）
        mp3_files = sorted(AUDIO_DIR.glob("*.mp3"))
        mp3_to_bind = [m for m in mp3_files if not (m.with_suffix('.wav')).exists()]
        if mp3_to_bind:
            if args.bind_existing:
                print(f"[信息] 发现 {len(mp3_to_bind)} 个已有 MP3 待绑定\n")
                for mp3 in mp3_to_bind:
                    process_mp3(mp3, chat_url)
                    print()
            else:
                print(f"[信息] 跳过 {len(mp3_to_bind)} 个已有 MP3（用 --bind-existing 可强制绑定）\n")
    
    if not target_stems and not list(AUDIO_DIR.glob("*.wav")) and not list(AUDIO_DIR.glob("*.mp3")):
        print("[信息] 没有需要处理的音频文件")
    
    print("=" * 60)
    print("全部处理完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
