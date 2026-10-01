@echo off
rem OAS MCP launcher: portable entry for opencode.json (no absolute paths).
rem Resolves the project root from this script location, checks toolkit, then starts the server.
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHON=%SCRIPT_DIR%..\toolkit\python.exe"
if not exist "%PYTHON%" (
  echo [oas-mcp] toolkit\python.exe not found: %PYTHON% 1>&2
  echo [oas-mcp] Run the deploy installer first: deploy\launcher\oas-gui.bat 1>&2
  exit /b 1
)
"%PYTHON%" "%SCRIPT_DIR%mcp_server.py"
exit /b %errorlevel%
