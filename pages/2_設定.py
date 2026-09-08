"""設定・状態確認ページ。"""

import os

import streamlit as st

import config  # noqa: F401  .env / st.secrets を環境変数へ（最初に実行）
from auth import is_locked, require_login
from db.session import init_db, DATABASE_URL
from mail import gmail
from mail.gmail import GmailError
from ocr.extract import DEFAULT_MODEL
from services import cards, mail_log

st.set_page_config(page_title="設定", page_icon="⚙️", layout="centered")

require_login()
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

st.subheader("アクセス制限")
if is_locked():
    st.success("パスワードロック: 有効")
    st.caption("このアプリを開くにはパスワードが必要です。")
else:
    st.error(
        "パスワードロック: 無効 — URLを知っている人は誰でも名刺データを閲覧できます。"
        "Streamlit Cloud の Settings → Secrets に `APP_PASSWORD` を設定してください。"
    )

st.subheader("Gmail（お礼メールの下書き）")
if gmail.is_configured():
    st.success("認証情報: 設定済み")
else:
    st.warning(
        "認証情報: 未設定 — Mac のターミナルで `python3 tools/gmail_auth.py` を実行し、"
        "表示された3つの値を .env（クラウドは Secrets）に登録してください。"
    )
st.caption("権限は「下書きの作成」のみ。このアプリからメールを送信することはありません。")

if st.button("🔌 Gmail 接続テスト"):
    with st.spinner("Gmail に接続しています…"):
        try:
            account = gmail.current_account()
        except GmailError as e:
            st.error(str(e))
        except Exception as e:  # noqa: BLE001
            st.error(f"想定外のエラー: {e}")
        else:
            st.success(f"✅ 接続中: {account}")

st.subheader("お礼メールの下書き履歴")
rows = mail_log.recent(20)
if rows:
    st.dataframe(
        [
            {
                "作成日時": r["created_at_jst"],
                "宛先": r["to_email"],
                "パターン": r["pattern_label"],
                "件名": r["subject"],
            }
            for r in rows
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.caption("まだ作成した下書きはありません。")

st.subheader("使い方")
st.markdown(
    "1. `.env` に `ANTHROPIC_API_KEY` を設定\n"
    "2. 「名刺を登録」で撮影/アップロード → 「読み取る」→ 内容を確認/修正 → 「保存」\n"
    "3. 「一覧・検索」で検索・編集・削除、CSV/vCard エクスポート\n"
    "4. 「一覧・検索」の詳細欄からお礼メールの Gmail 下書きを作成"
)
