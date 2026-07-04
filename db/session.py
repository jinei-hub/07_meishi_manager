import os

import config  # .env / st.secrets を環境変数へ読み込む（副作用）
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base

def _normalize_db_url(raw: str | None) -> str:
    """Secretsの貼り付けミスを吸収する。
    - 前後の空白・引用符を除去
    - Neonが表示する `psql '...'` 形式のコマンドラッパーを剥がす
    - SQLAlchemy 2.0 が受け付けない `postgres://` を `postgresql://` に補正
    """
    if not raw:
        return "sqlite:///data/meishi.db"
    url = raw.strip().strip("'").strip('"').strip()
    if url.lower().startswith("psql "):
        url = url[len("psql "):].strip().strip("'").strip('"').strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url or "sqlite:///data/meishi.db"


# 未設定ならローカルSQLite。クラウドでは Neon の postgresql URL を DATABASE_URL に設定する。
DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL"))

# SQLite 固有の接続引数は Postgres では使わない
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Neon 等はアイドルで切断されるため pre_ping で自動再接続
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """テーブルが存在しない場合のみ作成"""
    if DATABASE_URL.startswith("sqlite"):
        os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
