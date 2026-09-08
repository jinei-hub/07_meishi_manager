"""お礼メールの文面テンプレート（3パターン）。

ここは Claude を通さない純粋な文字列組み立て。自由記述による微調整は mail/compose.py が担当する。
文面は実際に送ったメールを基準にしている。

パターンの追加・文言の修正はこのファイルだけを直せばよい（UI は PATTERNS を読んで組み立てる）。
"""

from __future__ import annotations

import os

# ══════════════════════════════════════════════════════════════════
# ここから下の文字列を直せば文面が変わる（直接編集してよい箇所）
# ══════════════════════════════════════════════════════════════════

# 署名は環境変数 MAIL_SIGNATURE で差し替える。
# 住所・電話番号をリポジトリに置かないための措置（このリポジトリは公開されている）。
# 値の中の \n は改行として扱うので、.env / Secrets には1行で書ける:
#   MAIL_SIGNATURE="--\n\n株式会社DiPilot 奥河 鎮映\n\nTEL：...\nEmail：..."
# 未設定なら下の既定値（公開情報のみ）を使う。
DEFAULT_SIGNATURE = """--

_______________________________

株式会社DiPilot 奥河 鎮映 / Jinei Okugawa

Email：jinei@dipilot.jp
Web：https://dipilot.jp/
_______________________________"""


def signature() -> str:
    """メール末尾の署名。MAIL_SIGNATURE があればそれを使う。"""
    raw = (os.getenv("MAIL_SIGNATURE") or "").strip()
    if not raw:
        return DEFAULT_SIGNATURE
    # .env / TOML どちらで書いてもリテラルの \n を改行にする
    return raw.replace("\\n", "\n")

OPENING = """お世話になっております。
株式会社DiPilotでございます。

本日はご多忙の中、お時間をいただき誠にありがとうございました。"""

CLOSING = "今後とも何卒よろしくお願い申し上げます。"

# 宛名に氏名が無いときの代替
FALLBACK_ADDRESSEE = "ご担当者様"

# ══════════════════════════════════════════════════════════════════


def build_greeting(card: dict) -> str:
    """宛名ブロックを組み立てる。

        株式会社アイエステクノサービス
        代表取締役 今井 直樹 様

    氏名が無いときは部署・役職も出さない（「代表取締役 様」は不自然なため）。
    """
    company = (card.get("company") or "").strip()
    name = (card.get("name") or "").strip()
    dept = (card.get("department") or "").strip()
    title = (card.get("title") or "").strip()

    lines = []
    if company:
        lines.append(company)
    if name:
        # 名刺に「様」は入らないが、手入力で足された場合の二重敬称を防ぐ
        honor = name if name.endswith("様") else f"{name} 様"
        prefix = " ".join(p for p in (dept, title) if p)
        lines.append(f"{prefix} {honor}".strip())
    else:
        lines.append(FALLBACK_ADDRESSEE)
    return "\n".join(lines)


def _honorific(card: dict) -> str:
    """本文中で相手を指すときの呼び方。氏名が無ければ「貴社」。"""
    name = (card.get("name") or "").strip()
    if not name:
        return "貴社"
    return name if name.endswith("様") else f"{name}様"


def _assemble(card: dict, blocks: list[str]) -> str:
    """宛名 + 出だし + 本体ブロック + 締め + 署名。空ブロックは落とす。"""
    parts = [build_greeting(card), OPENING, *[b for b in blocks if b], CLOSING, signature()]
    return "\n\n".join(parts)


# ── パターンA: 契約のお礼 ────────────────────────────────────────
def _build_contract(card: dict, inputs: dict, has_attachment: bool) -> str:
    contract_name = (inputs.get("contract_name") or "").strip()
    sign_method = (inputs.get("sign_method") or "").strip()
    start_date = (inputs.get("start_date") or "").strip()
    first_day = (inputs.get("first_day") or "").strip()

    if contract_name:
        thanks = (
            f"また、貴社の{contract_name}として弊社をお選びいただき、"
            "ご契約を賜りましたこと、心より御礼申し上げます。"
        )
    else:
        thanks = "また、この度はご契約を賜りましたこと、心より御礼申し上げます。"
    thanks += (
        f"\n{_honorific(card)}のご期待に沿えるよう、"
        "AI技術を活用した事業推進・課題解決に向けて全力を尽くしてまいります。"
    )

    # 添付の有無で文言が変わるため、添付は文面生成より前に確定させる必要がある
    attach_line = (
        "契約書につきましては、本メールに添付いたしましたのでご確認ください。"
        if has_attachment
        else "契約書につきましては、追ってご送付させていただきます。"
    )
    procedure = "【ご契約手続きについて】\n" + attach_line
    if sign_method:
        procedure += f"\nつきましては、ご契約の締結方法は{sign_method}で問題ないでしょうか？"

    blocks = [thanks, procedure]
    if start_date:
        blocks.append(f"【今後のスケジュール】\n・参画開始日：{start_date}")
    if first_day:
        blocks.append(f"【初日の進め方について（ご提案）】\n{first_day}")
    blocks.append(
        "スムーズな参画に向け、しっかりと準備を進めてまいります。\n"
        "事前のご確認事項やご不明な点がございましたら、お気軽にお申し付けください。"
    )
    return _assemble(card, blocks)


