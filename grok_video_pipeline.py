#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Semi-automatic Grok Imagine image-to-video pipeline.

The script drives the visible Grok web UI through a regular Chrome window
opened with a local CDP port. It does not use private endpoints.
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

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


GROK_IMAGINE_URL = "https://grok.com/imagine"

PROMPT_SELECTORS = [
    "textarea",
    "[contenteditable='true']",
    "input[type='text']",
]

UPLOAD_BUTTON_SELECTORS = [
    "button[aria-label*='Upload']",
    "button[aria-label*='Attach']",
    "button[aria-label*='Image']",
    "button[aria-label*='Photo']",
    "button[aria-label*='上传']",
    "button[aria-label*='图片']",
    "button:has-text('Upload')",
    "button:has-text('Image')",
    "button:has-text('上传')",
    "div:has-text('上传或丢弃图像')",
    "div:has-text('上传或丢弃图片')",
    "div:has-text('Upload or drop image')",
    "div:has-text('Upload image')",
]

SUBMIT_SELECTORS = [
    "button[aria-label*='Generate']",
    "button[aria-label*='Create']",
    "button[aria-label*='Imagine']",
    "button[aria-label*='Submit']",
    "button[aria-label*='发送']",
    "button[aria-label*='提交']",
    "button:has-text('Generate')",
    "button:has-text('Create')",
    "button:has-text('Imagine')",
    "button:has-text('生成')",
    "button:has-text('提交')",
]

DOWNLOAD_SELECTORS = [
    "button[aria-label*='Download']",
    "a[aria-label*='Download']",
    "button:has-text('Download')",
    "a:has-text('Download')",
    "button[aria-label*='下载']",
    "a[aria-label*='下载']",
    "button:has-text('下载')",
    "a:has-text('下载')",
]


