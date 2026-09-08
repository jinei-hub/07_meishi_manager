"""Gmail の「下書き」を作る。送信は行わない。

■ 送信しないことの担保（このファイルの不変条件）
  1. スコープは gmail.compose のみ。gmail.send は要求しない
  2. このファイルに drafts().send / messages().send を一切書かない
     → `grep -rn "messages()\\.send\\|drafts()\\.send" mail/` は常に 0 件であること
  実際の送信は、人が Gmail で下書きを開いて自分で押す。

■ 認証情報の優先順
  1. 環境変数 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GMAIL_REFRESH_TOKEN
     （Streamlit Cloud では config.py が st.secrets から橋渡しする）
  2. credentials/token.json（ローカルで tools/gmail_auth.py を叩いた直後の退路）
  クラウドには永続ディスクが無いため 1 が本線。ローカルも同じ経路を通るので
  「ローカルでは動くのにクラウドで落ちる」が起きない。

■ google 系ライブラリは必ず関数内で import する
  このモジュールはページのロード時に読まれるため、トップレベル import だと
  未インストール環境でページ全体が落ちる（ocr/extract.py が anthropic を
  関数内 import しているのと同じ理由）。
"""

from __future__ import annotations

import base64
import mimetypes
import os
from email import policy
from email.message import EmailMessage
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

TOKEN_PATH = Path(__file__).resolve().parent.parent / "credentials" / "token.json"

# Gmail の上限は 25MB だが、Streamlit 経由の往復と Neon への負荷を考えて控えめに切る
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024

DRAFTS_URL = "https://mail.google.com/mail/u/0/#drafts"

_ENV_KEYS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")

_SETUP_HINT = (
    "Mac のターミナルで次を実行し、表示された3つの値を "
    ".env（クラウドは Streamlit Secrets）に登録してください:\n"
    "    python3 tools/gmail_auth.py"
)


class GmailError(Exception):
    """Gmail 連携の失敗（UI へ分かりやすく伝える用）。"""


def is_configured() -> bool:
    """認証情報が揃っているか（通信はしない）。設定ページの表示用。"""
    return all(os.getenv(k) for k in _ENV_KEYS) or TOKEN_PATH.exists()


def _credentials():
    """有効な Credentials を返す。

    Raises:
        GmailError: ライブラリ未導入・未設定・失効。いずれも次の一手を含めて返す。
    """
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as e:
        raise GmailError(
            "google-auth が未インストールです。"
            "`pip install -r requirements.txt` を実行してください。"
        ) from e

    values = {k: os.getenv(k) for k in _ENV_KEYS}
    present = [k for k, v in values.items() if v]

    if len(present) == len(_ENV_KEYS):
        creds = Credentials.from_authorized_user_info(
            {
                "client_id": values["GOOGLE_CLIENT_ID"],
                "client_secret": values["GOOGLE_CLIENT_SECRET"],
                "refresh_token": values["GMAIL_REFRESH_TOKEN"],
            },
            SCOPES,
        )
    elif present:
        # 一部だけ設定されている状態は、token.json に落とすと原因が分かりにくくなる
        missing = "、".join(k for k in _ENV_KEYS if not values[k])
        raise GmailError(
            f"Gmail の設定が途中までしかありません（不足: {missing}）。\n{_SETUP_HINT}"
        )
    elif TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    else:
        raise GmailError(f"Gmail の認証情報が未設定です。\n{_SETUP_HINT}")

    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise GmailError(
                "Gmail の認証が切れています（refresh_token が失効）。\n"
                f"{_SETUP_HINT}\n"
                "※ GCP の OAuth 同意画面が「外部 + テスト」のままだと 7 日で失効します。"
                "「内部」にするか「本番環境に公開」してください。"
            ) from e
        except Exception as e:
            raise GmailError(f"Gmail の認証更新に失敗しました: {e}") from e
    return creds


def _service():
    try:
        from googleapiclient.discovery import build
    except ImportError as e:
        raise GmailError(
            "google-api-python-client が未インストールです。"
            "`pip install -r requirements.txt` を実行してください。"
        ) from e
    return build("gmail", "v1", credentials=_credentials(), cache_discovery=False)


