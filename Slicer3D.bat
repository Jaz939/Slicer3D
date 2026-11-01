@echo off
rem Double-click to start Slicer3D. The first run sets things up (a few minutes).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Setting up Slicer3D for the first time...
    py -3 -m venv .venv || python -m venv .venv
    if errorlevel 1 (
        echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH" during setup.
        pause
        exit /b 1
    )
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -e .
    if errorlevel 1 (
        echo Installing the required libraries failed. See the messages above.
        pause
        exit /b 1
    )
)
start "" .venv\Scripts\pythonw.exe -m slicer3d gui
