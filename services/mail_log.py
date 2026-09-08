"""お礼メールの下書きを作った記録（いつ・どの名刺に・どのパターンで）。

services/cards.py と同じ SessionLocal + try/finally の型を守る。
created_at は既存の慣習どおり UTC で保存し、表示用に JST を足して返す。
"""

from __future__ import annotations

from datetime import timedelta, timezone

from db.models import MailDraftLog
from db.session import SessionLocal
from mail import templates

JST = timezone(timedelta(hours=9))


def _decorate(d: dict) -> dict:
    """表示用の項目（JST日時・パターンの日本語ラベル）を足す。"""
    dt = d.get("created_at")
    # DBから返る naive datetime は UTC として保存したもの
    d["created_at_jst"] = (
        dt.replace(tzinfo=timezone.utc).astimezone(JST).strftime("%Y-%m-%d %H:%M")
        if dt else ""
    )
    d["pattern_label"] = templates.label_of(d.get("pattern", ""))
    return d


def add(card_id: int, pattern: str, subject: str, to_email: str, draft_id: str) -> None:
    """下書きを作った記録を1件足す。"""
    db = SessionLocal()
    try:
        db.add(MailDraftLog(
            card_id=card_id,
            pattern=pattern or "",
            subject=subject or "",
            to_email=to_email or "",
            draft_id=draft_id or "",
        ))
        db.commit()
    finally:
        db.close()


def list_for_card(card_id: int) -> list[dict]:
    """その名刺の履歴を新しい順で返す（二重作成の警告に使う）。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(MailDraftLog)
            .filter(MailDraftLog.card_id == card_id)
            .order_by(MailDraftLog.created_at.desc())
            .all()
        )
        return [_decorate(r.as_dict()) for r in rows]
    finally:
        db.close()


def recent(limit: int = 20) -> list[dict]:
    """全体の履歴を新しい順で返す（設定ページの一覧用）。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(MailDraftLog)
            .order_by(MailDraftLog.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_decorate(r.as_dict()) for r in rows]
    finally:
        db.close()


def delete_for_card(card_id: int) -> None:
    """名刺を消すときに履歴も掃除する（ForeignKey を張っていないため手で消す）。"""
    db = SessionLocal()
    try:
        db.query(MailDraftLog).filter(MailDraftLog.card_id == card_id).delete()
        db.commit()
    finally:
        db.close()


def count() -> int:
    db = SessionLocal()
    try:
        return db.query(MailDraftLog).count()
    finally:
        db.close()