def _http_message(e: Exception) -> str:
    """googleapiclient の HttpError を、次の一手つきの日本語にする。"""
    try:
        from googleapiclient.errors import HttpError
    except ImportError:
        return f"Gmail への接続に失敗しました: {e}"

    if not isinstance(e, HttpError):
        return f"Gmail への接続に失敗しました: {e}"

    status = getattr(e.resp, "status", 0)
    detail = (e.content or b"").decode("utf-8", "ignore")

    if status == 403 and ("SCOPE_INSUFFICIENT" in detail or "insufficient" in detail):
        return (
            "Gmail の権限（スコープ）が足りません。\n"
            "GCP の OAuth 同意画面に gmail.compose を追加したうえで、"
            "`python3 tools/gmail_auth.py` をやり直してください。"
        )
    if status == 403 and ("has not been used in project" in detail
                          or "accessNotConfigured" in detail):
        return (
            "GCP プロジェクトで Gmail API が有効になっていません。\n"
            "Google Cloud コンソール →「APIとサービス」→「ライブラリ」→ Gmail API →"
            "「有効にする」を実行してください。"
        )
    if status == 429 or status >= 500:
        return "Gmail API が混雑しています。少し待ってからもう一度お試しください。"
    return f"Gmail API エラー（{status}）: {getattr(e, 'reason', e)}"


def current_account() -> str:
    """認証中のアカウントのメールアドレス。取り違え・失効の検知に使う。

    users.getProfile は gmail.compose スコープで許可されている。
    """
    try:
        profile = _service().users().getProfile(userId="me").execute()
    except GmailError:
        raise
    except Exception as e:
        raise GmailError(_http_message(e)) from e
    return profile.get("emailAddress", "")


def _split_addresses(value: str) -> list[str]:
    """カンマ/読点/セミコロン区切りのアドレス列を配列にする。"""
    if not value:
        return []
    for sep in ("、", ";", "\n"):
        value = value.replace(sep, ",")
    return [a.strip() for a in value.split(",") if a.strip()]


def looks_like_email(value: str) -> bool:
    """宛先として使えそうか。厳密な検証はしない
    （正規表現の誤判定で入力を弾く方が実害が大きい）。"""
    parts = _split_addresses(value)
    return bool(parts) and all(
        "@" in p and "." in p.split("@")[-1] for p in parts
    )


def build_message(to: str, subject: str, body: str,
                  attachments: list[tuple[str, bytes]] | None = None,
                  cc: str = "") -> EmailMessage:
    """下書きにする EmailMessage を組み立てる。

    Args:
        attachments: [(ファイル名, バイト列), ...]
    Raises:
        GmailError: 宛先が空 / 添付が上限超過。
    """
    to_list = _split_addresses(to)
    if not to_list:
        raise GmailError("宛先が空です。メールアドレスを入力してください。")

    attachments = attachments or []
    total = sum(len(data) for _name, data in attachments)
    if total > MAX_ATTACHMENT_BYTES:
        raise GmailError(
            f"添付の合計が {total / 1024 / 1024:.1f}MB です。"
            f"{MAX_ATTACHMENT_BYTES // 1024 // 1024}MB 以下にしてください"
            "（大きいファイルは Google ドライブのリンク共有をご検討ください）。"
        )

    msg = EmailMessage()
    # From は指定しない。省略すると Gmail が認証アカウントの既定の送信元で埋めてくれる。
    msg["To"] = ", ".join(to_list)
    if cc_list := _split_addresses(cc):
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject or ""
    msg.set_content(body or "", subtype="plain", charset="utf-8", cte="base64")

    for name, data in attachments:
        ctype, _encoding = mimetypes.guess_type(name)
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        # filename に日本語が入っても EmailMessage が RFC 2231 で符号化してくれる
        msg.add_attachment(data, maintype=maintype,
                           subtype=subtype or "octet-stream", filename=name)
    return msg


def create_draft(to: str, subject: str, body: str,
                 attachments: list[tuple[str, bytes]] | None = None,
                 cc: str = "") -> str:
    """Gmail の下書きを作り、その下書きIDを返す。送信はしない。

    Raises:
        GmailError: 認証・権限・通信・入力のいずれかの失敗。
    """
    msg = build_message(to, subject, body, attachments, cc)
    # policy.SMTP = 改行を CRLF に揃えたもの。Gmail に渡す raw はこれが安全。
    raw = base64.urlsafe_b64encode(msg.as_bytes(policy=policy.SMTP)).decode("ascii")

    try:
        draft = (
            _service().users().drafts()
            .create(userId="me", body={"message": {"raw": raw}})
            .execute()
        )
    except GmailError:
        raise
    except Exception as e:
        raise GmailError(_http_message(e)) from e

    draft_id = draft.get("id", "")
    if not draft_id:
        raise GmailError("下書きは作成されましたが、IDを取得できませんでした。")
    return draft_id
