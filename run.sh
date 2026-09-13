#!/usr/bin/env bash
# ローカルで動かすとき用。8501 は 02_sns_analyser が使うので 8502 で起動する。
#   ./run.sh
# （配色を配るため .streamlit/config.toml をコミットしており、そこに port は書けない）
cd "$(dirname "$0")" || exit 1
exec .venv/bin/streamlit run main.py --server.port 8502 "$@"
