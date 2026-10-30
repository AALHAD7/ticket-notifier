#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/Google Login.py"
python3 "$SCRIPT_DIR/ticket_notifier.py"
