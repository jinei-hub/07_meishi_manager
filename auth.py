"""アプリ全体のパスワードロック。

このリポジトリは公開されているため、URLを知っただけで名刺データ（取引先の
個人情報）が読める状態にしてはいけない。全ページの先頭で require_login() を呼ぶ。

■ ログイン保持の方式（Cookie は使えない）
  Streamlit Cloud はアプリに Cookie を渡さない（st.context.cookies が常に空。
  2026-09-13 に実測）。そのため Cookie 方式は成立しない。
  代わりに URL のクエリ `?k=` に署名付きトークンを持たせる。
    - 中身は「有効期限 + それを APP_PASSWORD で署名した HMAC」だけ。
      パスワードそのものは入らず、期限を書き換えれば署名が合わなくなる。
    - APP_PASSWORD を変えると全端末の保持が即座に無効になる（紛失時の対処）。
    - ホーム画面に追加しておけば、次からパスワードなしで開ける。
  ⚠️ この URL を渡した相手は期限内ログインできてしまう。共有しないこと。
     共有端末では保持のチェックを外す。

■ APP_PASSWORD 未設定のとき
  ローカル開発を止めないため通すが、画面に警告を出し続ける。
  クラウドでは必ず Secrets に設定すること。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

import streamlit as st

QUERY_KEY = "k"
REMEMBER_DAYS = 30

_SESSION_KEY = "_authenticated"


def is_locked() -> bool:
    """パスワードロックが有効か（設定ページの表示用）。"""
    return bool(os.getenv("APP_PASSWORD"))


# ── トークン（有効期限に APP_PASSWORD で署名したもの）────────────
def _sign(expiry: int, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), str(expiry).encode("ascii"),
                    hashlib.sha256).hexdigest()


def make_token(secret: str, days: int = REMEMBER_DAYS) -> str:
    expiry = int(time.time()) + days * 86400
    return f"{expiry}.{_sign(expiry, secret)}"


def token_is_valid(token: str, secret: str) -> bool:
    """期限内で、かつ署名が一致するか。"""
    if not token or not secret or "." not in token:
        return False
    exp_s, _, sig = token.partition(".")
    try:
        expiry = int(exp_s)
    except ValueError:
        return False
    if expiry < time.time():
        return False
    return hmac.compare_digest(sig, _sign(expiry, secret))


def _token_from_url() -> str:
    """URL の ?k= を取り出す。

    通常は文字列だが、?k=a&k=b のように複数付いた場合や実行環境によっては
    リストで返る。どちらでも落ちないようにしておく。
    """
    value = st.query_params.get(QUERY_KEY, "")
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "")


def remember_status() -> tuple[bool, int]:
    """(このURLで保持されているか, 残り日数)。設定ページの表示用。"""
    secret = os.getenv("APP_PASSWORD") or ""
    token = _token_from_url()
    if not token_is_valid(token, secret):
        return False, 0
    expiry = int(token.partition(".")[0])
    return True, max(0, (expiry - int(time.time())) // 86400)


# ── 本体 ─────────────────────────────────────────────────────────
def require_login() -> None:
    """未ログインならログイン画面を出して st.stop() する。"""
    expected = os.getenv("APP_PASSWORD") or ""

    if not expected:
        st.warning(
            "⚠️ このアプリはパスワード保護されていません。"
            "URLを知っている人は誰でも名刺データを閲覧できます。"
            "Streamlit Cloud の Secrets に `APP_PASSWORD` を設定してください。",
            icon="🔓",
        )
        return

    if st.session_state.get(_SESSION_KEY):
        return

    # URL に有効なトークンがあればパスワードを聞かない
    if token_is_valid(_token_from_url(), expected):
        st.session_state[_SESSION_KEY] = True
        return

    st.title("🔒 名刺管理")
    st.caption("パスワードを入力してください。")
    with st.form("login"):
        pw = st.text_input("パスワード", type="password")
        remember = st.checkbox(
            f"このブラウザで{REMEMBER_DAYS}日間ログインを保持する", value=True,
            help="保持するとURLに合言葉が入ります。共有端末では外してください。",
        )
        if st.form_submit_button("ログイン", type="primary"):
            # compare_digest は非ASCII文字列を受け付けないので必ず bytes で比較する
            # （日本語のパスワードを入れるとクラッシュするため）
            if hmac.compare_digest(pw.encode("utf-8"), expected.encode("utf-8")):
                st.session_state[_SESSION_KEY] = True
                if remember:
                    st.query_params[QUERY_KEY] = make_token(expected)
                st.rerun()
            else:
                st.error("パスワードが違います。")
    st.stop()


def logout() -> None:
    """ログアウトし、URL の合言葉も消す（この端末の保持を解除）。"""
    st.session_state.pop(_SESSION_KEY, None)
    if QUERY_KEY in st.query_params:
        del st.query_params[QUERY_KEY]
    st.rerun()
