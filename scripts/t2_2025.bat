@echo off
REM ===============================================================
REM  T2 VERIFY - REAL DATA 2025-12
REM  Double-click runner. No arguments needed.
REM  Simply forwards fixed args to t2_verify.bat.
REM
REM  THIS FILE IS PURE ASCII ON PURPOSE - cmd.exe parses UTF-8
REM  Chinese as GBK and garbles it.
REM
REM  DO NOT copy multi-line text from markdown / README into cmd.
REM  Use this file instead.
REM ===============================================================

call "%~dp0t2_verify.bat" 2025 12
