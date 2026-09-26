"""起動スモークテスト: python tests/smoke_test.py（リポジトリ直下で実行）

クラウドと同じ依存を入れた環境で「アプリが起動画面まで辿り着けるか」だけを見る。
2026-09-26 に SQLAlchemy 2.1 の既定ドライバ変更で本番が落ちた。こういう
「上流の更新で import / 接続準備の段階で落ちる」事故を、依存を上げる前に捕まえる。

外部には一切接続しない（DB は URL を解釈してドライバを読み込むところまで、
API キーはダミー）。GitHub Actions からも .env 無しでそのまま走る。
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# ローカルで走らせても本物の Neon / API を触らないよう、ダミーを先に入れる。
# config.py は load_dotenv(override=True) なので .env があると上書きされる。
# ただし下のチェックはどれも接続しないので、上書きされても安全。
os.environ.setdefault("APP_PASSWORD", "smoke-test")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-dummy")

failures: list[str] = []


def check(name: str, fn) -> None:
    try:
        fn()
        print(f"ok   {name}")
    except Exception as e:  # noqa: BLE001 — 何で落ちても一覧に載せたい
        failures.append(name)
        print(f"FAIL {name}: {type(e).__name__}: {e}")


# 1. Postgres(Neon) のドライバが解決できるか。今回の事故そのもの。
#    create_engine はドライバの import までやるが、接続はしない。
def _postgres_driver():
    from sqlalchemy import create_engine
    from db.session import _normalize_db_url

    for raw in (
        "postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
        "postgres://u:p@ep-x.neon.tech/db?sslmode=require",
        "psql 'postgresql://u:p@ep-x.neon.tech/db?sslmode=require'",
    ):
        engine = create_engine(_normalize_db_url(raw), pool_pre_ping=True)
        assert engine.dialect.driver == "psycopg2", engine.dialect.driver


check("postgres driver (psycopg2)", _postgres_driver)

# 2. 関数内 import にしているライブラリも含め、モジュールが読み込めるか
for mod in (
    "db.models", "db.session", "ocr.extract",
    "services.cards", "services.export", "services.imaging", "services.mail_log",
    "mail.templates", "mail.compose", "mail.gmail", "mail.ui",
    "anthropic", "pillow_heif",
    "googleapiclient.discovery", "google.oauth2.credentials", "google_auth_oauthlib.flow",
):
    check(f"import {mod}", lambda m=mod: importlib.import_module(m))

# 3. 各ページを Streamlit の中で実際に1回実行する。
#    APP_PASSWORD があるのでログイン画面で止まるが、ページ冒頭の import は全部通る。
def _run_page(path: str):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / path), default_timeout=60).run()
    if at.exception:
        raise RuntimeError(" / ".join(e.message for e in at.exception))


for page in ["main.py", *sorted(str(p) for p in Path("pages").glob("*.py"))]:
    check(f"page {page}", lambda p=page: _run_page(p))

if failures:
    print(f"\n{len(failures)} 件失敗: {', '.join(failures)}")
    sys.exit(1)
print("\nall ok")
