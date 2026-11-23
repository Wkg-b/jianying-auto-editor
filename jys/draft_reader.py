# -*- coding: utf-8 -*-
"""读取明文草稿，提取同款模板。

说明：剪映 7 及以上默认对 draft_content.json 加密，本模块只处理可解析的
明文草稿（例如剪映 5.9/6 的工程，或剪映导出的草稿包）。遇到加密文件时
给出明确提示，而不是静默失败。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

SEC = 1_000_000


class DraftLockedError(RuntimeError):
    """草稿文件被加密，无法直接解析。"""


def _parse_json(path: Path) -> Dict:
    raw = path.read_bytes()
    try:
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise DraftLockedError(
            f"{path} 不是明文 JSON（当前剪映 7+ 默认加密草稿）。\n"
            "可选方案：1) 用剪映 5.9/6 保存一份同款工程；"
            "2) 直接对已发布的成片视频做 analyze。"
        ) from exc


def _us(v: Optional[int]) -> float:
    return round((v or 0) / SEC, 3)


def _text_style(mat: Dict) -> Dict[str, object]:
    """尽量从文本素材里还原字号/颜色/描边，失败则用默认值。"""
    size = 8.0
    color = [1.0, 1.0, 1.0]
    try:
        content = json.loads(mat.get("content", "{}"))
        st = (content.get("styles") or [{}])[0]
        if isinstance(st.get("size"), (int, float)):
            size = float(st["size"])
        fill = st.get("fill", {}).get("content", {}).get("solid", {})
        if fill.get("color"):
            color = list(fill["color"])
    except Exception:  # noqa: BLE001
        pass
    return {"size": size, "color": color, "bold": False, "border": True, "border_width": 40}


def template_from_draft(draft_root: str | Path, draft_name: str) -> Dict[str, object]:
    """从明文草稿提取模板，结构与 analyze.analyze_video 完全一致。"""
    folder = Path(draft_root) / draft_name
    content = folder / "draft_content.json"
    if not content.is_file():
        raise FileNotFoundError(f"草稿不存在：{content}")
    data = _parse_json(content)

    canvas_cfg = data.get("canvas_config") or {}
    width = int(canvas_cfg.get("width", 1080))
    height = int(canvas_cfg.get("height", 1920))
    fps = int(data.get("fps", 30))
    duration = _us(data.get("duration"))

    tracks = data.get("tracks") or []
    video_tracks = [t for t in tracks if t.get("type") == "video"]
    text_tracks = [t for t in tracks if t.get("type") == "text"]

    # 主轨道 = 片段最多的视频轨
    main = max(video_tracks, key=lambda t: len(t.get("segments") or []), default=None)
    shots: List[Dict[str, float]] = []
    if main:
        for seg in sorted(main.get("segments") or [],
                          key=lambda s: (s.get("target_timerange") or {}).get("start", 0)):
            tr = seg.get("target_timerange") or {}
            start = _us(tr.get("start"))
            end = round(start + _us(tr.get("duration")), 3)
            shots.append({"start": start, "end": end})
    if not shots:
        shots = [{"start": 0.0, "end": duration or 1.0}]

    # 字幕区间 + 样式
    texts_by_id = {m.get("id"): m for m in (data.get("materials", {}).get("texts") or [])}
    windows: List[Dict[str, float]] = []
    style = {"size": 8.0, "color": [1.0, 1.0, 1.0], "bold": False,
             "border": True, "border_width": 40}
    transform_y = -0.72
    for track in text_tracks:
        for seg in track.get("segments") or []:
            tr = seg.get("target_timerange") or {}
            start = _us(tr.get("start"))
            end = round(start + _us(tr.get("duration")), 3)
            windows.append({"start": start, "end": end})
            mat = texts_by_id.get(seg.get("material_id"), {})
            if mat:
                style = _text_style(mat)
            ty = (((seg.get("clip") or {}).get("transform") or {}).get("y"))
            if isinstance(ty, (int, float)):
                transform_y = float(ty)

    windows.sort(key=lambda w: w["start"])

    cuts = [s["start"] for s in shots]
    sections = [{"name": "main", "start": 0.0, "end": duration or shots[-1]["end"]}]
    # 有字幕区间时按第一个字幕起点粗略切成"开场/正片"
    if windows and windows[0]["start"] > 1.0:
        first = windows[0]["start"]
        sections = [
            {"name": "hook", "start": 0.0, "end": first},
            {"name": "main", "start": first, "end": duration or shots[-1]["end"]},
        ]

    return {
        "version": 1,
        "name": draft_name,
        "source": {"type": "draft", "path": str(content)},
        "canvas": {"width": width, "height": height, "fps": fps},
        "duration": duration,
        "shots": shots,
        "sections": sections,
        "subtitles": {
            "windows": windows,
            "placement": {"transform_y": transform_y},
            "style": style,
            "chars_per_sec": 4.5,
            "max_chars_per_line": 16,
        },
        "audio": {"loud": []},
        "analysis": {
            "source": "draft",
            "cut_count": len(shots),
            "subtitle_window_count": len(windows),
            "cuts": cuts,
        },
    }
