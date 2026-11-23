# -*- coding: utf-8 -*-
"""参考视频分析器。

从一条已发布的同系列成片里反推四样东西：
1. 镜头切点（决定新视频的画面节奏）；
2. 字幕带的纵向位置（不同账号/系列位置不同，必须自动定位）；
3. 字幕显示区间（只在这些时间段显示字幕，而不是全片铺满）；
4. 响度段落（用来粗略判断"舞台/演唱段"的位置）。

字幕定位思路：字幕是"大量白色细笔画"，用 HSV 阈值取白色像素，再做连通域
筛选出字号大小的字形块；统计这些字形块在纵向的分布，分布最集中、且在时间上
反复出现/消失的那几条横带就是字幕带。相比边缘统计，这种方法不会被黑边、
画面边框、白衣服等大块白色区域干扰。
"""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

BAND_TOP, BAND_BOTTOM = 0.40, 0.95   # 字幕搜索范围（相对画面高度）
ROWS = 24                            # 搜索带内的纵向分档数
SAMPLE_WIDTH = 360                   # 采样缩小后的宽度


# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------

def _merge_runs(flags: Sequence[bool], times: Sequence[float],
                min_dur: float, max_gap: float) -> List[Tuple[float, float]]:
    """把逐采样点的布尔序列压成时间区间。"""
    runs: List[List[float]] = []
    for flag, t in zip(flags, times):
        if flag:
            if runs and t - runs[-1][1] <= max_gap:
                runs[-1][1] = t
            else:
                runs.append([t, t])
    step = (times[1] - times[0]) if len(times) > 1 else 0.0
    return [(s, e + step) for s, e in runs if (e + step - s) >= min_dur]


def _robust_threshold(values: np.ndarray, k: float, floor: float) -> float:
    """中位数 + k 倍 MAD 的稳健阈值。"""
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med))) * 1.4826
    return max(med + k * mad, floor)


def _nearest_before(cuts: Sequence[float], target: float, window: float = 3.0) -> float:
    """取 target 之前、距离不超过 window 的最近切点。"""
    near = [c for c in cuts if 0 < target - c <= window]
    return max(near) if near else target


# ----------------------------------------------------------------------------
# 音频响度
# ----------------------------------------------------------------------------

def _loud_windows(video_path: Path, tmp_wav: Path) -> List[Tuple[float, float]]:
    """抽单声道音频并算 RMS 包络，返回明显高于底噪的区间。"""
    try:
        import imageio_ffmpeg

        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video_path),
             "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(tmp_wav)],
            check=True,
        )
        with wave.open(str(tmp_wav), "rb") as wf:
            rate = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
    except Exception:  # noqa: BLE001
        return []
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    hop = max(1, int(rate * 0.1))
    if len(data) < hop:
        return []
    frames = len(data) // hop
    rms = np.array([float(np.sqrt(np.mean(data[i * hop:(i + 1) * hop] ** 2)))
                    for i in range(frames)], dtype=np.float32)
    thr = max(_robust_threshold(rms, 1.5, 0.02), float(np.percentile(rms, 60)))
    times = [i * 0.1 for i in range(frames)]
    return _merge_runs((rms > thr).tolist(), times, min_dur=1.2, max_gap=0.6)


# ----------------------------------------------------------------------------
# 字幕：白色字形检测
# ----------------------------------------------------------------------------

def _glyph_boxes(hsv: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """从画面里找出"像字"的白色连通域，返回 (x, y, w, h)。"""
    h, w = hsv.shape[:2]
    v, s = hsv[:, :, 2], hsv[:, :, 1]
    white = ((v >= 190) & (s <= 90)).astype(np.uint8)
    num, _, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)

    boxes: List[Tuple[int, int, int, int]] = []
    min_h, max_h = max(4, int(h * 0.018)), int(h * 0.10)
    for i in range(1, num):
        x, y, bw, bh, area = stats[i]
        if bh < min_h or bh > max_h or bw < 3 or bw > int(w * 0.4):
            continue
        if area < 0.18 * bw * bh or bh > bw * 2.4:
            continue
        boxes.append((int(x), int(y), int(bw), int(bh)))
    return boxes


def _row_hits(boxes: Sequence[Tuple[int, int, int, int]], fh: int) -> np.ndarray:
    """把字形块按纵向位置投到 ROWS 个档位里。"""
    hits = np.zeros(ROWS, dtype=np.float32)
    span = (BAND_BOTTOM - BAND_TOP) * fh
    for _, y, _, bh in boxes:
        c = y + bh / 2
        if c < BAND_TOP * fh or c > BAND_BOTTOM * fh:
            continue
        k = int((c - BAND_TOP * fh) / span * ROWS)
        hits[min(ROWS - 1, max(0, k))] += 1.0
    return hits


