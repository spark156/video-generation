@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title Frameflow Web UI
python launch_web_ui.py
if errorlevel 1 pause
