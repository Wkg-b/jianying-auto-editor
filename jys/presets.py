# -*- coding: utf-8 -*-
"""剪映原生预设文本模板库。

原型是从「11月15日」草稿里提取的文本模板（预设 1/6/11/12），包含字体、
渐变/纹理填充、描边、发光动画等全部样式。生成新草稿时克隆一份并重映射 id，
再把文本内容替换成本集文案，这样视觉和剪映里手动加预设文本完全一致。
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _new_id() -> str:
    return str(uuid.uuid4()).upper()


def _remap(obj, id_map: Dict[str, str]):
    if isinstance(obj, str):
        return id_map.get(obj, obj)
    if isinstance(obj, list):
        return [_remap(x, id_map) for x in obj]
    if isinstance(obj, dict):
        return {k: _remap(v, id_map) for k, v in obj.items()}
    return obj


class PresetLibrary:
    """读取 presets_*.json，按预设编号克隆出可注入草稿的素材和片段。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: Dict[str, Dict] = json.loads(self.path.read_text(encoding="utf-8"))

    def has(self, key) -> bool:
        return str(key) in self.data

    def keys(self) -> List[str]:
        return sorted(self.data)

    def build(self, key, *, text: Optional[str] = None, start_us: int = 0,
              duration_us: int = 1_000_000,
              transform_x: Optional[float] = None,
              transform_y: Optional[float] = None,
              scale: Optional[float] = None) -> Tuple[List[Tuple[str, Dict]], Dict, Dict]:
        """返回 (素材列表[(kind, obj)], 片段, 文本轨道原型)。"""
        proto = self.data[str(key)]
        mats = proto["materials"]
        id_map = {old: _new_id() for old in mats}

        new_mats: List[Tuple[str, Dict]] = []
        for old, item in mats.items():
            obj = _remap(copy.deepcopy(item["obj"]), id_map)
            new_mats.append((item["kind"], obj))

        for kind, obj in new_mats:
            if kind == "texts" and text is not None:
                content = obj.get("content")
                if isinstance(content, str) and content.lstrip().startswith("{"):
                    try:
                        c = json.loads(content)
                        c["text"] = text
                        obj["content"] = json.dumps(c, ensure_ascii=False)
                    except json.JSONDecodeError:
                        obj["content"] = text
                else:
                    obj["content"] = text
            if kind == "text_templates":
                for ti in obj.get("text_info_resources") or []:
                    ai = ti.get("attach_info")
                    if isinstance(ai, dict):
                        ai["duration"] = int(duration_us)
                        ai["start_time"] = 0

        seg = _remap(copy.deepcopy(proto["segment"]), id_map)
        seg["id"] = _new_id()
        seg["target_timerange"] = {"start": int(start_us), "duration": int(duration_us)}
        seg["source_timerange"] = None
        clip = seg.setdefault("clip", {})
        if scale is not None:
            clip["scale"] = {"x": float(scale), "y": float(scale)}
        tr = clip.setdefault("transform", {"x": 0.0, "y": 0.0})
        if transform_x is not None:
            tr["x"] = float(transform_x)
        if transform_y is not None:
            tr["y"] = float(transform_y)

        track = copy.deepcopy(proto.get("text_track") or {"type": "text"})
        track["id"] = _new_id()
        track["segments"] = []
        return new_mats, seg, track