def _text_lines(boxes: Sequence[Tuple[int, int, int, int]],
                width: int) -> List[List[Tuple[int, int, int, int]]]:
    """把同一水平线上的字形块归成一行，只保留像整行字幕的行。"""
    if not boxes:
        return []
    order = sorted(enumerate(boxes), key=lambda ib: ib[1][1] + ib[1][3] / 2)
    heights = [b[3] for _, b in order]
    med_h = float(np.median(heights))
    used = set()
    lines: List[List[Tuple[int, int, int, int]]] = []
    for pos, (_, base) in enumerate(order):
        if pos in used:
            continue
        cy = base[1] + base[3] / 2
        members = [(p, b) for p, (_, b) in enumerate(order)
                   if abs(b[1] + b[3] / 2 - cy) <= 0.5 * med_h]
        if len(members) < 3:
            continue
        xs = [b[0] for _, b in members]
        span = (max(xs) + max(b[2] for _, b in members)) - min(xs)
        if span < width * 0.06:
            continue
        used.update(p for p, _ in members)
        lines.append([b for _, b in members])
    return lines


def _pick_band(hist: np.ndarray) -> Tuple[int, int]:
    """在纵向分布里挑出最集中的连续 2~5 个档位。"""
    best = (0, 0, -1.0)
    for n in range(2, 6):
        for lo in range(0, ROWS - n + 1):
            score = float(hist[lo:lo + n].sum())
            if score > best[2]:
                best = (lo, lo + n - 1, score)
    return best[0], best[1]


# ----------------------------------------------------------------------------
# 段落猜测
# ----------------------------------------------------------------------------

def _guess_sections(duration: float, cuts: List[float],
                    loud: List[Tuple[float, float]]) -> List[Dict[str, object]]:
    """按响度最长段猜"舞台段"，再切出开场/原创 demo/尾声。初稿可手改。"""
    stage: Optional[Tuple[float, float]] = None
    if loud:
        cand = max(loud, key=lambda w: w[1] - w[0])
        if cand[1] - cand[0] >= duration * 0.15:
            stage = cand
    if stage is None:
        stage = (duration * 0.35, duration * 0.85)

    stage_start, stage_end = stage
    hook_end = _nearest_before(cuts, min(4.5, max(2.0, stage_start * 0.25)))
    if hook_end <= 0.5:
        hook_end = min(3.0, stage_start)

    sections = [
        {"name": "hook", "start": 0.0, "end": round(hook_end, 3)},
        {"name": "demo", "start": round(hook_end, 3), "end": round(stage_start, 3)},
        {"name": "stage", "start": round(stage_start, 3), "end": round(stage_end, 3)},
        {"name": "outro", "start": round(stage_end, 3), "end": round(duration, 3)},
    ]
    cleaned = [s for s in sections if float(s["end"]) - float(s["start"]) > 0.2]
    return cleaned or [{"name": "main", "start": 0.0, "end": round(duration, 3)}]


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------

