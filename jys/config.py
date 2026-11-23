# -*- coding: utf-8 -*-
"""路径与运行环境探测。

设计原则：所有路径都从本文件位置推导，工具整体可搬移，不写死盘符。
"""
from __future__ import annotations

import os
from pathlib import Path

# 工作台根目录（本文件位于 <root>/jys/config.py）
ROOT: Path = Path(__file__).resolve().parent.parent

TEMPLATE_DIR: Path = ROOT / "templates"   # 系列模板 JSON
OUTPUT_DIR: Path = ROOT / "output"        # 分析报告与校验截图
CLIP_DIR: Path = ROOT / "clips"           # 新素材：clips/<段落名>/*.mp4
SERIES_DIR: Path = ROOT / "series"        # 系列规则：片头/结尾/结构/花字/字幕样式
EPISODE_DIR: Path = ROOT / "episodes"     # 单集配置：每首歌的素材与点评
ASSET_DIR: Path = ROOT / "assets"         # 单集生成的定格/朗读/BGM/花字
PLAN_DIR: Path = ROOT / "plans"           # 由单集配置生成的剪辑清单

# 剪映草稿目录候选：优先读环境变量，其次为默认安装位置
_DRAFT_CANDIDATES = [
    os.environ.get("JY_DRAFT_DIR", ""),
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"),
]


def draft_dir() -> Path:
    """返回当前生效的剪映草稿目录，找不到时抛出带指引的异常。"""
    for item in _DRAFT_CANDIDATES:
        if not item:
            continue
        p = Path(item)
        if p.is_dir():
            return p
    raise FileNotFoundError(
        "未找到剪映草稿目录。请设置环境变量 JY_DRAFT_DIR 指向 "
        r"%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft"
    )


def ensure_dirs() -> None:
    """确保工作台子目录存在。"""
    for d in (TEMPLATE_DIR, OUTPUT_DIR, CLIP_DIR,
              SERIES_DIR, EPISODE_DIR, ASSET_DIR, PLAN_DIR):
        d.mkdir(parents=True, exist_ok=True)
