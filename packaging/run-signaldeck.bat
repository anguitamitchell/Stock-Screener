@echo off
setlocal
cd /d "%~dp0\.."
py -m self_screener.cli serve --host 127.0.0.1 --port 8765
