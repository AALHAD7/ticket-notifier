#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "=========================================================="
echo "               🎫 TICKET NOTIFIER"
echo "=========================================================="
echo "Started at: $(date)"
echo "Directory : $SCRIPT_DIR"
echo ""

# Find suitable python
if [ -x "/opt/anaconda3/bin/python3" ]; then
    PYTHON_BIN="/opt/anaconda3/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "[ERROR] python3 not found!"
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi

echo "Running with: $PYTHON_BIN"
echo "Checking tickets and syncing..."
echo "----------------------------------------------------------"

"$PYTHON_BIN" "$SCRIPT_DIR/ticket_notifier.py"
EXIT_CODE=$?

echo "----------------------------------------------------------"
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "  ✅ Ticket Notifier completed successfully!"
else
    echo "  ❌ Ticket Notifier ended with exit code: $EXIT_CODE"
fi
echo "  Finished at: $(date)"
echo "=========================================================="
echo ""
read -n 1 -s -r -p "Press any key to close this window..."
echo ""
exit $EXIT_CODE
