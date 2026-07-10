#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

from prompt_templates import EVALUATION_PROMPT_TEMPLATE, GENERATION_PROMPT_TEMPLATE, VIDEO_COPY_PROMPT_TEMPLATE
from webui.jobs import JobManager
from webui.pipelines import get_pipeline, pipeline_schemas


WORKSPACE = Path(__file__).resolve().parent
DATA_DIR = WORKSPACE / "web_data"
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
job_manager = JobManager(WORKSPACE, DATA_DIR)


DEFAULT_CONFIG = {
    "chatgpt_cdp_url": "http://127.0.0.1:9333",
    "grok_cdp_url": "http://127.0.0.1:9444",
    "resolution": "720p",
    "duration": "10s",
    "aspect_ratio": "9:16",
    "expected_candidates": 4,
    "max_candidates": 4,
    "generation_timeout": 1200,
    "evaluation_timeout": 300,
    "video_timeout": 1800,
    "runs_dir": "runs",
    "allow_partial_candidates": False,
    "reuse_current_grok_page": False,
    "generation_prompt_template": GENERATION_PROMPT_TEMPLATE,
    "evaluation_prompt_template": EVALUATION_PROMPT_TEMPLATE,
    "copy_brand_name": "禅缘古艺",
    "copy_business_scope": "喜马拉雅艺术品，东方工艺的老物件",
    "copy_context": "",
    "copy_prompt_template": VIDEO_COPY_PROMPT_TEMPLATE,
    "copy_output_dir": "copy_runs",
    "copy_upload_settle_seconds": 15,
    "copy_response_timeout": 600,
}


def json_error(message, status=400):
    response = jsonify({"ok": False, "error": message})
    response.status_code = status
    return response


