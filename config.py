"""
設定の一元管理。

ローカルでは .env を読み、クラウド(Streamlit Community Cloud)では st.secrets を読む。
どちらの環境でも os.environ に橋渡しすることで、anthropic SDK や SQLAlchemy が
そのまま環境変数から値を拾えるようにする。
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# ローカルの .env を優先（シェルに残った空の値を上書き）
load_dotenv(override=True)

_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "DATABASE_URL")

# Streamlit Cloud の Secrets を環境変数へ橋渡し（.env が無い環境向け）
try:
    import streamlit as st

    for _k in _KEYS:
        # os.getenv が空/未設定なら secrets から補う
        if not os.getenv(_k) and _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    # Streamlit ランタイム外や secrets 未設定でも動くようにする
    pass


def get(key: str, default: str | None = None) -> str | None:
    val = os.getenv(key)
    return val if val else default
