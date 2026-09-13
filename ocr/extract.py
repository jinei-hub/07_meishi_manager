"""
名刺画像を Claude Vision に渡し、氏名・会社・役職・連絡先・住所を
構造化JSON(structured outputs)で確実に抽出する。

02_sns_analyser/analysis/report.py の呼び出し・例外ハンドリングを踏襲。
読み取れない項目は空文字 "" を返させ、値の捏造は禁止する。
"""

from __future__ import annotations

import base64
import json
import os

DEFAULT_MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = (
    "あなたは日本語で対応する名刺OCR・情報抽出の専門家です。"
    "1枚の画像を渡されます。画像には名刺が1枚以上写っている可能性があります。"
    "写っている名刺を1枚ずつ検出し、それぞれを cards 配列の要素として、"
    "指定スキーマの各項目に振り分けてください。名刺がN枚あれば配列の長さもNにすること。"
    "各名刺で、画像から読み取れない・判読できない項目は必ず空文字 \"\" にすること。"
    "推測や補完で値を捏造してはいけません。"
    "電話・FAX・携帯は区別が付く場合のみ振り分け、不明なものは phone に入れること。"
    "郵便番号は数字とハイフンのみ（例: 123-4567）にし、それ以外の住所は address に入れること。"
    "名刺が1枚も無い場合は cards を空配列にすること。"
    "さらに各名刺について、その名刺が画像のどこにあるかを bbox に入れること。"
    "bbox は渡された画像の**ピクセル座標**で、左上が原点(0,0)、"
    "x は右へ、y は下へ増える。x1,y1 が左上の角、x2,y2 が右下の角。"
    "名刺の紙の外周にぴったり合わせ、他の名刺や背景を含めないこと。"
    "名刺が1枚だけの場合もその1枚の外周を返すこと。"
)

_CARD_PROPS = {
    "name":        {"type": "string", "description": "氏名（フルネーム）"},
    "company":     {"type": "string", "description": "会社名・組織名"},
    "department":  {"type": "string", "description": "部署名"},
    "title":       {"type": "string", "description": "役職・肩書き"},
    "phone":       {"type": "string", "description": "固定電話番号"},
    "fax":         {"type": "string", "description": "FAX番号"},
    "mobile":      {"type": "string", "description": "携帯電話番号"},
    "email":       {"type": "string", "description": "メールアドレス"},
    "website":     {"type": "string", "description": "ウェブサイトURL"},
    "postal_code": {"type": "string", "description": "郵便番号（123-4567形式）"},
    "address":     {"type": "string", "description": "住所"},
}
_CARD_KEYS = list(_CARD_PROPS.keys())

# 名刺の位置。複数枚を1枚の写真で撮ったとき、1枚ずつ切り出して保存するために使う。
# 公式ドキュメントの指示どおり「正規化」ではなく絶対ピクセル座標で返させる
# （正規化座標はうまく動かないと明記されている。2026-09-13 に確認）。
_BBOX_PROP = {
    "type": "object",
    "description": "この名刺の外接矩形（渡された画像のピクセル座標）",
    "properties": {
        "x1": {"type": "integer", "description": "左上の x"},
        "y1": {"type": "integer", "description": "左上の y"},
        "x2": {"type": "integer", "description": "右下の x"},
        "y2": {"type": "integer", "description": "右下の y"},
    },
    "required": ["x1", "y1", "x2", "y2"],
    "additionalProperties": False,
}

# 構造化出力スキーマ（additionalProperties:false 必須）: 複数名刺に対応
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "description": "検出した名刺（画像内の枚数分）",
            "items": {
                "type": "object",
                "properties": {**_CARD_PROPS, "bbox": _BBOX_PROP},
                "required": _CARD_KEYS + ["bbox"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["cards"],
    "additionalProperties": False,
}


def current_model() -> str:
    """実際に使うモデル名。画像サイズの上限を合わせるために外から参照する。"""
    return os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)


