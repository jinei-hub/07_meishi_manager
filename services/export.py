"""連絡先を CSV / vCard(.vcf) 形式に書き出す。"""

from __future__ import annotations

import io

import pandas as pd

from db.models import FIELDS


def to_csv_bytes(cards: list[dict]) -> bytes:
    """カード一覧を CSV(bytes) に。ExcelでのUTF-8文字化けを防ぐためBOM付き。"""
    columns = [key for key, _label in FIELDS] + ["memo"]
    labels = {key: label for key, label in FIELDS}
    labels["memo"] = "メモ"
    rows = [{labels[c]: (card.get(c) or "") for c in columns} for card in cards]
    df = pd.DataFrame(rows, columns=[labels[c] for c in columns])
    return df.to_csv(index=False).encode("utf-8-sig")


def _escape(value: str) -> str:
    """vCard の値をエスケープ（RFC 6350）。"""
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _card_to_vcard(card: dict) -> str:
    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    name = _escape(card.get("name"))
    lines.append(f"FN:{name}")
    lines.append(f"N:{name};;;;")

    org_parts = [p for p in [card.get("company"), card.get("department")] if p]
    if org_parts:
        lines.append("ORG:" + ";".join(_escape(p) for p in org_parts))
    if card.get("title"):
        lines.append(f"TITLE:{_escape(card.get('title'))}")
    if card.get("phone"):
        lines.append(f"TEL;TYPE=WORK,VOICE:{_escape(card.get('phone'))}")
    if card.get("mobile"):
        lines.append(f"TEL;TYPE=CELL:{_escape(card.get('mobile'))}")
    if card.get("fax"):
        lines.append(f"TEL;TYPE=WORK,FAX:{_escape(card.get('fax'))}")
    if card.get("email"):
        lines.append(f"EMAIL;TYPE=WORK:{_escape(card.get('email'))}")
    if card.get("website"):
        lines.append(f"URL:{_escape(card.get('website'))}")
    if card.get("address") or card.get("postal_code"):
        # ADR: PO;拡張;番地;市区町村;都道府県;郵便番号;国
        adr = f";;{_escape(card.get('address'))};;;{_escape(card.get('postal_code'))};"
        lines.append(f"ADR;TYPE=WORK:{adr}")
    lines.append("END:VCARD")
    return "\r\n".join(lines)


def to_vcard_bytes(cards: list[dict]) -> bytes:
    """カード一覧を vCard(bytes) に。"""
    buf = io.StringIO()
    for card in cards:
        buf.write(_card_to_vcard(card))
        buf.write("\r\n")
    return buf.getvalue().encode("utf-8")
