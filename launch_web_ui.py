#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import os
import socket
import subprocess
import sys
import time
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 7860


def port_is_open():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex((HOST, PORT)) == 0
    finally:
        sock.close()


def powershell_text(command):
    return subprocess.check_output(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
    ).strip()


def stop_old_web_server():
    if not port_is_open():
        return
    pid_text = powershell_text(
        "$c=Get-NetTCPConnection -LocalPort {0} -State Listen -ErrorAction SilentlyContinue; "
        "if($c){{$c[0].OwningProcess}}".format(PORT)
    )
    if not pid_text.isdigit():
        raise RuntimeError("Port {0} is occupied and its process cannot be identified.".format(PORT))
    command_line = powershell_text(
        "$p=Get-CimInstance Win32_Process -Filter 'ProcessId={0}'; $p.CommandLine".format(pid_text)
    )
    if "web_app.py" not in command_line.lower():
        raise RuntimeError("Port {0} is occupied by another program (PID {1}).".format(PORT, pid_text))
    subprocess.check_call(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Stop-Process -Id {0} -Force".format(pid_text),
        ]
    )
    deadline = time.time() + 5
    while time.time() < deadline and port_is_open():
        time.sleep(0.2)
    if port_is_open():
        raise RuntimeError("The old web server did not stop.")


def main():
    os.chdir(str(WORKSPACE))
    stop_old_web_server()
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    command = [
        sys.executable,
        str(WORKSPACE / "web_app.py"),
        "--host",
        HOST,
        "--port",
        str(PORT),
    ]
    print("Frameflow: http://{0}:{1}".format(HOST, PORT))
    return subprocess.call(command, cwd=str(WORKSPACE), env=environment)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("Failed to start Frameflow: {0}".format(exc))
        sys.exit(1)
