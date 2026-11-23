# -*- coding: utf-8 -*-
"""校验剪辑清单/草稿是否符合系列规则。

重点检查之前反复出错的地方：
- 点评金字是否只出现在点评段；
- 段前标注是否每个 DEMO/舞台恰好一个；
- 字幕是否只落在 DEMO/舞台段，没有串到点评或结尾；
- 角标 11/12 是否从 DEMO 铺到结尾前；
- 主画面是否首尾相接、没有空档或重叠。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

VIDEO_ROLES = {"demo", "stage"}


def _load_plan(path: str | Path) -> Dict:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"剪辑清单不存在：{p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _timeline(plan: Dict) -> Tuple[List[Dict], float]:
    """给每个条目算出绝对起止时间。clip 按出现顺序首尾相接。"""
    out: List[Dict] = []
    cursor = 0.0
    for item in plan.get("items", []):
        kind = item.get("type")
        if kind == "clip":
            speed = float(item.get("speed", 1.0) or 1.0)
            dur = (float(item.get("end", 0)) - float(item.get("start", 0))) / speed
            start, end = cursor, cursor + dur
            cursor += dur
        elif kind == "audio":
            start = float(item.get("at", 0))
            end = start + float(item.get("dur", 0))
        elif kind in ("caption", "title", "preset", "image", "text_preset"):
            start = float(item.get("start", 0))
            end = float(item.get("end", 0))
            if kind == "image" and item.get("track") == "main_video":
                cursor = max(cursor, end)  # 主轨定格/相框图同样占用主画面时长
        else:
            continue
        out.append({"kind": kind, "track": item.get("track", "main_video"),
                    "start": round(start, 3), "end": round(end, 3), "item": item})
    return out, round(cursor, 3)


def _inside(inner: Dict, outer: Dict, tol: float = 0.05) -> bool:
    return inner["start"] >= outer["start"] - tol and inner["end"] <= outer["end"] + tol


def verify_plan(plan: Dict) -> Dict[str, object]:
    errors: List[str] = []
    warnings: List[str] = []
    timeline, total = _timeline(plan)
    spans = plan.get("segments") or []
    if not spans:
        errors.append("清单缺少 segments，无法按段落校验（请用 compose 重新生成）")

    # 1) 文件存在性
    for entry in timeline:
        src = entry["item"].get("src")
        if src and not Path(str(src)).is_file():
            errors.append(f"{entry['kind']} 素材不存在：{src}")

    # 2) 主画面与段落首尾相接（视频片段 + 放在主轨上的定格/相框图片）
    main = [e for e in timeline
            if e["kind"] == "clip" or (e["kind"] == "image" and e["track"] == "main_video")]
    main.sort(key=lambda e: e["start"])
    if main:
        if abs(main[0]["start"]) > 0.05:
            errors.append(f"主画面没有从 0 开始：{main[0]['start']}")
        for a, b in zip(main, main[1:]):
            if abs(a["end"] - b["start"]) > 0.05:
                errors.append(f"主画面有空档/重叠：{a['end']} → {b['start']}")
        if spans:
            # 一个段落可能由多段画面拼成（如舞台多段拼接），按落在哪段内分组求和
            for span in spans:
                inside = [c for c in main
                          if c["start"] >= float(span["start"]) - 0.05
                          and c["end"] <= float(span["end"]) + 0.05]
                sum_dur = round(sum(c["end"] - c["start"] for c in inside), 2)
                span_dur = round(float(span["end"]) - float(span["start"]), 2)
                if abs(sum_dur - span_dur) > 0.1:
                    errors.append(f"第{span['index']}段({span['role']}) 画面时长 "
                                  f"{sum_dur} 与段落时长 {span_dur} 不一致")
        if abs(main[-1]["end"] - total) > 0.1:
            warnings.append("主画面结束时间与清单总时长不一致")

    # 3) 逐段检查
    review_text = [e for e in timeline if e["track"] == "review_text"]
    freeze_imgs = [e for e in timeline if e["track"] == "overlay_review"]
    labels = [e for e in timeline if e["track"] == "label"]
    voices = [e for e in timeline if e["track"] == "voice"]
    bgms = [e for e in timeline if e["track"] == "bgm"]
    captions = [e for e in timeline if e["kind"] == "caption"]
    review_spans = [s for s in spans if s["role"] == "review"]
    gen_voice = bool((plan.get("review") or {}).get("generate_voice", False))

    for span in spans:
        role = span["role"]
        tag = f"第{span['index']}段({role}) {span['start']}-{span['end']}s"
        if role == "review":
            hit = [e for e in review_text if _inside(e, span)]
            if not hit:
                errors.append(f"{tag} 点评文字一行都没有")
            hit = [e for e in freeze_imgs if _inside(e, span)]
            if len(hit) != 1:
                errors.append(f"{tag} 蒙版高光定格数量应为 1，实际 {len(hit)}")
            pools = [("高光BGM", bgms)]
            if gen_voice:
                pools.append(("朗读", voices))
            for name, pool in pools:
                hit = [e for e in pool if _inside(e, span)]
                if len(hit) != 1:
                    errors.append(f"{tag} {name}数量应为 1，实际 {len(hit)}")
        elif role in VIDEO_ROLES:
            # demo 的标注出现在前面的过渡图里，检查窗口向前放宽
            window = dict(span)
            if role == "demo":
                tr = next((s for s in spans if s["role"] == "transition"), None)
                if tr:
                    window["start"] = min(float(tr["start"]), float(span["start"]))
            hit = [e for e in labels if _inside(e, window)]
            if len(hit) != 1:
                errors.append(f"{tag} 段前标注应为 1，实际 {len(hit)}")
        else:
            hit = [e for e in captions if _inside(e, span)]
            if hit:
                errors.append(f"{tag} 不应有字幕，实际 {len(hit)} 条")

    # 4) 字幕只能落在 DEMO/舞台
    for cap in captions:
        if not any(_inside(cap, s) for s in spans if s["role"] in VIDEO_ROLES):
            errors.append(f"字幕 {cap['start']}-{cap['end']} 不在 DEMO/舞台段内")

    # 5) 点评文字/定格/标注不能压到别的段
    for entry in review_text + freeze_imgs:
        owners = [s for s in spans if _inside(entry, s)]
        if len(owners) != 1 or owners[0]["role"] != "review":
            errors.append(f"{entry['track']} {entry['start']}-{entry['end']} 越出点评段")
    label_windows = []
    for s in spans:
        if s["role"] not in VIDEO_ROLES:
            continue
        w0 = float(s["start"])
        if s["role"] == "demo":
            tr = next((x for x in spans if x["role"] == "transition"), None)
            if tr:
                w0 = min(w0, float(tr["start"]))
        label_windows.append((w0, float(s["end"])))
    for lab in labels:
        if not any(w0 - 0.06 <= lab["start"] and lab["end"] <= w1 + 0.06
                   for w0, w1 in label_windows):
            errors.append(f"段前标注 {lab['start']}-{lab['end']} 越出 DEMO/舞台/过渡段")

    if len(labels) != len([s for s in spans if s["role"] in VIDEO_ROLES]):
        errors.append(f"段前标注 {len(labels)} 个，DEMO/舞台段 "
                      f"{len([s for s in spans if s['role'] in VIDEO_ROLES])} 个")
    if not gen_voice and voices:
        errors.append(f"系列未开启自动朗读，但清单里有 {len(voices)} 条 voice 音频")

    # 6) 角标覆盖范围 + 转场边界
    bf_role = str(plan.get("badge_from", "demo"))
    bt_role = str(plan.get("badge_to", "ending"))
    bf = next((s for s in spans if s["role"] == bf_role), None)
    bt = next((s for s in spans if s["role"] == bt_role), None)
    if bf and bt:
        for track in ("badge_a", "badge_b"):
            hit = [e for e in timeline if e["track"] == track]
            if len(hit) != 1:
                errors.append(f"{track} 角标数量应为 1，实际 {len(hit)}")
                continue
            badge = hit[0]
            if abs(badge["start"] - bf["start"]) > 0.06:
                errors.append(f"{track} 起点 {badge['start']} 应为 {bf_role} 起点 {bf['start']}")
            if abs(badge["end"] - bt["start"]) > 0.06:
                errors.append(f"{track} 终点 {badge['end']} 应为 {bt_role} 起点 {bt['start']}")

    expect = {"intro": {"transition"}, "demo": {"review"}, "stage": {"review"},
              "review": {"stage", "ending"}}
    for span, nxt in zip(spans, spans[1:]):
        if nxt["role"] not in expect.get(span["role"], set()):
            continue
        if span.get("item_index") is None:
            continue
        item = plan["items"][span["item_index"]]
        if not item.get("transition"):
            errors.append(f"{span['role']}→{nxt['role']} 缺少转场")

    lines = [f"清单：{plan.get('name', '-')}   总时长 {int(total // 60):02d}:{total % 60:05.2f}"]
    for span in spans:
        caps = [e for e in captions if _inside(e, span)]
        extra = f"  字幕 {len(caps)}" if caps else ""
        lines.append(f"  {span['index']}. {span['role']:<7} "
                     f"{float(span['start']):7.2f} → {float(span['end']):7.2f}s"
                     f" ({float(span['duration']):6.2f}s){extra}")
    lines.append(f"字幕 {len(captions)} 条，点评 {len(review_text)} 行，"
                 f"段前标注 {len(labels)} 个")
    if warnings:
        lines.append("[警告]")
        lines.extend(f"  - {w}" for w in warnings)
    if errors:
        lines.append("[错误]")
        lines.extend(f"  - {e}" for e in errors)
    else:
        lines.append("[通过] 结构与覆盖范围符合系列规则")

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "total": total, "report": "\n".join(lines)}


def verify_draft(draft_root: str | Path, name: str) -> Dict[str, object]:
    """读取生成后的草稿并检查同轨重叠；草稿被剪映加密时给出提示。"""
    path = Path(draft_root) / name / "draft_content.json"
    if not path.is_file():
        raise FileNotFoundError(f"草稿不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": None, "report": f"{path} 已不是明文 JSON（多半被剪映打开后加密），"
                                      f"无法自动校验；请改用 verify --plan 校验清单。"}

    def sec(v) -> float:
        return float(v or 0) / 1e6

    errors: List[str] = []
    lines: List[str] = []
    for track in data.get("tracks", []):
        segs = track.get("segments") or []
        if not segs:
            continue
        lines.append(f"{track.get('type')} / {track.get('name')}  ({len(segs)} 段)")
        rows = []
        for seg in segs:
            tr = seg.get("target_timerange") or {}
            rows.append((sec(tr.get("start")), sec(tr.get("start")) + sec(tr.get("duration"))))
        rows.sort()
        for (s1, e1), (s2, _) in zip(rows, rows[1:]):
            if s2 < e1 - 0.05:
                errors.append(f"{track.get('name')} 轨道重叠：{s1:.2f}-{e1:.2f} 与 {s2:.2f}")
        for s, e in rows[:40]:
            lines.append(f"  {s:8.2f} → {e:8.2f}")
        if len(rows) > 40:
            lines.append(f"  ... 其余 {len(rows) - 40} 段省略")

    lines.append("[通过] 同轨无重叠" if not errors else "[错误]")
    lines.extend(f"  - {e}" for e in errors)
    return {"ok": not errors, "errors": errors, "report": "\n".join(lines)}


def verify_plan_file(path: str | Path) -> Dict[str, object]:
    return verify_plan(_load_plan(path))


def print_report(info: Dict[str, object]) -> None:
    print(info["report"])
