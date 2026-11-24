@echo off
rem Build without questions: installs Inno Setup automatically if it is missing.
call "%~dp0build_installer.bat" /auto > "%~dp0build_log.txt" 2>&1
type "%~dp0build_log.txt"
pause
