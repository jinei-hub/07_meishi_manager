"""アプリ全体のパスワードロック。

このリポジトリは公開されているため、URLを知っただけで名刺データ（取引先の
個人情報）が読める状態にしてはいけない。全ページの先頭で require_login() を呼ぶ。

■ 02_sns_analyser との違い
  02 は streamlit-authenticator + .streamlit/auth.yaml（複数ユーザー・role管理）。
  07 は利用者が1人で、かつ Streamlit Cloud には永続ディスクが無く auth.yaml を
  置けないため、Secrets の APP_PASSWORD と突き合わせるだけの最小構成にする。

■ ログインの保持（スマホでの再入力を減らすため）
  ログイン後、署名付きトークンを Cookie に置いて既定30日間は再入力を省く。
  Cookie に入るのは「有効期限 + その HMAC」だけで、パスワードそのものは入らない。
  鍵は APP_PASSWORD なので、値を書き換えても検証に落ちるだけで通れない。
  APP_PASSWORD を変更すると、既存の Cookie は自動的に全て無効になる。

  Cookie の読み取りは st.context.cookies（リクエストヘッダ由来）。
  書き込みは Streamlit に API が無いので JS でやる。JS はログイン直後の1回だけ
  描画する（高さ0の不可視コンポーネント）。

■ APP_PASSWORD 未設定のとき
  ローカル開発を止めないため通すが、画面に警告を出し続ける。
  クラウドでは必ず Secrets に設定すること。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import streamlit as st

COOKIE_NAME = "meishi_auth"
REMEMBER_DAYS = 30

_SESSION_KEY = "_authenticated"
_PENDING_COOKIE = "_auth_cookie_pending"


def is_locked() -> bool:
    """パスワードロックが有効か（設定ページの表示用）。"""
    return bool(os.getenv("APP_PASSWORD"))


# ── トークン（有効期限に APP_PASSWORD で署名したもの）────────────
def _sign(expiry: int, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), str(expiry).encode("ascii"),
                    hashlib.sha256).hexdigest()


def make_token(secret: str, days: int = REMEMBER_DAYS) -> tuple[str, int]:
    """(トークン, 有効秒数) を返す。"""
    max_age = days * 86400
    expiry = int(time.time()) + max_age
    return f"{expiry}.{_sign(expiry, secret)}", max_age


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


# ── Cookie の読み書き ────────────────────────────────────────────
def _cookie_from_request() -> str:
    try:
        return st.context.cookies.get(COOKIE_NAME, "") or ""
    except Exception:  # noqa: BLE001  古い Streamlit や実行環境の違いで落とさない
        return ""


def _write_cookie(value: str, max_age: int) -> None:
    """JS で Cookie を書く（消すときは max_age=0）。

    アプリは *.streamlit.app 上の iframe の中で動くため、まず親フレームの
    document に書く。失敗したら自分の document にフォールバックする。

    st.components.v1.html は 1.63 で非推奨（st.iframe を使えと警告が出る）だが、
    ここは置き換えられない:
      - st.iframe は src（URL/Path）しか取れず、その場の JS を実行できない
      - data: URL にすると独自オリジンになり window.parent.document を触れない
      - st.html はスクリプトを実行しない
    srcdoc の iframe は親と同一オリジンなので Cookie を書ける。この方式が必要。
    """
    from streamlit.components.v1 import html

    js = """
<script>
(function () {
  var name = %s, value = %s, maxAge = %d;
  function put(doc, proto) {
    var secure = proto === 'https:' ? '; Secure' : '';
    doc.cookie = name + '=' + value + '; path=/; max-age=' + maxAge
               + '; SameSite=Lax' + secure;
  }
  try { put(window.parent.document, window.parent.location.protocol); }
  catch (e) { put(document, location.protocol); }
})();
</script>
""" % (json.dumps(COOKIE_NAME), json.dumps(value), int(max_age))
    html(js, height=0)


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

    # ログイン直後・ログアウト直後に一度だけ Cookie を書く
    pending = st.session_state.pop(_PENDING_COOKIE, None)
    if pending is not None:
        _write_cookie(*pending)

    if st.session_state.get(_SESSION_KEY):
        return

    # Cookie が生きていればパスワードを聞かない
    if token_is_valid(_cookie_from_request(), expected):
        st.session_state[_SESSION_KEY] = True
        return

    st.title("🔒 名刺管理")
    st.caption("パスワードを入力してください。")
    with st.form("login"):
        pw = st.text_input("パスワード", type="password")
        remember = st.checkbox(
            f"このブラウザに{REMEMBER_DAYS}日間ログインを保持する", value=True,
            help="共有端末では外してください。",
        )
        if st.form_submit_button("ログイン", type="primary"):
            # compare_digest は非ASCII文字列を受け付けないので必ず bytes で比較する
            # （日本語のパスワードを入れるとクラッシュするため）
            if hmac.compare_digest(pw.encode("utf-8"), expected.encode("utf-8")):
                st.session_state[_SESSION_KEY] = True
                if remember:
                    st.session_state[_PENDING_COOKIE] = make_token(expected)
                st.rerun()
            else:
                st.error("パスワードが違います。")
    st.stop()


def remember_status() -> tuple[bool, int]:
    """(このブラウザにログインが保持されているか, 残り日数)。

    サーバに Cookie が届いているかを見るので、保持の仕組みが実際に
    効いているかの確認に使える。
    """
    secret = os.getenv("APP_PASSWORD") or ""
    token = _cookie_from_request()
    if not token_is_valid(token, secret):
        return False, 0
    expiry = int(token.partition(".")[0])
    return True, max(0, (expiry - int(time.time())) // 86400)


def logout() -> None:
    """このブラウザのログインを解除する（Cookie も消す）。"""
    st.session_state.pop(_SESSION_KEY, None)
    # 実際の削除は次の実行の先頭で行う（JS を確実に描画させるため）
    st.session_state[_PENDING_COOKIE] = ("", 0)
    st.rerun()
