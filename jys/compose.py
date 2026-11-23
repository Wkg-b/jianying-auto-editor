# -*- coding: utf-8 -*-
"""把系列规则 + 单集配置装配成剪辑清单，再生成剪映草稿。

装配顺序（角色顺序可参数化）：
    片头 → 过渡图 → DEMO → 点评 → 舞台 → 点评 → … → 片尾

叠加元素全部使用剪映原生预设文本：
- 预设 1：段前标注「XX版」（居中）
- 预设 6：点评文字逐行出现
- 预设 11/12：左上「考研中」/ 右下「NO PAUSE」角标
点评画面 = 相框图 + 对应高光定格（矩形蒙版）+ 高光 BGM + 逐行金字。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from . import config, media
from .episode import _segment_clips, validate_episode
from .series import _is_auto, source_path

VIDEO_ROLES = {"demo", "stage"}
FIXED_ROLES = {"intro", "ending"}

_BAD_FILENAME = re.compile(r'[\\/:*?"<>|]')

_DEFAULT_TRANSITIONS = {
    "intro": {"name": "打板转场_I", "duration": 1.4},
    "stage_to_review": {"name": "相片切换", "duration": 2.133},
    "review_to_stage": {"name": "横移模糊", "duration": 1.533},
    "ending": {"name": "推远_II", "duration": 1.067},
}


def _safe_name(name: str) -> str:
    return _BAD_FILENAME.sub("_", name).strip() or "episode"


def _project_path(value: str) -> Path:
    p = Path(str(value))
    if p.is_absolute():
        return p
    return Path(__file__).resolve().parent.parent / p


def compose(series: Dict, episode: Dict, *, draft_root: Optional[str] = None,
            plan_only: bool = False, allow_replace: bool = True,
            log=print) -> Dict[str, object]:
    from . import plan as plan_mod

    problems = validate_episode(episode, series)
    if problems:
        raise ValueError("单集配置有问题：\n" + "\n".join(f"  - {p}" for p in problems))

    config.ensure_dirs()
    name = str(episode["name"])
    fixed = series.get("fixed") or {}
    label_cfg = series.get("label") or {}
    review_cfg = series.get("review") or {}
    transitions = {**_DEFAULT_TRANSITIONS, **(series.get("transitions") or {})}
    # 未显式声明时：系列配了 voice 就自动朗读（兼容旧 zangli），否则留空手动加
    gen_voice = bool(review_cfg.get("generate_voice", "voice" in review_cfg))
    transition_cfg = series.get("transition")
    segments = episode["segments"]

    presets_path = series.get("presets")
    if not presets_path:
        raise ValueError("系列缺少 presets（原生预设文本原型 json 路径）")
    presets_path = _project_path(str(presets_path))

    # ---- 画布：auto 时探测第一段录屏（demo 或首个 stage）的实际分辨率 ----
    canvas = series.get("canvas") or {}
    fps = int(canvas.get("fps", 30))
    width = canvas.get("width")
    height = canvas.get("height")
    if _is_auto(width) or _is_auto(height):
        probe_seg = next((s for s in segments if s.get("role") == "demo"), None) \
            or next((s for s in segments if s.get("role") == "stage"), None)
        if probe_seg is None:
            raise ValueError("canvas=auto 但单集没有 demo/stage 段可供探测分辨率")
        probe_src = str(source_path(str(_segment_clips(probe_seg)[0]["src"])))
        pw, ph = media.probe_resolution(probe_src)
        width = pw if _is_auto(width) else int(width)
        height = ph if _is_auto(height) else int(height)
        log(f"  画布 auto 探测：{Path(probe_src).name} → {width}x{height}")
    else:
        width, height = int(width), int(height)

    asset_dir = config.ASSET_DIR / _safe_name(name)
    demo_src = str(source_path(str(_segment_clips(
        next(s for s in segments if s.get("role") == "demo"))[0]["src"])))

    items: List[Dict] = []
    spans: List[Dict] = []
    prev_video_clips: List[Dict] = []

    def add_item(item: Dict) -> int:
        items.append(item)
        return len(items) - 1

    def push_span(role: str, start: float, end: float, *, label: Optional[str] = None,
                  item_index: Optional[int] = None) -> Dict:
        span = {"index": len(spans) + 1, "role": role,
                "start": round(start, 2), "end": round(end, 2),
                "duration": round(end - start, 2)}
        if label is not None:
            span["label"] = label
        if item_index is not None:
            span["item_index"] = item_index
        spans.append(span)
        log(f"  {span['index']}. {role:<9} {start:7.2f} → {end:7.2f}s")
        return span

    for seg in segments:
        role = str(seg["role"])
        seg_start = _span_end(spans)

        if role in FIXED_ROLES:
            cfg = fixed.get(role) or seg
            src = str(source_path(str(cfg["src"])))
            start = float(cfg["start"])
            end = float(cfg["end"])
            speed = float(cfg.get("speed", 1.0) or 1.0)
            dur = round((end - start) / speed, 3)
            item: Dict = {"type": "clip", "src": src, "start": start, "end": end}
            if speed != 1.0:
                item["speed"] = speed
            push_span(role, seg_start, seg_start + dur, item_index=add_item(item))

        elif role in VIDEO_ROLES:
            clips = _segment_clips(seg)
            dur = 0.0
            last_index = None
            prev_video_clips = []
            for c in clips:
                csrc = str(source_path(str(c["src"])))
                cstart, cend = float(c["start"]), float(c["end"])
                last_index = add_item({"type": "clip", "src": csrc,
                                       "start": cstart, "end": cend})
                dur += cend - cstart
                prev_video_clips.append({"src": csrc, "start": cstart, "end": cend})
            dur = round(dur, 3)
            push_span(role, seg_start, seg_start + dur, label=str(seg["label"]),
                      item_index=last_index)

            for cap in seg.get("captions") or []:
                rel_start, rel_end, text = float(cap[0]), float(cap[1]), str(cap[2]).strip()
                if not text or rel_end <= rel_start:
                    continue
                cap_start = seg_start + max(0.0, rel_start)
                cap_end = seg_start + min(dur, rel_end)
                if cap_end - cap_start < 0.3:
                    warnings_text = f"第{len(spans)}段字幕过短已跳过：{text}"
                    log(f"  [警告] {warnings_text}")
                    continue
                add_item({"type": "caption", "start": round(cap_start, 2),
                          "end": round(cap_end, 2), "text": text})

        elif role == "review":
            text_lines = [str(t).strip() for t in seg["text"]]
            rsrc = str(source_path(str(seg["src"])))
            if gen_voice:
                voice_text = "，".join(text_lines)
                voice_path = media.make_tts(voice_text, asset_dir,
                                            str(review_cfg.get("voice", "zh-CN-YunjianNeural")),
                                            str(review_cfg.get("voice_rate", "+0%")))
                voice_dur = media.audio_duration(voice_path)
                tail = float(review_cfg.get("tail", 0.8))
                dur = round(voice_dur + tail, 2)
            else:
                voice_path = None
                voice_dur = 0.0
                dur = round(float(seg["duration"]), 2)

            # 相框图铺满点评段（主轨）
            frame_src = review_cfg.get("frame_src")
            if not frame_src:
                raise ValueError("系列 review.frame_src 缺失（点评相框图）")
            frame_path = str(source_path(str(frame_src)))
            main_index = add_item({"type": "image", "track": "main_video",
                                   "src": frame_path,
                                   "start": round(seg_start, 2),
                                   "end": round(seg_start + dur, 2),
                                   "scale": float(review_cfg.get("frame_scale", 1.0))})

            # 高光定格 + 矩形蒙版（相框内展示）——必须取自前面对应演唱者的画面
            freeze_src, freeze_at = _clamp_to_clips(
                rsrc, float(seg["frame"]), prev_video_clips, "高光定格", log)
            freeze = media.make_freeze(freeze_src, freeze_at, dur, asset_dir,
                                       width, height, fps)
            fz = review_cfg.get("freeze") or {}
            add_item({"type": "image", "track": "overlay_review",
                      "src": str(freeze),
                      "start": round(seg_start, 2),
                      "end": round(seg_start + dur, 2),
                      "scale": float(fz.get("scale", 1.0)),
                      "x": float(fz.get("x", 0.0)),
                      "y": float(fz.get("y", 0.0)),
                      "mask": fz.get("mask")})

            # 点评 BGM：同样必须截取前面对应演唱者的歌曲高光时刻
            bgm_src, bgm_at = _clamp_to_clips(
                rsrc, float(seg["bgm"]), prev_video_clips, "BGM", log)
            sound = media.extract_audio(bgm_src, bgm_at,
                                        dur + float(review_cfg.get("bgm_tail", 1.0)),
                                        asset_dir)
            add_item({"type": "audio", "track": "bgm", "src": str(sound),
                      "at": round(seg_start, 2), "dur": dur,
                      "volume": float(review_cfg.get("bgm_volume", 0.28))})
            if gen_voice:
                add_item({"type": "audio", "track": "voice", "src": str(voice_path),
                          "at": round(seg_start + float(review_cfg.get("voice_offset", 0.35)), 2),
                          "dur": round(max(0.3, voice_dur - 0.02), 2), "volume": 1.0})

            # 点评文字：预设 6，逐行按字数分配时长
            for line, (ls, le) in _distribute_lines(
                    text_lines, seg_start, dur,
                    lead=float(review_cfg.get("text_lead", 0.9)),
                    tail=float(review_cfg.get("text_tail", 0.6))):
                add_item({"type": "text_preset", "track": "review_text",
                          "preset": review_cfg.get("preset", 6),
                          "text": line, "start": round(ls, 2), "end": round(le, 2)})
            push_span(role, seg_start, seg_start + dur, item_index=main_index)
        else:
            raise ValueError(f"未知段落：{role}")

        # 片头之后、DEMO 之前插入过渡图（从 DEMO 录屏抽帧定格）
        if role == "intro" and transition_cfg:
            tr_start = _span_end(spans)
            tr_dur = round(float(transition_cfg["duration"]), 3)
            tr_frame = float((episode.get("transition") or {}).get("frame", 0.0))
            tr_clip = media.make_freeze(demo_src, tr_frame, tr_dur, asset_dir,
                                        width, height, fps)
            tr_index = add_item({"type": "clip", "src": str(tr_clip),
                                 "start": 0.0, "end": tr_dur})
            push_span("transition", tr_start, tr_start + tr_dur, item_index=tr_index)

    # ---- 转场：加在前一段的最后一个画面上 ----
    for i, span in enumerate(spans[:-1]):
        nxt = spans[i + 1]
        trans = None
        if span["role"] == "intro" and nxt["role"] == "transition":
            trans = transitions["intro"]
        elif span["role"] in VIDEO_ROLES and nxt["role"] == "review":
            trans = transitions["stage_to_review"]
        elif span["role"] == "review" and nxt["role"] == "stage":
            trans = transitions["review_to_stage"]
        elif span["role"] == "review" and nxt["role"] == "ending":
            trans = transitions["ending"]
        if trans and span.get("item_index") is not None:
            items[span["item_index"]]["transition"] = dict(trans)
            log(f"  转场 {span['role']}→{nxt['role']}：{trans['name']} {trans['duration']}s")

    # ---- 段前标注：预设 1，正中间 ----
    demo_span = next((s for s in spans if s["role"] == "demo"), None)
    trans_span = next((s for s in spans if s["role"] == "transition"), None)
    label_duration = float(label_cfg.get("duration", 3.0))
    for span in spans:
        if span["role"] not in VIDEO_ROLES or not span.get("label"):
            continue
        if span["role"] == "demo" and trans_span:
            # 标注在过渡图期间出现（DEMO 开始前 label.demo_lead 秒）
            start = float(span["start"]) - float(label_cfg.get("demo_lead", 1.57))
            start = max(start, float(trans_span["start"]))
        else:
            start = float(span["start"]) + float(label_cfg.get("offset", 0.3))
        end = min(float(span["end"]), start + label_duration)
        if end <= start:
            continue
        add_item({"type": "text_preset", "track": "label",
                  "preset": label_cfg.get("preset", 1), "text": span["label"],
                  "start": round(start, 2), "end": round(end, 2),
                  "x": 0.0, "y": 0.0})

    # ---- 角标 11/12：从指定段落起点铺到另一段落起点 ----
    badge_from_role = str(series.get("badge_from", "demo"))
    badge_to_role = str(series.get("badge_to", "ending"))
    badge_from = _span_start(spans, badge_from_role)
    badge_to = _span_start(spans, badge_to_role)
    for badge in series.get("badges") or []:
        track = str(badge.get("track", f"badge{badge['preset']}"))
        add_item({"type": "text_preset", "track": track,
                  "preset": badge["preset"], "text": None,
                  "start": round(badge_from, 2), "end": round(badge_to, 2),
                  "scale": badge.get("scale")})
        log(f"  角标 预设{badge['preset']} {badge_from:.2f} → {badge_to:.2f}s")

    plan = {
        "name": name,
        "series": series.get("name", ""),
        "canvas": {"width": width, "height": height, "fps": fps},
        "caption_style": series.get("caption_style", {}),
        "title_style": series.get("title_style", {}),
        "review": {"generate_voice": gen_voice},
        "badge_from": badge_from_role,
        "badge_to": badge_to_role,
        "presets_path": str(presets_path),
        "segments": spans,
        "items": items,
    }
    plan_path = config.PLAN_DIR / f"{_safe_name(name)}.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"剪辑清单：{plan_path}")

    info: Dict[str, object] = {
        "plan": str(plan_path),
        "duration": round(_span_end(spans), 2),
        "segments": spans,
        "items": len(items),
        "warnings": [],
    }
    if not plan_only:
        info["draft"] = plan_mod.build_from_plan(plan, draft_root=draft_root,
                                                 allow_replace=allow_replace,
                                                 log=log)["draft"]
    return info


def _span_end(spans: List[Dict]) -> float:
    return float(spans[-1]["end"]) if spans else 0.0


def _span_start(spans: List[Dict], role: str) -> float:
    for span in spans:
        if span["role"] == role:
            return float(span["start"])
    return 0.0


def _clamp_to_clips(src: str, at: float, clips: List[Dict], kind: str, log) -> tuple:
    """把 (源文件, 时间点) 约束到前一段演唱画面的实际区间内。

    点评的高光定格图与 BGM 都必须取自紧邻的上一段 DEMO/舞台，
    否则会出现「画面/音乐是别的歌手」的错配。源不匹配或时间越界时，
    回退到上一段同源画面的可用区间，并打警告。
    """
    if not clips:
        return src, at
    same = [c for c in clips if c["src"] == src]
    if not same:
        c = clips[-1]
        log(f"  [警告] 点评{kind}源不是前一段演唱画面，已改用其起点 {c['start']:.2f}s")
        return c["src"], c["start"]
    for c in same:
        if c["start"] <= at <= c["end"]:
            return src, at
    nearest = min(same, key=lambda c: min(abs(at - c["start"]), abs(at - c["end"])))
    clamped = max(nearest["start"], min(at, nearest["end"]))
    log(f"  [警告] 点评{kind} {at:.2f}s 不在前一段画面内，已夹到 {clamped:.2f}s")
    return src, clamped


def _distribute_lines(lines: List[str], start: float, duration: float,
                      *, lead: float = 0.9, tail: float = 0.6):
    """按字数比例把点评行铺满整段，逐行出现。"""
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        return
    avail = max(1.0, duration - lead - tail)
    weights = [max(4, len(ln)) for ln in lines]
    total_w = sum(weights)
    cursor = start + min(lead, max(0.0, duration - 1.0))
    for ln, w in zip(lines, weights):
        span = max(1.0, avail * w / total_w)
        end = min(start + duration, cursor + span)
        if end - cursor < 0.5:
            break
        yield ln, (cursor, end)
        cursor = end