# 表と裏を1回で読むときの指示。複数画像は短いラベルで区切ると取り違えにくい
# （公式ドキュメントの推奨。2026-09-13 に確認）。
BACK_INSTRUCTION = (
    "画像1と画像2は同じ名刺の表面と裏面です。両面の情報を1人分にまとめ、"
    "cards には1件だけ返してください。"
    "表面にしかない項目・裏面にしかない項目の両方を埋めること。"
    "同じ項目が両面にあって表記が違う場合（日本語と英語など）は日本語表記を採用すること。"
    "bbox は画像1（表面）での名刺の位置、back_bbox は画像2（裏面）での名刺の位置を、"
    "それぞれの画像自身のピクセル座標で返すこと。"
)


def _schema(with_back: bool) -> dict:
    """裏面ありのときだけ back_bbox を足したスキーマを返す。"""
    if not with_back:
        return EXTRACT_SCHEMA
    cards = EXTRACT_SCHEMA["properties"]["cards"]
    item = cards["items"]
    return {
        **EXTRACT_SCHEMA,
        "properties": {"cards": {**cards, "items": {
            **item,
            "properties": {**item["properties"], "back_bbox": _BBOX_PROP},
            "required": item["required"] + ["back_bbox"],
        }}},
    }


class ExtractError(Exception):
    """抽出の失敗（UI へ分かりやすく伝える用）。"""


def extract_cards(image_bytes: bytes, media_type: str = "image/jpeg",
                  model: str | None = None,
                  back_image_bytes: bytes | None = None) -> list[dict]:
    """
    画像(bytes)を Claude Vision に渡し、写っている名刺を1枚以上抽出してリストで返す。

    Returns:
        [{name, company, department, title, phone, fax, mobile,
          email, website, postal_code, address, bbox}, ...]  （検出した枚数分）
        bbox は {x1,y1,x2,y2} のピクセル座標（取得できなければ None）
        back_image_bytes を渡すと表裏を1人分に統合し、back_bbox（裏面での位置）も返す
    Raises:
        ExtractError: APIキー未設定・認証失敗・通信エラー・応答パース失敗など。
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ExtractError(
            "ANTHROPIC_API_KEY が未設定です。.env に API キーを設定してください。"
        )

    try:
        import anthropic
    except ImportError as e:
        raise ExtractError(
            "anthropic パッケージが未インストールです。"
            "`pip install -r requirements.txt` を実行してください。"
        ) from e

    model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
    client = anthropic.Anthropic()

    def _image(data: bytes) -> dict:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type,
                       "data": base64.standard_b64encode(data).decode("ascii")},
        }

    if back_image_bytes:
        # 表と裏を1回のリクエストで送る（呼び出し回数を増やさない）
        content = [
            {"type": "text", "text": "画像1: 名刺の表面"},
            _image(image_bytes),
            {"type": "text", "text": "画像2: 同じ名刺の裏面"},
            _image(back_image_bytes),
            {"type": "text", "text": BACK_INSTRUCTION},
        ]
    else:
        content = [
            _image(image_bytes),
            {
                "type": "text",
                "text": "この画像に写っている名刺をすべて検出し、1枚ずつ cards 配列に振り分けてください。",
            },
        ]

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium", "format": {
                "type": "json_schema",
                "schema": _schema(with_back=bool(back_image_bytes)),
            }},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError as e:
        raise ExtractError("APIキーが無効です（認証エラー）。.env の ANTHROPIC_API_KEY を確認してください。") from e
    except anthropic.RateLimitError as e:
        raise ExtractError("レート制限に達しました。しばらく待って再試行してください。") from e
    except anthropic.APIConnectionError as e:
        raise ExtractError("Anthropic API への接続に失敗しました。ネットワークを確認してください。") from e
    except anthropic.APIStatusError as e:
        raise ExtractError(f"API エラー（{e.status_code}）: {e.message}") from e

    if response.stop_reason == "refusal":
        raise ExtractError("モデルが生成を拒否しました。画像内容を見直してください。")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise ExtractError("モデルから有効な応答が得られませんでした。")

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractError("抽出結果の解析に失敗しました（不正なJSON）。") from e

    cards = result.get("cards", [])
    # 各カードを既定キーで正規化（欠損は空文字）。bbox は辞書のまま持つ。
    out = []
    for c in cards:
        item = {k: (c.get(k) or "") for k in _CARD_KEYS}
        item["bbox"] = c.get("bbox") if isinstance(c.get("bbox"), dict) else None
        back = c.get("back_bbox")
        item["back_bbox"] = back if isinstance(back, dict) else None
        out.append(item)
    return out