def now_run_name():
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def mkdir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def write_json(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
    raise SystemExit("未找到 Google Chrome。请用 --chrome-path 指定 chrome.exe 的完整路径。")


def cdp_json_url(port):
    return "http://127.0.0.1:{0}/json/version".format(port)


def cdp_is_available(port):
    try:
        with urllib.request.urlopen(cdp_json_url(port), timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def launch_debug_chrome(args):
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    mkdir(str(profile_dir))
    chrome_path = find_chrome_executable(args.chrome_path)

    if cdp_is_available(args.debug_port):
        print("检测到本地调试端口已经可用: {0}".format(cdp_json_url(args.debug_port)))
        print("请在该 Chrome 窗口完成 Grok 登录，然后用 --cdp-url 运行正式流程。")
        return

    command = [
        chrome_path,
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port={0}".format(args.debug_port),
        "--remote-allow-origins=*",
        "--user-data-dir={0}".format(profile_dir),
        "--no-first-run",
        GROK_IMAGINE_URL,
    ]

    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    subprocess.Popen(command, creationflags=creation_flags)

    print("已启动普通 Chrome，并开放本机 CDP 调试端口。")
    print("  Profile: {0}".format(profile_dir))
    print("  CDP: http://127.0.0.1:{0}".format(args.debug_port))
    print("请在打开的 Grok Imagine 页面完成登录，并保持窗口打开。")


def require_human(message):
    print("")
    print(message)
    try:
        input("完成后按 Enter 继续...")
    except EOFError:
        pass


def open_grok_page(playwright, args):
    if args.cdp_url:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = None
        pages = list(context.pages)
        if args.reuse_current_page:
            for existing_page in reversed(pages):
                if "grok.com" in (existing_page.url or ""):
                    page = existing_page
                    break
        else:
            for existing_page in pages:
                if "grok.com" in (existing_page.url or ""):
                    page = existing_page
                    break
        if page is None:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(GROK_IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
        else:
            page.bring_to_front()
            if not args.reuse_current_page and "grok.com/imagine" not in (page.url or ""):
                page.goto(GROK_IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
        return context, page, browser, True

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(Path(args.profile_dir).expanduser().resolve()),
        channel=args.browser_channel,
        headless=False,
        accept_downloads=True,
        viewport={"width": 1440, "height": 1000},
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(GROK_IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
    return context, page, None, False


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


def click_text_option(page, labels, timeout_ms=5000):
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        for label in labels:
            selectors = [
                "button:has-text('{0}')".format(label),
                "[role='button']:has-text('{0}')".format(label),
                "[role='option']:has-text('{0}')".format(label),
                "label:has-text('{0}')".format(label),
            ]
            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.count() > 0 and locator.is_visible(timeout=300):
                        locator.click(timeout=3000)
                        return True
                except Exception:
                    continue
        time.sleep(0.25)
    return False


def click_composer_option(page, labels, timeout_ms=5000, exact=True):
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        clicked = page.evaluate(
            """({ labels, exact }) => {
                const wanted = labels.map((label) => String(label).trim().toLowerCase()).filter(Boolean);
                const candidates = Array.from(document.querySelectorAll(
                    "button,[role='button'],[role='option'],label"
                ));
                const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;

                function visible(el) {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0
                        && rect.height > 0
                        && style.visibility !== "hidden"
                        && style.display !== "none"
                        && rect.bottom >= viewportH * 0.45
                        && rect.top <= viewportH
                        && rect.width <= 260
                        && rect.height <= 90;
                }

                function clean(text) {
                    return String(text || "").replace(/\\s+/g, " ").trim().toLowerCase();
                }

                for (const el of candidates) {
                    if (!visible(el)) continue;
                    const text = clean(el.innerText || el.textContent || el.getAttribute("aria-label"));
                    if (!text) continue;
                    const matched = wanted.some((label) => exact ? text === label : text.includes(label));
                    if (!matched) continue;
                    el.scrollIntoView({ block: "center", inline: "center" });
                    el.click();
                    return { ok: true, text, tag: el.tagName, aria: el.getAttribute("aria-label") || "" };
                }
                return { ok: false };
            }""",
            {"labels": labels, "exact": exact},
        )
        if clicked and clicked.get("ok"):
            return True
        time.sleep(0.25)
    return False


def click_visible_option(page, labels, timeout_ms=5000, exact=False):
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        clicked = page.evaluate(
            """({ labels, exact }) => {
                const wanted = labels.map((label) => String(label).replace(/\\s+/g, "").toLowerCase()).filter(Boolean);
                const candidates = Array.from(document.querySelectorAll(
                    "button,[role='button'],[role='option'],label,div,span"
                ));

                function visible(el) {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0
                        && rect.height > 0
                        && style.visibility !== "hidden"
                        && style.display !== "none"
                        && rect.width <= 320
                        && rect.height <= 120;
                }

                function clean(text) {
                    return String(text || "").replace(/\\s+/g, "").trim().toLowerCase();
                }

                for (const el of candidates) {
                    if (!visible(el)) continue;
                    const text = clean(el.innerText || el.textContent || el.getAttribute("aria-label"));
                    if (!text) continue;
                    const matched = wanted.some((label) => exact ? text === label : text.includes(label));
                    if (!matched) continue;
                    el.scrollIntoView({ block: "center", inline: "center" });
                    el.click();
                    return true;
                }
                return false;
            }""",
            {"labels": labels, "exact": exact},
        )
        if clicked:
            return True
        time.sleep(0.25)
    return False


def is_bottom_option_selected(page, labels):
    return page.evaluate(
        """(labels) => {
            const wanted = labels.map((label) => String(label).replace(/\\s+/g, "").toLowerCase()).filter(Boolean);
            const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
            const candidates = Array.from(document.querySelectorAll("button,[role='radio'],[role='button'],label"));
            for (const el of candidates) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width <= 0 || rect.height <= 0) continue;
                if (style.visibility === "hidden" || style.display === "none") continue;
                if (rect.bottom < viewportH * 0.55) continue;
                if (rect.width > 220 || rect.height > 100) continue;
                const text = String(el.innerText || el.textContent || el.getAttribute("aria-label") || "")
                    .replace(/\\s+/g, "")
                    .trim()
                    .toLowerCase();
                if (!wanted.some((label) => text === label || text.includes(label))) continue;

                const ariaChecked = el.getAttribute("aria-checked");
                const ariaPressed = el.getAttribute("aria-pressed");
                const ariaSelected = el.getAttribute("aria-selected");
                const disabled = el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true";
                const cls = String(el.className || "").toLowerCase();
                const selected =
                    ariaChecked === "true"
                    || ariaPressed === "true"
                    || ariaSelected === "true"
                    || cls.includes("selected")
                    || cls.includes("active")
                    || cls.includes("checked")
                    || cls.includes("bg-")
                    || disabled;
                return { found: true, selected, text, ariaChecked, ariaPressed, ariaSelected, className: String(el.className || "") };
            }
            return { found: false, selected: false };
        }""",
        labels,
    )


def ensure_composer_option(page, labels, args, exact=True):
    state = is_bottom_option_selected(page, labels)
    if state.get("found") and state.get("selected"):
        return True
    return click_composer_option(
        page,
        labels,
        timeout_ms=args.option_timeout * 1000,
        exact=exact,
    )


def click_bottom_ratio_dropdown(page, timeout_ms=5000):
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        clicked = page.evaluate(
            """() => {
                const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
                const candidates = Array.from(document.querySelectorAll("button,[role='button'],label,div"));
                const ratioRe = /^(?:\\d+\\s*:\\s*\\d+|原始|original|aspect|比例)/i;
                const matches = [];

                for (const el of candidates) {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    if (style.visibility === "hidden" || style.display === "none") continue;
                    if (rect.bottom < viewportH * 0.55) continue;
                    if (rect.width > 180 || rect.height > 90) continue;

                    const text = String(el.innerText || el.textContent || el.getAttribute("aria-label") || "")
                        .replace(/\\s+/g, " ")
                        .trim()
                        .toLowerCase();
                    if (!ratioRe.test(text)) continue;
                    matches.push({ el, x: rect.x, y: rect.y, area: rect.width * rect.height });
                }

                matches.sort((a, b) => {
                    const byY = b.y - a.y;
                    if (Math.abs(byY) > 10) return byY;
                    return a.area - b.area;
                });
                if (!matches.length) return false;
                matches[0].el.scrollIntoView({ block: "center", inline: "center" });
                matches[0].el.click();
                return true;
            }"""
        )
        if clicked:
            return True
        time.sleep(0.25)
    return False


def select_aspect_ratio(page, args):
    labels = [args.aspect_ratio, args.aspect_ratio.replace(":", " : "), "9:16", "9 : 16", "Portrait", "竖屏"]
    if click_composer_option(page, labels, timeout_ms=args.option_timeout * 1000, exact=False):
        return True
    current_ratio = find_visible_ratio_control(page)
    if not current_ratio:
        print("未看到比例下拉控件，按上传图片比例继承处理。")
        return "inherited_from_image"
    if click_bottom_ratio_dropdown(page, timeout_ms=args.option_timeout * 1000):
        time.sleep(0.5)
        if click_visible_option(page, labels, timeout_ms=args.option_timeout * 1000, exact=False):
            return True
    return False


def find_visible_ratio_control(page):
    return page.evaluate(
        """() => {
            const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
            const candidates = Array.from(document.querySelectorAll("button,[role='button'],label,div"));
            const ratioRe = /^(?:\\d+\\s*:\\s*\\d+|原始|original|aspect|比例)/i;
            for (const el of candidates) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width <= 0 || rect.height <= 0) continue;
                if (style.visibility === "hidden" || style.display === "none") continue;
                if (rect.bottom < viewportH * 0.55) continue;
                if (rect.width > 180 || rect.height > 90) continue;
                const text = String(el.innerText || el.textContent || el.getAttribute("aria-label") || "")
                    .replace(/\\s+/g, " ")
                    .trim();
                if (ratioRe.test(text)) {
                    return { text, x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) };
                }
            }
            return null;
        }"""
    )


def close_preview_modal_if_open(page):
    closed = False
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
            closed = True
            time.sleep(0.2)
        except Exception:
            break
    return closed


def collect_bottom_controls(page):
    return page.evaluate(
        """() => {
            const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
            const candidates = Array.from(document.querySelectorAll("button,[role='button'],label,div,span"));
            const items = [];
            for (const el of candidates) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width <= 0 || rect.height <= 0) continue;
                if (style.visibility === "hidden" || style.display === "none") continue;
                if (rect.bottom < viewportH * 0.55) continue;
                if (rect.width > 240 || rect.height > 100) continue;
                const text = String(el.innerText || el.textContent || el.getAttribute("aria-label") || "")
                    .replace(/\\s+/g, " ")
                    .trim();
                if (!text) continue;
                items.push({
                    tag: el.tagName,
                    role: el.getAttribute("role") || "",
                    ariaChecked: el.getAttribute("aria-checked") || "",
                    ariaPressed: el.getAttribute("aria-pressed") || "",
                    ariaSelected: el.getAttribute("aria-selected") || "",
                    text,
                    rect: {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height)
                    }
                });
            }
            return items.slice(-80);
        }"""
    )


def safe_fill_prompt(page, text):
    if not text:
        return False
    prompt_box = find_first_visible(page, PROMPT_SELECTORS, 8000)
    if prompt_box is None:
        return False
    try:
        prompt_box.click(timeout=5000)
        prompt_box.fill(text, timeout=10000)
        return True
    except Exception:
        prompt_box.evaluate(
            """(el, text) => {
                el.focus();
                if (el.isContentEditable) {
                    el.innerText = text;
                    el.dispatchEvent(new InputEvent('input', {
                        bubbles: true,
                        inputType: 'insertText',
                        data: text
                    }));
                } else {
                    el.value = text;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                }
            }""",
            text,
        )
        return True


def set_input_files_if_possible(page, file_paths):
    input_locators = [
        "input[type='file'][accept*='image']",
        "input[type='file']",
    ]
    for selector in input_locators:
        locator = page.locator(selector)
        try:
            count = locator.count()
            for idx in range(count - 1, -1, -1):
                candidate = locator.nth(idx)
                candidate.set_input_files(file_paths)
                print("Grok 上传方式: input[type=file] ({0})".format(selector))
                return True
        except Exception:
            continue

    if click_upload_drop_zone_file_chooser(page, file_paths):
        print("Grok 上传方式: 底部上传区文件选择器")
        return True

    if drop_file_on_upload_zone(page, file_paths[0]):
        print("Grok 上传方式: 拖拽 drop 兜底")
        return True

    try:
        button = find_first_visible(page, UPLOAD_BUTTON_SELECTORS, 7000)
        if button is None:
            return False
        with page.expect_file_chooser(timeout=7000) as chooser_info:
            button.click(timeout=5000)
        chooser_info.value.set_files(file_paths)
        return True
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def upload_drop_zone_rect(page):
    return page.evaluate(
        """() => {
            const labels = [
                "上传或丢弃图像",
                "上传或丢弃图片",
                "上传或丢弃图",
                "Upload or drop image",
                "Upload image",
                "Drop image"
            ];
            const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
            const candidates = Array.from(document.querySelectorAll("button,div,[role='button'],label"));
            const normalizedLabels = labels.map((label) => label.replace(/\\s+/g, "").toLowerCase());
            const matches = [];
            for (const el of candidates) {
                const text = String(el.innerText || el.textContent || el.getAttribute("aria-label") || "")
                    .replace(/\\s+/g, "")
                    .trim()
                    .toLowerCase();
                if (!normalizedLabels.some((label) => text.includes(label))) continue;
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) continue;
                if (rect.bottom < viewportH * 0.45) continue;
                if (rect.width > 420 || rect.height > 260) continue;
                matches.push({
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                    area: rect.width * rect.height
                });
            }
            matches.sort((a, b) => a.area - b.area);
            return matches[0] || null;
        }"""
    )


def click_upload_drop_zone_file_chooser(page, file_paths):
    try:
        rect = upload_drop_zone_rect(page)
        if not rect:
            return False
        with page.expect_file_chooser(timeout=7000) as chooser_info:
            page.mouse.click(
                rect["x"] + rect["width"] / 2,
                rect["y"] + rect["height"] / 2,
            )
        chooser_info.value.set_files(file_paths)
        return True
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def drop_file_on_upload_zone(page, file_path):
    path = Path(file_path)
    data_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    mime_type = mimetypes.guess_type(str(path))[0] or "image/png"
    return page.evaluate(
        """async ({ name, mimeType, dataB64 }) => {
            const labels = [
                "上传或丢弃图像",
                "上传或丢弃图片",
                "上传或丢弃图",
                "Upload or drop image",
                "Upload image",
                "Drop image"
            ];
            const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
            const normalizedLabels = labels.map((label) => label.replace(/\\s+/g, "").toLowerCase());
            const candidates = Array.from(document.querySelectorAll("button,div,[role='button'],label"));
            const matches = [];

            for (const el of candidates) {
                const text = String(el.innerText || el.textContent || el.getAttribute("aria-label") || "")
                    .replace(/\\s+/g, "")
                    .trim()
                    .toLowerCase();
                if (!normalizedLabels.some((label) => text.includes(label))) continue;
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) continue;
                if (rect.bottom < viewportH * 0.45) continue;
                if (rect.width > 420 || rect.height > 260) continue;
                matches.push({ el, area: rect.width * rect.height });
            }
            matches.sort((a, b) => a.area - b.area);
            const target = matches[0] && matches[0].el;
            if (!target) return false;

            const binary = atob(dataB64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
            const file = new File([bytes], name, { type: mimeType });
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(file);

            const dispatch = (el, type) => {
                const event = new DragEvent(type, {
                    bubbles: true,
                    cancelable: true,
                    dataTransfer
                });
                el.dispatchEvent(event);
            };

            target.scrollIntoView({ block: "center", inline: "center" });
            let el = target;
            for (let depth = 0; el && depth < 5; depth++, el = el.parentElement) {
                dispatch(el, "dragenter");
                dispatch(el, "dragover");
                dispatch(el, "drop");
            }
            return true;
        }""",
        {
            "name": path.name,
            "mimeType": mime_type,
            "dataB64": data_b64,
        },
    )


def collect_media_infos(page):
    return page.evaluate(
        """() => ({
            images: Array.from(document.images).map((img, index) => {
                const rect = img.getBoundingClientRect();
                return {
                    index,
                    src: img.currentSrc || img.src || '',
                    alt: img.alt || '',
                    width: img.naturalWidth || 0,
                    height: img.naturalHeight || 0,
                    clientWidth: Math.round(rect.width || 0),
                    clientHeight: Math.round(rect.height || 0)
                };
            }),
            videos: Array.from(document.querySelectorAll('video')).map((video, index) => {
                const rect = video.getBoundingClientRect();
                return {
                    index,
                    src: video.currentSrc || video.src || '',
                    poster: video.poster || '',
                    duration: Number.isFinite(video.duration) ? video.duration : null,
                    clientWidth: Math.round(rect.width || 0),
                    clientHeight: Math.round(rect.height || 0)
                };
            }),
            links: Array.from(document.querySelectorAll('a[href]')).map((a, index) => ({
                index,
                href: a.href,
                text: (a.innerText || a.getAttribute('aria-label') || '').trim()
            }))
        })"""
    )


def collect_composer_attachments(page):
    return page.evaluate(
        """() => {
            const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
            const editors = Array.from(document.querySelectorAll(
                "textarea,[contenteditable='true'],input[type='text']"
            )).filter((el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width >= 120 && rect.height >= 20
                    && rect.bottom >= viewportH * 0.55
                    && rect.top <= viewportH
                    && style.visibility !== "hidden"
                    && style.display !== "none";
            });

            editors.sort((a, b) => b.getBoundingClientRect().bottom - a.getBoundingClientRect().bottom);
            const editor = editors[0];
            if (!editor) return { found: false, attachments: [] };

            let root = null;
            let node = editor;
            for (let depth = 0; node && depth < 10; depth++, node = node.parentElement) {
                const rect = node.getBoundingClientRect();
                const text = String(node.innerText || node.textContent || "").replace(/\s+/g, " ");
                const hasControls = /视频|Video|720p|480p|10s|10秒/i.test(text);
                if (hasControls
                    && rect.width >= 300
                    && rect.height >= 90
                    && rect.height <= Math.min(500, viewportH * 0.60)
                    && rect.bottom >= viewportH * 0.72) {
                    root = node;
                    break;
                }
            }
            if (!root) root = editor.parentElement;

            const attachments = [];
            const seen = new Set();
            for (const img of Array.from(root.querySelectorAll("img"))) {
                const rect = img.getBoundingClientRect();
                const src = img.currentSrc || img.src || "";
                if (!src || seen.has(src)) continue;
                if (rect.width < 24 || rect.height < 24) continue;
                if (rect.width > 260 || rect.height > 260) continue;
                if (rect.bottom < 0 || rect.top > viewportH) continue;
                seen.add(src);
                attachments.push({
                    src,
                    alt: img.alt || "",
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                });
            }
            return { found: true, attachments };
        }"""
    )


def wait_for_upload_preview(page, before_attachments, args):
    deadline = time.time() + args.upload_timeout
    before_srcs = set(item.get("src") for item in before_attachments if item.get("src"))
    last_status = None
    while time.time() < deadline:
        state = collect_composer_attachments(page)
        attachments = state.get("attachments") or []
        current_srcs = set(item.get("src") for item in attachments if item.get("src"))
        changed = bool(current_srcs - before_srcs) or len(attachments) > len(before_attachments)
        status = (state.get("found"), len(attachments), changed)
        if status != last_status:
            if state.get("found"):
                print("Grok 编辑框参考图状态: {0} 张，{1}".format(
                    len(attachments),
                    "已出现本次图片" if changed else "等待本次图片",
                ))
            else:
                print("等待定位 Grok 底部编辑框...")
            last_status = status
        if attachments and changed:
            time.sleep(args.upload_settle_seconds)
            print("已确认 Grok 参考图上传完成。")
            return True
        time.sleep(args.poll_interval)
    return False


def set_grok_options(page, args):
    results = {}

    results["video_mode"] = ensure_composer_option(page, ["视频", "Video"], args, exact=True)

    if results["video_mode"]:
        time.sleep(1)

    results["resolution"] = ensure_composer_option(
        page,
        [args.resolution, args.resolution.upper(), "720P"],
        args,
        exact=False,
    )
    results["duration"] = ensure_composer_option(
        page,
        [args.duration, args.duration.replace("s", " sec"), args.duration.replace("s", " seconds"), "10 秒"],
        args,
        exact=False,
    )
    results["aspect_ratio"] = select_aspect_ratio(page, args)
    return results


def bottom_submit_button_rect(page):
    return page.evaluate(
        """() => {
            const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
            const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
            const buttons = Array.from(document.querySelectorAll("button,[role='button']"));
            const candidates = [];

            for (const el of buttons) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width < 28 || rect.height < 28) continue;
                if (rect.width > 96 || rect.height > 96) continue;
                if (rect.bottom < viewportH * 0.55) continue;
                if (rect.right < viewportW * 0.55) continue;
                if (style.visibility === "hidden" || style.display === "none") continue;
                if (el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") continue;

                const text = String(el.innerText || el.textContent || el.getAttribute("aria-label") || "")
                    .replace(/\\s+/g, " ")
                    .trim();
                const submitHint = /提交|发送|生成|submit|send|generate|create/i.test(text) ? 10000 : 0;
                const darkHint = /rgb\\(0, 0, 0\\)|rgba\\(0, 0, 0/.test(style.backgroundColor) ? 1000 : 0;
                const score = submitHint + darkHint + rect.right * 2 + rect.bottom;
                candidates.push({
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                    text,
                    aria: el.getAttribute("aria-label") || "",
                    score
                });
            }

            candidates.sort((a, b) => b.score - a.score);
            return candidates[0] || null;
        }"""
    )


def submit_generation(page, args):
    deadline = time.time() + args.send_timeout
    last_rect = None
    while time.time() < deadline:
        rect = bottom_submit_button_rect(page)
        if rect:
            last_rect = rect
            x = rect["x"] + rect["width"] / 2
            y = rect["y"] + rect["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            time.sleep(0.05)
            page.mouse.up()
            print("已点击 Grok 生成按钮: x={0:.0f}, y={1:.0f}, text={2!r}".format(
                x,
                y,
                rect.get("text", ""),
            ))
            return True
        time.sleep(1)

    raise RuntimeError("未找到 Grok 生成按钮。最后检测结果: {0}".format(last_rect))


def extension_from_mime(mime_type, fallback=".mp4"):
    if not mime_type:
        return fallback
    mime_type = mime_type.split(";")[0].strip().lower()
    guessed = mimetypes.guess_extension(mime_type)
    if guessed == ".mp4v":
        return ".mp4"
    return guessed or fallback


def fetch_page_url(page, url):
    if url.startswith("data:"):
        header, b64_data = url.split(",", 1)
        mime_match = re.match(r"data:([^;]+)", header)
        mime_type = mime_match.group(1) if mime_match else "video/mp4"
        return base64.b64decode(b64_data), mime_type

    return page.evaluate(
        """async (url) => {
            const response = await fetch(url);
            const blob = await response.blob();
            const buffer = await blob.arrayBuffer();
            const bytes = new Uint8Array(buffer);
            let binary = '';
            const chunkSize = 0x8000;
            for (let i = 0; i < bytes.length; i += chunkSize) {
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
            }
            return {
                mime: blob.type || response.headers.get('content-type') || 'video/mp4',
                b64: btoa(binary)
            };
        }""",
        url,
    )


def save_video_from_url(page, url, output_stem):
    data = None
    mime_type = None

    if not url.startswith("blob:") and not url.startswith("data:"):
        try:
            response = page.context.request.get(url, timeout=120000)
            if response.ok:
                data = response.body()
                mime_type = response.headers.get("content-type") or "video/mp4"
        except Exception:
            data = None

    if data is None:
        result = fetch_page_url(page, url)
        data = base64.b64decode(result["b64"])
        mime_type = result.get("mime") or "video/mp4"

    ext = extension_from_mime(mime_type)
    out_path = Path(str(output_stem) + ext)
    out_path.write_bytes(data)
    return str(out_path)


def try_click_download(page, output_path, timeout_ms=5000):
    button = find_first_visible(page, DOWNLOAD_SELECTORS, timeout_ms)
    if button is None:
        return None
    try:
        with page.expect_download(timeout=timeout_ms) as download_info:
            button.click(timeout=3000)
        download = download_info.value
        return save_validated_video_download(download, output_path)
    except Exception:
        return None


def detect_download_file_type(path):
    path = Path(path)
    with path.open("rb") as handle:
        header = handle.read(64)

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", ".png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg", ".jpg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif", ".gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp", ".webp"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "mp4", ".mp4"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm", ".webm"
    if header.startswith(b"OggS"):
        return "ogg", ".ogv"
    if header.startswith(b"\x47"):
        return "mpeg-ts", ".ts"
    if header.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        return "html", ".html"
    return "unknown", ""


def save_validated_video_download(download, output_path):
    output_path = Path(output_path)
    temp_path = output_path.with_name(output_path.name + ".download")
    download.save_as(str(temp_path))
    file_type, extension = detect_download_file_type(temp_path)

    if file_type not in ["mp4", "webm", "ogg", "mpeg-ts"]:
        try:
            temp_path.unlink()
        except OSError:
            pass
        print("下载内容不是视频，而是 {0}；已丢弃并继续定位本次视频。".format(file_type))
        return None

    final_path = output_path.with_suffix(extension)
    os.replace(str(temp_path), str(final_path))
    return str(final_path)


def collect_generation_result_state(page):
    return page.evaluate(
        """() => {
            const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
            const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
            const results = [];
            const seenResultKeys = new Set();

            for (const el of Array.from(document.querySelectorAll("span,div,p,time"))) {
                const text = String(el.innerText || el.textContent || "").replace(/\\s+/g, "").trim();
                if (!/^(0:10|00:10)$/.test(text)) continue;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width <= 0 || rect.height <= 0 || rect.width > 100 || rect.height > 60) continue;
                if (rect.top < 0 || rect.top > viewportH * 0.65 || rect.left > viewportW * 0.45) continue;
                if (style.visibility === "hidden" || style.display === "none") continue;

                let node = el;
                let media = null;
                let target = el;
                for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
                    media = node.querySelector && node.querySelector("video,img");
                    const nodeRect = node.getBoundingClientRect();
                    if (media
                        && nodeRect.width >= 30 && nodeRect.height >= 30
                        && nodeRect.width <= 320 && nodeRect.height <= 320) {
                        target = node;
                        break;
                    }
                }
                const mediaSrc = media
                    ? (media.currentSrc || media.src || media.poster || media.getAttribute("src") || "")
                    : "";
                const key = mediaSrc
                    ? [text, mediaSrc].join("|")
                    : [text, Math.round(rect.x / 5), Math.round(rect.y / 5)].join("|");
                if (!seenResultKeys.has(key)) {
                    seenResultKeys.add(key);
                    const targetRect = target.getBoundingClientRect();
                    results.push({
                        key,
                        media_src: mediaSrc,
                        x: targetRect.x,
                        y: targetRect.y,
                        width: targetRect.width,
                        height: targetRect.height
                    });
                }
            }

            const readyVideos = [];
            const seenVideoSrcs = new Set();
            for (const video of Array.from(document.querySelectorAll("video"))) {
                const src = video.currentSrc || video.src || "";
                if (!src || seenVideoSrcs.has(src)) continue;
                seenVideoSrcs.add(src);
                const rect = video.getBoundingClientRect();
                const duration = Number.isFinite(video.duration) ? video.duration : null;
                const visible = rect.width >= 160 && rect.height >= 160
                    && rect.bottom > 0 && rect.top < viewportH
                    && rect.right > 0 && rect.left < viewportW;
                if (video.readyState >= 2 && duration && duration >= 8) {
                    readyVideos.push({
                        src,
                        duration,
                        readyState: video.readyState,
                        visible,
                        width: Math.round(rect.width),
                        height: Math.round(rect.height)
                    });
                }
            }

            return {
                url: location.href,
                result_keys: results.map((item) => item.key),
                results,
                ready_videos: readyVideos
            };
        }"""
    )


def right_toolbar_button_rects(page):
    return page.evaluate(
        """() => {
            const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
            const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
            const candidates = [];
            const elements = Array.from(document.querySelectorAll("button,[role='button'],a"));

            for (const el of elements) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width < 28 || rect.height < 28) continue;
                if (rect.width > 80 || rect.height > 80) continue;
                if (rect.left < viewportW * 0.82) continue;
                if (rect.top < viewportH * 0.20 || rect.bottom > viewportH * 0.96) continue;
                if (style.visibility === "hidden" || style.display === "none") continue;
                if (el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") continue;

                const text = String(el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
                const aria = el.getAttribute("aria-label") || "";
                const title = el.getAttribute("title") || "";
                const testId = el.getAttribute("data-testid") || "";
                const href = el.getAttribute("href") || "";
                const html = String(el.innerHTML || "").slice(0, 1200);
                const semantic = [text, aria, title, testId, href, html].join(" ");
                candidates.push({
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                    text,
                    aria,
                    title,
                    test_id: testId,
                    href,
                    semantic,
                    download_hint: /下载|download|save|lucide-download/i.test(semantic)
                });
            }

            candidates.sort((a, b) => a.y - b.y);
            return candidates;
        }"""
    )


def visible_download_tooltip(page):
    return page.evaluate(
        """() => {
            const wanted = /^(下载|下载视频|保存|Download|Download video|Save)$/i;
            for (const el of Array.from(document.querySelectorAll("[role='tooltip'],div,span"))) {
                const text = String(el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
                if (!wanted.test(text)) continue;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width > 0 && rect.height > 0
                    && style.visibility !== "hidden" && style.display !== "none") {
                    return text;
                }
            }
            return "";
        }"""
    )


def find_right_toolbar_download_button(page):
    candidates = right_toolbar_button_rects(page)
    for candidate in candidates:
        if candidate.get("download_hint"):
            return candidate, "semantic"

    for candidate in candidates:
        x = candidate["x"] + candidate["width"] / 2
        y = candidate["y"] + candidate["height"] / 2
        try:
            page.mouse.move(x, y)
            time.sleep(0.35)
            tooltip = visible_download_tooltip(page)
            if tooltip:
                candidate["tooltip"] = tooltip
                return candidate, "tooltip"
        except Exception:
            continue
    return None, ""


def try_click_right_toolbar_download(page, output_path, timeout_ms=8000):
    rect, matched_by = find_right_toolbar_download_button(page)
    if not rect:
        return None

    x = rect["x"] + rect["width"] / 2
    y = rect["y"] + rect["height"] / 2
    print("尝试点击本次结果的下载按钮: x={0:.0f}, y={1:.0f}, matched_by={2}, label={3!r}".format(
        x,
        y,
        matched_by,
        rect.get("tooltip") or rect.get("aria") or rect.get("title") or rect.get("text", ""),
    ))

    try:
        with page.expect_download(timeout=timeout_ms) as download_info:
            page.mouse.move(x, y)
            page.mouse.down()
            time.sleep(0.05)
            page.mouse.up()
        download = download_info.value
        return save_validated_video_download(download, output_path)
    except Exception:
        return None


def select_generated_result(page, item, announce=False):
    if not item:
        return False
    width = item.get("width") or 0
    height = item.get("height") or 0
    if width <= 0 or height <= 0:
        return False
    x = item.get("x", 0) + width / 2
    y = item.get("y", 0) + height / 2
    try:
        page.mouse.click(x, y)
        if announce:
            print("已选中本次生成的 0:10 视频结果。")
        return True
    except Exception:
        return False


def wait_and_download_generated_video(page, baseline, output_stem, args):
    deadline = time.time() + args.video_timeout
    output_path = Path(str(output_stem) + ".mp4")
    baseline_keys = set(baseline.get("result_keys") or [])
    baseline_video_srcs = set(
        item.get("src") for item in (baseline.get("ready_videos") or []) if item.get("src")
    )
    baseline_url = baseline.get("url") or ""
    completion_reported = False
    last_download_attempt = 0
    last_status_report = 0
    last_download_wait_report = 0
    selected_result_key = None

    while time.time() < deadline:
        state = collect_generation_result_state(page)
        new_result_keys = set(state.get("result_keys") or []) - baseline_keys
        new_result_items = [
            item for item in (state.get("results") or []) if item.get("key") in new_result_keys
        ]
        new_ready_videos = [
            item for item in (state.get("ready_videos") or [])
            if item.get("src") and item.get("src") not in baseline_video_srcs
        ]
        url_changed = bool(baseline_url and state.get("url") != baseline_url)
        complete = bool(new_result_keys) or bool(url_changed and new_ready_videos)

        if complete and not completion_reported:
            print("已检测到本次 10 秒视频生成完成，开始下载。")
            completion_reported = True
        elif not complete and time.time() - last_status_report >= 30:
            print("正在等待本次 10 秒视频生成完成...")
            last_status_report = time.time()

        if complete and time.time() - last_download_attempt >= max(8, args.poll_interval):
            last_download_attempt = time.time()

            if new_result_items:
                result_item = sorted(new_result_items, key=lambda item: (item.get("y", 99999), item.get("x", 99999)))[0]
                announce = result_item.get("key") != selected_result_key
                if select_generated_result(page, result_item, announce=announce):
                    selected_result_key = result_item.get("key")
                    time.sleep(2)
                    state = collect_generation_result_state(page)
                    new_ready_videos = [
                        item for item in (state.get("ready_videos") or [])
                        if item.get("src") and item.get("src") not in baseline_video_srcs
                    ]

            saved = try_click_right_toolbar_download(page, output_path, timeout_ms=10000)
            if saved:
                print("视频已下载: {0}".format(saved))
                return saved, {"type": "right_toolbar_download"}

            visible_videos = [item for item in new_ready_videos if item.get("visible")]
            if visible_videos:
                try:
                    saved = save_video_from_url(page, visible_videos[-1]["src"], str(output_stem))
                    print("视频已保存: {0}".format(saved))
                    return saved, {"type": "visible_video", "item": visible_videos[-1]}
                except Exception as exc:
                    print("读取当前结果视频地址失败，继续等待下载按钮: {0}".format(exc))

            if time.time() - last_download_wait_report >= 30:
                print("本次结果已经完成，正在等待右侧下载按钮可用...")
                last_download_wait_report = time.time()

        time.sleep(args.poll_interval)

    return None, None


def wait_for_video(page, before_video_srcs, before_link_hrefs, args):
    deadline = time.time() + args.video_timeout
    last_count = -1
    while time.time() < deadline:
        media = collect_media_infos(page)
        videos = [
            item for item in media["videos"]
            if item.get("src") and item.get("src") not in before_video_srcs
        ]
        links = []
        count = len(videos)
        if count != last_count:
            print("检测到新视频数量: {0}".format(count))
            last_count = count
        if videos:
            return {"type": "video", "item": videos[-1]}
        time.sleep(args.poll_interval)
    return None


def resolve_images(args):
    images = []
    if args.images:
        images.extend(args.images)
    if args.run_dir:
        passed_dir = Path(args.run_dir).expanduser().resolve() / "passed"
        if not passed_dir.exists():
            raise SystemExit("未找到 passed 目录: {0}".format(passed_dir))
        images.extend(str(path) for path in sorted(passed_dir.iterdir()) if path.is_file())

    resolved = []
    for image in images:
        path = Path(image).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise SystemExit("图片不存在: {0}".format(path))
        if path.suffix.lower() not in [".png", ".jpg", ".jpeg", ".webp"]:
            continue
        resolved.append(str(path))

    if not resolved:
        raise SystemExit("没有可用图片。请提供 --images 或 --run-dir。")
    return resolved


def read_source_request(run_dir):
    if not run_dir:
        return ""
    request_json = Path(run_dir).expanduser().resolve() / "request.json"
    if not request_json.exists():
        return ""
    try:
        return json.loads(request_json.read_text(encoding="utf-8")).get("request", "")
    except Exception:
        return ""


def build_video_prompt(args):
    if args.prompt:
        return args.prompt.strip()
    source_request = read_source_request(args.run_dir)
    if source_request:
        return (
            "Use the uploaded image as the first frame. Create a natural, realistic 10-second vertical video. "
            "Keep the subject identity, composition, colors, and photographic realism consistent. "
            "Use subtle camera motion and natural movement only. Avoid AI artifacts, warping, flicker, text, watermark, "
            "unnatural filters, over-saturated HDR, and plastic textures. Original request: {0}"
        ).format(source_request)
    return (
        "Use the uploaded image as the first frame. Create a natural, realistic 10-second vertical video. "
        "Use subtle camera motion and natural movement only. Avoid AI artifacts, warping, flicker, text, watermark, "
        "unnatural filters, over-saturated HDR, and plastic textures."
    )


def prepare_output_dir(args):
    run_dir = Path(args.output_dir).expanduser().resolve() / now_run_name()
    dirs = {
        "run": str(run_dir),
        "input": str(run_dir / "input"),
        "videos": str(run_dir / "videos"),
        "logs": str(run_dir / "logs"),
    }
    for path in dirs.values():
        mkdir(path)
    return dirs


def process_one_image(page, image_path, index, prompt, dirs, args):
    image_path = Path(image_path).resolve()
    copied = Path(dirs["input"]) / ("source_{0:02d}{1}".format(index, image_path.suffix))
    shutil.copy2(str(image_path), str(copied))

    if args.reuse_current_page:
        page.bring_to_front()
        if args.confirm_before_start and index == 1:
            require_human(
                "请确认当前 Grok 页面已经是你手动打开的新版 Imagine 输入框，然后继续。"
            )
    else:
        page.goto(GROK_IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)

    before_attachments = collect_composer_attachments(page).get("attachments") or []

    if not set_input_files_if_possible(page, [str(image_path)]):
        print("未能自动定位 Grok 上传控件。")
        print("请手动上传图片: {0}".format(image_path))
        require_human("确认图片上传完成后继续。")
    elif args.auto_continue:
        if not wait_for_upload_preview(page, before_attachments, args):
            require_human("自动模式未能确认图片上传完成。请检查页面，确认上传完成后继续。")
    else:
        require_human("请确认图片已上传到 Grok Imagine。")

    close_preview_modal_if_open(page)

    option_results = set_grok_options(page, args)
    bottom_controls = collect_bottom_controls(page)
    print("Grok 选项设置尝试结果: {0}".format(option_results))

    if not all(option_results.values()):
        print("底部控件快照:")
        for item in bottom_controls:
            print(" - {0}".format(item))
        require_human(
            "请在 Grok 页面确认已选择 Video、720p、10s、9:16。"
        )
    elif not args.auto_continue:
        require_human("请确认 Grok 选项为 Video、720p、10s、9:16。")

    if not safe_fill_prompt(page, prompt):
        print("未能自动定位 Grok prompt 输入框。")
        require_human("请手动填入视频 prompt 后继续。")

    generation_baseline = collect_generation_result_state(page)

    if args.no_submit:
        require_human("脚本已完成上传、选项和 prompt。请你检查并手动生成。")
    else:
        submit_generation(page, args)

    output_stem = Path(dirs["videos"]) / ("video_{0:02d}".format(index))

    if args.no_submit:
        require_human(
            "请手动点击生成，等待视频完成后手动下载。下载完成后继续。"
        )
        result = None
        saved_path = None
    else:
        saved_path, result = wait_and_download_generated_video(
            page,
            generation_baseline,
            str(output_stem),
            args,
        )

        if not saved_path:
            downloaded = try_click_download(page, Path(str(output_stem) + ".mp4"), timeout_ms=10000)
            if downloaded:
                saved_path = downloaded
                result = {"type": "download_selector"}

        if not saved_path:
            require_human(
                "未能自动下载视频。请在页面手动点击下载；下载完成后按 Enter，脚本会继续处理下一张。"
            )

    record = {
        "index": index,
        "source_image": str(image_path),
        "copied_image": str(copied),
        "prompt": prompt,
        "options": {
            "mode": "video",
            "resolution": args.resolution,
            "duration": args.duration,
            "aspect_ratio": args.aspect_ratio,
        },
        "option_results": option_results,
        "bottom_controls": bottom_controls,
        "saved_video": saved_path,
        "media_detection": result,
    }
    write_json(Path(dirs["logs"]) / ("video_{0:02d}.json".format(index)), record)
    return record


def run_pipeline(args):
    images = resolve_images(args)
    prompt = build_video_prompt(args)
    dirs = prepare_output_dir(args)
    write_json(
        Path(dirs["run"]) / "request.json",
        {
            "images": images,
            "prompt": prompt,
            "grok_url": GROK_IMAGINE_URL,
            "options": {
                "mode": "video",
                "resolution": args.resolution,
                "duration": args.duration,
                "aspect_ratio": args.aspect_ratio,
            },
            "created_at": _dt.datetime.now().isoformat(),
        },
    )

    with sync_playwright() as playwright:
        context, page, browser, connected_over_cdp = open_grok_page(playwright, args)
        require_human(
            "请确认当前 Grok Imagine 页面已登录，并可使用 Imagine。"
        ) if args.confirm_login else None

        records = []
        for index, image_path in enumerate(images, start=1):
            print("")
            print("开始处理第 {0}/{1} 张: {2}".format(index, len(images), image_path))
            records.append(process_one_image(page, image_path, index, prompt, dirs, args))

        write_json(Path(dirs["run"]) / "videos.json", records)

        if args.keep_open:
            require_human("脚本阶段已完成。Grok 页面保持打开供你检查。")
        if not connected_over_cdp:
            context.close()
        elif browser is not None:
            print("CDP 模式下保留普通 Chrome 窗口打开。")

    return dirs["run"]


def download_current_video(args):
    dirs = prepare_output_dir(args)
    output_path = Path(dirs["videos"]) / "current_video.mp4"
    with sync_playwright() as playwright:
        context, page, browser, connected_over_cdp = open_grok_page(playwright, args)
        state = collect_generation_result_state(page)
        results = state.get("results") or []
        if results:
            result_item = sorted(results, key=lambda item: (item.get("y", 99999), item.get("x", 99999)))[0]
            if select_generated_result(page, result_item, announce=True):
                time.sleep(2)
        saved = try_click_right_toolbar_download(page, output_path, timeout_ms=15000)
        if not saved:
            require_human(
                "未能自动触发当前视频下载。请确认当前 Grok 页面右侧下载按钮可见，然后继续。"
            )
            saved = try_click_right_toolbar_download(page, output_path, timeout_ms=15000)
        if saved:
            print("当前视频已下载: {0}".format(saved))
        write_json(
            Path(dirs["run"]) / "download_current.json",
            {
                "saved_video": saved,
                "created_at": _dt.datetime.now().isoformat(),
            },
        )
        if args.keep_open:
            require_human("当前下载流程完成，页面保持打开供你检查。")
        if not connected_over_cdp:
            context.close()
        elif browser is not None:
            print("CDP 模式下保留普通 Chrome 窗口打开。")
    return dirs["run"]


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="通过 Grok Imagine 网页把 passed 图片生成 720p/10s/9:16 视频并下载。"
    )
    parser.add_argument("--images", nargs="+", help="要生成视频的图片。")
    parser.add_argument("--run-dir", help="ChatGPT 图片流水线的运行目录；默认读取其中 passed/*.png。")
    parser.add_argument("--prompt", default="", help="图生视频 prompt；不填时会基于 request.json 自动生成。")
    parser.add_argument("--output-dir", default="video_runs", help="视频输出目录，默认 video_runs。")
    parser.add_argument("--profile-dir", default="profiles/grok_chrome", help="Grok 专用 Chrome profile。")
    parser.add_argument("--browser-channel", default="chrome", help="Playwright 浏览器 channel，默认 chrome。")
    parser.add_argument("--chrome-path", help="普通 Chrome 的 chrome.exe 路径。")
    parser.add_argument("--debug-port", type=int, default=9444, help="Grok 普通 Chrome CDP 端口，默认 9444。")
    parser.add_argument("--launch-debug-chrome", action="store_true", help="启动普通 Chrome 并打开 Grok Imagine。")
    parser.add_argument("--cdp-url", help="连接已打开的普通 Chrome，例如 http://127.0.0.1:9444。")
    parser.add_argument("--resolution", default="720p", help="视频分辨率，默认 720p。")
    parser.add_argument("--duration", default="10s", help="视频时长，默认 10s。")
    parser.add_argument("--aspect-ratio", default="9:16", help="视频比例，默认 9:16。")
    parser.add_argument("--upload-timeout", type=int, default=240, help="等待上传预览最长秒数，默认 240。")
    parser.add_argument("--upload-settle-seconds", type=int, default=8, help="上传预览出现后等待秒数，默认 8。")
    parser.add_argument("--option-timeout", type=int, default=4, help="每个选项自动点击最长秒数，默认 4。")
    parser.add_argument("--send-timeout", type=int, default=240, help="等待生成按钮可用最长秒数，默认 240。")
    parser.add_argument("--video-timeout", type=int, default=1800, help="等待视频生成最长秒数，默认 1800。")
    parser.add_argument("--poll-interval", type=float, default=3.0, help="轮询间隔秒数，默认 3。")
    parser.add_argument("--auto-continue", action="store_true", help="跳过可自动判断的确认。")
    parser.add_argument("--no-submit", action="store_true", help="只上传、设置选项、填 prompt，不自动点击生成。")
    parser.add_argument("--keep-open", action="store_true", help="结束后保留页面供检查。")
    parser.add_argument("--confirm-login", action="store_true", help="正式流程开始时先让你确认 Grok 已登录。")
    parser.add_argument("--reuse-current-page", action="store_true", help="复用当前 Grok 页面，不在每张图开始时重新跳转 imagine。")
    parser.add_argument("--confirm-before-start", action="store_true", help="开始处理第一张图前暂停，让你确认当前页面状态。")
    parser.add_argument("--download-current", action="store_true", help="只下载当前 Grok 页面已选中的视频，不重新生成。")
    args = parser.parse_args(argv)

    if not args.launch_debug_chrome and not args.download_current and not args.images and not args.run_dir:
        parser.error("必须提供 --images 或 --run-dir，除非使用 --launch-debug-chrome。")
    return args


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    args = parse_args(argv)
    try:
        if args.launch_debug_chrome:
            launch_debug_chrome(args)
            return 0
        if args.download_current:
            run_dir = download_current_video(args)
            print("")
            print("完成。当前视频下载产物目录: {0}".format(run_dir))
            return 0
        run_dir = run_pipeline(args)
    except PlaywrightError as exc:
        print("Playwright 执行失败: {0}".format(exc), file=sys.stderr)
        return 2

    print("")
    print("完成。视频运行产物目录: {0}".format(run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
