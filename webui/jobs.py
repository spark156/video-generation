#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import datetime as _dt
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .pipelines import get_pipeline


FINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


def iso_now():
    return _dt.datetime.now().isoformat(timespec="seconds")


def decode_process_output(data):
    if not data:
        return ""
    if isinstance(data, str):
        return data
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class JobManager(object):
    def __init__(self, workspace, data_dir):
        self.workspace = Path(workspace).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs = {}
        self._processes = {}
        self._lock = threading.RLock()
        self._queue = queue.Queue()
        self._runtime_check = {"checked_at": 0, "ok": False, "message": "尚未检查"}
        self._load_jobs()
        self._worker = threading.Thread(target=self._worker_loop, name="pipeline-job-worker")
        self._worker.daemon = True
        self._worker.start()

    def _load_jobs(self):
        for metadata_path in self.jobs_dir.glob("*/job.json"):
            try:
                job = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if job.get("status") in {"queued", "running"}:
                job["status"] = "interrupted"
                job["finished_at"] = iso_now()
                job["error"] = "网页服务重启，任务已中断。"
                self._write_job(job)
            self._jobs[job["id"]] = job

    def create_job(self, pipeline_id, config, image_paths, original_names):
        adapter = get_pipeline(pipeline_id)
        if adapter is None:
            raise ValueError("未知流水线: {0}".format(pipeline_id))
        errors = adapter.validate(config, image_paths)
        if errors:
            raise ValueError("\n".join(errors))

        job_id = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        job = {
            "id": job_id,
            "pipeline_id": pipeline_id,
            "pipeline_name": adapter.name,
            "status": "queued",
            "stage": "preflight",
            "created_at": iso_now(),
            "started_at": None,
            "finished_at": None,
            "return_code": None,
            "error": None,
            "config": config,
            "input_files": list(image_paths),
            "input_images": list(image_paths),
            "input_names": list(original_names),
            "artifacts": {},
        }
        with self._lock:
            self._jobs[job_id] = job
            self._write_job(job)
        self._queue.put(job_id)
        return self.public_job(job)

    def list_jobs(self):
        with self._lock:
            jobs = [self.public_job(job) for job in self._jobs.values()]
        return sorted(jobs, key=lambda item: item.get("created_at") or "", reverse=True)

    def get_job(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return self.public_job(job) if job else None

    def cancel_job(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.get("status") in FINAL_STATUSES:
                return self.public_job(job)
            job["status"] = "cancelled"
            job["finished_at"] = iso_now()
            job["error"] = "用户已停止任务。"
            process = self._processes.get(job_id)
            self._write_job(job)
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        self._append_log(job_id, "\n[系统] 已请求停止任务。\n")
        return self.get_job(job_id)

    def read_log(self, job_id, offset=0):
        log_path = self.jobs_dir / job_id / "output.log"
        if not log_path.exists():
            return "", 0
        size = log_path.stat().st_size
        offset = max(0, min(int(offset or 0), size))
        with log_path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
            next_offset = handle.tell()
        return data.decode("utf-8", errors="replace"), next_offset

    def check_runtime(self, force=False):
        with self._lock:
            cached = dict(self._runtime_check)
        if not force and time.time() - cached.get("checked_at", 0) < 30:
            return cached

        command = [
            sys.executable,
            "-c",
            "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); p.stop(); print('ok')",
        ]
        try:
            process = self._start_process(command)
            output_bytes, _ = process.communicate(timeout=30)
            output = decode_process_output(output_bytes)
            ok = process.returncode == 0 and "ok" in (output or "")
            if ok:
                message = "Playwright 可用"
                details = ""
            else:
                details = (output or "").strip()[-3000:]
                if "PermissionError" in details or "WinError 5" in details:
                    message = "Playwright 被当前进程权限限制，请在普通 CMD 中重新启动网页服务。"
                else:
                    message = "Playwright 启动失败。"
        except Exception as exc:
            ok = False
            message = str(exc)
            details = str(exc)
        result = {"checked_at": time.time(), "ok": ok, "message": message, "details": details}
        with self._lock:
            self._runtime_check = result
        return dict(result)

    def _worker_loop(self):
        while True:
            job_id = self._queue.get()
            try:
                self._run_job(job_id)
            finally:
                self._queue.task_done()

    def _run_job(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") == "cancelled":
                return
            adapter = get_pipeline(job["pipeline_id"])
            preflight_errors = adapter.preflight(job["config"])
            if preflight_errors:
                job["status"] = "failed"
                job["stage"] = "preflight"
                job["finished_at"] = iso_now()
                job["error"] = "前置检查失败：" + "；".join(preflight_errors)
                self._write_job(job)
                self._append_log(job_id, "[系统] {0}\n".format(job["error"]))
                return
            job["status"] = "running"
            if len(adapter.stages) > 1:
                job["stage"] = adapter.stages[1]["id"]
            job["started_at"] = iso_now()
            job["started_at_epoch"] = time.time()
            self._write_job(job)

        input_paths = job.get("input_files") or job.get("input_images") or []
        command = adapter.build_command(self.workspace, job["config"], input_paths)
        self._append_log(job_id, "[系统] 任务已启动。\n")
        try:
            process = self._start_process(command)
            with self._lock:
                self._processes[job_id] = process

            for raw_line in iter(process.stdout.readline, b""):
                if not raw_line and process.poll() is not None:
                    break
                line = decode_process_output(raw_line)
                self._append_log(job_id, line)
                stage = adapter.detect_stage(line)
                if stage:
                    with self._lock:
                        current = self._jobs.get(job_id)
                        if current and current.get("status") == "running" and current.get("stage") != stage:
                            current["stage"] = stage
                            self._write_job(current)

            return_code = process.wait()
            with self._lock:
                current = self._jobs[job_id]
                if current.get("status") != "cancelled":
                    current["return_code"] = return_code
                    current["finished_at"] = iso_now()
                    if return_code == 0:
                        current["status"] = "completed"
                        current["stage"] = "complete"
                    else:
                        current["status"] = "failed"
                        current["error"] = "流水线退出码: {0}".format(return_code)
                current["artifacts"] = adapter.collect_artifacts(
                    self.workspace,
                    current["config"],
                    current.get("started_at_epoch") or time.time(),
                )
                self._write_job(current)
        except Exception as exc:
            self._append_log(job_id, "\n[系统] 任务执行失败: {0}\n".format(exc))
            with self._lock:
                current = self._jobs[job_id]
                if current.get("status") != "cancelled":
                    current["status"] = "failed"
                    current["finished_at"] = iso_now()
                    current["error"] = str(exc)
                    self._write_job(current)
        finally:
            with self._lock:
                self._processes.pop(job_id, None)

    def _start_process(self, command):
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        return subprocess.Popen(
            command,
            cwd=str(self.workspace),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=environment,
        )

    def _append_log(self, job_id, text):
        log_path = self.jobs_dir / job_id / "output.log"
        with log_path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()

    def _write_job(self, job):
        job_dir = self.jobs_dir / job["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        temp_path = job_dir / "job.json.tmp"
        target_path = job_dir / "job.json"
        temp_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(temp_path), str(target_path))

    @staticmethod
    def public_job(job):
        if not job:
            return None
        result = dict(job)
        result.pop("started_at_epoch", None)
        return result
