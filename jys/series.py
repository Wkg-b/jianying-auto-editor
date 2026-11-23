# -*- coding: utf-8 -*-
"""系列规则。

一个系列只描述"每集都一样"的东西：画布、成片结构、固定片头/结尾、
段前标注用哪个预设、点评怎么呈现、角标 11/12 的覆盖范围、字幕样式。
单集只负责填本集的素材时间、点评文字和文案，见 episode.py。

这样换一首歌时不需要重写脚本，也不需要重新分析成片结构。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

ROLES = {"intro", "demo", "stage", "review", "ending"}
# 合法顺序规则里允许出现的段落角色（过渡图由系列配置自动插入，不在单集 segments 里）


def resolve_series(name_or_path: str | Path) -> Path:
    """接受系列名（series/<名>.json）或直接路径。"""
    p = Path(name_or_path)
    if p.is_file():
        return p
    from . import config

    for cand in (config.SERIES_DIR / f"{name_or_path}.json",
                 config.SERIES_DIR / str(name_or_path)):
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"找不到系列配置：{name_or_path}（应位于 series/ 下）")


def load_series(name_or_path: str | Path) -> Dict:
    path = resolve_series(name_or_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = validate_series(data)
    if problems:
        raise ValueError(f"系列配置有问题：{path}\n" + "\n".join(f"  - {p}" for p in problems))
    return data


def save_series(series: Dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(series, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def source_path(src: str) -> Path:
    """素材路径：绝对路径原样使用，相对路径相对工作台根目录。"""
    from . import config

    p = Path(src)
    return p if p.is_absolute() else (config.ROOT / p)


def _is_auto(v) -> bool:
    return isinstance(v, str) and v.strip().lower() == "auto"


def validate_series(series: Dict) -> List[str]:
    problems: List[str] = []
    canvas = series.get("canvas") or {}
    # width/height 可以是数字，也可以是 "auto"（compose 时探测第一段录屏分辨率）
    for key in ("width", "height"):
        v = canvas.get(key)
        if not (isinstance(v, (int, float)) or _is_auto(v)):
            problems.append(f"canvas.{key} 缺失或不是数字/auto")
    if not isinstance(canvas.get("fps"), (int, float)):
        problems.append("canvas.fps 缺失或不是数字")

    structure = series.get("structure")
    if structure is None:
        structure = []  # 允许不显式写 structure，单集自己决定段落
    if not isinstance(structure, list):
        problems.append("structure 必须是数组")
        structure = []
    unknown = [r for r in structure if r not in ROLES]
    if unknown:
        problems.append(f"structure 含未知段落：{unknown}")

    fixed = series.get("fixed") or {}
    for role in ("intro", "ending"):
        cfg = fixed.get(role) or {}
        if not cfg:
            continue  # 单集也可以不依赖固定片头/结尾，这里不强制
        for key in ("src", "start", "end"):
            if key not in cfg:
                problems.append(f"fixed.{role}.{key} 缺失")
        if cfg.get("src") and not source_path(str(cfg["src"])).is_file():
            problems.append(f"fixed.{role} 素材不存在：{cfg['src']}")
        spd = cfg.get("speed")
        if spd is not None and (not isinstance(spd, (int, float)) or spd <= 0):
            problems.append(f"fixed.{role}.speed 必须是正数")

    # 过渡图：片头 → DEMO 之间。时长由系列定，抽哪一帧由单集 episode.transition.frame 定。
    transition = series.get("transition")
    if transition is not None:
        if not isinstance(transition, dict):
            problems.append("transition 必须是对象")
        else:
            dur = transition.get("duration")
            if not isinstance(dur, (int, float)) or not (1.7 <= float(dur) <= 3.0):
                problems.append("transition.duration 需在 1.7–3.0 秒之间")

    label = series.get("label") or {}
    if "preset" not in label:
        problems.append("label.preset 缺失（段前标注用的预设文本编号）")
    for key in ("offset", "duration"):
        if not isinstance(label.get(key), (int, float)):
            problems.append(f"label.{key} 缺失或不是数字")

    review = series.get("review") or {}
    if "preset" not in review:
        problems.append("review.preset 缺失")
    if "frame_src" not in review:
        problems.append("review.frame_src 缺失（点评相框图）")
    else:
        frame_p = Path(str(review["frame_src"]))
        if not frame_p.is_absolute():
            frame_p = Path(__file__).resolve().parent.parent / frame_p
        if not frame_p.is_file():
            problems.append(f"review.frame_src 不存在：{review['frame_src']}")
    if review.get("generate_voice", "voice" in review):
        for key in ("tail", "voice_offset", "voice"):
            if key not in review:
                problems.append(f"review.{key} 缺失（自动朗读时需要）")
    for key in ("bgm_volume",):
        if not isinstance(review.get(key), (int, float)):
            problems.append(f"review.{key} 缺失或不是数字")

    # 原生预设文本原型
    presets = series.get("presets")
    if not presets:
        problems.append("presets 缺失（原生预设文本原型 json 路径）")
    else:
        pp = Path(str(presets))
        if not pp.is_absolute():
            pp = Path(__file__).resolve().parent.parent / pp
        if not pp.is_file():
            problems.append(f"presets 原型不存在：{presets}")

    # 转场规则：名称必须是 pyJianYingDraft 支持的转场
    transitions = series.get("transitions")
    if transitions is not None:
        if not isinstance(transitions, dict):
            problems.append("transitions 必须是对象")
        else:
            for key, cfg in transitions.items():
                if not isinstance(cfg, dict) or not cfg.get("name"):
                    problems.append(f"transitions.{key} 缺少 name")
                    continue
                if not isinstance(cfg.get("duration"), (int, float)) or float(cfg["duration"]) <= 0:
                    problems.append(f"transitions.{key}.duration 必须是正数")

    for key in ("caption_style", "title_style"):
        if not isinstance(series.get(key), dict):
            problems.append(f"{key} 缺失")

    return problems


def summarize_series(series: Dict) -> str:
    canvas = series.get("canvas", {})
    structure = " → ".join(series.get("structure", []))
    fixed = series.get("fixed", {})
    label = series.get("label", {})
    review = series.get("review", {})
    badges = series.get("badges", [])
    lines = [
        f"系列：{series.get('name', '-')}",
        f"画布：{canvas.get('width')}x{canvas.get('height')} @ {canvas.get('fps')}fps",
        f"结构：{structure or '（单集自定义，规则：片头/DEMO/舞台/点评交替/片尾）'}",
    ]
    for role in ("intro", "ending"):
        cfg = fixed.get(role)
        if cfg:
            spd = cfg.get("speed")
            suffix = f" x{spd}" if spd not in (None, 1, 1.0) else ""
            lines.append(f"固定{role}：{cfg.get('src')} [{cfg.get('start')} → {cfg.get('end')}]{suffix}")
    transition = series.get("transition")
    if transition:
        lines.append(f"过渡图：片头→DEMO 之间 {transition.get('duration')}s"
                     f"（每集从 DEMO 录屏抽帧，帧时间见单集 transition.frame）")
    lines.append(f"段前标注：预设{label.get('preset')}，延后 {label.get('offset')}s，"
                 f"显示 {label.get('duration')}s")
    gen_voice = review.get("generate_voice", False)
    voice_txt = "自动生成朗读" if gen_voice else "朗读留空（剪映手动加）"
    lines.append(f"点评：预设{review.get('preset')}，{voice_txt}，"
                 f"高光 BGM 音量 {review.get('bgm_volume')}")
    if badges:
        names = "、".join(f"预设{b.get('preset')}→{b.get('track')}" for b in badges)
        lines.append(f"角标：{names}（{series.get('badge_from')} → {series.get('badge_to')}）")
    return "\n".join(lines)