# ── パターンB: 本日のお礼だけ ────────────────────────────────────
def _build_thanks(card: dict, inputs: dict, has_attachment: bool) -> str:
    return _assemble(card, [
        "引き続き、貴社のお役に立てるよう努めてまいります。\n"
        "ご不明な点やご確認事項がございましたら、お気軽にお申し付けください。"
    ])


# ── パターンC: 次回日程の提案 ────────────────────────────────────
def _build_next_meeting(card: dict, inputs: dict, has_attachment: bool) -> str:
    date1 = (inputs.get("date1") or "").strip()
    date2 = (inputs.get("date2") or "").strip()
    purpose = (inputs.get("purpose") or "").strip()

    lines = ["【次回のお打ち合わせについて】"]
    if date1 or date2:
        lines.append("下記の日程でご都合はいかがでしょうか。")
        if date1:
            lines.append(f"・第1希望：{date1}")
        if date2:
            lines.append(f"・第2希望：{date2}")
        lines.append(
            "\nご都合が合わない場合は、ご希望の日時をいくつかお知らせいただけますと幸いです。"
        )
    else:
        # 候補日が未入力でも文として成立させる（空欄の箇条書きを出さない）
        lines.append("ご都合のよろしい日時をいくつかお知らせいただけますと幸いです。")
    schedule = "\n".join(lines)

    blocks = [schedule]
    if purpose:
        blocks.append(f"次回は{purpose}について、お時間をいただければと考えております。")
    return _assemble(card, blocks)


# ── パターン定義 ────────────────────────────────────────────────
# inputs: UI が st.text_input / st.text_area を組み立てるための定義
PATTERNS = [
    {
        "id": "contract",
        "label": "A. 契約のお礼（添付あり）",
        "hint": "契約書を添付して送るとき",
        "subject": "本日のお礼と今後の進め方について【株式会社DiPilot・奥河鎮映】",
        "inputs": [
            {"key": "contract_name", "label": "ご契約の内容",
             "default": "AIパートナー（FDE）", "placeholder": "AIパートナー（FDE）"},
            {"key": "sign_method", "label": "契約の締結方法",
             "default": "クラウドサイン（電子契約）", "placeholder": "クラウドサイン（電子契約）"},
            {"key": "start_date", "label": "参画開始日（空欄なら記載しない）",
             "default": "", "placeholder": "10月19日（月）10:00〜13:00"},
            {"key": "first_day", "label": "初日の進め方（空欄なら記載しない）",
             "default": "", "multiline": True,
             "placeholder": "まずはご担当者様とお打ち合わせをさせていただき、"
                            "AIの基礎知識の共有、業務内容のキャッチアップと"
                            "理解を深めるお時間にできればと考えております。"},
        ],
        "build": _build_contract,
    },
    {
        "id": "thanks",
        "label": "B. 本日のお礼だけ（シンプル）",
        "hint": "打ち合わせ後の定型のお礼",
        "subject": "本日のお礼【株式会社DiPilot・奥河鎮映】",
        "inputs": [],
        "build": _build_thanks,
    },
    {
        "id": "next_meeting",
        "label": "C. 次回日程の提案",
        "hint": "「次はこの日で進められたら」を伝えるとき",
        "subject": "次回のお打ち合わせ日程のご相談【株式会社DiPilot・奥河鎮映】",
        "inputs": [
            {"key": "date1", "label": "第1希望", "default": "",
             "placeholder": "10月19日（月）10:00〜"},
            {"key": "date2", "label": "第2希望（任意）", "default": "",
             "placeholder": "10月21日（水）14:00〜"},
            {"key": "purpose", "label": "次回の目的（任意）", "default": "",
             "placeholder": "AI導入対象業務のヒアリング"},
        ],
        "build": _build_next_meeting,
    },
]

DEFAULT_PATTERN_ID = PATTERNS[0]["id"]


def get(pattern_id: str) -> dict:
    """PATTERNS から1件返す。"""
    for p in PATTERNS:
        if p["id"] == pattern_id:
            return p
    raise ValueError(f"未知のパターンです: {pattern_id}")


def label_of(pattern_id: str) -> str:
    """履歴表示用。未知のIDはそのまま返す（古い記録が壊れないように）。"""
    for p in PATTERNS:
        if p["id"] == pattern_id:
            return p["label"]
    return pattern_id or ""


def render(pattern_id: str, card: dict, inputs: dict,
           has_attachment: bool = False) -> tuple[str, str]:
    """(件名, 本文) を返す。Claude は通さない純粋なテンプレ展開。"""
    pattern = get(pattern_id)
    body = pattern["build"](card, inputs or {}, has_attachment)
    return pattern["subject"], body
