# -*- coding: utf-8 -*-
"""把剪映预设文本渲染成透明 PNG。

剪映 8.9 的草稿里直接挂 effect_id 经常只显示普通文字，所以这里读取预设
自带的字体文件，在本地把预设 1 / 6 / 11 / 12 画成全画布 PNG，再作为
图片轨叠加。显示时段、尺寸和位置都由本工具控制，稳定可复现。

缺点：生成的是图片，不是剪映原生可编辑花字；要改字必须重新生成。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
from pathlib import Path
from typing import Dict, List, Sequence

from PIL import Image, ImageDraw, ImageFont

BASE_WIDTH = 3234


def preset_dir() -> Path:
    """定位剪映文本预设目录。"""
    candidates = [
        os.environ.get("JY_PRESET_DIR", ""),
        os.path.expandvars(r"%LOCALAPPDATA%\JianyingPro\User Data\Presets\Text_V2"),
    ]
    for item in candidates:
        if item and Path(item).is_dir():
            return Path(item)
    raise FileNotFoundError(
        "找不到剪映文本预设目录，请设置环境变量 JY_PRESET_DIR 指向 "
        r"%LOCALAPPDATA%\JianyingPro\User Data\Presets\Text_V2"
    )


def preset_font(n: int, contains: str) -> str:
    """从预设文本 n 里取出字体文件路径（按文件名关键字匹配）。"""
    path = preset_dir() / f"预设文本{n}.textpreset"
    if not path.is_file():
        raise FileNotFoundError(f"预设文本{n} 不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    for res in data.get("resources", []):
        if res.get("panel") == "fonts" and contains in res.get("file_path", ""):
            return res["file_path"]
    raise FileNotFoundError(f"预设文本{n} 里找不到字体 {contains}")


def _star_points(cx: float, cy: float, r_out: float, r_in: float, n: int,
                 rot: float = 0.0, jitter: float = 0.0, seed: int = 0,
                 squash: float = 1.0) -> List[tuple]:
    rnd = random.Random(seed)
    pts = []
    for i in range(n * 2):
        ang = rot + math.pi * i / n
        r = r_out if i % 2 == 0 else r_in
        r *= 1 + rnd.uniform(-jitter, jitter)
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang) * squash))
    return pts


class OverlayRenderer:
    """按单集素材目录渲染并缓存花字 PNG。"""

    def __init__(self, asset_dir: str | Path, width: int, height: int,
                 supersample: int = 2):
        self.dir = Path(asset_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.width = int(width)
        self.height = int(height)
        self.ss = int(supersample)
        # 原始坐标按 3234 宽设计，这里等比例缩放
        self.s = self.ss * self.width / BASE_WIDTH
        self.manifest: Dict[str, str] = {}

    # ------------------------------------------------------------------ 基础
    def _canvas(self):
        img = Image.new("RGBA", (self.width * self.ss, self.height * self.ss),
                        (0, 0, 0, 0))
        return img, ImageDraw.Draw(img)

    def _save(self, img: Image.Image, key: str) -> str:
        img = img.resize((self.width, self.height), Image.LANCZOS)
        digest = hashlib.md5(img.tobytes()).hexdigest()[:8]
        path = self.dir / f"{key}_{digest}.png"
        if not path.exists():
            img.save(path)
        self.manifest[key] = str(path)
        return str(path)

    # ------------------------------------------------------------ 预设11/12
    def badge(self, preset: int, key: str | None = None) -> str:
        key = key or f"preset{preset}"
        if preset == 11:
            return self._badge_11(key)
        if preset == 12:
            return self._badge_12(key)
        raise ValueError(f"不支持把预设{preset}渲染成角标")

    def _badge_11(self, key: str) -> str:
        """左上白色胶囊「考研中 ✓」。"""
        img, d = self._canvas()
        s = self.s
        x, y, w, h = 130 * s, 40 * s, 760 * s, 190 * s
        d.rounded_rectangle((x, y, x + w, y + h), radius=30 * s,
                            fill=(255, 255, 255, 255))
        font = ImageFont.truetype(preset_font(11, "新青年体"), int(122 * s))
        d.text((x + 56 * s, y + h / 2), "考研中", font=font,
               fill=(20, 20, 20, 255), anchor="lm")
        bx = x + w - 190 * s
        d.rounded_rectangle((bx, y + 26 * s, bx + 140 * s, y + h - 26 * s),
                            radius=24 * s, fill=(58, 58, 58, 255))
        px, py = bx + 70 * s, y + h / 2
        d.line([(px - 34 * s, py), (px - 8 * s, py + 30 * s), (px + 40 * s, py - 32 * s)],
               fill=(126, 217, 87, 255), width=int(18 * s), joint="curve")
        return self._save(img, key)

    def _badge_12(self, key: str) -> str:
        """右下白色爆炸框「NO PAUSE」。"""
        img, d = self._canvas()
        s = self.s
        cx, cy = 2660 * s, 1540 * s
        pts = _star_points(cx, cy, 425 * s, 275 * s, 14,
                           rot=-0.10, jitter=0.07, seed=7, squash=0.45)
        shadow = [(x + 16 * s, y + 20 * s) for x, y in pts]
        d.polygon(shadow, fill=(18, 18, 18, 255))
        d.polygon(pts, fill=(255, 255, 255, 255), outline=(18, 18, 18, 255),
                  width=int(14 * s))
        for ex in (-318, -228):
            d.ellipse([(cx + ex * s - 32 * s), (cy - 258 * s),
                       (cx + ex * s + 32 * s), (cy - 160 * s)],
                      fill=(255, 255, 255, 255), outline=(18, 18, 18, 255),
                      width=int(10 * s))
        size = int(140 * s)
        probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        while size > int(70 * s):
            font = ImageFont.truetype(preset_font(12, "Facon"), size)
            tw = probe.textlength("NO PAUSE", font=font)
            if tw <= 640 * s:
                break
            size -= max(1, int(6 * s))
        box_w = int(tw) + int(80 * s)
        txt = Image.new("RGBA", (box_w, int(260 * s)), (0, 0, 0, 0))
        td = ImageDraw.Draw(txt)
        td.text((box_w // 2, 130 * s), "NO PAUSE", font=font,
                fill=(245, 169, 63, 255), anchor="mm",
                stroke_width=int(11 * s), stroke_fill=(18, 18, 18, 255))
        img.alpha_composite(txt, (int(cx - box_w / 2), int(cy - 130 * s)))
        return self._save(img, key)

    # -------------------------------------------------------------- 预设1
    def label(self, text: str, key: str) -> str:
        """绿色爆炸框 + 白字「演唱人名+版」。"""
        img, d = self._canvas()
        s = self.s
        font = ImageFont.truetype(preset_font(1, "字由棒棒"), int(150 * s))
        probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        tw = probe.textlength(text, font=font)
        cx, cy = self.width * self.ss // 2, 330 * s
        half_w = int(tw / 2 + 120 * s)
        half_h = 170 * s
        pts = _star_points(cx, cy, half_w, half_h, 11,
                           rot=0.0, jitter=0.10, seed=3)
        d.polygon(pts, fill=(186, 228, 162, 255),
                  outline=(242, 163, 60, 255), width=int(12 * s))
        d.text((cx, cy), text, font=font, fill=(255, 255, 255, 255), anchor="mm",
               stroke_width=int(9 * s), stroke_fill=(70, 70, 70, 255))
        return self._save(img, key)

    # -------------------------------------------------------------- 预设6
    def review(self, lines: Sequence[str], key: str) -> str:
        """金色手写体点评，奶油色描边。"""
        img, d = self._canvas()
        s = self.s
        font = ImageFont.truetype(preset_font(6, "以梦为马"), int(160 * s))
        max_w = 2080 * s
        while True:
            widest = max(d.textlength(t, font=font) for t in lines)
            if widest <= max_w or font.size <= int(80 * s):
                break
            font = ImageFont.truetype(preset_font(6, "以梦为马"),
                                      font.size - max(1, int(6 * s)))
        line_h = 200 * s
        top = 1130 * s
        mask = Image.new("L", img.size, 0)
        md = ImageDraw.Draw(mask)
        for i, t in enumerate(lines):
            md.text((self.width * self.ss // 2, top + i * line_h), t, font=font,
                    fill=255, anchor="mm", stroke_width=int(24 * s), stroke_fill=255)
        stroke = Image.new("RGBA", img.size, (0, 0, 0, 0))
        stroke.paste((250, 238, 220, 255), (0, 0), mask)
        img.alpha_composite(stroke)
        inner = Image.new("L", img.size, 0)
        idr = ImageDraw.Draw(inner)
        for i, t in enumerate(lines):
            idr.text((self.width * self.ss // 2, top + i * line_h), t,
                     font=font, fill=255, anchor="mm")
        grad = Image.new("RGBA", img.size)
        gd = ImageDraw.Draw(grad)
        for yy in range(self.height * self.ss):
            k = yy / (self.height * self.ss)
            gd.line([(0, yy), (self.width * self.ss, yy)],
                    fill=(int(255 - 45 * k), int(196 - 60 * k), int(70 - 40 * k), 255))
        img.paste(grad, (0, 0), inner)
        return self._save(img, key)

    # ------------------------------------------------------------- manifest
    def write_manifest(self) -> Path:
        digest = hashlib.md5(
            json.dumps(self.manifest, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:8]
        path = self.dir / f"manifest_{digest}.json"
        path.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path
