# SignalDeck Windows Packaging

This project is a local dashboard. The packaged Windows app starts in the background and opens:

```text
http://127.0.0.1:8765/
```

## Best Option: Non-Technical User

Give the user the full folder:

```text
dist\SignalDeck\
```

They only need to double-click:

```text
SignalDeck.exe
```

SignalDeck opens the dashboard in their browser automatically. They do not need PowerShell, Command Prompt, Python, or Git.

To close the app, click the `×` button at the bottom of the left sidebar inside SignalDeck, then close the browser tab.

## Option 1: Run From Source

Install Python 3.11 or newer from python.org, then run:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m self_screener.cli serve
```

Or double-click:

```text
packaging\run-signaldeck.bat
```

## Option 2: Build A Windows EXE

On the Windows machine:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
```

The app will be created at:

```text
dist\SignalDeck\SignalDeck.exe
```

This build is windowless, so it behaves like a normal desktop app instead of leaving a terminal window open.

## Deployment Folder

Copy the whole folder below to the target Windows device:

```text
dist\SignalDeck\
```

Do not copy only the `.exe`; the folder also contains the bundled runtime and local app files.

## Data Notes

SignalDeck writes local caches, reports, snapshots, and review queue data under:

```text
data\
```

If data is missing, the app can rebuild most of it from public sources when it has internet access.
