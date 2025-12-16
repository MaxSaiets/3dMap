@echo off
echo Installing test dependencies...
venv\Scripts\pip.exe install -r requirements-test.txt

echo.
echo Running tests...
venv\Scripts\pytest.exe tests\ -v

pause

