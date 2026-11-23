# -*- coding: utf-8 -*-
"""单集配置。

一集 = 一首歌。只填这一集才有的东西：用哪段录屏、从第几秒到第几秒、
演唱人标注写什么、点评写什么、画面定格在哪一帧、高光 BGM 从哪一秒起。

段落顺序必须和系列 structure 一致，validate 会直接拒绝错位的配置。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .series import ROLES, source_path

VIDEO_ROLES = {"demo", "stage"}
FIXED_ROLES = {"intro", "ending"}


def load_episode(path: str | Path) -> Dict:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"单集配置不存在：{p}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_episode(episode: Dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _segment_clips(seg: Dict) -> List[Dict]:
    """统一返回该段的视频片段列表：优先 clips，否则扁平 src/start/end。"""
    clips = seg.get("clips")
    if isinstance(clips, list) and clips:
        return clips
    return [{"src": seg.get("src", ""), "start": seg.get("start", 0.0),
             "end": seg.get("end", 0.0)}]


def new_skeleton(series: Dict, name: str) -> Dict:
    """按系列结构生成空骨架，用户确认时再填素材与文案。

    系列 structure 只是默认模板；舞台数/歌手都可在单集里增删，校验只看顺序规则。
    """
    structure = series.get("structure") or ["intro", "demo", "review", "stage", "review", "ending"]
    segments: List[Dict] = []
    for role in structure:
        seg: Dict = {"role": role}
        if role in VIDEO_ROLES:
            seg.update({"src": "", "start": 0.0, "end": 0.0, "label": "", "captions": []})
        elif role == "review":
            base = {"src": "", "frame": 0.0, "bgm": 0.0, "text": []}
            if not (series.get("review") or {}).get("generate_voice", False):
                base["duration"] = 0.0
            seg.update(base)
        segments.append(seg)
    ep = {"version": 1, "series": series.get("name", ""), "name": name, "segments": segments}
    if series.get("transition"):
        ep["transition"] = {"frame": 0.0}
    return ep


def validate_episode(episode: Dict, series: Dict) -> List[str]:
    problems: List[str] = []
    if not episode.get("name"):
        problems.append("缺少 name（草稿名）")
    if not episode.get("series"):
        problems.append("缺少 series（系列名）")

    segments = episode.get("segments") or []
    if not isinstance(segments, list) or not segments:
        problems.append("segments 必须是非空数组")
        return problems

    roles = [str(s.get("role") or "") for s in segments]

    # 顺序规则：角色合法、首尾固定、恰好一个 demo、点评前必须是 demo/stage、舞台在 demo 之后
    unknown = [r for r in roles if r not in ROLES]
    if unknown:
        problems.append(f"含未知段落角色：{unknown}")
    if roles[0] != "intro":
        problems.append("第一段必须是 intro（片头）")
    if roles[-1] != "ending":
        problems.append("最后一段必须是 ending（片尾）")
    if roles.count("demo") != 1:
        problems.append(f"必须恰好有一个 demo 段，实际 {roles.count('demo')} 个")
    demo_seen = False
    for i, role in enumerate(roles):
        if role == "demo":
            demo_seen = True
        elif role == "stage" and not demo_seen:
            problems.append(f"第{i + 1}段 stage 出现在 demo 之前")
        elif role == "review":
            prev = roles[i - 1] if i > 0 else None
            if prev not in ("demo", "stage"):
                problems.append(f"第{i + 1}段 review 前必须是 demo 或 stage，实际是 {prev}")

    # 顶层 transition：系列要求过渡图时，单集需给 frame
    if series.get("transition"):
        tr = episode.get("transition")
        if not isinstance(tr, dict) or not isinstance(tr.get("frame"), (int, float)):
            problems.append("系列要求过渡图，单集需提供 transition.frame（抽帧秒数）")

    review_cfg = series.get("review") or {}
    gen_voice = bool(review_cfg.get("generate_voice", "voice" in review_cfg))
    fixed = series.get("fixed") or {}

    for i, seg in enumerate(segments, 1):
        role = str(seg.get("role") or "")
        tag = f"第{i}段({role})"
        if role in FIXED_ROLES:
            if fixed.get(role):
                continue
            # 系列没配固定素材，但单集也没给 → 暂不强制（允许单集自带 intro/ending 素材）
            if not seg.get("src"):
                problems.append(f"{tag}：系列无固定 {role}，单集也未提供 src")
            continue
        if role in VIDEO_ROLES:
            clips = _segment_clips(seg)
            for j, c in enumerate(clips, 1):
                csrc = str(c.get("src") or "")
                if not csrc:
                    problems.append(f"{tag} 片段{j}：缺少 src")
                elif not source_path(csrc).is_file():
                    problems.append(f"{tag} 片段{j}：素材不存在 {csrc}")
                try:
                    cstart, cend = float(c.get("start", 0)), float(c.get("end", 0))
                except (TypeError, ValueError):
                    problems.append(f"{tag} 片段{j}：start/end 不是数字")
                    continue
                if cend <= cstart:
                    problems.append(f"{tag} 片段{j}：end 必须大于 start")
            if not str(seg.get("label") or "").strip():
                problems.append(f"{tag}：缺少 label（演唱人名+版）")
            for cap in seg.get("captions") or []:
                if len(cap) != 3:
                    problems.append(f"{tag}：字幕条目必须是 [起, 止, 文本]")
                    continue
                if float(cap[1]) <= float(cap[0]):
                    problems.append(f"{tag}：字幕 {cap[2]!r} 的结束时间不大于开始")
            continue
        if role == "review":
            src = str(seg.get("src") or "")
            if not src:
                problems.append(f"{tag}：缺少 src")
            elif not source_path(src).is_file():
                problems.append(f"{tag}：素材不存在 {src}")
            for key in ("frame", "bgm"):
                if not isinstance(seg.get(key), (int, float)):
                    problems.append(f"{tag}：{key} 缺失或不是数字")
            if not gen_voice:
                dur = seg.get("duration")
                if not isinstance(dur, (int, float)) or float(dur) <= 0:
                    problems.append(f"{tag}：generate_voice=false 时必须提供正数 duration")
            text = seg.get("text") or []
            if not isinstance(text, list) or not text or not all(str(t).strip() for t in text):
                problems.append(f"{tag}：text 至少需要一行点评")
            continue
        # unknown roles 已在前面统一报过，这里不再重复
    return problems


def summarize_episode(episode: Dict) -> str:
    lines = [f"单集：{episode.get('name', '-')}   系列：{episode.get('series', '-')}"]
    tr = episode.get("transition")
    if tr:
        lines.append(f"  过渡图：抽帧 @ {tr.get('frame')}s（取自 DEMO 录屏）")
    for i, seg in enumerate(episode.get("segments") or [], 1):
        role = seg.get("role")
        if role in VIDEO_ROLES:
            clips = _segment_clips(seg)
            desc = " + ".join(f"{Path(str(c.get('src'))).name}[{c.get('start')}→{c.get('end')}]"
                              for c in clips)
            lines.append(f"  {i}. {role:<6} {desc} 标注 {seg.get('label')}"
                         f"  字幕 {len(seg.get('captions') or [])} 条")
        elif role == "review":
            text = " / ".join(str(t) for t in (seg.get("text") or []))
            dur = f" 时长 {seg.get('duration')}s" if seg.get("duration") else ""
            lines.append(f"  {i}. review 定格 {seg.get('frame')}s 高光 {seg.get('bgm')}s{dur}  "
                         f"「{text}」")
        else:
            lines.append(f"  {i}. {role}")
    return "\n".join(lines)
