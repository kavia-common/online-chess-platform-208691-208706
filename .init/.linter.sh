#!/bin/bash
cd /home/kavia/workspace/code-generation/online-chess-platform-208691-208706/chess_backend
source venv/bin/activate
flake8 .
LINT_EXIT_CODE=$?
if [ $LINT_EXIT_CODE -ne 0 ]; then
  exit 1
fi

