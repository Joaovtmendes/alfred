#!/usr/bin/env bash
# PreToolUse: block Claude from writing secret files (.env, keys, certificates).
# Input: hook JSON on stdin. Exit 2 = block (stderr is shown to Claude).
path=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')
name=$(basename "$path")
case "$name" in
  .env.example) exit 0 ;;
  .env|.env.*|*.pem|*.key|*.p12)
    echo "Bloqueado: $name contém segredos. Edite à mão ou use .env.example." >&2
    exit 2 ;;
esac
case "$path" in */secrets/*) echo "Bloqueado: pasta secrets/." >&2; exit 2 ;; esac
exit 0
