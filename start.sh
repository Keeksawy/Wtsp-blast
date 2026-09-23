#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "Starting WA Outreach..."
.venv/bin/python app.py
