# -*- coding: utf-8 -*-
"""素材生成：定格画面、抽高光音频、朗读。

这些产物都会被写进单集 assets 目录，再由 compose 装配进剪辑清单。
"""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(cmd) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def _digest(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:8]


def make_freeze(src: str, at: float, duration: float, out_dir: str | Path,
                width: int, height: int, fps: int) -> Path:
    """截取 src 的 at 秒画面，生成 duration 秒的定格视频。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = f"freeze_{_digest(src, at, round(duration, 2), width, height)}"
    out = out_dir / f"{key}.mp4"
    if out.is_file() and out.stat().st_size > 0:
        return out
    jpg = out_dir / f"{key}.jpg"
    _run([ffmpeg_exe(), "-y", "-ss", f"{at:.3f}", "-i", src,
          "-frames:v", "1", "-q:v", "2", str(jpg)])
    _run([ffmpeg_exe(), "-y", "-loop", "1", "-i", str(jpg),
          "-t", f"{duration:.2f}", "-r", str(fps),
          "-vf", (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                  f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])
    return out


def extract_frame(src: str, at: float, out_dir: str | Path) -> Path:
    """截取 src 的 at 秒画面，输出一张 jpg（用于过渡图等静帧）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = f"frame_{_digest(src, round(at, 3))}"
    out = out_dir / f"{key}.jpg"
    if out.is_file() and out.stat().st_size > 0:
        return out
    _run([ffmpeg_exe(), "-y", "-ss", f"{at:.3f}", "-i", src,
          "-frames:v", "1", "-q:v", "2", str(out)])
    return out


def extract_audio(src: str, start: float, duration: float,
                  out_dir: str | Path) -> Path:
    """从 src 的 start 秒起截一段音频，作为点评高光 BGM。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = f"sound_{_digest(src, start, round(duration, 2))}"
    out = out_dir / f"{key}.m4a"
    if out.is_file() and out.stat().st_size > 0:
        return out
    _run([ffmpeg_exe(), "-y", "-ss", f"{start:.3f}", "-t", f"{duration:.2f}",
          "-i", src, "-vn", "-ac", "2", "-ar", "44100",
          "-c:a", "aac", "-b:a", "192k", str(out)])
    return out


def make_tts(text: str, out_dir: str | Path, voice: str,
             rate: str = "+0%") -> Path:
    """用 edge-tts 生成朗读音频。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = f"voice_{_digest(text, voice, rate)}"
    out = out_dir / f"{key}.mp3"
    if out.is_file() and out.stat().st_size > 0:
        return out

    async def go() -> None:
        import edge_tts

        tts = edge_tts.Communicate(text, voice, rate=rate)
        await tts.save(str(out))

    asyncio.run(go())
    return out


def audio_duration(path: str | Path) -> float:
    """返回音频时长（秒）。"""
    from pyJianYingDraft.local_materials import AudioMaterial

    return AudioMaterial(str(path)).duration / 1e6


def probe_resolution(src: str | Path) -> tuple[int, int]:
    """用 cv2 探测视频分辨率，失败时回退 1920x1080。"""
    import cv2

    cap = cv2.VideoCapture(str(src))
    try:
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    if w > 0 and h > 0:
        return w, h
    return 1920, 1080
