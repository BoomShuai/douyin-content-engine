@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m douyin_engine serve --open %*
) else (
  python -m douyin_engine serve --open %*
)
