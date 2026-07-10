#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ChatGPT web pipeline for turning an uploaded video into Douyin lead-in copy.

The script drives the visible ChatGPT web UI. It uploads one video, fills a
copywriting prompt, submits it, waits for the text response, and saves the
result locally for the web console.
"""

from __future__ import print_function

import argparse
import datetime as _dt
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from prompt_templates import VIDEO_COPY_PROMPT_TEMPLATE, render_named_prompt

from chatgpt_image_pipeline import (
    CHATGPT_URL,
    close_browser,
    is_chatgpt_auth_error,
    is_generation_active,
    latest_assistant_text,
    launch_debug_chrome,
    mkdir,
    open_chatgpt_page,
    require_human,
    run_login_only,
    safe_fill,
    set_input_files_if_possible,
    submit_prompt,
    wait_for_prompt_box,
    write_json,
    write_text,
)


VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v", ".ogv", ".ts"}


def now_run_name():
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def read_video_context(args):
    if args.video_context_file:
        return Path(args.video_context_file).read_text(encoding="utf-8").strip()
    return (args.video_context or "").strip()


def validate_video(video_path):
    path = Path(video_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise SystemExit("视频不存在: {0}".format(path))
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise SystemExit("不支持的视频格式: {0}".format(path.name))
    return str(path)


def prepare_run_dir(base_dir):
    run_dir = Path(base_dir).expanduser().resolve() / now_run_name()
    dirs = {
        "run": str(run_dir),
        "input": str(run_dir / "input"),
    }
    for path in dirs.values():
        mkdir(path)
    return dirs


def copy_input_video(video_path, input_dir):
    source = Path(video_path)
    suffix = source.suffix or ".mp4"
    destination = Path(input_dir) / ("input_video{0}".format(suffix))
    shutil.copy2(str(source), str(destination))
    return str(destination)


def render_video_copy_prompt(args, video_context):
    return render_named_prompt(
        args.prompt_template or VIDEO_COPY_PROMPT_TEMPLATE,
        {
            "brand_name": args.brand_name,
            "business_scope": args.business_scope,
            "video_context": video_context or "请先根据视频画面判断藏品、场景和氛围；没有把握的信息不要说死。",
        },
    )


def upload_video_or_manual(page, video_path, args):
    if set_input_files_if_possible(page, [video_path]):
        print("已触发视频上传: {0}".format(video_path))
        if args.auto_continue:
            print("等待 ChatGPT 处理视频上传，至少 {0} 秒...".format(args.upload_settle_seconds))
            time.sleep(args.upload_settle_seconds)
        else:
            require_human("请确认视频缩略图已经出现在 ChatGPT 输入框附近，并且上传完成。")
        return

    print("")
    print("未能自动定位 ChatGPT 上传控件。")
    print("请在打开的 ChatGPT 页面手动上传本次视频：")
    print(" - {0}".format(video_path))
    require_human("请确认视频上传完成后继续。")


def wait_for_copy_response(page, args, baseline_text):
    deadline = time.time() + args.response_timeout
    last_text = ""
    last_change_at = 0

    print("自动等待 ChatGPT 文案回复，最长等待 {0} 秒...".format(args.response_timeout))
    while time.time() < deadline:
        raw_text = latest_assistant_text(page)
        if raw_text and raw_text != baseline_text:
            if raw_text != last_text:
                last_text = raw_text
                last_change_at = time.time()
                print("检测到文案回复更新，当前字符数: {0}".format(len(raw_text)))
            elif (
                last_change_at
                and time.time() - last_change_at >= args.response_settle_seconds
                and not is_generation_active(page)
            ):
                print("文案回复已稳定，字符数: {0}".format(len(raw_text)))
                return raw_text

        time.sleep(args.poll_interval)

    if last_text:
        print("等待文案稳定超时，保存当前最新回复，字符数: {0}".format(len(last_text)))
        return last_text
    raise TimeoutError("等待 ChatGPT 文案回复超时。")


def run_pipeline(args):
    video_path = validate_video(args.video)
    video_context = read_video_context(args)
    prompt = render_video_copy_prompt(args, video_context)
    dirs = prepare_run_dir(args.output_dir)
    copied_video = copy_input_video(video_path, dirs["input"])

    write_text(Path(dirs["run"]) / "copy_prompt.txt", prompt)
    write_json(
        Path(dirs["run"]) / "request.json",
        {
            "video": video_path,
            "copied_video": copied_video,
            "brand_name": args.brand_name,
            "business_scope": args.business_scope,
            "video_context": video_context,
            "prompt_template": args.prompt_template or VIDEO_COPY_PROMPT_TEMPLATE,
            "chatgpt_url": CHATGPT_URL,
            "created_at": _dt.datetime.now().isoformat(),
        },
    )

    profile_dir = Path(args.profile_dir).expanduser().resolve()
    mkdir(str(profile_dir))

    with sync_playwright() as playwright:
        context, page, browser, connected_over_cdp = open_chatgpt_page(playwright, args)

        if is_chatgpt_auth_error(page.url):
            close_browser(context, args, browser=browser, connected_over_cdp=connected_over_cdp)
            raise SystemExit(
                "ChatGPT 登录流程进入 auth error。请先启动调试 Chrome 完成登录，"
                "再用 --cdp-url 运行文案流程。"
            )

        try:
            wait_for_prompt_box(page)
        except Exception:
            require_human(
                "如果当前未登录或卡在真人认证，请先完成登录；如果已经登录，请进入可发送消息的聊天页。"
            )

        baseline_text = latest_assistant_text(page)
        upload_video_or_manual(page, copied_video, args)
        prompt_box = safe_fill(page, prompt)

        if args.no_submit:
            require_human("脚本已填好视频文案提示词。请你检查并手动发送。")
        else:
            submit_prompt(page, prompt_box, args, prompt)
            print("已提交视频文案提示词。")

        if args.auto_continue and not args.no_submit:
            copy_text = wait_for_copy_response(page, args, baseline_text)
        else:
            require_human("请等待 ChatGPT 输出文案。确认完成后继续保存。")
            copy_text = latest_assistant_text(page)

        write_text(Path(dirs["run"]) / "copy_response.txt", copy_text)
        write_json(
            Path(dirs["run"]) / "copy_result.json",
            {
                "copy_text": copy_text,
                "prompt_path": str((Path(dirs["run"]) / "copy_prompt.txt").resolve()),
                "response_path": str((Path(dirs["run"]) / "copy_response.txt").resolve()),
                "input_video": copied_video,
                "created_at": _dt.datetime.now().isoformat(),
            },
        )

        close_browser(context, args, browser=browser, connected_over_cdp=connected_over_cdp)

    return dirs["run"]


def parse_args(argv):
    parser = argparse.ArgumentParser(description="上传视频到 ChatGPT，并生成抖音直播间引流文案。")
    parser.add_argument("--video", help="输入视频文件。")
    parser.add_argument(
        "--video-context",
        default="",
        help="视频拍摄来源、藏品故事、收回经过等背景线索。",
    )
    parser.add_argument("--video-context-file", help="从文本文件读取背景线索。")
    parser.add_argument("--brand-name", default="禅缘古艺", help="直播间名称。")
    parser.add_argument(
        "--business-scope",
        default="喜马拉雅艺术品，东方工艺的老物件",
        help="直播间主营方向。",
    )
    parser.add_argument(
        "--prompt-template",
        default="",
        help="自定义文案模板，支持 {{brand_name}}、{{business_scope}}、{{video_context}}。",
    )
    parser.add_argument("--output-dir", default="copy_runs", help="输出目录，默认 copy_runs。")
    parser.add_argument("--profile-dir", default="profiles/chatgpt_chrome", help="Chrome 专用用户数据目录。")
    parser.add_argument("--browser-channel", default="chrome", help="Playwright 浏览器 channel，默认 chrome。")
    parser.add_argument("--chrome-path", help="普通 Chrome 登录模式使用的 chrome.exe 路径。")
    parser.add_argument("--debug-port", type=int, default=9222, help="普通 Chrome CDP 调试端口。")
    parser.add_argument("--launch-debug-chrome", action="store_true", help="启动普通 Chrome 并开放 CDP。")
    parser.add_argument("--cdp-url", help="连接已打开的普通 Chrome，例如 http://127.0.0.1:9222。")
    parser.add_argument("--upload-settle-seconds", type=int, default=15, help="上传视频后等待页面处理的秒数。")
    parser.add_argument("--send-timeout", type=int, default=300, help="等待发送按钮可用的最长秒数。")
    parser.add_argument("--response-timeout", type=int, default=600, help="等待文案回复的最长秒数。")
    parser.add_argument("--response-settle-seconds", type=float, default=5.0, help="回复停止变化后的稳定等待秒数。")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="自动模式下轮询间隔秒数。")
    parser.add_argument("--auto-continue", action="store_true", help="自动上传、发送并等待文案输出。")
    parser.add_argument("--no-submit", action="store_true", help="只填写提示词，不自动点击发送。")
    parser.add_argument("--keep-open", action="store_true", help="结束前暂停，保留 Chrome 窗口给你检查。")
    parser.add_argument("--login-only", action="store_true", help="只打开专用 profile 登录 ChatGPT。")
    args = parser.parse_args(argv)

    if not args.login_only and not args.launch_debug_chrome and not args.video:
        parser.error("必须提供 --video。")
    return args


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    args = parse_args(argv)
    try:
        if args.login_only:
            run_login_only(args)
            return 0
        if args.launch_debug_chrome:
            launch_debug_chrome(args)
            return 0
        run_dir = run_pipeline(args)
    except PlaywrightError as exc:
        print("Playwright 执行失败: {0}".format(exc), file=sys.stderr)
        print("请确认已安装 Google Chrome，并且 Playwright Python 可用。", file=sys.stderr)
        return 2

    print("")
    print("文案生成完成。运行产物目录: {0}".format(run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
