#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Semi-automatic ChatGPT web image generation pipeline.

This script intentionally drives the visible ChatGPT web UI instead of private
or hidden endpoints. It expects a human to log in and confirm generation stages.
"""

from __future__ import print_function

import argparse
import base64
import datetime as _dt
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from prompt_templates import (
    EVALUATION_PROMPT_TEMPLATE,
    GENERATION_PROMPT_TEMPLATE,
    render_prompt,
)

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional validation helper
    Image = None

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


CHATGPT_URL = "https://chatgpt.com/"

PROMPT_SELECTORS = [
    "#prompt-textarea",
    "textarea[data-testid='prompt-textarea']",
    "div#prompt-textarea[contenteditable='true']",
    "[contenteditable='true'][id='prompt-textarea']",
    "textarea",
    "[contenteditable='true']",
]

SEND_SELECTORS = [
    "button[data-testid='send-button']",
    "button[aria-label*='Send']",
    "button[aria-label*='发送']",
    "button:has-text('Send')",
    "button:has-text('发送')",
]

UPLOAD_BUTTON_SELECTORS = [
    "button[aria-label*='Attach']",
    "button[aria-label*='Upload']",
    "button[aria-label*='Add photos']",
    "button[aria-label*='Add files']",
    "button[aria-label*='上传']",
    "button[aria-label*='附加']",
    "button[aria-label*='添加']",
    "button:has-text('Attach')",
    "button:has-text('Upload')",
    "button:has-text('上传')",
]

ASSISTANT_TEXT_SELECTORS = [
    "[data-message-author-role='assistant']",
    "article",
]


def as_abs_path(path_value):
    return str(Path(path_value).expanduser().resolve())


def now_run_name():
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def mkdir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def write_text(path, text):
    Path(path).write_text(text, encoding="utf-8")


def write_json(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_chatgpt_auth_error(url):
    normalized = (url or "").lower()
    return "/api/auth/error" in normalized or "/auth/error" in normalized


def find_chrome_executable(explicit_path=None):
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if path.exists():
            return str(path)
        raise SystemExit("指定的 Chrome 路径不存在: {0}".format(path))

    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for candidate in candidates:
        if str(candidate) and candidate.exists():
            return str(candidate)

    raise SystemExit(
        "未找到 Google Chrome。请用 --chrome-path 指定 chrome.exe 的完整路径。"
    )


def cdp_json_url(port):
    return "http://127.0.0.1:{0}/json/version".format(port)


def cdp_is_available(port):
    try:
        with urllib.request.urlopen(cdp_json_url(port), timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def run_login_only(args):
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    mkdir(str(profile_dir))
    chrome_path = find_chrome_executable(args.chrome_path)

    command = [
        chrome_path,
        "--user-data-dir={0}".format(profile_dir),
        "--no-first-run",
        CHATGPT_URL,
    ]

    print("将用普通 Chrome 打开专用登录目录：")
    print("  Chrome: {0}".format(chrome_path))
    print("  Profile: {0}".format(profile_dir))
    print("")
    print("请在打开的窗口里登录 ChatGPT。登录成功后，请关闭这个 Chrome 窗口，再回到终端继续。")

    process = subprocess.Popen(command)
    while process.poll() is None:
        try:
            input("登录成功并关闭 Chrome 窗口后按 Enter...")
        except EOFError:
            break
        if process.poll() is None:
            print("检测到 Chrome 仍在运行。为避免 profile 被锁，请先关闭刚才打开的 Chrome 窗口。")
            time.sleep(1)

    print("登录准备完成。现在可以重新运行正式生成命令。")


def launch_debug_chrome(args):
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    mkdir(str(profile_dir))
    chrome_path = find_chrome_executable(args.chrome_path)

    if cdp_is_available(args.debug_port):
        print("检测到本地调试端口已经可用: {0}".format(cdp_json_url(args.debug_port)))
        print("请直接在该 Chrome 窗口完成 ChatGPT 登录，然后用 --cdp-url 运行正式流程。")
        return

    command = [
        chrome_path,
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port={0}".format(args.debug_port),
        "--remote-allow-origins=*",
        "--user-data-dir={0}".format(profile_dir),
        "--no-first-run",
        CHATGPT_URL,
    ]

    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )

    subprocess.Popen(command, creationflags=creation_flags)

    print("已启动普通 Chrome，并开放本机 CDP 调试端口。")
    print("  Chrome: {0}".format(chrome_path))
    print("  Profile: {0}".format(profile_dir))
    print("  CDP: http://127.0.0.1:{0}".format(args.debug_port))
    print("")
    print("请在这个普通 Chrome 窗口里完成真人认证和 ChatGPT 登录，并保持窗口打开。")
    print("随后运行正式流程时加上：--cdp-url http://127.0.0.1:{0}".format(args.debug_port))


def read_request(args):
    if args.request_file:
        return Path(args.request_file).read_text(encoding="utf-8").strip()
    return args.request.strip()


def validate_images(image_paths):
    if not 1 <= len(image_paths) <= 3:
        raise SystemExit("请提供 1 到 3 张输入图片。")

    resolved = []
    for image_path in image_paths:
        path = Path(image_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise SystemExit("图片不存在: {0}".format(path))
        resolved.append(str(path))
    return resolved


def build_generation_prompt(modification_request):
    return render_prompt(GENERATION_PROMPT_TEMPLATE, modification_request, 4)


def build_evaluation_prompt(modification_request, count):
    return """请作为严格的商业摄影修片总监，评估我刚上传的 {count} 张候选图。

