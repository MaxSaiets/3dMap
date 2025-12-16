#!/bin/bash
echo "Installing test dependencies..."
source venv/bin/activate
pip install -r requirements-test.txt

echo ""
echo "Running tests..."
pytest tests/ -v