def analyze_video(video_path: str | Path, *, sample_fps: float = 8.0,
                  preview_dir: Optional[Path] = None,
                  log=print) -> Dict[str, object]:
    """分析参考视频，返回系列模板字典。"""
    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(f"参考视频不存在：{video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频：{video_path}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)
    stride = max(1, int(round(src_fps / sample_fps)))
    small_h = max(1, int(SAMPLE_WIDTH * height / width))

    times: List[float] = []
    diffs: List[float] = []
    frames_lines: List[List[List[Tuple[int, int, int, int]]]] = []
    preview_frames: List[np.ndarray] = []

    prev_hist = None
    prev_gray = None
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % stride == 0:
            t = idx / src_fps
            small = cv2.resize(frame, (SAMPLE_WIDTH, small_h))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            if prev_hist is None:
                d_hist, d_pix = 0.0, 0.0
            else:
                d_hist = 1.0 - float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL))
                d_pix = float(np.mean(cv2.absdiff(gray, prev_gray))) / 255.0

            frames_lines.append(_text_lines(_glyph_boxes(hsv), SAMPLE_WIDTH))
            times.append(t)
            diffs.append(max(d_hist, d_pix * 2.0))
            preview_frames.append(frame)

            prev_hist, prev_gray = hist, gray
        idx += 1
    cap.release()

    if len(times) < 4:
        raise RuntimeError("视频太短或无法解码，未采集到足够帧")

    duration = round(times[-1], 3)
    d_arr = np.array(diffs, dtype=np.float32)

    # 1) 切点
    cut_thr = _robust_threshold(d_arr[1:], 6.0, 0.18)
    cuts: List[float] = [0.0]
    for i in range(1, len(d_arr) - 1):
        if d_arr[i] >= cut_thr and d_arr[i] >= d_arr[i - 1] and d_arr[i] >= d_arr[i + 1]:
            if times[i] - cuts[-1] >= 0.45:
                cuts.append(round(times[i], 3))
    if duration - cuts[-1] < 0.4 and len(cuts) > 1:
        cuts.pop()

    # 2) 字幕：字形块的纵向分布 → 字幕带 → 字幕区间 → 位置/字号
    all_boxes = [b for lines in frames_lines for line in lines for b in line]
    row_hist = np.array([_row_hits(line, small_h) for lines in frames_lines for line in lines],
                        dtype=np.float32)
    hist_sum = row_hist.sum(axis=0) if len(row_hist) else np.zeros(ROWS, dtype=np.float32)
    sub_windows: List[Tuple[float, float]] = []
    strip_ratio = [0.0, 0.0]
    transform_x, transform_y, est_size = 0.0, -0.72, 8.0
    band_lo = band_hi = -1
    text_frame_ratio = 0.0

    if all_boxes and hist_sum.max() >= len(times) * 0.08:
        band_lo, band_hi = _pick_band(hist_sum)
        step = (BAND_BOTTOM - BAND_TOP) / ROWS
        strip_ratio = [BAND_TOP + band_lo * step, BAND_TOP + (band_hi + 1) * step]
        lo_y, hi_y = strip_ratio[0] * small_h, strip_ratio[1] * small_h

        # 只保留落在字幕带内的整行字幕
        in_band: List[List[Tuple[int, int, int, int]]] = []
        for lines in frames_lines:
            sel = [b for line in lines for b in line if lo_y <= b[1] + b[3] / 2 <= hi_y]
            in_band.append(sel)

        flags = [len(sel) >= 3 for sel in in_band]
        sub_windows = _merge_runs(flags, times, min_dur=0.35, max_gap=0.25)
        text_frame_ratio = round(float(np.mean(flags)), 3)

        hit_frames = [sel for sel in in_band if len(sel) >= 3]
        if hit_frames:
            centers = [(b[0] + b[2] / 2) / SAMPLE_WIDTH for sel in hit_frames for b in sel]
            transform_x = round(2.0 * float(np.median(centers)) - 1.0, 3)
            heights = [b[3] / small_h for sel in hit_frames for b in sel]
            line_ratio = float(np.percentile(heights, 75))
            # 单行字高约占画面 4.5% 时对应剪映 8 号字
            est_size = round(min(14.0, max(6.0, line_ratio / 0.045 * 8.0)), 1)
        transform_y = round(1.0 - (strip_ratio[0] + strip_ratio[1]), 3)
        log(f"字幕带命中：第 {band_lo}-{band_hi} 档"
            f"（高度比例 {strip_ratio[0]:.3f}~{strip_ratio[1]:.3f}），"
            f"含字幕帧 {int(sum(flags))}/{len(flags)}")
    else:
        log("未检测到字幕层（可能这条片子本身没字幕）")

    loud = _loud_windows(video_path, video_path.with_suffix(".jys.tmp.wav"))
    try:
        video_path.with_suffix(".jys.tmp.wav").unlink(missing_ok=True)
    except OSError:
        pass

    sections = _guess_sections(duration, cuts, loud)
    shots = [{"start": cuts[i], "end": cuts[i + 1]} for i in range(len(cuts) - 1)]
    shots.append({"start": cuts[-1], "end": duration})

    # 3) 校验截图：画框标出字幕带，便于肉眼确认
    if preview_dir is not None:
        preview_dir = Path(preview_dir)
        preview_dir.mkdir(parents=True, exist_ok=True)
        for n, (s, e) in enumerate(sub_windows[:60], 1):
            k = min(range(len(times)), key=lambda i: abs(times[i] - (s + e) / 2))
            vis = preview_frames[k].copy()
            if strip_ratio[1] > 0:
                y0 = int(vis.shape[0] * strip_ratio[0])
                y1 = int(vis.shape[0] * strip_ratio[1])
                cv2.rectangle(vis, (0, y0), (vis.shape[1] - 1, y1), (0, 0, 255), 3)
            cv2.putText(vis, f"{s:.1f}-{e:.1f}s", (12, max(28, int(vis.shape[0] * 0.06))),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
            cv2.imwrite(str(preview_dir / f"sub_{n:02d}_{s:.1f}s.jpg"), vis)
        log(f"校验截图已输出：{preview_dir}")

    return {
        "version": 1,
        "name": video_path.stem,
        "source": {"type": "video", "path": str(video_path)},
        "canvas": {"width": width, "height": height, "fps": int(round(src_fps))},
        "duration": duration,
        "shots": shots,
        "sections": sections,
        "subtitles": {
            "windows": [{"start": round(s, 3), "end": round(e, 3)} for s, e in sub_windows],
            "strip": [round(strip_ratio[0], 3), round(strip_ratio[1], 3)],
            "placement": {"transform_x": transform_x, "transform_y": transform_y},
            "style": {"size": est_size, "color": [1.0, 1.0, 1.0], "bold": False,
                      "border": True, "border_width": 40},
            "chars_per_sec": 4.5,
            "max_chars_per_line": 16,
            "confidence": round(float(hist_sum.max() / max(1, len(times))), 2),
        },
        "audio": {"loud": [{"start": round(s, 3), "end": round(e, 3)} for s, e in loud]},
        "analysis": {
            "sample_fps": sample_fps,
            "cut_count": len(shots),
            "subtitle_window_count": len(sub_windows),
            "text_frame_ratio": text_frame_ratio,
        },
    }


def write_template(template: Dict[str, object], out_path: str | Path) -> Path:
    """把模板写成 UTF-8 JSON 文件。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
