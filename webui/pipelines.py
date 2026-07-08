#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import json
import sys
import urllib.request
from pathlib import Path


VIDEO_SUFFIXES = {".mp4", ".webm", ".ogv", ".ts"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class PipelineAdapter(object):
    pipeline_id = "base"
    name = "Pipeline"
    description = ""
    providers = []
    stages = []

    def schema(self):
        return {
            "id": self.pipeline_id,
            "name": self.name,
            "description": self.description,
            "providers": self.providers,
            "stages": self.stages,
        }

    def validate(self, config, image_paths):
        raise NotImplementedError

    def build_command(self, workspace, config, image_paths):
        raise NotImplementedError

    def preflight(self, config):
        return []

    def detect_stage(self, line):
        return None

    def collect_artifacts(self, workspace, config, started_at_epoch):
        return {}


class FullVideoPipelineAdapter(PipelineAdapter):
    pipeline_id = "full_video"
    name = "图片到视频"
    description = "ChatGPT 生图审核，然后通过 Grok 生成视频"
    providers = ["chatgpt", "grok"]
    stages = [
        {"id": "preflight", "label": "浏览器检查"},
        {"id": "chatgpt", "label": "生图与审核"},
        {"id": "grok", "label": "视频生成"},
        {"id": "complete", "label": "完成"},
    ]

    def validate(self, config, image_paths):
        errors = []
        if not image_paths or len(image_paths) > 3:
            errors.append("请上传 1 到 3 张参考图片。")
        if not str(config.get("request") or "").strip():
            errors.append("请填写图片生成诉求。")
        if not str(config.get("chatgpt_cdp_url") or "").strip():
            errors.append("请填写 ChatGPT CDP 地址。")
        if not str(config.get("grok_cdp_url") or "").strip():
            errors.append("请填写 Grok CDP 地址。")
        return errors

    def build_command(self, workspace, config, image_paths):
        command = [
            sys.executable,
            "-u",
            str(Path(workspace) / "full_video_pipeline.py"),
            "--chatgpt-cdp-url", config.get("chatgpt_cdp_url", "http://127.0.0.1:9333"),
            "--grok-cdp-url", config.get("grok_cdp_url", "http://127.0.0.1:9444"),
            "--images",
        ]
        command.extend(str(path) for path in image_paths)
        command.extend([
            "--request", str(config.get("request") or "").strip(),
            "--runs-dir", str(config.get("runs_dir") or "runs"),
            "--expected-candidates", str(config.get("expected_candidates") or 4),
            "--max-candidates", str(config.get("max_candidates") or 4),
            "--generation-timeout", str(config.get("generation_timeout") or 1200),
            "--evaluation-timeout", str(config.get("evaluation_timeout") or 300),
            "--video-timeout", str(config.get("video_timeout") or 1800),
            "--resolution", str(config.get("resolution") or "720p"),
            "--duration", str(config.get("duration") or "10s"),
            "--aspect-ratio", str(config.get("aspect_ratio") or "9:16"),
        ])
        video_prompt = str(config.get("video_prompt") or "").strip()
        if video_prompt:
            command.extend(["--video-prompt", video_prompt])
        generation_template = str(config.get("generation_prompt_template") or "").strip()
        evaluation_template = str(config.get("evaluation_prompt_template") or "").strip()
        if generation_template:
            command.extend(["--generation-prompt-template", generation_template])
        if evaluation_template:
            command.extend(["--evaluation-prompt-template", evaluation_template])
        if config.get("allow_partial_candidates"):
            command.append("--allow-partial-candidates")
        if config.get("reuse_current_grok_page"):
            command.append("--reuse-current-grok-page")
        return command

    def preflight(self, config):
        errors = []
        endpoints = [
            ("ChatGPT", config.get("chatgpt_cdp_url", "http://127.0.0.1:9333")),
            ("Grok", config.get("grok_cdp_url", "http://127.0.0.1:9444")),
        ]
        for name, endpoint in endpoints:
            try:
                with urllib.request.urlopen(endpoint.rstrip("/") + "/json/version", timeout=3) as response:
                    if response.status != 200:
                        raise RuntimeError("HTTP {0}".format(response.status))
            except Exception as exc:
                errors.append("{0} 调试浏览器未就绪 ({1}): {2}".format(name, endpoint, exc))
        return errors

    def detect_stage(self, line):
        if "第一阶段：ChatGPT" in line:
            return "chatgpt"
        if "第二阶段：Grok" in line:
            return "grok"
        if "全流程完成" in line:
            return "complete"
        return None

    def collect_artifacts(self, workspace, config, started_at_epoch):
        runs_dir = Path(config.get("runs_dir") or "runs")
        if not runs_dir.is_absolute():
            runs_dir = Path(workspace) / runs_dir
        manifests = []
        if runs_dir.exists():
            for manifest_path in runs_dir.glob("*/full_pipeline.json"):
                try:
                    if manifest_path.stat().st_mtime >= started_at_epoch - 2:
                        manifests.append(manifest_path)
                except OSError:
                    continue
        if not manifests:
            return {"manifest": None, "passed": [], "failed": [], "needs_review": [], "videos": []}

        manifest_path = max(manifests, key=lambda path: path.stat().st_mtime)
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        image_run = Path(payload.get("image_run_dir") or manifest_path.parent)
        video_run = payload.get("video_run_dir")
        video_dir = Path(video_run) / "videos" if video_run else None

        return {
            "manifest": str(manifest_path.resolve()),
            "image_run_dir": str(image_run.resolve()),
            "video_run_dir": str(Path(video_run).resolve()) if video_run else None,
            "passed": self._files(image_run / "passed", IMAGE_SUFFIXES),
            "failed": self._files(image_run / "failed", IMAGE_SUFFIXES),
            "needs_review": self._files(image_run / "needs_review", IMAGE_SUFFIXES),
            "videos": self._files(video_dir, VIDEO_SUFFIXES) if video_dir else [],
            "summary": payload,
        }

    @staticmethod
    def _files(directory, suffixes):
        if not directory or not Path(directory).exists():
            return []
        return [
            str(path.resolve())
            for path in sorted(Path(directory).iterdir())
            if path.is_file() and path.suffix.lower() in suffixes
        ]


class ImageOnlyPipelineAdapter(FullVideoPipelineAdapter):
    pipeline_id = "image_only"
    name = "仅生成图片"
    description = "通过 ChatGPT 生图、审核并筛选真实自然的候选图"
    providers = ["chatgpt"]
    stages = [
        {"id": "preflight", "label": "浏览器检查"},
        {"id": "chatgpt", "label": "生图与审核"},
        {"id": "complete", "label": "完成"},
    ]

    def validate(self, config, image_paths):
        errors = []
        if not image_paths or len(image_paths) > 3:
            errors.append("请上传 1 到 3 张参考图片。")
        if not str(config.get("request") or "").strip():
            errors.append("请填写图片生成诉求。")
        if not str(config.get("chatgpt_cdp_url") or "").strip():
            errors.append("请填写 ChatGPT CDP 地址。")
        return errors

    def build_command(self, workspace, config, image_paths):
        command = [
            sys.executable,
            "-u",
            str(Path(workspace) / "chatgpt_image_pipeline.py"),
            "--cdp-url", config.get("chatgpt_cdp_url", "http://127.0.0.1:9333"),
            "--images",
        ]
        command.extend(str(path) for path in image_paths)
        command.extend([
            "--request", str(config.get("request") or "").strip(),
            "--runs-dir", str(config.get("runs_dir") or "runs"),
            "--expected-candidates", str(config.get("expected_candidates") or 4),
            "--max-candidates", str(config.get("max_candidates") or 4),
            "--generation-timeout", str(config.get("generation_timeout") or 1200),
            "--evaluation-timeout", str(config.get("evaluation_timeout") or 300),
            "--auto-continue",
        ])
        generation_template = str(config.get("generation_prompt_template") or "").strip()
        evaluation_template = str(config.get("evaluation_prompt_template") or "").strip()
        if generation_template:
            command.extend(["--generation-prompt-template", generation_template])
        if evaluation_template:
            command.extend(["--evaluation-prompt-template", evaluation_template])
        if config.get("allow_partial_candidates"):
            command.append("--allow-partial-candidates")
        return command

    def preflight(self, config):
        endpoint = config.get("chatgpt_cdp_url", "http://127.0.0.1:9333")
        try:
            with urllib.request.urlopen(endpoint.rstrip("/") + "/json/version", timeout=3) as response:
                if response.status != 200:
                    raise RuntimeError("HTTP {0}".format(response.status))
            return []
        except Exception as exc:
            return ["ChatGPT 调试浏览器未就绪 ({0}): {1}".format(endpoint, exc)]

    def detect_stage(self, line):
        return "complete" if "完成。运行产物目录" in line else None

    def collect_artifacts(self, workspace, config, started_at_epoch):
        runs_dir = Path(config.get("runs_dir") or "runs")
        if not runs_dir.is_absolute():
            runs_dir = Path(workspace) / runs_dir
        markers = []
        if runs_dir.exists():
            for marker in runs_dir.glob("*/request.json"):
                try:
                    if marker.stat().st_mtime >= started_at_epoch - 2:
                        markers.append(marker)
                except OSError:
                    continue
        if not markers:
            return {"manifest": None, "passed": [], "failed": [], "needs_review": [], "videos": []}
        marker = max(markers, key=lambda path: path.stat().st_mtime)
        run_dir = marker.parent
        return {
            "manifest": str(marker.resolve()),
            "image_run_dir": str(run_dir.resolve()),
            "passed": self._files(run_dir / "passed", IMAGE_SUFFIXES),
            "failed": self._files(run_dir / "failed", IMAGE_SUFFIXES),
            "needs_review": self._files(run_dir / "needs_review", IMAGE_SUFFIXES),
            "videos": [],
        }


class VideoOnlyPipelineAdapter(FullVideoPipelineAdapter):
    pipeline_id = "video_only"
    name = "仅生成视频"
    description = "将上传图片直接交给 Grok Imagine 生成视频"
    providers = ["grok"]
    stages = [
        {"id": "preflight", "label": "浏览器检查"},
        {"id": "grok", "label": "视频生成"},
        {"id": "complete", "label": "完成"},
    ]

    def validate(self, config, image_paths):
        errors = []
        if not image_paths or len(image_paths) > 3:
            errors.append("请上传 1 到 3 张图片。")
        if not str(config.get("grok_cdp_url") or "").strip():
            errors.append("请填写 Grok CDP 地址。")
        return errors

    def build_command(self, workspace, config, image_paths):
        command = [
            sys.executable,
            "-u",
            str(Path(workspace) / "grok_video_pipeline.py"),
            "--cdp-url", config.get("grok_cdp_url", "http://127.0.0.1:9444"),
            "--images",
        ]
        command.extend(str(path) for path in image_paths)
        command.extend([
            "--output-dir", str(config.get("video_output_dir") or "video_runs"),
            "--resolution", str(config.get("resolution") or "720p"),
            "--duration", str(config.get("duration") or "10s"),
            "--aspect-ratio", str(config.get("aspect_ratio") or "9:16"),
            "--video-timeout", str(config.get("video_timeout") or 1800),
            "--auto-continue",
        ])
        prompt = str(config.get("video_prompt") or "").strip()
        if prompt:
            command.extend(["--prompt", prompt])
        if config.get("reuse_current_grok_page"):
            command.append("--reuse-current-page")
        return command

    def preflight(self, config):
        endpoint = config.get("grok_cdp_url", "http://127.0.0.1:9444")
        try:
            with urllib.request.urlopen(endpoint.rstrip("/") + "/json/version", timeout=3) as response:
                if response.status != 200:
                    raise RuntimeError("HTTP {0}".format(response.status))
            return []
        except Exception as exc:
            return ["Grok 调试浏览器未就绪 ({0}): {1}".format(endpoint, exc)]

    def detect_stage(self, line):
        return "complete" if "完成。视频运行产物目录" in line else None

    def collect_artifacts(self, workspace, config, started_at_epoch):
        output_dir = Path(config.get("video_output_dir") or "video_runs")
        if not output_dir.is_absolute():
            output_dir = Path(workspace) / output_dir
        markers = []
        if output_dir.exists():
            for marker in output_dir.glob("*/request.json"):
                try:
                    if marker.stat().st_mtime >= started_at_epoch - 2:
                        markers.append(marker)
                except OSError:
                    continue
        if not markers:
            return {"manifest": None, "passed": [], "failed": [], "needs_review": [], "videos": []}
        marker = max(markers, key=lambda path: path.stat().st_mtime)
        run_dir = marker.parent
        return {
            "manifest": str(marker.resolve()),
            "video_run_dir": str(run_dir.resolve()),
            "passed": [],
            "failed": [],
            "needs_review": [],
            "videos": self._files(run_dir / "videos", VIDEO_SUFFIXES),
        }


PIPELINES = {
    FullVideoPipelineAdapter.pipeline_id: FullVideoPipelineAdapter(),
    ImageOnlyPipelineAdapter.pipeline_id: ImageOnlyPipelineAdapter(),
    VideoOnlyPipelineAdapter.pipeline_id: VideoOnlyPipelineAdapter(),
}


def get_pipeline(pipeline_id):
    return PIPELINES.get(pipeline_id)


def pipeline_schemas():
    return [adapter.schema() for adapter in PIPELINES.values()]
