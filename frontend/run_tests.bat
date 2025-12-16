@echo off
echo Installing test dependencies...
call npm install

echo.
echo Running tests...
call npm test

pause