def local_cdp_status(url):
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return {"online": False, "message": "仅支持本机 HTTP 调试地址。"}
        version_url = url.rstrip("/") + "/json/version"
        with urllib.request.urlopen(version_url, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {
            "online": response.status == 200,
            "message": payload.get("Browser") or "已连接",
        }
    except Exception as exc:
        return {"online": False, "message": str(exc)}


def cdp_port(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("仅支持本机 HTTP 调试地址。")
    if not parsed.port:
        raise ValueError("CDP 地址缺少端口。")
    return parsed.port


def browser_script(provider):
    if provider == "chatgpt":
        return WORKSPACE / "chatgpt_image_pipeline.py"
    if provider == "grok":
        return WORKSPACE / "grok_video_pipeline.py"
    raise ValueError("未知浏览器类型。")


def ensure_browser(provider, url, wait_seconds=10):
    status = local_cdp_status(url)
    if status.get("online"):
        status["launched"] = False
        return status

    port = cdp_port(url)
    script = browser_script(provider)
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.Popen(
        [sys.executable, str(script), "--launch-debug-chrome", "--debug-port", str(port)],
        cwd=str(WORKSPACE),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(0.5)
        status = local_cdp_status(url)
        if status.get("online"):
            break
    status["launched"] = True
    return status


def safe_media_path(path):
    candidate = Path(path).resolve()
    allowed_roots = [WORKSPACE, DATA_DIR]
    for root in allowed_roots:
        try:
            if os.path.commonpath([str(candidate), str(root.resolve())]) == str(root.resolve()):
                return candidate
        except ValueError:
            continue
    abort(403)


def media_items(job, kind):
    if kind == "inputs":
        return job.get("input_files") or job.get("input_images") or []
    artifacts = job.get("artifacts") or {}
    return artifacts.get(kind) or []


@app.route("/")
def index():
    return render_template("index.html")


@app.after_request
def disable_browser_cache(response):
    if request.path == "/" or request.path.startswith("/static/") or request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.route("/api/config")
def api_config():
    return jsonify({
        "ok": True,
        "defaults": DEFAULT_CONFIG,
        "pipelines": pipeline_schemas(),
    })


@app.route("/api/browser-status", methods=["POST"])
def api_browser_status():
    payload = request.get_json(silent=True) or {}
    return jsonify({
        "ok": True,
        "chatgpt": local_cdp_status(payload.get("chatgpt_cdp_url") or DEFAULT_CONFIG["chatgpt_cdp_url"]),
        "grok": local_cdp_status(payload.get("grok_cdp_url") or DEFAULT_CONFIG["grok_cdp_url"]),
    })


@app.route("/api/browser-launch/<provider>", methods=["POST"])
def api_browser_launch(provider):
    payload = request.get_json(silent=True) or {}
    try:
        browser_script(provider)
        default_key = "chatgpt_cdp_url" if provider == "chatgpt" else "grok_cdp_url"
        url = payload.get("url") or DEFAULT_CONFIG[default_key]
        return jsonify({"ok": True, "status": ensure_browser(provider, url)})
    except ValueError as exc:
        return json_error(str(exc), 404)
    except Exception as exc:
        return json_error("启动浏览器失败: {0}".format(exc), 500)


@app.route("/api/preflight", methods=["POST"])
def api_preflight():
    payload = request.get_json(silent=True) or {}
    pipeline_id = payload.get("pipeline_id") or "full_video"
    pipeline = get_pipeline(pipeline_id)
    if pipeline is None:
        return json_error("未知流水线: {0}".format(pipeline_id))
    chatgpt_url = payload.get("chatgpt_cdp_url") or DEFAULT_CONFIG["chatgpt_cdp_url"]
    grok_url = payload.get("grok_cdp_url") or DEFAULT_CONFIG["grok_cdp_url"]
    try:
        chatgpt = (ensure_browser("chatgpt", chatgpt_url)
                   if "chatgpt" in pipeline.providers
                   else {"online": False, "launched": False, "message": "当前模式不需要"})
        grok = (ensure_browser("grok", grok_url)
                if "grok" in pipeline.providers
                else {"online": False, "launched": False, "message": "当前模式不需要"})
        runtime = job_manager.check_runtime(force=True)
    except Exception as exc:
        return json_error("前置检查失败: {0}".format(exc), 500)

    launched = bool(chatgpt.get("launched") or grok.get("launched"))
    provider_statuses = {"chatgpt": chatgpt, "grok": grok}
    ports_online = all(provider_statuses[name].get("online") for name in pipeline.providers)
    ready = bool(ports_online and runtime.get("ok") and not launched)
    if launched and ports_online:
        provider_labels = {"chatgpt": "ChatGPT", "grok": "Grok"}
        required_names = "、".join(provider_labels.get(name, name) for name in pipeline.providers)
        message = "调试 Chrome 已启动。请确认 {0} 已登录，然后再次启动任务。".format(required_names)
    elif not ports_online:
        message = "调试 Chrome 未能就绪，请检查浏览器窗口和端口。"
    elif not runtime.get("ok"):
        message = "Playwright 运行环境不可用: {0}".format(runtime.get("message"))
    else:
        message = "前置检查通过。"
    return jsonify({
        "ok": True,
        "pipeline_id": pipeline_id,
        "ready": ready,
        "message": message,
        "chatgpt": chatgpt,
        "grok": grok,
        "runtime": runtime,
    })


@app.route("/api/jobs", methods=["GET"])
def api_jobs():
    return jsonify({"ok": True, "jobs": job_manager.list_jobs()})


@app.route("/api/jobs", methods=["POST"])
def api_create_job():
    try:
        config = json.loads(request.form.get("config") or "{}")
    except ValueError:
        return json_error("任务配置不是有效 JSON。")
    merged_config = dict(DEFAULT_CONFIG)
    merged_config.update(config)
    pipeline_id = request.form.get("pipeline_id") or "full_video"
    pipeline = get_pipeline(pipeline_id)
    if pipeline is None:
        return json_error("未知流水线: {0}".format(pipeline_id))
    provider_urls = {
        "chatgpt": merged_config["chatgpt_cdp_url"],
        "grok": merged_config["grok_cdp_url"],
    }
    offline = [name for name in pipeline.providers if not local_cdp_status(provider_urls[name]).get("online")]
    if offline:
        return json_error("前置检查未通过：{0} 调试浏览器必须保持在线。".format("、".join(offline)), 409)
    runtime_status = job_manager.check_runtime()
    if not runtime_status.get("ok"):
        return json_error("前置检查未通过：{0}".format(runtime_status.get("message")), 409)

    files = request.files.getlist("inputs") or request.files.getlist("images")
    min_files = getattr(pipeline, "input_min_files", 1)
    max_files = getattr(pipeline, "input_max_files", 3)
    input_label = getattr(pipeline, "input_label", "输入文件")
    if len(files) < min_files:
        return json_error("请上传至少 {0} 个{1}。".format(min_files, input_label))
    if len(files) > max_files:
        return json_error("最多上传 {0} 个{1}。".format(max_files, input_label))

    upload_batch = UPLOAD_DIR / uuid.uuid4().hex
    upload_batch.mkdir(parents=True, exist_ok=True)
    image_paths = []
    original_names = []
    allowed_suffixes = set(getattr(pipeline, "input_allowed_suffixes", {".png", ".jpg", ".jpeg", ".webp"}))
    try:
        for index, storage in enumerate(files, start=1):
            original_name = storage.filename or "input_{0}".format(index)
            suffix = Path(original_name).suffix.lower()
            if suffix not in allowed_suffixes:
                raise ValueError("不支持的文件格式: {0}".format(original_name))
            destination = upload_batch / ("input_{0:02d}{1}".format(index, suffix))
            storage.save(str(destination))
            image_paths.append(str(destination.resolve()))
            original_names.append(original_name)

        job = job_manager.create_job(pipeline_id, merged_config, image_paths, original_names)
        return jsonify({"ok": True, "job": job}), 201
    except ValueError as exc:
        shutil.rmtree(str(upload_batch), ignore_errors=True)
        return json_error(str(exc))
    except Exception as exc:
        shutil.rmtree(str(upload_batch), ignore_errors=True)
        return json_error("创建任务失败: {0}".format(exc), 500)


@app.route("/api/jobs/<job_id>", methods=["GET"])
def api_job(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return json_error("任务不存在。", 404)
    return jsonify({"ok": True, "job": job})


@app.route("/api/jobs/<job_id>/log", methods=["GET"])
def api_job_log(job_id):
    if not job_manager.get_job(job_id):
        return json_error("任务不存在。", 404)
    try:
        offset = int(request.args.get("offset") or 0)
    except ValueError:
        offset = 0
    text, next_offset = job_manager.read_log(job_id, offset)
    return jsonify({"ok": True, "text": text, "next_offset": next_offset})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_cancel_job(job_id):
    job = job_manager.cancel_job(job_id)
    if not job:
        return json_error("任务不存在。", 404)
    return jsonify({"ok": True, "job": job})


@app.route("/api/jobs/<job_id>/media/<kind>/<int:index>", methods=["GET"])
def api_job_media(job_id, kind, index):
    job = job_manager.get_job(job_id)
    if not job:
        abort(404)
    items = media_items(job, kind)
    if index < 0 or index >= len(items):
        abort(404)
    path = safe_media_path(items[index])
    if not path.exists() or not path.is_file():
        abort(404)
    return send_file(
        str(path),
        as_attachment=request.args.get("download") == "1",
        conditional=True,
        last_modified=path.stat().st_mtime,
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="视频生成流水线本地网页控制台。")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1。")
    parser.add_argument("--port", type=int, default=7860, help="监听端口，默认 7860。")
    parser.add_argument("--debug", action="store_true", help="启用 Flask 调试模式。")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True, use_reloader=False)