原始修改诉求：{request}

请逐张判断：
1. 是否符合原始修改诉求。
2. 是否是自然真实的摄影风格。
3. 是否存在明显 AI 味：塑料感、过度锐化、过饱和、HDR 过重、奇怪手指/五官/文字、边缘融化、背景逻辑错误、透视/阴影不一致、材质不真实等。

只输出严格 JSON，不要 Markdown，不要解释性段落。格式如下：
{{
  "items": [
    {{
      "index": 1,
      "pass": true,
      "realism_score": 8.5,
      "request_match_score": 8.0,
      "brief_reason": "一句话说明",
      "issues": ["问题1", "问题2"]
    }}
  ]
}}

判定规则：
- pass=true 只给真实感和诉求匹配都比较稳的图片。
- 只要有明显 AI 味、主体严重变形、背景/光影不合理或不符合诉求，就 pass=false。
- index 必须按我上传图片的顺序从 1 到 {count}。""".format(
        count=count,
        request=modification_request,
    )


def build_evaluation_prompt_v2(modification_request, count):
    return render_prompt(EVALUATION_PROMPT_TEMPLATE, modification_request, count)


def wait_for_prompt_box(page):
    last_error = None
    for selector in PROMPT_SELECTORS:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=2500)
            return locator
        except Exception as exc:
            last_error = exc
    raise PlaywrightTimeoutError("无法找到 ChatGPT 输入框: {0}".format(last_error))


def normalize_prompt_text(text):
    return " ".join(str(text or "").replace("\u00a0", " ").split())


def read_prompt_text(locator):
    return locator.evaluate(
        """el => {
            if (typeof el.value === 'string') return el.value;
            return el.innerText || el.textContent || '';
        }"""
    )


def prompt_text_matches(actual, expected):
    return normalize_prompt_text(actual) == normalize_prompt_text(expected)


def safe_fill(page, text):
    """Fill the current composer and prove the text survived any UI rerender."""
    last_value = ""
    last_error = None
    for attempt in range(3):
        prompt_box = wait_for_prompt_box(page)
        try:
            prompt_box.click(timeout=5000)
            if attempt == 0:
                prompt_box.fill(text, timeout=15000)
            elif attempt == 1:
                prompt_box.press("Control+A")
                prompt_box.press("Backspace")
                prompt_box.type(text, timeout=30000)
            else:
                prompt_box.evaluate(
                    """(el, value) => {
                        el.focus();
                        if (el.isContentEditable) {
                            el.textContent = '';
                            el.textContent = value;
                        } else {
                            const prototype = Object.getPrototypeOf(el);
                            const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
                            if (descriptor && descriptor.set) descriptor.set.call(el, value);
                            else el.value = value;
                        }
                        el.dispatchEvent(new InputEvent('input', {
                            bubbles: true,
                            inputType: 'insertText',
                            data: value
                        }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    text,
                )

            time.sleep(0.5)
            current_box = wait_for_prompt_box(page)
            last_value = read_prompt_text(current_box)
            if prompt_text_matches(last_value, text):
                time.sleep(0.5)
                stable_box = wait_for_prompt_box(page)
                stable_value = read_prompt_text(stable_box)
                if prompt_text_matches(stable_value, text):
                    print("提示词已填入并校验，字符数: {0}".format(len(text)))
                    return stable_box
                last_value = stable_value
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "ChatGPT 输入框未能保留完整提示词，已停止发送。期望 {0} 字符，实际 {1} 字符。{2}".format(
            len(text),
            len(last_value or ""),
            "错误: {0}".format(last_error) if last_error else "",
        )
    )


def find_first_visible(page, selectors, timeout_ms):
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() > 0 and locator.is_visible(timeout=300):
                    return locator
            except Exception:
                continue
        time.sleep(0.2)
    return None


def submit_prompt(page, prompt_box, args, expected_prompt):
    deadline = time.time() + args.send_timeout
    last_seen_button = None

    while time.time() < deadline:
        button = find_first_visible(page, SEND_SELECTORS, 2500)
        if button is not None:
            last_seen_button = button
            try:
                if button.is_enabled(timeout=500):
                    prompt_box = wait_for_prompt_box(page)
                    actual_prompt = read_prompt_text(prompt_box)
                    if not prompt_text_matches(actual_prompt, expected_prompt):
                        raise RuntimeError(
                            "发送前提示词校验失败，已阻止仅发送图片。期望 {0} 字符，实际 {1} 字符。".format(
                                len(expected_prompt), len(actual_prompt or "")
                            )
                        )
                    button.click(timeout=5000)
                    print("已提交提示词，字符数: {0}".format(len(expected_prompt)))
                    return True
            except RuntimeError:
                raise
            except Exception:
                pass
        time.sleep(1)

    if args.auto_continue:
        raise RuntimeError(
            "发送按钮在 {0} 秒内没有变为可用，可能是图片仍在上传或页面状态异常。".format(
                args.send_timeout
            )
        )

    if last_seen_button is None:
        actual_prompt = read_prompt_text(prompt_box)
        if not prompt_text_matches(actual_prompt, expected_prompt):
            raise RuntimeError("发送前提示词校验失败，已阻止仅发送图片。")
        prompt_box.press("Enter")
        return True

    raise RuntimeError("发送按钮一直不可用，请检查图片是否仍在上传。")


def set_input_files_if_possible(page, file_paths):
    input_locator = page.locator("input[type='file']").first
    try:
        if input_locator.count() > 0:
            input_locator.set_input_files(file_paths)
            return True
    except Exception:
        pass

    button = find_first_visible(page, UPLOAD_BUTTON_SELECTORS, 6000)
    if button is None:
        return False

    try:
        with page.expect_file_chooser(timeout=6000) as chooser_info:
            button.click(timeout=5000)
        chooser = chooser_info.value
        chooser.set_files(file_paths)
        return True
    except Exception:
        return False


def collect_image_infos(page):
    return page.evaluate(
        """() => Array.from(document.images).map((img, index) => {
            const rect = img.getBoundingClientRect();
            const message = img.closest('[data-message-author-role]');
            const role = message ? message.getAttribute('data-message-author-role') : '';
            return {
                index,
                src: img.currentSrc || img.src || '',
                alt: img.alt || '',
                role,
                width: img.naturalWidth || 0,
                height: img.naturalHeight || 0,
                clientWidth: Math.round(rect.width || 0),
                clientHeight: Math.round(rect.height || 0)
            };
        })"""
    )


def filter_new_candidate_images(image_infos, known_srcs, min_size, excluded_alt_names=None):
    candidates = []
    seen = set()
    excluded_alt_names = set(name.lower() for name in (excluded_alt_names or []))
    for info in image_infos:
        src = info.get("src") or ""
        if not src or src in known_srcs or src in seen:
            continue
        seen.add(src)

        role = (info.get("role") or "").lower()
        if role == "user":
            continue

        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        client_width = int(info.get("clientWidth") or 0)
        client_height = int(info.get("clientHeight") or 0)

        if max(width, height, client_width, client_height) < min_size:
            continue

        alt = (info.get("alt") or "").lower()
        if "avatar" in alt or "profile" in alt:
            continue
        if alt in excluded_alt_names:
            continue
        if alt and any(name and name in alt for name in excluded_alt_names):
            continue

        candidates.append(info)
    return candidates


def count_new_visible_images(page, before_srcs, min_size):
    infos = collect_image_infos(page)
    seen = set()
    count = 0
    for info in infos:
        src = info.get("src") or ""
        if not src or src in before_srcs or src in seen:
            continue
        seen.add(src)
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        client_width = int(info.get("clientWidth") or 0)
        client_height = int(info.get("clientHeight") or 0)
        if max(width, height, client_width, client_height) >= min_size:
            count += 1
    return count


def wait_for_upload_previews(page, before_srcs, expected_count, args):
    deadline = time.time() + args.upload_timeout
    last_count = -1

    print("自动等待上传预览出现，期望 {0} 个文件，最长等待 {1} 秒...".format(
        expected_count,
        args.upload_timeout,
    ))
    while time.time() < deadline:
        count = count_new_visible_images(page, before_srcs, args.upload_preview_min_size)
        if count != last_count:
            print("检测到新上传预览数量: {0}/{1}".format(count, expected_count))
            last_count = count

        if count >= expected_count:
            print("上传预览已达到预期，继续等待 {0} 秒让页面完成处理。".format(
                args.upload_settle_seconds
            ))
            time.sleep(args.upload_settle_seconds)
            return True

        time.sleep(args.poll_interval)

    return False


def wait_for_candidate_images(page, known_srcs, args, excluded_alt_names=None):
    deadline = time.time() + args.generation_timeout
    last_count = -1
    best_candidates = []

    print("自动等待候选图生成，最长等待 {0} 秒...".format(args.generation_timeout))
    while time.time() < deadline:
        image_infos = collect_image_infos(page)
        candidates = filter_new_candidate_images(
            image_infos,
            known_srcs,
            args.min_size,
            excluded_alt_names=excluded_alt_names,
        )
        count = len(candidates)

        if count != last_count:
            print("检测到候选图数量: {0}".format(count))
            last_count = count
            best_candidates = candidates

        if count >= args.expected_candidates:
            print("候选图数量已达到预期: {0}".format(count))
            return candidates

        time.sleep(args.poll_interval)

    if args.allow_partial_candidates and best_candidates:
        print("候选图等待超时，允许使用当前已检测到的 {0} 张。".format(len(best_candidates)))
        return best_candidates

    require_human(
        "自动等待候选图超时，目前只检测到 {0}/{1} 张。请确认 ChatGPT 已生成完成后继续。".format(
            len(best_candidates),
            args.expected_candidates,
        )
    )
    image_infos = collect_image_infos(page)
    return filter_new_candidate_images(
        image_infos,
        known_srcs,
        args.min_size,
        excluded_alt_names=excluded_alt_names,
    )


def extension_from_mime(mime_type, fallback=".png"):
    if not mime_type:
        return fallback
    mime_type = mime_type.split(";")[0].strip().lower()
    if mime_type == "image/jpeg":
        return ".jpg"
    guessed = mimetypes.guess_extension(mime_type)
    return guessed or fallback


def fetch_via_page(page, src):
    if src.startswith("data:"):
        header, b64_data = src.split(",", 1)
        mime_match = re.match(r"data:([^;]+)", header)
        mime_type = mime_match.group(1) if mime_match else "image/png"
        return base64.b64decode(b64_data), mime_type

    return page.evaluate(
        """async (src) => {
            const response = await fetch(src);
            const blob = await response.blob();
            const buffer = await blob.arrayBuffer();
            const bytes = new Uint8Array(buffer);
            let binary = '';
            const chunkSize = 0x8000;
            for (let i = 0; i < bytes.length; i += chunkSize) {
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
            }
            return {
                mime: blob.type || response.headers.get('content-type') || 'image/png',
                b64: btoa(binary)
            };
        }""",
        src,
    )


def download_image(page, src, output_stem):
    data = None
    mime_type = None

    if not src.startswith("blob:") and not src.startswith("data:"):
        try:
            response = page.context.request.get(src, timeout=60000)
            if response.ok:
                data = response.body()
                mime_type = response.headers.get("content-type") or "image/png"
        except Exception:
            data = None

    if data is None:
        result = fetch_via_page(page, src)
        if isinstance(result, dict):
            data = base64.b64decode(result["b64"])
            mime_type = result.get("mime") or "image/png"
        else:
            data, mime_type = result

    ext = extension_from_mime(mime_type)
    out_path = Path(str(output_stem) + ext)
    out_path.write_bytes(data)
    return str(out_path)


def screenshot_candidate_image(page, info, output_stem):
    image_index = int(info.get("index"))
    out_path = Path(str(output_stem) + ".png")
    locator = page.locator("img").nth(image_index)
    locator.scroll_into_view_if_needed(timeout=10000)
    locator.screenshot(path=str(out_path), timeout=30000)
    return str(out_path)


def download_candidate_image(page, info, output_stem):
    try:
        return download_image(page, info["src"], output_stem)
    except Exception as exc:
        print("直接下载失败，尝试页面截图兜底: {0}".format(exc))
        return screenshot_candidate_image(page, info, output_stem)


def validate_downloaded_image(path):
    if Image is None:
        return {"valid": True, "note": "Pillow 未启用，仅跳过尺寸校验"}
    try:
        with Image.open(path) as image:
            return {
                "valid": True,
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "format": image.format,
            }
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


def latest_assistant_text(page):
    texts = []
    for selector in ASSISTANT_TEXT_SELECTORS:
        try:
            values = page.locator(selector).all_inner_texts()
            for value in values:
                value = value.strip()
                if value:
                    texts.append(value)
        except Exception:
            continue
    return texts[-1] if texts else ""


def extract_json_object(text):
    text = text.strip()
    if not text:
        raise ValueError("empty response")

    try:
        return json.loads(text)
    except Exception:
        pass

    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, re.S)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except Exception:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError("未找到可解析的 JSON")


