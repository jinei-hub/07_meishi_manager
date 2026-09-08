"""Gmail の refresh_token を取得する初回セットアップ（Mac でのみ実行）。

    cd "/Users/jinei/Desktop/Claude Code/07_meishi_manager"
    source .venv/bin/activate
    python3 tools/gmail_auth.py

ブラウザが開くので jinei@dipilot.jp を選び、「Gmail の下書きの作成」を許可する。
成功すると credentials/token.json に保存し、.env / Streamlit Secrets に貼る値を表示する。

17_cossot_invoice/auth.py がベース。違いは次の3点:
  - スコープが gmail.compose（Drive/Sheets ではない）
  - config を import しない（07 の config.py は streamlit を引き込むため、単体で完結させる）
  - token.json に保存するだけでなく、クラウド用に refresh_token を標準出力へ出す
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
TOKEN_PATH = BASE_DIR / "credentials" / "token.json"
EXPECTED_ACCOUNT = "jinei@dipilot.jp"

# client_secret の探索順。
# 既存規約:「OAuthクライアント(credentials.json)は流用してよいが、token.json は共用しない」
CANDIDATES = [
    os.getenv("GOOGLE_CLIENT_SECRET_FILE"),
    BASE_DIR / "credentials" / "credentials.json",
    BASE_DIR.parent / "04_invoice_uploader" / "credentials" / "credentials.json",
]


def _find_client_secret() -> Path:
    for c in CANDIDATES:
        if not c:
            continue
        p = Path(c)
        if p.exists():
            return p
    print("❌ OAuth クライアント（credentials.json）が見つかりません。")
    print("   次のいずれかに置いてください:")
    for c in CANDIDATES[1:]:
        print(f"     - {c}")
    sys.exit(1)


def main() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("❌ 必要なライブラリが入っていません。先に次を実行してください:")
        print("   pip install -r requirements.txt")
        sys.exit(1)

    path = _find_client_secret()
    print(f"OAuth クライアント: {path}")
    print("ブラウザが開きます。jinei@dipilot.jp を選んでください…\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
    # prompt=consent: 2回目以降も refresh_token を必ず返させる
    # select_account: アカウント取り違えを防ぐ
    creds = flow.run_local_server(port=0, prompt="consent select_account")

    if not creds.refresh_token:
        print("❌ refresh_token が返りませんでした。")
        print("   https://myaccount.google.com/permissions で該当アプリのアクセスを")
        print("   削除してから、もう一度実行してください。")
        sys.exit(1)

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(TOKEN_PATH, 0o600)

    try:
        addr = (
            build("gmail", "v1", credentials=creds, cache_discovery=False)
            .users().getProfile(userId="me").execute()
        ).get("emailAddress", "")
    except Exception as e:
        print(f"⚠️ アカウントの確認に失敗しました（トークンは保存済み）: {e}")
        addr = ""

    print(f"\n✅ 認証したアカウント: {addr or '(不明)'}")
    if addr and addr != EXPECTED_ACCOUNT:
        print(f"⚠️ 想定（{EXPECTED_ACCOUNT}）と違うアカウントです。")
        print("   別のアカウントで下書きを作りたい場合を除き、やり直してください。")
    print(f"   トークン保存先: {TOKEN_PATH}")

    print("\n" + "─" * 60)
    print("【1】.env の末尾に貼る（ローカル用）")
    print("─" * 60)
    print(f"GOOGLE_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")

    print("\n" + "─" * 60)
    print("【2】Streamlit Cloud の Settings → Secrets に貼る（TOML）")
    print("─" * 60)
    print(f'GOOGLE_CLIENT_ID = "{creds.client_id}"')
    print(f'GOOGLE_CLIENT_SECRET = "{creds.client_secret}"')
    print(f'GMAIL_REFRESH_TOKEN = "{creds.refresh_token}"')

    print("\n⚠️ これらの値は GitHub に push しないこと（.env と credentials/ は .gitignore 済み）。")
    print("   チャットやメールにも貼らないこと。貼り終えたら `clear` で画面を消してよい。")


if __name__ == "__main__":
    main()
