@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run SETUP_WINDOWS.cmd first, then close the converter.
    pause
    exit /b 1
)
echo Installing local MinerU 4.1.7 and downloading its models.
echo This requires internet access, disk space and time for model downloads.
".venv\Scripts\python.exe" -m pip install "mineru==4.1.7"
if errorlevel 1 goto failed
set "MINERU_MODEL_SMALL_BACKEND=onnx"
set "MINERU_MODEL_VLM_ENGINE=llama-cpp"
set "MINERU_MODEL_SOURCE=huggingface"
".venv\Scripts\mineru-kit.exe" models download --tier standard --small-backend onnx --vlm-engine llama-cpp --source huggingface
if errorlevel 1 goto failed
set "MINERU_MODEL_SOURCE=local"
".venv\Scripts\mineru-kit.exe" models verify --tier standard --small-backend onnx --vlm-engine llama-cpp
if errorlevel 1 goto failed
echo Local models verified. Double-click START.cmd and try Local only on one PDF.
echo A successful model check still needs a real PDF conversion to verify inference.
pause
exit /b 0
:failed
echo Local setup failed. Read the error above.
echo If model downloads are blocked, consult the MinerU model-source documentation.
pause
exit /b 1
