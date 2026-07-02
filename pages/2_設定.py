"""設定・状態確認ページ。"""

import os

import streamlit as st

import config  # noqa: F401  .env / st.secrets を環境変数へ
from db.session import init_db, DATABASE_URL
from ocr.extract import DEFAULT_MODEL
from services import cards

st.set_page_config(page_title="設定", page_icon="⚙️", layout="centered")
init_db()

st.title("⚙️ 設定・状態")

key_set = bool(os.getenv("ANTHROPIC_API_KEY"))
model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)

st.subheader("Claude API")
if key_set:
    st.success("ANTHROPIC_API_KEY: 設定済み")
else:
    st.error("ANTHROPIC_API_KEY: 未設定 — .env に API キーを設定してください。")
st.write(f"使用モデル: `{model}`")

st.subheader("データベース")
# パスワードを伏せて種別・ホストのみ表示
if DATABASE_URL.startswith("sqlite"):
    st.write("接続先: `SQLite（ローカル）`")
else:
    host = DATABASE_URL.split("@")[-1].split("/")[0] if "@" in DATABASE_URL else "外部DB"
    st.write(f"接続先: `PostgreSQL @ {host}`")
st.metric("登録名刺数", cards.count())

st.subheader("使い方")
st.markdown(
    "1. `.env` に `ANTHROPIC_API_KEY` を設定\n"
    "2. 「名刺を登録」で撮影/アップロード → 「読み取る」→ 内容を確認/修正 → 「保存」\n"
    "3. 「一覧・検索」で検索・編集・削除、CSV/vCard エクスポート"
)
