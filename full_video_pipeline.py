#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Run the ChatGPT image stage and Grok video stage as one pipeline."""

from __future__ import print_function

import argparse
import datetime as _dt
import json
import sys
import urllib.request
from pathlib import Path

import chatgpt_image_pipeline as chatgpt_stage
import grok_video_pipeline as grok_stage


def write_json(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cdp_version_url(cdp_url):
    return cdp_url.rstrip("/") + "/json/version"


def require_cdp(name, cdp_url):
    url = cdp_version_url(cdp_url)
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            if response.status != 200:
                raise RuntimeError("HTTP {0}".format(response.status))
            json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise SystemExit(
            "{0} 浏览器调试端口不可用: {1}\n"
            "请先启动对应的 Chrome、完成登录并保持窗口打开。错误: {2}".format(
                name,
                cdp_url,
                exc,
            )
        )
    print("已连接 {0}: {1}".format(name, cdp_url))


def list_files(directory, suffixes=None):
    directory = Path(directory)
    if not directory.exists():
        return []
    suffixes = set(item.lower() for item in suffixes) if suffixes else None
    return [
        str(path.resolve())
        for path in sorted(directory.iterdir())
        if path.is_file() and (suffixes is None or path.suffix.lower() in suffixes)
    ]


def build_chatgpt_args(args):
    argv = [
        "--cdp-url", args.chatgpt_cdp_url,
        "--images",
    ] + list(args.images)

    if args.request_file:
        argv.extend(["--request-file", args.request_file])
    else:
        argv.extend(["--request", args.request])

    argv.extend([
        "--runs-dir", args.runs_dir,
        "--expected-candidates", str(args.expected_candidates),
        "--max-candidates", str(args.max_candidates),
        "--generation-timeout", str(args.generation_timeout),
        "--evaluation-timeout", str(args.evaluation_timeout),
        "--auto-continue",
    ])
    if args.generation_prompt_template:
        argv.extend(["--generation-prompt-template", args.generation_prompt_template])
    if args.evaluation_prompt_template:
        argv.extend(["--evaluation-prompt-template", args.evaluation_prompt_template])
    if args.allow_partial_candidates:
        argv.append("--allow-partial-candidates")
    return chatgpt_stage.parse_args(argv)


def build_grok_args(args, image_run_dir):
    output_dir = args.video_output_dir
    if not output_dir:
        output_dir = str(Path(image_run_dir) / "grok")

    argv = [
        "--cdp-url", args.grok_cdp_url,
        "--run-dir", str(image_run_dir),
        "--output-dir", output_dir,
        "--resolution", args.resolution,
        "--duration", args.duration,
        "--aspect-ratio", args.aspect_ratio,
        "--video-timeout", str(args.video_timeout),
        "--auto-continue",
    ]
    if args.video_prompt:
        argv.extend(["--prompt", args.video_prompt])
    if args.reuse_current_grok_page:
        argv.append("--reuse-current-page")
    return grok_stage.parse_args(argv)


def run_full_pipeline(args):
    require_cdp("ChatGPT", args.chatgpt_cdp_url)
    require_cdp("Grok", args.grok_cdp_url)

    started_at = _dt.datetime.now().isoformat()
    image_run_dir = None
    video_run_dir = None
    manifest = {
        "status": "running",
        "started_at": started_at,
        "input_images": [str(Path(path).expanduser().resolve()) for path in args.images],
        "request": args.request,
        "request_file": args.request_file,
        "video_prompt": args.video_prompt,
        "chatgpt_cdp_url": args.chatgpt_cdp_url,
        "grok_cdp_url": args.grok_cdp_url,
    }

    print("")
    print("========== 第一阶段：ChatGPT 生图与审核 ==========")
    try:
        image_run_dir = chatgpt_stage.run_pipeline(build_chatgpt_args(args))
        image_run_dir = str(Path(image_run_dir).resolve())
        passed_images = list_files(
            Path(image_run_dir) / "passed",
            suffixes=[".png", ".jpg", ".jpeg", ".webp"],
        )
        manifest["image_run_dir"] = image_run_dir
        manifest["passed_images"] = passed_images
        manifest["passed_count"] = len(passed_images)
        print("第一阶段完成，合格图片数量: {0}".format(len(passed_images)))

        if not passed_images:
            manifest["status"] = "stopped_no_passed_images"
            manifest["finished_at"] = _dt.datetime.now().isoformat()
            write_json(Path(image_run_dir) / "full_pipeline.json", manifest)
            raise SystemExit("没有通过审核的图片，已停止 Grok 视频阶段。")

        print("")
        print("========== 第二阶段：Grok 图生视频 ==========")
        video_run_dir = grok_stage.run_pipeline(build_grok_args(args, image_run_dir))
        video_run_dir = str(Path(video_run_dir).resolve())
        videos = list_files(
            Path(video_run_dir) / "videos",
            suffixes=[".mp4", ".webm", ".ogv", ".ts"],
        )
        manifest["video_run_dir"] = video_run_dir
        manifest["videos"] = videos
        manifest["video_count"] = len(videos)
        manifest["status"] = "complete" if videos else "complete_without_videos"
        manifest["finished_at"] = _dt.datetime.now().isoformat()
        write_json(Path(image_run_dir) / "full_pipeline.json", manifest)
        return image_run_dir, video_run_dir, videos
    except BaseException as exc:
        if image_run_dir:
            manifest["status"] = manifest.get("status") if manifest.get("status") != "running" else "failed"
            manifest["error"] = str(exc)
            manifest["video_run_dir"] = video_run_dir
            manifest["finished_at"] = _dt.datetime.now().isoformat()
            write_json(Path(image_run_dir) / "full_pipeline.json", manifest)
        raise


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="自动执行 ChatGPT 生图审核，再把 passed 图片交给 Grok 生成并下载视频。"
    )
    parser.add_argument("--images", nargs="+", required=True, help="输入参考图片，数量 1 到 3 张。")
    parser.add_argument("--request", default="", help="ChatGPT 图片修改诉求。")
    parser.add_argument("--request-file", help="从文本文件读取图片修改诉求。")
    parser.add_argument("--video-prompt", default="", help="Grok 图生视频要求；不填时自动基于图片诉求生成。")
    parser.add_argument("--generation-prompt-template", default="", help="ChatGPT 生图 Prompt 模板。")
    parser.add_argument("--evaluation-prompt-template", default="", help="ChatGPT 审核 Prompt 模板。")
    parser.add_argument(
        "--chatgpt-cdp-url",
        default="http://127.0.0.1:9222",
        help="已登录 ChatGPT 的 Chrome CDP 地址。",
    )
    parser.add_argument(
        "--grok-cdp-url",
        default="http://127.0.0.1:9444",
        help="已登录 Grok 的 Chrome CDP 地址。",
    )
    parser.add_argument("--runs-dir", default="runs", help="ChatGPT 阶段输出目录。")
    parser.add_argument(
        "--video-output-dir",
        default="",
        help="Grok 阶段输出根目录；默认放在本次 ChatGPT 运行目录的 grok/ 下。",
    )
    parser.add_argument("--expected-candidates", type=int, default=4, help="期望 ChatGPT 生成的候选图数量。")
    parser.add_argument("--max-candidates", type=int, default=4, help="最多下载和审核的候选图数量。")
    parser.add_argument("--generation-timeout", type=int, default=1200, help="等待 ChatGPT 生图最长秒数。")
    parser.add_argument("--evaluation-timeout", type=int, default=300, help="等待 ChatGPT 审核最长秒数。")
    parser.add_argument("--video-timeout", type=int, default=1800, help="每个 Grok 视频等待最长秒数。")
    parser.add_argument("--resolution", default="720p", help="Grok 视频分辨率。")
    parser.add_argument("--duration", default="10s", help="Grok 视频时长。")
    parser.add_argument("--aspect-ratio", default="9:16", help="Grok 视频比例。")
    parser.add_argument(
        "--allow-partial-candidates",
        action="store_true",
        help="ChatGPT 超时后允许用不足 expected-candidates 的图片继续。",
    )
    parser.add_argument(
        "--reuse-current-grok-page",
        action="store_true",
        help="复用当前 Grok 页面，不在视频阶段重新进入 imagine。",
    )
    args = parser.parse_args(argv)

    if not args.request and not args.request_file:
        parser.error("必须提供 --request 或 --request-file。")
    if len(args.images) < 1 or len(args.images) > 3:
        parser.error("--images 需要提供 1 到 3 张图片。")
    return args


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    image_run_dir, video_run_dir, videos = run_full_pipeline(args)
    print("")
    print("========== 全流程完成 ==========")
    print("图片与审核目录: {0}".format(image_run_dir))
    print("视频运行目录: {0}".format(video_run_dir))
    print("成功视频数量: {0}".format(len(videos)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
