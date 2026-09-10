import os

import config  # .env / st.secrets を環境変数へ読み込む（副作用）
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
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


_initialized = False


def init_db():
    """テーブルが存在しない場合のみ作成。

    Postgres(Neon) では、複数のセッションが同時に create_all を実行すると
    「存在確認 → CREATE TABLE」の隙間で競合し、pg_type の一意制約違反
    （IntegrityError）になることがある。Streamlit はセッションごとに
    スクリプトを並行実行するため、タブを複数開くと普通に起きる。

    競合した側は「相手が作り終えた」だけなので握りつぶしてよい。ただし
    本物の失敗を隠さないよう、テーブルが実際に揃ったかを確認してから通す。
    """
    global _initialized
    if _initialized:
        return
    if DATABASE_URL.startswith("sqlite"):
        os.makedirs("data", exist_ok=True)
    try:
        Base.metadata.create_all(bind=engine)
    except (IntegrityError, ProgrammingError, OperationalError):
        missing = set(Base.metadata.tables) - set(inspect(engine).get_table_names())
        if missing:
            raise   # 競合ではなく本当に作れていない
    _initialized = True


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
