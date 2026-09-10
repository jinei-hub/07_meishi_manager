"""テンプレートで作った下書きに、自由記述の指示だけを反映させる。

ocr/extract.py の呼び出し・例外ハンドリングを踏襲。
ゼロから書かせるのではなく「最小限の編集」に限定し、値の捏造を禁止する。
自由記述が空のときは呼ばないこと（UI 側で分岐する）。
"""

from __future__ import annotations

import json
import os

from ocr.extract import DEFAULT_MODEL

SYSTEM_PROMPT = (
    "あなたは日本語のビジネスメールを編集する担当者です。"
    "すでに完成している下書きに、依頼者（送信者本人）の指示だけを反映させます。"
    "ゼロから書き直してはいけません。\n"
    "\n"
    "厳守事項:\n"
    "1. 変更は指示に関係する箇所だけに限定する。"
    "指示と無関係な文・語順・改行は一字も変えない。\n"
    "2. 日付・時刻・金額・期間・人数・場所・固有名詞・約束事は、"
    "指示または相手情報に明記されたものだけを書く。推測や一般論で補ってはいけない。\n"
    "3. 指示に無い新しい提案・お礼・謝罪・次のアクションを足さない。\n"
    "4. 相手情報（会社名・氏名など）は宛名の整合性を保つためだけに使う。"
    "本文に新しい事実として持ち込まない。\n"
    "5. 署名・連絡先・会社情報を新たに書き足さない。"
    "下書きに署名が無いのは意図的で、送信時に Gmail 側が付けるため。"
    "下書きに署名がある場合は一字一句そのまま残す。\n"
    "6. 敬語は「です・ます」＋謙譲語を保つ。絵文字や記号装飾を足さない。\n"
    "7. 指示が下書きの記述と矛盾する場合は指示を優先し、矛盾する側を書き換える。\n"
    "8. 指示の意味が判然としない場合は、下書きを変更せずそのまま返す。"
    "勝手な解釈で書き足してはいけない。\n"
    "9. 件名は指示で明示的に求められた場合のみ変更する。それ以外は元のまま返す。\n"
    "\n"
    "出力は subject と body の2項目のみ。"
    "body は署名まで含めた本文全文を、改行をそのまま保って返すこと。"
)

REFINE_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
            "description": "件名。指示で明示的に求められた場合のみ変更し、それ以外は元のまま返す",
        },
        "body": {
            "type": "string",
            "description": "本文全文。署名まで含め、改行をそのまま保って返す",
        },
    },
    "required": ["subject", "body"],
    "additionalProperties": False,
}


class ComposeError(Exception):
    """文面調整の失敗（UI へ分かりやすく伝える用）。"""


def refine(subject: str, body: str, instruction: str, card: dict,
           pattern_label: str = "", model: str | None = None) -> tuple[str, str]:
    """下書きに自由記述の指示を反映させ、(件名, 本文) を返す。

    Raises:
        ComposeError: APIキー未設定・認証失敗・通信エラー・応答パース失敗など。
    """
    if not (instruction or "").strip():
        # 呼び出し側のバグ。API を無駄打ちせずそのまま返す。
        return subject, body

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ComposeError(
            "ANTHROPIC_API_KEY が未設定です。.env に API キーを設定してください。"
            "（自由記述を空にすればテンプレートのまま作成できます）"
        )

    try:
        import anthropic
    except ImportError as e:
        raise ComposeError(
            "anthropic パッケージが未インストールです。"
            "`pip install -r requirements.txt` を実行してください。"
        ) from e

    model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
    client = anthropic.Anthropic()

    user_text = (
        "# 相手（名刺の情報。宛名の整合性確認用）\n"
        f"会社: {card.get('company', '')}\n"
        f"部署: {card.get('department', '')}\n"
        f"役職: {card.get('title', '')}\n"
        f"氏名: {card.get('name', '')}\n\n"
        f"# パターン\n{pattern_label}\n\n"
        "# 下書き（この文面を最小限だけ直す）\n"
        f"件名: {subject}\n"
        "----- 本文ここから -----\n"
        f"{body}\n"
        "----- 本文ここまで -----\n\n"
        "# 依頼者からの指示（この内容だけを反映する）\n"
        f"{instruction.strip()}"
    )

    try:
        response = client.messages.create(
            model=model,
            # 署名込みの本文全文を返させるため extract.py(2000) より多めに取る
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium", "format": {
                "type": "json_schema",
                "schema": REFINE_SCHEMA,
            }},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.AuthenticationError as e:
        raise ComposeError("APIキーが無効です（認証エラー）。.env の ANTHROPIC_API_KEY を確認してください。") from e
    except anthropic.RateLimitError as e:
        raise ComposeError("レート制限に達しました。しばらく待って再試行してください。") from e
    except anthropic.APIConnectionError as e:
        raise ComposeError("Anthropic API への接続に失敗しました。ネットワークを確認してください。") from e
    except anthropic.APIStatusError as e:
        raise ComposeError(f"API エラー（{e.status_code}）: {e.message}") from e

    if response.stop_reason == "refusal":
        raise ComposeError("モデルが生成を拒否しました。指示の内容を見直してください。")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise ComposeError("モデルから有効な応答が得られませんでした。")

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise ComposeError("調整結果の解析に失敗しました（不正なJSON）。") from e

    # 空で返ってきたら元を残す（本文が消える事故を防ぐ）
    new_subject = (result.get("subject") or "").strip() or subject
    new_body = result.get("body") or body
    return new_subject, new_body
