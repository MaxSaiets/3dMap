@echo off
echo Running building coordinate tests...
cd /d %~dp0
python -m pytest tests/test_building_coordinates.py -v --tb=short
pause