def wait_for_evaluation_json(page, args, baseline_text):
    deadline = time.time() + args.evaluation_timeout
    last_error = None

    print("自动等待评估 JSON，最长等待 {0} 秒...".format(args.evaluation_timeout))
    while time.time() < deadline:
        raw_text = latest_assistant_text(page)
        if raw_text and raw_text != baseline_text:
            try:
                payload = extract_json_object(raw_text)
                if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                    print("已解析到评估 JSON。")
                    return raw_text, payload
            except Exception as exc:
                last_error = exc

        time.sleep(args.poll_interval)

    raise ValueError("等待评估 JSON 超时: {0}".format(last_error or "no parseable JSON"))


def truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "y", "1", "pass", "passed", "合格")
    return False


def classify_images(candidate_paths, evaluation_payload, output_dirs):
    by_index = {}
    for item in evaluation_payload.get("items", []):
        try:
            by_index[int(item.get("index"))] = item
        except Exception:
            continue

    classified = []
    for offset, source_path in enumerate(candidate_paths, start=1):
        item = by_index.get(offset)
        if item is None:
            target_dir = output_dirs["needs_review"]
            status = "needs_review"
        elif "request_satisfied" not in item or "filter_natural" not in item:
            target_dir = output_dirs["needs_review"]
            status = "needs_review"
        elif (
            truthy(item.get("pass"))
            and truthy(item.get("request_satisfied"))
            and truthy(item.get("filter_natural"))
        ):
            target_dir = output_dirs["passed"]
            status = "passed"
        else:
            target_dir = output_dirs["failed"]
            status = "failed"

        destination = Path(target_dir) / Path(source_path).name
        shutil.copy2(source_path, str(destination))
        classified.append(
            {
                "index": offset,
                "source": source_path,
                "destination": str(destination),
                "status": status,
                "evaluation": item,
            }
        )
    return classified


