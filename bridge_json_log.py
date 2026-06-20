#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Structured JSON task logs for Doubao Bridge."""

import json
import os
import time
from pathlib import Path


DEFAULT_VAULT = Path(os.environ.get("DOUBAO_OBSIDIAN_VAULT", r"E:\Obsidian\主仓库"))
JSON_LOG_DIR = Path(os.environ.get(
    "DOUBAO_BRIDGE_JSON_DIR",
    str(DEFAULT_VAULT / "90-归档" / "DoubaoBridgeJson"),
))
LATEST_A_FILE = JSON_LOG_DIR / "latest_a_generate.json"
LATEST_B_FILE = JSON_LOG_DIR / "latest_b_download_bind.json"


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def task_id(prefix):
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}"


def ensure_log_dir():
    JSON_LOG_DIR.mkdir(parents=True, exist_ok=True)


def write_json(path, data):
    ensure_log_dir()
    path = Path(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def write_task_log(data, latest_file=None):
    ensure_log_dir()
    filename = f"{data.get('task_id', task_id('task'))}.json"
    path = write_json(JSON_LOG_DIR / filename, data)
    if latest_file:
        write_json(latest_file, data)
    return path
