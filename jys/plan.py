# -*- coding: utf-8 -*-
"""按剪辑清单（plan.json）直接生成剪映草稿。

和模板模式不同，这里不做"结构推断"，而是精确指定每个片段用哪个文件、
从第几秒到第几秒，以及每句字幕的绝对时间。适合已经确定分镜的成片。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import pyJianYingDraft as draft
from pyJianYingDraft.metadata.effect_meta import EffectMeta

from .presets import PresetLibrary

SEC = 1_000_000


def _text_style(cfg: Dict, default_size: float) -> draft.TextStyle:
    return draft.TextStyle(
        size=float(cfg.get("size", default_size)),
        color=tuple(cfg.get("color", (1.0, 1.0, 1.0))),
        bold=bool(cfg.get("bold", False)),
        align=int(cfg.get("align", 1)),
    )


def _clip_settings(cfg: Dict) -> draft.ClipSettings:
    return draft.ClipSettings(
        transform_x=float(cfg.get("transform_x", 0.0)),
        transform_y=float(cfg.get("transform_y", 0.0)),
    )


def _border(cfg: Dict) -> Optional[draft.TextBorder]:
    if not cfg.get("border", True):
        return None
    return draft.TextBorder(width=float(cfg.get("border_width", 40)))


def load_plan(path: str | Path) -> Dict:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if not plan.get("items"):
        raise ValueError("剪辑清单为空：items 至少需要一个片段")
    if not plan.get("name"):
        raise ValueError("剪辑清单缺少 name")
    return plan


def build_from_plan(plan: Dict, *, draft_root: Optional[str | Path] = None,
                    allow_replace: bool = True, log=print) -> Dict[str, object]:
    """按剪辑清单生成草稿，返回统计信息。"""
    from . import config

    root = Path(draft_root) if draft_root else config.draft_dir()
    canvas = plan.get("canvas", {})
    width = int(canvas.get("width", 1920))
    height = int(canvas.get("height", 1080))
    fps = int(canvas.get("fps", 30))
    name = str(plan["name"])

    folder = draft.DraftFolder(str(root))
    script = folder.create_draft(name, width, height, fps, allow_replace=allow_replace)
    script.append_tracks([
        draft.TrackSpec(draft.TrackType.video, "main_video"),
        draft.TrackSpec(draft.TrackType.audio, "bgm"),
        draft.TrackSpec(draft.TrackType.audio, "voice"),
        draft.TrackSpec(draft.TrackType.text, "caption"),
        draft.TrackSpec(draft.TrackType.text, "title"),
        draft.TrackSpec(draft.TrackType.text, "preset_a"),
        draft.TrackSpec(draft.TrackType.text, "preset_b"),
        draft.TrackSpec(draft.TrackType.text, "review"),
        draft.TrackSpec(draft.TrackType.video, "overlay_a"),
        draft.TrackSpec(draft.TrackType.video, "overlay_b"),
        draft.TrackSpec(draft.TrackType.video, "overlay_label"),
        draft.TrackSpec(draft.TrackType.video, "overlay_review"),
    ])

    cap_cfg = plan.get("caption_style", {})
    title_cfg = plan.get("title_style", {})
    cap_style = _text_style(cap_cfg, 7.5)
    cap_place = _clip_settings(cap_cfg)
    cap_border = _border(cap_cfg)
    title_style = _text_style(title_cfg, 14.0)
    title_place = _clip_settings(title_cfg)
    title_border = _border(title_cfg)

    materials: Dict[str, draft.VideoMaterial] = {}
    cursor = 0
    clips: List[Dict[str, float]] = []
    captions = 0
    titles = 0
    presets = 0
    audios = 0
    images = 0
    text_presets: List[Dict] = []
    warnings: List[str] = []

    for item in plan["items"]:
        kind = item.get("type")
        if kind == "clip":
            src = str(item["src"])
            if not Path(src).is_file():
                warnings.append(f"素材不存在：{src}")
                continue
            mat = materials.get(src) or draft.VideoMaterial(src)
            materials[src] = mat
            src_start = float(item.get("start", 0.0))
            src_end = float(item.get("end", 0.0))
            if src_end <= src_start:
                warnings.append(f"片段起止时间无效：{src} {src_start}-{src_end}")
                continue
            speed = float(item.get("speed", 1.0) or 1.0)
            if speed <= 0:
                warnings.append(f"片段速度无效：{src} speed={speed}")
                continue
            # 源时长（微秒），受素材实际长度限制
            src_dur = min(int((src_end - src_start) * SEC),
                          max(0, int(mat.duration) - int(src_start * SEC)))
            if src_dur <= 3000:
                warnings.append(f"片段太短已跳过：{src} {src_start}-{src_end}")
                continue
            target_dur = int(src_dur / speed)  # 变速后显示时长
            seg = draft.VideoSegment(
                mat,
                draft.Timerange(cursor, target_dur),
                source_timerange=draft.Timerange(int(src_start * SEC), src_dur),
                speed=speed if speed != 1.0 else None,
                volume=float(item.get("volume", 1.0)),
            )
            trans = item.get("transition")
            if trans:
                seg.add_transition(getattr(draft.TransitionType, str(trans["name"])),
                                   duration=int(float(trans["duration"]) * SEC))
            script.add_segment(seg, "main_video")
            clips.append({"src": src, "start": round(cursor / SEC, 2),
                          "end": round((cursor + target_dur) / SEC, 2),
                          "src_start": src_start, "speed": speed})
            cursor += target_dur
        elif kind in ("caption", "title"):
            start = float(item["start"])
            end = float(item["end"])
            text = str(item.get("text", "")).strip()
            if not text or end <= start:
                continue
            if kind == "caption":
                seg = draft.TextSegment(text, draft.trange_seconds(start, end=end),
                                        style=cap_style, border=cap_border,
                                        clip_settings=cap_place)
                script.add_segment(seg, "caption")
                captions += 1
            else:
                seg = draft.TextSegment(text, draft.trange_seconds(start, end=end),
                                        style=title_style, border=title_border,
                                        clip_settings=title_place)
                script.add_segment(seg, "title")
                titles += 1
        elif kind == "preset":
            start = float(item["start"])
            end = float(item["end"])
            text = str(item.get("text", "")).strip()
            if not text or end <= start:
                continue
            scale = float(item.get("scale", 1.0))
            style = draft.TextStyle(
                size=float(item.get("size", 8.0)),
                color=tuple(item.get("color", (1.0, 1.0, 1.0))),
                align=int(item.get("align", 1)),
            )
            place = draft.ClipSettings(
                scale_x=scale, scale_y=scale,
                transform_x=float(item.get("x", 0.0)),
                transform_y=float(item.get("y", 0.0)),
            )
            seg = draft.TextSegment(text, draft.trange_seconds(start, end=end),
                                    style=style, clip_settings=place)
            font_rid = str(item.get("font", ""))
            if font_rid:
                seg.font = EffectMeta(str(item.get("name", "preset")), False, font_rid, "", "", [])
            effect_id = str(item.get("effect", ""))
            if effect_id:
                seg.add_effect(effect_id)
            script.add_segment(seg, str(item.get("track", "review")))
            presets += 1
        elif kind == "image":
            src = str(item["src"])
            if not Path(src).is_file():
                warnings.append(f"图片不存在：{src}")
                continue
            start = float(item["start"])
            end = float(item["end"])
            if end <= start:
                continue
            start_us = int(start * SEC)
            end_us = int(end * SEC)
            # 主轨必须首尾相接：清单按 0.01s 取整后可能与前一段产生几毫秒重叠
            if str(item.get("track")) == "main_video" and start_us < cursor:
                start_us = cursor
            dur = max(0, end_us - start_us)
            if dur <= 3000:
                continue
            mat = draft.VideoMaterial(src)
            scale = item.get("scale")
            place = None
            if scale is not None or item.get("x") is not None or item.get("y") is not None:
                sc = float(scale if scale is not None else 1.0)
                place = draft.ClipSettings(scale_x=sc, scale_y=sc,
                                           transform_x=float(item.get("x", 0.0)),
                                           transform_y=float(item.get("y", 0.0)))
            seg = draft.VideoSegment(
                mat,
                draft.Timerange(start_us, dur),
                source_timerange=draft.Timerange(0, dur),
                clip_settings=place,
            )
            mask = item.get("mask")
            if mask:
                seg.add_mask(draft.MaskType.矩形,
                             center_x=float(mask.get("center_x", 0.0)),
                             center_y=float(mask.get("center_y", 0.0)),
                             size=float(mask.get("size", 0.5)),
                             rect_width=mask.get("width"),
                             feather=float(mask.get("feather", 0.0)),
                             invert=bool(mask.get("invert", False)),
                             rotation=float(mask.get("rotation", 0.0)),
                             round_corner=float(mask.get("round_corner", 0.0)))
            trans = item.get("transition")
            if trans:
                seg.add_transition(getattr(draft.TransitionType, str(trans["name"])),
                                   duration=int(float(trans["duration"]) * SEC))
            script.add_segment(seg, str(item.get("track", "overlay_a")))
            if str(item.get("track")) == "main_video":
                cursor = max(cursor, start_us + dur)
            images += 1
        elif kind == "audio":
            src = str(item["src"])
            if not Path(src).is_file():
                warnings.append(f"音频不存在：{src}")
                continue
            at = float(item.get("at", 0.0))
            src_start = float(item.get("start", 0.0))
            mat = draft.AudioMaterial(src)
            dur = float(item.get("dur", 0.0))
            if dur <= 0:
                dur = max(0.0, (int(mat.duration) - int(src_start * SEC)) / SEC)
            if dur <= 0.1:
                warnings.append(f"音频时长无效：{src}")
                continue
            seg = draft.AudioSegment(
                mat,
                draft.Timerange(int(at * SEC), int(dur * SEC)),
                source_timerange=draft.Timerange(int(src_start * SEC), int(dur * SEC)),
                volume=float(item.get("volume", 1.0)),
            )
            script.add_segment(seg, str(item.get("track", "bgm")))
            audios += 1
        elif kind == "text_preset":
            try:
                if float(item["end"]) <= float(item["start"]):
                    continue
            except (KeyError, TypeError, ValueError):
                warnings.append(f"预设文本时间无效：{item}")
                continue
            text_presets.append(item)
        else:
            warnings.append(f"未知条目类型：{kind}")

    script.save()
    if text_presets:
        _inject_text_presets(root, name, plan, text_presets, log)
    total = cursor / SEC
    log(f"草稿已生成：{Path(root) / name}")
    log(f"时长 {int(total // 60):02d}:{total % 60:05.2f}，"
        f"画面 {len(clips)} 段，字幕 {captions} 条，标题 {titles} 条，"
        f"花字 {presets} 条，音频 {audios} 段")
    for w in warnings:
        log(f"[警告] {w}")
    return {
        "draft": str(Path(root) / name),
        "duration": round(total, 2),
        "clips": clips,
        "captions": captions,
        "titles": titles,
        "presets": presets,
        "text_presets": len(text_presets),
        "audios": audios,
        "images": images,
        "warnings": warnings,
    }


def _inject_text_presets(root: str | Path, name: str, plan: Dict,
                         items: List[Dict], log=print) -> None:
    """把原生预设文本（1/6/11/12）注入已生成的草稿 JSON。

    同一 track 的条目共用一个文本轨；不同 track 各自一条，避免同时段重叠。
    """
    path = Path(root) / name / "draft_content.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    lib = PresetLibrary(plan["presets_path"])
    tracks: Dict[str, Dict] = {}
    count = 0
    for item in sorted(items, key=lambda x: float(x["start"])):
        start = int(float(item["start"]) * SEC)
        dur = int((float(item["end"]) - float(item["start"])) * SEC)
        mats, seg, track = lib.build(
            item["preset"],
            text=item.get("text"),
            start_us=start,
            duration_us=dur,
            transform_x=item.get("x"),
            transform_y=item.get("y"),
            scale=item.get("scale"),
        )
        for kind, obj in mats:
            data.setdefault("materials", {}).setdefault(kind, []).append(obj)
        track_name = str(item.get("track", "preset_text"))
        holder = tracks.get(track_name)
        if holder is None:
            track["name"] = track_name
            data.setdefault("tracks", []).append(track)
            tracks[track_name] = holder = track
        holder["segments"].append(seg)
        count += 1
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    log(f"  原生预设文本注入 {count} 条 / {len(tracks)} 条轨道：{', '.join(tracks)}")