def copy_inputs(image_paths, input_dir):
    copied = []
    for idx, image_path in enumerate(image_paths, start=1):
        suffix = Path(image_path).suffix or ".png"
        destination = Path(input_dir) / "input_{0:02d}{1}".format(idx, suffix)
        shutil.copy2(image_path, str(destination))
        copied.append(str(destination))
    return copied


def prepare_run_dir(base_dir):
    run_dir = Path(base_dir).expanduser().resolve() / now_run_name()
    dirs = {
        "run": str(run_dir),
        "input": str(run_dir / "input"),
        "candidates": str(run_dir / "candidates"),
        "passed": str(run_dir / "passed"),
        "failed": str(run_dir / "failed"),
        "needs_review": str(run_dir / "needs_review"),
    }
    for path in dirs.values():
        mkdir(path)
    return dirs


def require_human(message):
    print("")
    print(message)
    try:
        input("完成后按 Enter 继续...")
    except EOFError:
        pass


def upload_files_or_manual(page, file_paths, manual_message, confirm_message, args):
    before_srcs = set(info.get("src") for info in collect_image_infos(page) if info.get("src"))
    if set_input_files_if_possible(page, file_paths):
        if args.auto_continue:
            if wait_for_upload_previews(page, before_srcs, len(file_paths), args):
                return
            require_human(
                "自动模式未能确认上传完成。请检查 ChatGPT 页面里的图片缩略图和上传状态，确认完成后继续。"
            )
        else:
            time.sleep(3)
            require_human(confirm_message)
        return

    print("")
    print("未能自动定位 ChatGPT 上传控件。")
    print(manual_message)
    for path in file_paths:
        print(" - {0}".format(path))
    require_human("请在打开的 ChatGPT 页面手动上传以上文件。")


