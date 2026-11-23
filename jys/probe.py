# -*- coding: utf-8 -*-
"""从原始录屏里给出候选时间点，供人工确认后写入单集配置。

识别三类信号：
1. 大标题卡：画面上半部出现大块白色文字（歌名/演唱人标题），通常是
   DEMO 或舞台的起点；
2. 响度段：明显高于底噪的连续声音，通常是演唱/舞台；
3. 切点：画面突变，作为候选边界的对齐点。

输出只是候选，不直接生成草稿。用户确认/微调后再 compose。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .analyze import _loud_windows, _merge_runs

SAMPLE_WIDTH = 480
TITLE_TOP, TITLE_BOTTOM = 0.10, 0.62   # 标题搜索范围（避免字幕带）


def _title_present(small_bgr: np.ndarray) -> bool:
    """画面中上部是否有成组的大号白色文字。"""
    h, w = small_bgr.shape[:2]
    hsv = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] >= 200) & (hsv[:, :, 1] <= 60)).astype(np.uint8)
    white[: int(h * TITLE_TOP), :] = 0
    white[int(h * TITLE_BOTTOM):, :] = 0
    num, _, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    min_h, max_h = h * 0.045, h * 0.16
    total_w, count = 0, 0
    for i in range(1, num):
        x, y, bw, bh, area = stats[i]
        if not (min_h <= bh <= max_h) or bw < 4 or bw > w * 0.7:
            continue
        if area < 0.20 * bw * bh:
            continue
        total_w += bw
        count += 1
    return count >= 2 and total_w >= w * 0.22


def _cut_times(times: Sequence[float], diffs: Sequence[float]) -> List[float]:
    arr = np.array(diffs, dtype=np.float32)
    if len(arr) < 3:
        return [0.0]
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med))) * 1.4826
    thr = max(med + 6.0 * mad, 0.16)
    cuts = [0.0]
    for i in range(1, len(arr) - 1):
        if arr[i] >= thr and arr[i] >= arr[i - 1] and arr[i] >= arr[i + 1]:
            if times[i] - cuts[-1] >= 0.5:
                cuts.append(round(times[i], 3))
    return cuts


def _snap(cuts: Sequence[float], t: float, before: float = 2.5) -> float:
    near = [c for c in cuts if 0 <= t - c <= before]
    return max(near) if near else t


def _merge_intervals(windows: Sequence[Tuple[float, float]],
                     max_gap: float = 5.0) -> List[List[float]]:
    """把响度段按间隙合并，避免伴奏里的弱拍把一段演唱切碎。"""
    out: List[List[float]] = []
    for s, e in sorted(windows):
        if out and s - out[-1][1] <= max_gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def _probe_one(path: Path, sample_fps: float, log=print) -> Dict[str, object]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频：{path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)
    stride = max(1, int(round(src_fps / sample_fps)))
    small_h = max(1, int(SAMPLE_WIDTH * height / width))

    times: List[float] = []
    diffs: List[float] = []
    title_flags: List[bool] = []
    prev_hist = None
    prev_gray = None
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % stride == 0:
            small = cv2.resize(frame, (SAMPLE_WIDTH, small_h))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if prev_hist is None:
                d_hist = d_pix = 0.0
            else:
                d_hist = 1.0 - float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL))
                d_pix = float(np.mean(cv2.absdiff(gray, prev_gray))) / 255.0
            times.append(idx / src_fps)
            diffs.append(max(d_hist, d_pix * 2.0))
            title_flags.append(_title_present(small))
            prev_hist, prev_gray = hist, gray
        idx += 1
    cap.release()
    if len(times) < 4:
        raise RuntimeError(f"视频太短或无法解码：{path}")

    duration = round(times[-1], 3)
    cuts = _cut_times(times, diffs)
    title_runs = _merge_runs(title_flags, times, min_dur=0.6, max_gap=1.2)

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "probe.wav"
        loud = _loud_windows(path, wav)

    stages: List[Dict[str, object]] = []
    merged = _merge_intervals(loud)
    for s, e in merged:
        if e - s < 30:
            continue
        start = round(_snap(cuts, s), 2)
        end = round(e, 2)
        highlights = []
        for ratio in (0.35, 0.55, 0.75):
            point = round(_snap(cuts, start + (end - start) * ratio), 2)
            highlights.append({"frame": point, "bgm": round(point + 10.0, 2)})
        stages.append({"src": str(path), "start": start, "end": end,
                       "duration": round(end - start, 2),
                       "confidence": "high", "highlights": highlights})
    if not stages:
        # 兜底：没有 30 秒以上的连续段时，给出较长的几段并标明置信度低
        for s, e in merged:
            if e - s < 10:
                continue
            start = round(_snap(cuts, s), 2)
            end = round(e, 2)
            mid = start + (end - start) * 0.5
            stages.append({"src": str(path), "start": start, "end": end,
                           "duration": round(end - start, 2),
                           "confidence": "low",
                           "highlights": [{"frame": round(mid, 2),
                                           "bgm": round(mid + 10.0, 2)}]})
    stages.sort(key=lambda x: float(x["duration"]), reverse=True)

    log(f"{path.name}：{duration:.1f}s，切点 {len(cuts)}，标题卡 {len(title_runs)} 段，"
        f"长响度段 {len(stages)} 段")
    return {
        "path": str(path),
        "duration": duration,
        "canvas": {"width": width, "height": height, "fps": int(round(src_fps))},
        "cuts": cuts,
        "title_windows": [{"start": round(s, 2), "end": round(e, 2)} for s, e in title_runs],
        "loud_windows": [{"start": round(s, 2), "end": round(e, 2)} for s, e in loud],
        "stage_candidates": stages,
    }


def probe(videos: Sequence[str | Path], *, sample_fps: float = 2.0,
          log=print) -> Dict[str, object]:
    sources = [_probe_one(Path(v), sample_fps, log=log) for v in videos]

    # 建议：第一个源里第一张标题卡作为 DEMO 起点；最长的两个舞台作为正片
    demo = None
    for src in sources:
        windows = src.get("title_windows") or []
        if windows:
            start = max(0.0, float(windows[0]["start"]) - 0.3)
            demo = {"src": src["path"], "start": round(start, 2),
                    "end": round(min(float(src["duration"]), start + 180.0), 2)}
            break
    stages = [s for src in sources for s in (src.get("stage_candidates") or [])][:2]
    return {
        "version": 1,
        "sample_fps": sample_fps,
        "sources": sources,
        "suggested": {"demo": demo, "stages": stages},
    }


def apply_suggestions(episode: Dict, data: Dict) -> Dict:
    """把 probe 的候选时间填进单集骨架（标注、文字、字幕仍需人工补）。"""
    sug = data.get("suggested") or {}
    demo = sug.get("demo")
    stages = list(sug.get("stages") or [])
    stage_i = 0
    prev_src = None
    prev_highlights: List[Dict] = []
    prev_filled = False

    for seg in episode.get("segments") or []:
        role = seg.get("role")
        if role == "demo" and demo:
            seg.update({"src": demo["src"], "start": demo["start"], "end": demo["end"]})
            prev_src = demo["src"]
            mid = (float(demo["start"]) + float(demo["end"])) / 2
            prev_highlights = [{"frame": round(mid, 2), "bgm": round(mid + 10, 2)}]
            prev_filled = True
        elif role == "stage" and stage_i < len(stages):
            st = stages[stage_i]
            stage_i += 1
            seg.update({"src": st["src"], "start": st["start"], "end": st["end"]})
            prev_src = st["src"]
            prev_highlights = list(st.get("highlights") or [])
            prev_filled = True
        elif role == "review" and prev_src and prev_filled:
            hint = prev_highlights[0] if prev_highlights else {"frame": 0.0, "bgm": 0.0}
            seg.update({"src": prev_src, "frame": hint["frame"], "bgm": hint["bgm"]})
        elif role in ("demo", "stage"):
            prev_filled = False
    return episode


def write_probe(data: Dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
