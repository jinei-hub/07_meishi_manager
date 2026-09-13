"""アプリ全体のパスワードロック。

このリポジトリは公開されているため、URLを知っただけで名刺データ（取引先の
個人情報）が読める状態にしてはいけない。全ページの先頭で require_login() を呼ぶ。

■ 02_sns_analyser との違い
  02 は streamlit-authenticator + .streamlit/auth.yaml（複数ユーザー・role管理）。
  07 は利用者が1人で、かつ Streamlit Cloud には永続ディスクが無く auth.yaml を
  置けないため、Secrets の APP_PASSWORD と突き合わせるだけの最小構成にする。

■ 「ログインを長期保持」は Streamlit Cloud では実装できない（2026-09-13 実測）
  Cookie に署名付きトークンを置く方式を試したが、Streamlit Cloud は
  アプリまで Cookie を渡さない（st.context.cookies が常に空）。
  そのため保持はサーバ側のセッションが生きている間だけになる。
  恒久的に入力を省きたい場合は st.login()（OIDC）への移行が必要。

■ APP_PASSWORD 未設定のとき
  ローカル開発を止めないため通すが、画面に警告を出し続ける。
  クラウドでは必ず Secrets に設定すること。
"""

from __future__ import annotations

import hmac
import os

import streamlit as st

_SESSION_KEY = "_authenticated"


def is_locked() -> bool:
    """パスワードロックが有効か（設定ページの表示用）。"""
    return bool(os.getenv("APP_PASSWORD"))


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

    st.title("🔒 名刺管理")
    st.caption("パスワードを入力してください。")
    with st.form("login"):
        pw = st.text_input("パスワード", type="password")
        if st.form_submit_button("ログイン", type="primary"):
            # compare_digest は非ASCII文字列を受け付けないので必ず bytes で比較する
            # （日本語のパスワードを入れるとクラッシュするため）
            if hmac.compare_digest(pw.encode("utf-8"), expected.encode("utf-8")):
                st.session_state[_SESSION_KEY] = True
                st.rerun()
            else:
                st.error("パスワードが違います。")
    st.stop()


def logout() -> None:
    """このセッションのログインを解除する。"""
    st.session_state.pop(_SESSION_KEY, None)
    st.rerun()