def close_browser(context, args, browser=None, connected_over_cdp=False):
    if args.keep_open:
        require_human("脚本阶段已完成。Chrome 会先保持打开，方便你检查页面；按 Enter 后关闭。")
    if connected_over_cdp:
        print("CDP 模式下保留普通 Chrome 窗口打开。")
        return
    context.close()


def open_chatgpt_page(playwright, args):
    if args.cdp_url:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        contexts = browser.contexts
        if not contexts:
            context = browser.new_context(accept_downloads=True)
        else:
            context = contexts[0]

        page = None
        for existing_page in context.pages:
            if "chatgpt.com" in (existing_page.url or ""):
                page = existing_page
                break
        if page is None:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60000)
        else:
            page.bring_to_front()
            if not page.url or page.url == "about:blank":
                page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60000)

        return context, page, browser, True

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(Path(args.profile_dir).expanduser().resolve()),
        channel=args.browser_channel,
        headless=False,
        accept_downloads=True,
        viewport={"width": 1440, "height": 1000},
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60000)
    return context, page, None, False


def run_pipeline(args):
    request_text = read_request(args)
    image_paths = validate_images(args.images)
    input_alt_names = set()
    for path in image_paths:
        input_path = Path(path)
        input_alt_names.add(input_path.name.lower())
        if len(input_path.stem) >= 4:
            input_alt_names.add(input_path.stem.lower())
    dirs = prepare_run_dir(args.runs_dir)

    copied_inputs = copy_inputs(image_paths, dirs["input"])
    generation_template = args.generation_prompt_template or GENERATION_PROMPT_TEMPLATE
    generation_prompt = render_prompt(generation_template, request_text, args.expected_candidates)
    write_text(Path(dirs["run"]) / "generation_prompt.txt", generation_prompt)
    write_json(
        Path(dirs["run"]) / "request.json",
        {
            "request": request_text,
            "input_images": image_paths,
            "copied_input_images": copied_inputs,
            "chatgpt_url": CHATGPT_URL,
            "generation_prompt_template": generation_template,
            "evaluation_prompt_template": args.evaluation_prompt_template or EVALUATION_PROMPT_TEMPLATE,
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
                "ChatGPT 登录流程进入 auth error。请先运行：python .\\chatgpt_image_pipeline.py --launch-debug-chrome，"
                "在普通 Chrome 里完成登录并保持窗口打开，然后正式命令加上 --cdp-url http://127.0.0.1:9222。"
            )

        try:
            wait_for_prompt_box(page)
        except Exception:
            require_human(
                "如果当前未登录或卡在真人认证，请先停止脚本，然后运行：python .\\chatgpt_image_pipeline.py "
                "--launch-debug-chrome。完成登录后保持普通 Chrome 打开，再用 --cdp-url 运行正式流程。"
                "如果已经登录，请进入可发送消息的聊天页。"
            )

        prompt_box = wait_for_prompt_box(page)
        before_upload_srcs = set(info.get("src") for info in collect_image_infos(page) if info.get("src"))

        upload_files_or_manual(
            page,
            image_paths,
            "请手动上传本次输入的 1 到 3 张参考图片：",
            "请确认参考图缩略图已经出现在 ChatGPT 输入框附近，并且上传完成。",
            args,
        )

        after_upload_srcs = set(info.get("src") for info in collect_image_infos(page) if info.get("src"))
        known_srcs = set(src for src in after_upload_srcs.union(before_upload_srcs) if src)

        prompt_box = safe_fill(page, generation_prompt)

        if args.no_submit:
            require_human("脚本已填好生成提示词。请你检查并手动发送。")
        else:
            submit_prompt(page, prompt_box, args, generation_prompt)

        if args.auto_continue and not args.no_submit:
            candidates = wait_for_candidate_images(
                page,
                known_srcs,
                args,
                excluded_alt_names=input_alt_names,
            )
        else:
            require_human(
                "请等待 ChatGPT 图片生成完成。确认页面上已经出现候选图后，再继续下载。"
            )
            image_infos = collect_image_infos(page)
            candidates = filter_new_candidate_images(
                image_infos,
                known_srcs,
                args.min_size,
                excluded_alt_names=input_alt_names,
            )
        write_json(Path(dirs["run"]) / "detected_images.json", candidates)

        if not candidates:
            print("没有自动识别到新的候选图片。已保存 DOM 图片列表供排查。")
            close_browser(context, args, browser=browser, connected_over_cdp=connected_over_cdp)
            return dirs["run"]

        candidate_paths = []
        for idx, info in enumerate(candidates[: args.max_candidates], start=1):
            stem = Path(dirs["candidates"]) / "candidate_{0:02d}".format(idx)
            try:
                downloaded = download_candidate_image(page, info, str(stem))
                validation = validate_downloaded_image(downloaded)
                candidate_paths.append(downloaded)
                print("已下载候选图 {0}: {1} {2}".format(idx, downloaded, validation))
            except Exception as exc:
                print("候选图 {0} 下载失败: {1}".format(idx, exc))

        if not candidate_paths:
            print("候选图识别到了，但没有任何图片成功下载。")
            close_browser(context, args, browser=browser, connected_over_cdp=connected_over_cdp)
            return dirs["run"]

        if args.skip_evaluation:
            for path in candidate_paths:
                shutil.copy2(path, str(Path(dirs["needs_review"]) / Path(path).name))
            write_json(
                Path(dirs["run"]) / "classification.json",
                [{"source": path, "status": "needs_review"} for path in candidate_paths],
            )
            close_browser(context, args, browser=browser, connected_over_cdp=connected_over_cdp)
            return dirs["run"]

        evaluation_template = args.evaluation_prompt_template or EVALUATION_PROMPT_TEMPLATE
        evaluation_prompt = render_prompt(evaluation_template, request_text, len(candidate_paths))
        write_text(Path(dirs["run"]) / "evaluation_prompt.txt", evaluation_prompt)

        prompt_box = wait_for_prompt_box(page)
        evaluation_baseline_text = latest_assistant_text(page)
        upload_files_or_manual(
            page,
            candidate_paths,
            "请按 candidate_01、candidate_02 的顺序手动上传候选图：",
            "请确认候选图缩略图已经按顺序出现在 ChatGPT 输入框附近，并且上传完成。",
            args,
        )
        prompt_box = safe_fill(page, evaluation_prompt)

        if args.no_submit:
            require_human("脚本已填好评估提示词。请你检查并手动发送。")
        else:
            submit_prompt(page, prompt_box, args, evaluation_prompt)

        try:
            if args.auto_continue and not args.no_submit:
                raw_text, evaluation_payload = wait_for_evaluation_json(
                    page,
                    args,
                    evaluation_baseline_text,
                )
            else:
                require_human("请等待 ChatGPT 输出评估 JSON。确认评估完成后继续分类。")
                raw_text = latest_assistant_text(page)
                evaluation_payload = extract_json_object(raw_text)
            write_text(Path(dirs["run"]) / "evaluation_raw.txt", raw_text)
            write_json(Path(dirs["run"]) / "evaluation.json", evaluation_payload)
        except Exception as exc:
            print("评估 JSON 解析失败: {0}".format(exc))
            raw_text = latest_assistant_text(page)
            write_text(Path(dirs["run"]) / "evaluation_raw.txt", raw_text)
            evaluation_payload = {"items": []}
            write_json(
                Path(dirs["run"]) / "evaluation_parse_error.json",
                {"error": str(exc), "raw_text_path": str(Path(dirs["run"]) / "evaluation_raw.txt")},
            )

        classified = classify_images(candidate_paths, evaluation_payload, dirs)
        write_json(Path(dirs["run"]) / "classification.json", classified)

        close_browser(context, args, browser=browser, connected_over_cdp=connected_over_cdp)

    return dirs["run"]


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="通过 ChatGPT 网页半自动生成图片、下载候选图并按真实摄影风格评估分类。"
    )
    parser.add_argument(
        "--images",
        nargs="+",
        help="输入参考图片，数量 1 到 3 张。",
    )
    parser.add_argument(
        "--request",
        default="",
        help="图片修改诉求。也可以用 --request-file。",
    )
    parser.add_argument(
        "--request-file",
        help="从文本文件读取图片修改诉求。",
    )
    parser.add_argument(
        "--generation-prompt-template",
        default="",
        help="自定义生图模板，支持 {{request}} 和 {{count}} 占位符。",
    )
    parser.add_argument(
        "--evaluation-prompt-template",
        default="",
        help="自定义审核模板，支持 {{request}} 和 {{count}} 占位符。",
    )
    parser.add_argument(
        "--runs-dir",
        default="runs",
        help="输出目录，默认 runs。",
    )
    parser.add_argument(
        "--profile-dir",
        default="profiles/chatgpt_chrome",
        help="Chrome 专用用户数据目录，用于保存 ChatGPT 登录态。",
    )
    parser.add_argument(
        "--browser-channel",
        default="chrome",
        help="Playwright 浏览器 channel，默认 chrome。",
    )
    parser.add_argument(
        "--chrome-path",
        help="普通 Chrome 登录模式使用的 chrome.exe 路径。通常不需要设置。",
    )
    parser.add_argument(
        "--debug-port",
        type=int,
        default=9222,
        help="普通 Chrome CDP 调试端口，默认 9222。",
    )
    parser.add_argument(
        "--launch-debug-chrome",
        action="store_true",
        help="启动普通 Chrome 并开放本机 CDP 端口，用于手动认证和登录。",
    )
    parser.add_argument(
        "--cdp-url",
        help="连接已打开的普通 Chrome，例如 http://127.0.0.1:9222。",
    )
    parser.add_argument(
        "--min-size",
        type=int,
        default=512,
        help="识别候选图的最小边界阈值，默认 512。",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=8,
        help="最多下载的候选图数量，默认 8。",
    )
    parser.add_argument(
        "--expected-candidates",
        type=int,
        default=4,
        help="自动模式下期望等待到的候选图数量，默认 4。",
    )
    parser.add_argument(
        "--generation-timeout",
        type=int,
        default=1200,
        help="自动模式下等待图片生成的最长秒数，默认 1200。",
    )
    parser.add_argument(
        "--evaluation-timeout",
        type=int,
        default=300,
        help="自动模式下等待评估 JSON 的最长秒数，默认 300。",
    )
    parser.add_argument(
        "--upload-timeout",
        type=int,
        default=240,
        help="自动模式下等待上传预览出现的最长秒数，默认 240。",
    )
    parser.add_argument(
        "--upload-preview-min-size",
        type=int,
        default=32,
        help="判定上传预览图的最小显示尺寸，默认 32。",
    )
    parser.add_argument(
        "--upload-settle-seconds",
        type=int,
        default=8,
        help="检测到上传预览后继续等待页面处理的秒数，默认 8。",
    )
    parser.add_argument(
        "--send-timeout",
        type=int,
        default=240,
        help="等待发送按钮可用的最长秒数，默认 240。",
    )
    parser.add_argument(
        "--allow-partial-candidates",
        action="store_true",
        help="生成超时后允许使用不足 expected-candidates 的候选图继续。",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=3.0,
        help="自动模式下轮询间隔秒数，默认 3。",
    )
    parser.add_argument(
        "--auto-continue",
        action="store_true",
        help="跳过可自动判断的 Enter 确认，自动等待生成图片和评估 JSON。",
    )
    parser.add_argument(
        "--skip-evaluation",
        action="store_true",
        help="只下载候选图，不回传 ChatGPT 评估。",
    )
    parser.add_argument(
        "--no-submit",
        action="store_true",
        help="只填写提示词，不自动点击发送按钮。",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="结束前暂停，保留 Chrome 窗口给你检查；按 Enter 后关闭。",
    )
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="只用普通 Chrome 打开专用 profile 登录 ChatGPT，不执行生成流程。",
    )
    args = parser.parse_args(argv)

    if not args.login_only and not args.launch_debug_chrome:
        if not args.images:
            parser.error("必须提供 --images，数量 1 到 3 张。")
        if not args.request and not args.request_file:
            parser.error("必须提供 --request 或 --request-file。")
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
    print("完成。运行产物目录: {0}".format(run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
