# -*- coding: utf-8 -*-
"""用系列模板生成新的剪映草稿。

核心思路：模板只描述"结构与节奏"（镜头切点、段落、字幕区间、样式），
本模块负责把新素材和新文案按这套结构铺到时间轴上，几秒内产出草稿。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pyJianYingDraft as draft

SEC = 1_000_000  # pyJianYingDraft 内部时间单位为微秒

# 句子切分：中英文常见停顿标点
_SPLIT_RE = re.compile(r"(?<=[。！？；，、：!?;,])")


# ----------------------------------------------------------------------------
# 文案处理
# ----------------------------------------------------------------------------

def split_script(text: str, max_chars: int) -> List[str]:
    """把整段文案切成适合单条字幕的短句。

    规则：先按标点切，再按最大字数二次切分；`#` 开头的行视为注释忽略。
    """
    chunks: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for piece in _SPLIT_RE.split(line):
            piece = piece.strip()
            while len(piece) > max_chars:
                chunks.append(piece[:max_chars])
                piece = piece[max_chars:]
            if piece:
                chunks.append(piece)
    return chunks


def merge_windows(windows: Sequence[Dict[str, float]], max_gap: float = 1.2,
                  min_dur: float = 0.6) -> List[Tuple[float, float]]:
    """把相邻字幕区间合并成"连续有字幕的块"，块内按字数分配时长。"""
    blocks: List[List[float]] = []
    for w in sorted(windows, key=lambda x: float(x["start"])):
        s, e = float(w["start"]), float(w["end"])
        if blocks and s - blocks[-1][1] <= max_gap:
            blocks[-1][1] = max(blocks[-1][1], e)
        else:
            blocks.append([s, e])
    long_blocks = [(s, e) for s, e in blocks if e - s >= min_dur]
    return long_blocks or [(s, e) for s, e in blocks]


def assign_subtitles(windows: Sequence[Dict[str, float]], chunks: List[str],
                     chars_per_sec: float,
                     duration: Optional[float] = None) -> List[Tuple[float, float, str]]:
    """把短句按顺序铺进模板的字幕块。

    时长按字数比例分配（语速由"总字数 / 字幕总时长"自适应），
    一条字幕放不下就跳到下一个字幕块，避免把一句话拆得支离破碎。
    模板字幕块用尽后，剩余文案接在最后一条之后继续排到视频结尾为止。
    """
    blocks = merge_windows(windows)
    if not blocks or not chunks:
        return []

    total_time = sum(e - s for s, e in blocks)
    total_chars = sum(len(c) for c in chunks)
    cps = total_chars / total_time if total_time > 0 else chars_per_sec
    cps = min(max(cps, 2.5), 9.0)

    result: List[Tuple[float, float, str]] = []
    bi, cursor, idx = 0, None, 0
    while idx < len(chunks):
        chunk = chunks[idx]
        dur = max(0.7, len(chunk) / cps)
        placed = False
        while bi < len(blocks):
            s, e = blocks[bi]
            if cursor is None or cursor < s:
                cursor = s
            if cursor + dur <= e + 0.25:
                result.append((round(cursor, 3), round(min(e, cursor + dur), 3), chunk))
                cursor += dur + 0.05
                placed = True
                break
            bi += 1
            cursor = None
        if not placed:
            break
        idx += 1

    # 兜底：模板区间不够用时，剩余文案从最后一条之后顺延，不越过视频结尾
    if idx < len(chunks) and duration:
        t = result[-1][1] + 0.05 if result else 0.0
        while idx < len(chunks) and t < duration:
            chunk = chunks[idx]
            end = min(float(duration), t + max(0.7, len(chunk) / cps))
            if end - t < 0.5:
                break
            result.append((round(t, 3), round(end, 3), chunk))
            t = end + 0.05
            idx += 1
    return result


# ----------------------------------------------------------------------------
# 素材编排
# ----------------------------------------------------------------------------

def collect_clips(clip_dir: Path) -> Dict[str, List[draft.VideoMaterial]]:
    """按段落收集素材：clips/<段落名>/*.mp4；根目录下的素材归入 default。"""
    pools: Dict[str, List[draft.VideoMaterial]] = {}
    exts = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
    if not clip_dir.is_dir():
        return pools
    for entry in sorted(clip_dir.iterdir()):
        if entry.is_dir():
            files = [f for f in sorted(entry.iterdir()) if f.suffix.lower() in exts]
            if files:
                pools[entry.name] = [draft.VideoMaterial(str(f)) for f in files]
        elif entry.suffix.lower() in exts:
            pools.setdefault("default", []).append(draft.VideoMaterial(str(entry)))
    return pools


def _section_at(sections: Sequence[Dict[str, object]], t: float) -> str:
    """返回时间点 t 所属的段落名。"""
    for s in sections:
        if float(s["start"]) <= t < float(s["end"]):
            return str(s["name"])
    return str(sections[-1]["name"]) if sections else "default"


def _add_video(script, template: Dict, pools: Dict[str, List[draft.VideoMaterial]], log) -> int:
    """按模板镜头长度铺画面，返回总时长（微秒）。"""
    shots = template["shots"]
    sections = template["sections"]
    cursors: Dict[str, int] = {}   # 素材路径 -> 已用到的素材内偏移（微秒）
    cycles: Dict[str, int] = {}    # 每个素材池的轮询指针

    if not pools:
        raise ValueError("没有可用素材，请把新视频放进 clips/ 或 clips/<段落名>/")

    t = 0
    for shot in shots:
        shot_dur = int(round((float(shot["end"]) - float(shot["start"])) * SEC))
        if shot_dur <= 2000:
            continue

        section = _section_at(sections, float(shot["start"]))
        pool = pools.get(section) or pools.get("default") or next(iter(pools.values()))
        key = section if section in pools else ("default" if "default" in pools else "all")
        idx = cycles.get(key, 0)
        clip = pool[idx % len(pool)]
        cycles[key] = idx + 1

        used = cursors.get(clip.material_name, 0)
        remain = shot_dur
        while remain > 2000:
            avail = int(clip.duration) - used
            if avail < 300_000:          # 素材剩余不足 0.3s 就从头循环
                used, avail = 0, int(clip.duration)
            take = min(remain, avail)
            seg = draft.VideoSegment(
                clip,
                draft.Timerange(t, take),
                source_timerange=draft.Timerange(used, take),
            )
            script.add_segment(seg, "main_video")
            t += take
            remain -= take
            used += take
            cursors[clip.material_name] = used % max(1, int(clip.duration))
    return t


def _add_text(script, template: Dict, chunks: List[str], log) -> Tuple[int, int]:
    """按模板字幕区间添加文本，返回 (已用字数条目数, 丢弃条目数)。"""
    sub = template.get("subtitles", {})
    windows = sub.get("windows", [])
    style_cfg = sub.get("style", {})
    place = sub.get("placement", {})
    cps = float(sub.get("chars_per_sec", 4.5))

    if not windows or not chunks:
        return 0, 0

    items = assign_subtitles(windows, chunks, cps, float(template.get("duration", 0)) or None)
    style = draft.TextStyle(
        size=float(style_cfg.get("size", 8.0)),
        color=tuple(style_cfg.get("color", (1.0, 1.0, 1.0))),
        bold=bool(style_cfg.get("bold", False)),
        align=1,
    )
    border = draft.TextBorder(width=float(style_cfg.get("border_width", 40))) \
        if style_cfg.get("border", True) else None
    clip_cfg = draft.ClipSettings(
        transform_x=float(place.get("transform_x", 0.0)),
        transform_y=float(place.get("transform_y", -0.72)),
    )

    for start, end, text in items:
        seg = draft.TextSegment(
            text,
            draft.trange_seconds(start, end=end),
            style=style,
            border=border,
            clip_settings=clip_cfg,
        )
        script.add_segment(seg, "caption")

    consumed = sum(len(t) for _, _, t in items)
    total_chars = sum(len(c) for c in chunks)
    dropped = 1 if consumed < total_chars else 0
    return len(items), dropped


def _add_bgm(script, template: Dict, bgm: Path, total_us: int, volume: float) -> None:
    """铺背景音乐：素材不够长时循环填满，首尾各留淡入淡出。"""
    if not bgm or not Path(bgm).is_file() or total_us <= 0:
        return
    mat = draft.AudioMaterial(str(bgm))
    t, remain = 0, total_us
    first = True
    while remain > 5000:
        take = min(remain, int(mat.duration))
        seg = draft.AudioSegment(mat, draft.Timerange(t, take), volume=volume)
        if first:
            seg.add_fade("800ms", "0s")
            first = False
        script.add_segment(seg, "bgm")
        t += take
        remain -= take


# ----------------------------------------------------------------------------
# 对外入口
# ----------------------------------------------------------------------------

def build_draft(template: Dict, *, name: str, clips_dir: str | Path,
                script_text: str = "", bgm: Optional[str] = None,
                bgm_volume: float = 0.35, draft_root: Optional[str | Path] = None,
                allow_replace: bool = True, log=print) -> Dict[str, object]:
    """按模板生成一个剪映草稿。

    Args:
        template: 系列模板（analyze 或 draft_reader 的输出）。
        name: 草稿名，同时也是剪映里的项目名。
        clips_dir: 新素材根目录，支持按段落分子文件夹。
        script_text: 点评/歌词文案全文。
        bgm: 背景音乐路径，可留空。
        bgm_volume: BGM 音量，0-1。
        draft_root: 草稿目录，默认自动探测。
        allow_replace: 同名草稿是否覆盖。
    """
    from . import config

    root = Path(draft_root) if draft_root else config.draft_dir()
    canvas = template.get("canvas", {})
    width = int(canvas.get("width", 1080))
    height = int(canvas.get("height", 1920))
    fps = int(canvas.get("fps", 30))

    folder = draft.DraftFolder(str(root))
    script = folder.create_draft(name, width, height, fps, allow_replace=allow_replace)
    script.append_tracks([
        draft.TrackSpec(draft.TrackType.audio, "bgm"),
        draft.TrackSpec(draft.TrackType.video, "main_video"),
        draft.TrackSpec(draft.TrackType.text, "caption"),
    ])

    pools = collect_clips(Path(clips_dir))
    total_us = _add_video(script, template, pools, log)

    max_chars = int(template.get("subtitles", {}).get("max_chars_per_line", 16))
    chunks = split_script(script_text, max_chars) if script_text else []
    used, dropped = _add_text(script, template, chunks, log)

    if bgm:
        _add_bgm(script, template, Path(bgm), total_us, bgm_volume)

    script.save()
    return {
        "draft": str(Path(root) / name),
        "duration": round(total_us / SEC, 2),
        "shots": len(template.get("shots", [])),
        "subtitle_items": used,
        "script_chunks": len(chunks),
        "script_overflow": dropped,
        "clips": {k: len(v) for k, v in pools.items()},
    }
