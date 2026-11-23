# -*- coding: utf-8 -*-
"""系列模板的读写与可视化摘要。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


def load_template(path: str | Path) -> Dict:
    """读取模板 JSON，并做最小结构校验。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"模板不存在：{p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    missing = [k for k in ("canvas", "duration", "shots", "sections", "subtitles") if k not in data]
    if missing:
        raise ValueError(f"模板缺少字段：{', '.join(missing)}")
    if not data["shots"]:
        raise ValueError("模板 shots 为空，请重新分析参考视频")
    return data


def save_template(template: Dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def summarize(template: Dict) -> str:
    """生成一份人能快速核对的节奏表。"""
    canvas = template.get("canvas", {})
    sub = template.get("subtitles", {})
    lines: List[str] = []
    lines.append(f"模板：{template.get('name', '-')}   来源：{template.get('source', {}).get('type', '-')}")
    lines.append(f"画布：{canvas.get('width')}x{canvas.get('height')} @ {canvas.get('fps')}fps   "
                 f"总时长：{template.get('duration')}s")
    lines.append("")
    lines.append("段落：")
    for s in template.get("sections", []):
        lines.append(f"  {s['name']:<6} {s['start']:>6.2f} → {s['end']:>6.2f}s   "
                     f"({float(s['end']) - float(s['start']):.2f}s)")
    lines.append("")
    shots = template.get("shots", [])
    lines.append(f"镜头：{len(shots)} 个")
    for i, sh in enumerate(shots[:12], 1):
        lines.append(f"  #{i:<2} {sh['start']:>6.2f} → {sh['end']:>6.2f}s")
    if len(shots) > 12:
        lines.append(f"  ... 其余 {len(shots) - 12} 个省略")
    lines.append("")
    windows = sub.get("windows", [])
    lines.append(f"字幕区间：{len(windows)} 段（只在这些时间段显示字幕）")
    for w in windows[:10]:
        lines.append(f"  {w['start']:>6.2f} → {w['end']:>6.2f}s")
    if len(windows) > 10:
        lines.append(f"  ... 其余 {len(windows) - 10} 段省略")
    lines.append("")
    st = sub.get("style", {})
    lines.append(f"字幕样式：字号 {st.get('size')}，颜色 {st.get('color')}，"
                 f"纵向位置 {sub.get('placement', {}).get('transform_y')}")
    return "\n".join(lines)
