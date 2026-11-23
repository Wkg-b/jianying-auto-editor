# -*- coding: utf-8 -*-
"""命令行入口。

常用：
    python run.py check
    python run.py analyze "参考成片.mp4" --preview
    python run.py show templates/归期雨抄.json
    python run.py build --template templates/归期雨抄.json --script 文案.txt --name 新一期
    python run.py build ... --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import build, config, template as tpl_mod


def _utf8_console() -> None:
    """Windows 控制台默认 GBK，统一切到 UTF-8 避免中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def _cmd_check(_args) -> int:
    from . import config as cfg

    print("工作台：", cfg.ROOT)
    try:
        print("草稿目录：", cfg.draft_dir())
    except FileNotFoundError as exc:
        print("草稿目录：未找到 ->", exc)
    try:
        import pyJianYingDraft
        print("pyJianYingDraft：已安装")
    except ImportError:
        print("pyJianYingDraft：未安装（请 pip install pyJianYingDraft）")
    try:
        import cv2
        print("opencv：", cv2.__version__)
    except ImportError:
        print("opencv：未安装，analyze 无法运行")
    try:
        import imageio_ffmpeg
        print("ffmpeg：", imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:  # noqa: BLE001
        print("ffmpeg：缺失，响度分析会被跳过")
    return 0


def _cmd_analyze(args) -> int:
    from .analyze import analyze_video, write_template

    config.ensure_dirs()
    preview = Path(args.preview) if args.preview else config.OUTPUT_DIR / f"{Path(args.video).stem}_check"
    tpl = analyze_video(args.video, sample_fps=args.sample_fps, preview_dir=preview)
    out = Path(args.out) if args.out else config.TEMPLATE_DIR / f"{Path(args.video).stem}.json"
    write_template(tpl, out)
    print(tpl_mod.summarize(tpl))
    print("\n模板已保存：", out)
    return 0


def _cmd_from_draft(args) -> int:
    from .draft_reader import template_from_draft

    root = Path(args.draft_root) if args.draft_root else config.draft_dir()
    tpl = template_from_draft(root, args.draft_name)
    out = Path(args.out) if args.out else config.TEMPLATE_DIR / f"{args.draft_name}.json"
    tpl_mod.save_template(tpl, out)
    print(tpl_mod.summarize(tpl))
    print("\n模板已保存：", out)
    return 0


def _cmd_show(args) -> int:
    print(tpl_mod.summarize(tpl_mod.load_template(args.template)))
    return 0


def _cmd_list(args) -> int:
    import pyJianYingDraft as draft

    root = Path(args.draft_root) if args.draft_root else config.draft_dir()
    for name in draft.DraftFolder(str(root)).list_drafts():
        print(name)
    return 0


def _cmd_build(args) -> int:
    config.ensure_dirs()
    tpl = tpl_mod.load_template(args.template)
    script_text = Path(args.script).read_text(encoding="utf-8") if args.script else ""

    if args.dry_run:
        chunks = build.split_script(script_text, int(tpl["subtitles"].get("max_chars_per_line", 16)))
        windows = tpl["subtitles"].get("windows", [])
        items = build.assign_subtitles(windows, chunks,
                                       float(tpl["subtitles"].get("chars_per_sec", 4.5)),
                                       float(tpl.get("duration", 0)) or None)
        print(tpl_mod.summarize(tpl))
        print(f"\n[试运行] 文案切分 {len(chunks)} 条，字幕区间 {len(windows)} 段，"
              f"可排入 {len(items)} 条")
        for start, end, text in items[:15]:
            print(f"  {start:>6.2f}-{end:>6.2f}  {text}")
        if len(items) > 15:
            print(f"  ... 其余 {len(items) - 15} 条省略")
        if len(chunks) > len(items):
            print(f"  [提示] 有 {len(chunks) - len(items)} 条文案因区间不足未排入")
        return 0

    info = build.build_draft(
        tpl,
        name=args.name,
        clips_dir=args.clips,
        script_text=script_text,
        bgm=args.bgm,
        bgm_volume=args.bgm_volume,
        draft_root=args.draft_root,
    )
    print("草稿已生成：", info["draft"])
    print(f"时长 {info['duration']}s，镜头 {info['shots']} 个，字幕 {info['subtitle_items']} 条，"
          f"素材池 {info['clips']}")
    if info["script_overflow"]:
        print("[提示] 文案超出模板字幕区间，末尾部分未排入，可增加区间或缩短文案")
    print("下一步：打开剪映 → 进入草稿列表（必要时退出再进入刷新）→ 打开该草稿预览导出")
    return 0


def _cmd_build_plan(args) -> int:
    from . import plan as plan_mod

    config.ensure_dirs()
    data = plan_mod.load_plan(args.plan)
    if args.name:
        data["name"] = args.name
    info = plan_mod.build_from_plan(data, draft_root=args.draft_root)
    print("草稿已生成：", info["draft"])
    print("下一步：打开剪映 → 进入草稿列表（必要时退出再进入刷新）→ 打开该草稿预览导出")
    return 0


# ---------------------------------------------------------------------------
# 系列 / 单集 / 候选 / 装配 / 校验
# ---------------------------------------------------------------------------

def _cmd_series_show(args) -> int:
    from .series import load_series, summarize_series

    print(summarize_series(load_series(args.series)))
    return 0


def _cmd_episode_new(args) -> int:
    import json

    from .episode import new_skeleton, save_episode
    from .probe import apply_suggestions
    from .series import load_series

    config.ensure_dirs()
    series = load_series(args.series)
    episode = new_skeleton(series, args.name)
    if args.probe:
        data = json.loads(Path(args.probe).read_text(encoding="utf-8"))
        apply_suggestions(episode, data)
        print("已按候选填充 DEMO/舞台/点评时间，仍需填写标注与点评文字")
    out = Path(args.out) if args.out else config.EPISODE_DIR / f"{args.name}.json"
    save_episode(episode, out)
    print("单集骨架已生成：", out)
    print("请补全 src/start/end/label、review.text 与 captions，再运行 compose")
    return 0


def _cmd_episode_show(args) -> int:
    from .episode import load_episode, summarize_episode

    print(summarize_episode(load_episode(args.episode)))
    return 0


def _cmd_probe(args) -> int:
    from .probe import probe, write_probe

    config.ensure_dirs()
    data = probe(args.videos, sample_fps=args.sample_fps)
    out = Path(args.out) if args.out else config.OUTPUT_DIR / "probe.json"
    write_probe(data, out)
    print("\n候选已保存：", out)
    print("下一步：episode-new --series <系列> --name <单集> --probe", out)
    return 0


def _cmd_compose(args) -> int:
    from .compose import compose
    from .episode import load_episode
    from .series import load_series

    series = load_series(args.series)
    episode = load_episode(args.episode)
    if args.name:
        episode["name"] = args.name
    print(f"装配 {episode['name']}（系列 {series.get('name')}）")
    info = compose(series, episode, draft_root=args.draft_root,
                   plan_only=args.plan_only)
    print(f"\n总时长 {int(info['duration'] // 60):02d}:{info['duration'] % 60:05.2f}，"
          f"条目 {info['items']} 个")
    print("剪辑清单：", info["plan"])
    if info.get("draft"):
        print("草稿已生成：", info["draft"])
        print("下一步：打开剪映 → 草稿列表刷新 → 打开草稿核验后手动导出")
    for w in info.get("warnings") or []:
        print("[警告]", w)
    return 0


def _cmd_verify(args) -> int:
    from . import config as cfg
    from .verify import verify_draft, verify_plan_file

    if args.plan:
        info = verify_plan_file(args.plan)
    elif args.draft:
        info = verify_draft(args.draft_root or cfg.draft_dir(), args.draft)
    else:
        print("[失败] 请指定 --plan 或 --draft")
        return 1
    print(info["report"])
    return 0 if info.get("ok") else 1



def main(argv=None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(
        prog="jy_series",
        description="读取同系列成片/草稿得到模板，再批量生成新内容的剪映草稿",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="检查环境与草稿目录").set_defaults(func=_cmd_check)

    p = sub.add_parser("analyze", help="从参考成片反推模板")
    p.add_argument("video", help="同系列已发布成片路径")
    p.add_argument("-o", "--out", help="模板输出路径，默认 templates/<视频名>.json")
    p.add_argument("--preview", help="校验截图输出目录")
    p.add_argument("--sample-fps", type=float, default=8.0, help="采样帧率，默认 8")
    p.set_defaults(func=_cmd_analyze)

    p = sub.add_parser("template-from-draft", help="从明文草稿反推模板")
    p.add_argument("draft_name", help="剪映草稿名（文件夹名）")
    p.add_argument("-o", "--out", help="模板输出路径")
    p.add_argument("--draft-root", help="草稿根目录，默认自动探测")
    p.set_defaults(func=_cmd_from_draft)

    p = sub.add_parser("show", help="打印模板节奏表")
    p.add_argument("template")
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser("list-drafts", help="列出剪映草稿")
    p.add_argument("--draft-root")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("build", help="按模板生成新草稿")
    p.add_argument("--template", required=True)
    p.add_argument("--clips", default=str(config.CLIP_DIR), help="新素材目录，支持按段落分子文件夹")
    p.add_argument("--script", help="文案 txt 路径")
    p.add_argument("--name", required=True, help="新草稿名")
    p.add_argument("--bgm", help="背景音乐路径")
    p.add_argument("--bgm-volume", type=float, default=0.35)
    p.add_argument("--draft-root", help="草稿根目录，默认自动探测")
    p.add_argument("--dry-run", action="store_true", help="只预览排布，不写草稿")
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("build-plan", help="按剪辑清单 plan.json 精确生成草稿")
    p.add_argument("--plan", required=True, help="剪辑清单 JSON 路径")
    p.add_argument("--name", help="覆盖草稿名")
    p.add_argument("--draft-root", help="草稿根目录，默认自动探测")
    p.set_defaults(func=_cmd_build_plan)

    p = sub.add_parser("series-show", help="查看系列规则（结构/片头/结尾/花字）")
    p.add_argument("series", help="系列名（series/<名>.json）或路径")
    p.set_defaults(func=_cmd_series_show)

    p = sub.add_parser("episode-new", help="按系列结构创建单集骨架")
    p.add_argument("--series", required=True, help="系列名或路径")
    p.add_argument("--name", required=True, help="单集/草稿名")
    p.add_argument("--probe", help="probe 输出的候选 JSON，可用于预填时间")
    p.add_argument("-o", "--out", help="单集配置输出路径，默认 episodes/<名>.json")
    p.set_defaults(func=_cmd_episode_new)

    p = sub.add_parser("episode-show", help="查看单集配置")
    p.add_argument("episode", help="单集配置路径")
    p.set_defaults(func=_cmd_episode_show)

    p = sub.add_parser("probe", help="从录屏给出 DEMO/舞台/高光候选")
    p.add_argument("videos", nargs="+", help="原始录屏路径，可多个")
    p.add_argument("-o", "--out", help="候选输出路径，默认 output/probe.json")
    p.add_argument("--sample-fps", type=float, default=2.0, help="采样帧率，默认 2")
    p.set_defaults(func=_cmd_probe)

    p = sub.add_parser("compose", help="按系列+单集装配剪辑清单并生成草稿")
    p.add_argument("--series", required=True, help="系列名或路径")
    p.add_argument("--episode", required=True, help="单集配置路径")
    p.add_argument("--name", help="覆盖草稿名")
    p.add_argument("--plan-only", action="store_true", help="只生成清单，不写草稿")
    p.add_argument("--draft-root", help="草稿根目录，默认自动探测")
    p.set_defaults(func=_cmd_compose)

    p = sub.add_parser("verify", help="校验剪辑清单或草稿是否符合系列规则")
    p.add_argument("--plan", help="剪辑清单 JSON 路径")
    p.add_argument("--draft", help="草稿名（需草稿仍为明文 JSON）")
    p.add_argument("--draft-root", help="草稿根目录，默认自动探测")
    p.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
