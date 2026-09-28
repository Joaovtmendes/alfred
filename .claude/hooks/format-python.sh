#!/usr/bin/env bash
# PostToolUse: auto-fix + format every Python file Claude edits (ruff).
# Never blocks: formatting problems are reported, not fatal.
path=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')
[[ "$path" == *.py && -f "$path" ]] || exit 0
command -v ruff >/dev/null 2>&1 || exit 0
ruff check --fix --quiet "$path" >/dev/null 2>&1
ruff format --quiet "$path" >/dev/null 2>&1
exit 0
