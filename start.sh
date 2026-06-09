#!/bin/bash
cd "$(dirname "$0")"
echo "🚀 Boekhouding opstarten op http://localhost:5050"
open http://localhost:5050
python3 app.py
