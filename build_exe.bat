@echo off
setlocal

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

set "PYTHON_EXE=C:\Users\Antonis\AppData\Local\Programs\Python\Python312\python.exe"

"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean JS8Mesh.spec

echo.
if exist "dist\JS8Mesh-v0.10.4-beta.exe" (
    echo Build complete: dist\JS8Mesh-v0.10.4-beta.exe
) else (
    echo Build finished, but dist\JS8Mesh-v0.10.4-beta.exe was not found.
)

pause
