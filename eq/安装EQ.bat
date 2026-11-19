@echo off
chcp 65001 >nul
setlocal
echo ============================================
echo   FPS 脚步增强 EQ  （Equalizer APO）
echo ============================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_eq.ps1" %*
echo.
pause
