from sqlalchemy import Column, String, Integer, Text, DateTime, LargeBinary
from sqlalchemy.orm import declarative_base, deferred, column_property
from datetime import datetime

Base = declarative_base()

# 抽出・表示・エクスポートで共通に使う項目キー（表示順）
FIELDS = [
    ("name", "氏名"),
    ("company", "会社名"),
    ("department", "部署"),
    ("title", "役職"),
    ("phone", "電話"),
    ("fax", "FAX"),
    ("mobile", "携帯"),
    ("email", "メール"),
    ("website", "URL"),
    ("postal_code", "郵便番号"),
    ("address", "住所"),
]


class MeishiCard(Base):
    """1枚の名刺"""
    __tablename__ = "meishi_cards"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    # 名刺画像(JPEG bytes)をDBに保存（クラウドでも消えない）。
    # deferred: 既定では SELECT に含めない。一覧/検索は全件を一度に引くため、
    # 通常の列にすると全名刺のJPEGがDBから転送され、無料枠の時間とメモリを食う。
    # 本体が要るのは詳細表示だけで、そこは cards.get_image(id) が明示的に読む。
    image       = deferred(Column(LargeBinary))

    name        = Column(String, default="")
    company     = Column(String, default="")
    department  = Column(String, default="")
    title       = Column(String, default="")
    phone       = Column(String, default="")
    fax         = Column(String, default="")
    mobile      = Column(String, default="")
    email       = Column(String, default="")
    website     = Column(String, default="")
    postal_code = Column(String, default="")
    address     = Column(String, default="")

    memo        = Column(Text, default="")
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def as_dict(self) -> dict:
        # 画像(blob)は含めない。画像は cards.get_image(id) で取得する。
        # has_image は下の column_property がSQL側で判定した真偽値で、
        # self.image に触らない（触ると deferred が1件ずつ読み込みに行く）。
        d = {"id": self.id, "has_image": bool(self.has_image), "memo": self.memo or ""}
        for key, _label in FIELDS:
            d[key] = getattr(self, key) or ""
        d["created_at"] = self.created_at
        return d


# 「画像があるか」はDB側で判定させる。image 本体を転送せずに真偽値だけ受け取るため、
# deferred を解除せずに済む。クラス定義後に足すのは image 列を参照する必要があるから。
MeishiCard.has_image = column_property(MeishiCard.__table__.c.image.isnot(None))


class MailDraftLog(Base):
    """お礼メールの下書きを作った記録（いつ・どの名刺に・どのパターンで）。

    新テーブルなので init_db() の create_all() でそのまま作られる
    （既存テーブルへの列追加と違い、手動 ALTER が要らない）。
    meishi_cards への ForeignKey は張らない: 既存テーブルに手を入れずに済ませたいのと、
    ondelete の挙動が SQLite(既定OFF) と Postgres で揃わないため。
    代わりに services/cards.py の delete() から delete_for_card() を呼んで掃除する。
    """
    __tablename__ = "mail_draft_logs"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    card_id    = Column(Integer, index=True)
    pattern    = Column(String, default="")   # "contract" / "thanks" / "next_meeting"
    subject    = Column(String, default="")
    to_email   = Column(String, default="")
    draft_id   = Column(String, default="")   # Gmail の下書きID
    created_at = Column(DateTime, default=datetime.utcnow)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "card_id": self.card_id,
            "pattern": self.pattern or "",
            "subject": self.subject or "",
            "to_email": self.to_email or "",
            "draft_id": self.draft_id or "",
            "created_at": self.created_at,
        }
