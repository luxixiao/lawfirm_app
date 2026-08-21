@echo off
chcp 65001 >nul
cd /d %~dp0
call C:\Users\big\.workbuddy\binaries\python\envs\default\Scripts\activate.bat
python main.py
pause
