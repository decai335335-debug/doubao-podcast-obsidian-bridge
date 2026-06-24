#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI-friendly runner for the video-sub-md source program."""

import argparse
import builtins
import os
import sys
from pathlib import Path


def answer_for_prompt(prompt, args):
    text = str(prompt)
    if "0/1" in text or "选择" in text:
        return args.ai_choice
    if "SESSDATA" in text:
        return args.sessdata or ""
    if "下载全部" in text:
        return args.playlist_choice
    if "SenseVoice" in text or "本地" in text and "识别" in text:
        return args.asr_choice
    if "深度分析" in text:
        return args.analysis_choice
    if "翻译" in text:
        return args.translate_choice
    return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-dir", required=True)
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--lang", default="")
    parser.add_argument("--max-concurrent", type=int, default=5)
    parser.add_argument("--sessdata", default="")
    parser.add_argument("--ai-choice", default="")
    parser.add_argument("--analysis-choice", default="b")
    parser.add_argument("--translate-choice", default="b")
    parser.add_argument("--playlist-choice", default="b")
    parser.add_argument("--asr-choice", default="b")
    args = parser.parse_args()

    tool_dir = Path(args.tool_dir)
    os.chdir(tool_dir)
    sys.path.insert(0, str(tool_dir))

    if args.sessdata:
        os.environ["BILI_COOKIE"] = ""
        os.environ["BILIBILI_SESSDATA"] = ""

    original_input = builtins.input

    def patched_input(prompt=""):
        reply = answer_for_prompt(prompt, args)
        print(f"{prompt}{reply}")
        return reply

    builtins.input = patched_input
    try:
        import main as video_main

        video_main.download(
            urls=args.url,
            lang=args.lang or None,
            max_concurrent=args.max_concurrent,
            cookie=args.sessdata or None,
        )
    finally:
        builtins.input = original_input


if __name__ == "__main__":
    main()
