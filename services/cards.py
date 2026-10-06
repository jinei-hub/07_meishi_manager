"""名刺の保存・一覧・検索・更新・削除（CRUD）。画像はDBにBLOBで保存。"""

from __future__ import annotations

from sqlalchemy import and_, or_

from db.models import MeishiCard, FIELDS
from db.session import SessionLocal

# 検索対象（横断LIKE検索）
SEARCHABLE = ["name", "company", "department", "title",
              "phone", "fax", "mobile", "email", "website",
              "postal_code", "address", "memo"]


def create(fields: dict, image_bytes: bytes | None = None) -> int:
    """名刺を1件登録し、id を返す。fields は FIELDS のキー + memo。"""
    db = SessionLocal()
    try:
        card = MeishiCard(image=image_bytes)
        for key, _label in FIELDS:
            setattr(card, key, (fields.get(key) or "").strip())
        card.memo = (fields.get("memo") or "").strip()
        db.add(card)
        db.commit()
        db.refresh(card)
        return card.id
    finally:
        db.close()


def list_all() -> list[dict]:
    """全件を新しい順に返す。"""
    db = SessionLocal()
    try:
        rows = db.query(MeishiCard).order_by(MeishiCard.created_at.desc()).all()
        return [r.as_dict() for r in rows]
    finally:
        db.close()


def search(query: str = "", filters: dict | None = None) -> list[dict]:
    """横断検索と項目別検索を組み合わせて返す。両方空なら全件。

    query   … SEARCHABLE 全体への OR 検索（キーワード1本で広く拾う）
    filters … {列名: 文字列} の項目別検索。指定した項目は AND で効く
              （会社名が「A」かつ役職が「部長」のような絞り込み）

    列名は SEARCHABLE に無いものを無視する。画面から来た文字列を
    getattr に通すため、ここで弾かないと任意の属性を触れてしまう。
    """
    q = (query or "").strip()
    conds = []
    if q:
        like = f"%{q}%"
        conds.append(or_(*[getattr(MeishiCard, c).ilike(like) for c in SEARCHABLE]))
    for col, raw in (filters or {}).items():
        value = (raw or "").strip()
        if not value or col not in SEARCHABLE:
            continue
        conds.append(getattr(MeishiCard, col).ilike(f"%{value}%"))

    if not conds:
        return list_all()

    db = SessionLocal()
    try:
        rows = (
            db.query(MeishiCard)
            .filter(and_(*conds))
            .order_by(MeishiCard.created_at.desc())
            .all()
        )
        return [r.as_dict() for r in rows]
    finally:
        db.close()


def get(card_id: int) -> dict | None:
    db = SessionLocal()
    try:
        row = db.get(MeishiCard, card_id)
        return row.as_dict() if row else None
    finally:
        db.close()


def get_image(card_id: int) -> bytes | None:
    """カードの画像(JPEG bytes)を返す。無ければ None。"""
    db = SessionLocal()
    try:
        row = db.get(MeishiCard, card_id)
        return bytes(row.image) if row and row.image else None
    finally:
        db.close()


def update(card_id: int, fields: dict) -> None:
    """既存カードの項目を更新。"""
    db = SessionLocal()
    try:
        card = db.get(MeishiCard, card_id)
        if not card:
            return
        for key, _label in FIELDS:
            if key in fields:
                setattr(card, key, (fields.get(key) or "").strip())
        if "memo" in fields:
            card.memo = (fields.get("memo") or "").strip()
        db.commit()
    finally:
        db.close()


def delete(card_id: int) -> None:
    """カードを削除（画像もDBから消える）。お礼メールの履歴も一緒に消す。"""
    from services import mail_log  # 循環 import 回避のため関数内で読む

    mail_log.delete_for_card(card_id)
    db = SessionLocal()
    try:
        card = db.get(MeishiCard, card_id)
        if card:
            db.delete(card)
            db.commit()
    finally:
        db.close()


def count() -> int:
    db = SessionLocal()
    try:
        return db.query(MeishiCard).count()
    finally:
        db.close()
