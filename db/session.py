import os

import config  # .env / st.secrets を環境変数へ読み込む（副作用）
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base

# 未設定ならローカルSQLite。クラウドでは Neon の postgresql URL を DATABASE_URL に設定する。
DATABASE_URL = os.getenv("DATABASE_URL") or "sqlite:///data/meishi.db"

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
