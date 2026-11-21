"""起動スモークテスト: python tests/smoke_test.py（リポジトリ直下で実行）

クラウドと同じ依存を入れた環境で「アプリが起動画面まで辿り着けるか」だけを見る。
2026-09-26 に SQLAlchemy 2.1 の既定ドライバ変更で本番が落ちた。こういう
「上流の更新で import / 接続準備の段階で落ちる」事故を、依存を上げる前に捕まえる。

外部には一切接続しない（DB は URL を解釈してドライバを読み込むところまで、
API キーはダミー）。GitHub Actions からも .env 無しでそのまま走る。
"""

from __future__ import annotations

import importlib
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# ローカルで走らせても本物の Neon / API を触らないよう、ダミーを先に入れる。
# config.py は load_dotenv(override=True) なので .env があると上書きされる。
# ただし下のチェックはどれも接続しないので、上書きされても安全。
os.environ.setdefault("APP_PASSWORD", "smoke-test")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-dummy")

failures: list[str] = []


def check(name: str, fn) -> None:
    try:
        fn()
        print(f"ok   {name}")
    except Exception as e:  # noqa: BLE001 — 何で落ちても一覧に載せたい
        failures.append(name)
        print(f"FAIL {name}: {type(e).__name__}: {e}")


# 1. Postgres(Neon) のドライバが解決できるか。今回の事故そのもの。
#    create_engine はドライバの import までやるが、接続はしない。
def _postgres_driver():
    from sqlalchemy import create_engine
    from db.session import _normalize_db_url

    for raw in (
        "postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
        "postgres://u:p@ep-x.neon.tech/db?sslmode=require",
        "psql 'postgresql://u:p@ep-x.neon.tech/db?sslmode=require'",
    ):
        engine = create_engine(_normalize_db_url(raw), pool_pre_ping=True)
        assert engine.dialect.driver == "psycopg2", engine.dialect.driver


check("postgres driver (psycopg2)", _postgres_driver)

# 2. 関数内 import にしているライブラリも含め、モジュールが読み込めるか
for mod in (
    "db.models", "db.session", "ocr.extract",
    "services.cards", "services.export", "services.imaging", "services.mail_log",
    "mail.templates", "mail.compose", "mail.gmail", "mail.ui",
    "theme", "pwa",
    "anthropic", "pillow_heif",
    "googleapiclient.discovery", "google.oauth2.credentials", "google_auth_oauthlib.flow",
):
    check(f"import {mod}", lambda m=mod: importlib.import_module(m))

# 2.5 抽出スキーマが structured outputs(json_schema) で通る形か。
#     minItems/maxItems などはサポート外で、書くとAPIにスキーマごと弾かれる。
#     ここが落ちると名刺を1枚も読めなくなるので、外部接続なしで形だけ確かめる。
UNSUPPORTED_SCHEMA_KEYS = {
    "minItems", "maxItems", "minLength", "maxLength",
    "minimum", "maximum", "pattern", "format", "default",
}


def _schema_keywords():
    from ocr.extract import EXTRACT_SCHEMA, _schema

    def walk(node, path="$"):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in UNSUPPORTED_SCHEMA_KEYS:
                    raise AssertionError(f"{path} に未対応キーワード {key}")
                if key == "additionalProperties" and value is not False:
                    raise AssertionError(f"{path} の additionalProperties は False 固定")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(EXTRACT_SCHEMA, "EXTRACT_SCHEMA")
    walk(_schema(with_back=True), "schema(裏面あり)")


check("抽出スキーマに未対応キーワードが無い", _schema_keywords)


# 2.6 抽出結果の正規化が bbox / quad を落とさないか。
#     ここで足し忘れると、エラーも出ないまま切り抜きだけ効かなくなる。
def _extract_keys():
    import ocr.extract as ex

    for with_back in (False, True):
        item = ex._schema(with_back=with_back)["properties"]["cards"]["items"]
        for key in (["bbox", "quad", "text_up"]
                    + (["back_bbox", "back_quad", "back_text_up"] if with_back else [])):
            assert key in item["properties"], f"スキーマに {key} が無い"
            assert key in item["required"], f"{key} が required に無い"
    src = (ROOT / "ocr" / "extract.py").read_text()
    for key in ("bbox", "quad", "text_up", "back_bbox", "back_quad", "back_text_up"):
        assert f'item["{key}"]' in src, f"返り値に {key} を詰めていない"


check("抽出結果に bbox / quad を詰めている", _extract_keys)


# 2.7 台形補正が「置いた角度」で落ちないか。
#     以前は面積を軸平行の外接矩形と比べていたため、45度前後に置いた名刺は
#     四隅が正しくても必ず却下され、黙って bbox 切り出しに落ちていた
#     （2026-10-12 に実測）。角度によらず通ること、かつ壊れた四隅は
#     今も弾けることの両方を見る。
def _quad_angles():
    import io
    import math

    from PIL import Image

    from services.imaging import _encode, _quad_points, crop_quad

    w, h, cw, ch = 2236, 1677, 900, 544
    blank = _encode(Image.new("RGB", (w, h), "black"))

    def quad_at(deg):
        a = math.radians(deg)
        corners = [(-cw / 2, -ch / 2), (cw / 2, -ch / 2), (cw / 2, ch / 2), (-cw / 2, ch / 2)]
        return [{"x": w / 2 + x * math.cos(a) - y * math.sin(a),
                 "y": h / 2 + x * math.sin(a) + y * math.cos(a)} for x, y in corners]

    for deg in range(0, 180, 5):
        out = crop_quad(blank, quad_at(deg))
        assert out is not None, f"{deg}度の名刺が却下された"
        size = Image.open(io.BytesIO(out)).size
        ratio = size[0] / size[1]
        assert 1.5 < ratio < 1.8, f"{deg}度で縦横比が戻らない（{ratio:.2f}）"

    broken = {
        "点が3つ": [{"x": 10, "y": 10}, {"x": 900, "y": 10}, {"x": 900, "y": 500}],
        "全部同じ点": [{"x": 500, "y": 500}] * 4,
        "一直線": [{"x": 100, "y": 100}, {"x": 900, "y": 100},
                   {"x": 1700, "y": 100}, {"x": 500, "y": 101}],
        "順序が交差": [{"x": 100, "y": 100}, {"x": 1000, "y": 600},
                       {"x": 1000, "y": 100}, {"x": 100, "y": 600}],
        "極小": [{"x": 10, "y": 10}, {"x": 40, "y": 10}, {"x": 40, "y": 30}, {"x": 10, "y": 30}],
    }
    for name, quad in broken.items():
        assert _quad_points(blank, quad, 0.01) is None, f"壊れた四隅を通した: {name}"


check("台形補正がどの角度でも効く", _quad_angles)


# 2.8 読み取りが返す四隅は数%ずれる。そのまま切ると机が残り、端が欠ける
#     （2026-10-12 に実機で発生）。紙の縁を画像から探して四隅を引き直す
#     （services.imaging の _refined_corners）のが効いているかを見る。
#     同時に「縁が見えない場面で無理をしない」ことも確かめる。悪化させるくらいなら
#     読み取りの四隅のまま出す方がよい。
def _quad_snap():
    import io
    import math
    import random

    from PIL import Image, ImageDraw

    from services.imaging import _encode, crop_quad

    w, h, cw, ch = 2236, 1677, 980, 594

    def photo(desk, paper):
        """机の上に名刺を17度傾けて置いた写真と、その正しい四隅を作る。"""
        img = Image.new("RGB", (w, h), desk)
        d0 = ImageDraw.Draw(img)
        for i in range(0, w, 53):  # 机の模様（縁と紛らわしくする）
            d0.line([(i, 0), (i + 40, h)], fill=tuple(max(0, c - 12) for c in desk), width=7)
        card = Image.new("RGB", (cw, ch), paper)
        d = ImageDraw.Draw(card)
        for y in range(40, ch - 40, 70):  # 印刷。縁より内側にある
            d.rectangle([60, y, 60 + 300, y + 24], fill=(128, 128, 132))
        deg = 17
        rot = card.rotate(deg, expand=True, resample=Image.BICUBIC)
        mask = Image.new("L", (cw, ch), 255).rotate(deg, expand=True, resample=Image.BICUBIC)
        img.paste(rot, ((w - rot.width) // 2, (h - rot.height) // 2), mask)
        a = math.radians(deg)
        quad = [(w / 2 + x * math.cos(a) + y * math.sin(a),
                 h / 2 - x * math.sin(a) + y * math.cos(a))
                for x, y in ((-cw / 2, -ch / 2), (cw / 2, -ch / 2),
                             (cw / 2, ch / 2), (-cw / 2, ch / 2))]
        return _encode(img), quad

    def error(jpeg, quad, refine):
        out = crop_quad(jpeg, quad, refine=refine)
        assert out is not None, "切り出しに失敗した"
        size = Image.open(io.BytesIO(out)).size
        return max(abs(size[0] - cw) / cw, abs(size[1] - ch) / ch) * 100

    rng = random.Random(11)
    jpeg, truth = photo((34, 32, 36), (221, 217, 211))
    # 正解の四隅がそもそも紙の上に乗っているか（ここが狂うと試験が無意味になる）。
    # crop_quad は既定で margin=0.01 だけ外へ広げるので、正解でも 1% は出る。
    assert error(jpeg, [{"x": x, "y": y} for x, y in truth], False) < 1.5, "正解の四隅が狂っている"

    raw, snapped = [], []
    for _ in range(6):
        off = [{"x": x + rng.uniform(-0.05, 0.05) * cw, "y": y + rng.uniform(-0.05, 0.05) * ch}
               for x, y in truth]
        raw.append(error(jpeg, off, False))
        snapped.append(error(jpeg, off, True))
    assert max(raw) > 2.0, f"試験がズレを作れていない（最大{max(raw):.1f}%）"
    assert max(snapped) < 1.5, f"縁に吸着できていない（最大{max(snapped):.1f}%）"

    # 白い机に白い名刺＝縁が見えない。ここで無理をすると大きく外す。
    pale, pale_truth = photo((236, 234, 230), (246, 245, 243))
    off = [{"x": x + 0.03 * cw, "y": y - 0.03 * ch} for x, y in pale_truth]
    before, after = error(pale, off, False), error(pale, off, True)
    assert after <= before + 0.5, f"縁が見えない場面で悪化した（{before:.1f}% → {after:.1f}%）"


check("切り出しが紙の縁に吸着する", _quad_snap)


# 2.9 実写で出た症状。木目の机・紙に落ちた影・読み取りの四隅が1割以上大きい。
#     以前は探す範囲が狭く、影で明るさの閾値も効かず、机を残したまま保存していた。
def _quad_shadow():
    import numpy as np
    from PIL import Image, ImageDraw

    from services.imaging import _encode, crop_quad

    w, h, x0, y0, cw, ch = 1600, 1200, 300, 300, 1000, 600

    def photo(stripe=False):
        base = np.zeros((h, w, 3), np.float32) + np.array((150, 100, 60), np.float32)
        base = base + (np.sin(np.arange(h) / 9.0) * 14)[:, None, None]
        base[y0:y0 + ch, x0:x0 + cw] = (238, 232, 218)
        if stripe:
            base[y0:y0 + ch, x0:x0 + 150] = (30, 50, 120)
        base[:, :x0 + 450] *= 0.6                # 紙の途中まで影が落ちている
        img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
        ImageDraw.Draw(img).rectangle((x0 + 500, y0 + 200, x0 + 900, y0 + 230), fill=(30, 30, 30))
        return _encode(img)

    quad = [{"x": x0 - 30, "y": y0 - 20}, {"x": x0 + cw + 130, "y": y0 - 15},
            {"x": x0 + cw + 120, "y": y0 + ch + 80}, {"x": x0 - 20, "y": y0 + ch + 75}]
    for stripe in (False, True):
        detail: dict = {}
        out = Image.open(io.BytesIO(crop_quad(photo(stripe), quad, detail=detail))).convert("RGB")
        assert detail.get("refined"), f"縁に吸着できなかった（stripe={stripe}）"
        assert abs(out.width - cw) <= cw * 0.015, f"幅が合わない: {out.width}（stripe={stripe}）"
        assert abs(out.height - ch) <= ch * 0.015, f"高さが合わない: {out.height}"
        if stripe:   # 紙の端の色帯を机とみなして切り落としていないか
            r, _g, b = out.getpixel((10, out.height // 2))
            assert b > r, "紙の端の色帯を切り落とした"


check("影と木目があっても机を残さない", _quad_shadow)


# 2.95 黒い机に複数枚を並べ、写真いっぱいに撮った場面。実写で机と隣の名刺が残った。
def _quad_neighbors():
    import numpy as np
    from PIL import Image

    from services.imaging import _encode, crop_quad

    w, h, cw, ch = 1400, 1000, 1000, 600
    rng = np.random.default_rng(0)
    base = np.clip(rng.normal(30, 18, (h, w, 1)), 0, 255).repeat(3, axis=2)
    paper = (235, 228, 205)
    x0, y0 = 60, 20                              # 目的の名刺は写真の左上の端に近い
    base[y0:y0 + ch, x0:x0 + cw] = paper
    base[y0:y0 + ch, x0 + cw + 40:] = paper      # 右隣の名刺
    base[y0 + ch + 40:, :] = paper               # 下の名刺
    jpeg = _encode(Image.fromarray(base.astype(np.uint8)))

    quad = [{"x": 10, "y": 2}, {"x": x0 + cw + 110, "y": 2},
            {"x": x0 + cw + 110, "y": y0 + ch + 90}, {"x": 10, "y": y0 + ch + 90}]
    detail: dict = {}
    out = Image.open(io.BytesIO(crop_quad(jpeg, quad, detail=detail)))
    assert detail.get("refined"), "縁に吸着できなかった"
    assert abs(out.width - cw) <= cw * 0.015, f"幅が合わない: {out.width}"
    assert abs(out.height - ch) <= ch * 0.015, f"高さが合わない: {out.height}"


check("隣の名刺と黒い机を残さない", _quad_neighbors)


# 2.10 四隅の順序が狂っていても text_up で正立する。実写で横向きのまま保存された。
def _text_up():
    from services.imaging import _orient_quad

    box = [{"x": 100, "y": 100}, {"x": 700, "y": 100},
           {"x": 700, "y": 460}, {"x": 100, "y": 460}]   # 写真上で時計回り
    first = {"top": (100, 100), "right": (700, 100),
             "bottom": (700, 460), "left": (100, 460)}
    for up, corner in first.items():
        for shift in range(4):
            given = box[shift:] + box[:shift]
            for quad in (given, given[::-1]):            # 回転ずれ・逆回りの両方
                got = _orient_quad(quad, up)
                assert (got[0]["x"], got[0]["y"]) == corner, f"{up}: 左上を取り違えた"
    assert _orient_quad(box, None) == box, "text_up が無ければそのまま返す"
    assert _orient_quad(box, "diagonal") == box, "不正な text_up でもそのまま返す"
    assert _orient_quad(None, "top") is None, "壊れた四隅でも落ちない"


check("四隅の順序を text_up で直す", _text_up)


# 2.11 横倒しの名刺が上下逆で保存される件（モデルは文字の向きの left/right を区別できず、
#      どちらも right と答える。2026-11-21 に実機で確認）。自動では直せないので画面の
#      「90°回転」ボタンで人が直す。その回転が素直に時計回り90°ずつ回ること、本番の
#      切り出しが縁の吸着(refine)を使わない（まっすぐなモデルの四隅を歪めて下の行を
#      切り落とすため。同日に実機で確認）ことを固定する。
def _manual_rotate():
    import numpy as np
    from PIL import Image
    from services.imaging import _encode, rotate_quarters

    w, h = 400, 240
    jpeg = _encode(Image.new("RGB", (w, h), "white"))
    for turns, size in [(0, (w, h)), (1, (h, w)), (2, (w, h)), (3, (h, w)), (4, (w, h))]:
        got = Image.open(io.BytesIO(rotate_quarters(jpeg, turns))).size
        assert got == size, f"{turns}回転でサイズが {got}（期待 {size}）"

    # 向きが実際に変わること。左半分を赤にして、2回転(180°)で赤が右へ、4回転で元に戻る
    # ことを見る（回転方向は運用で合わせるので、ここでは180°・一周だけ担保する）。
    half = Image.new("RGB", (w, h), "white")
    half.paste((220, 30, 30), (0, 0, w // 2, h))
    base = _encode(half)

    def left_is_red(img_bytes) -> bool:
        a = np.asarray(Image.open(io.BytesIO(img_bytes)).convert("RGB"), dtype=float)
        redness = a[..., 0] - (a[..., 1] + a[..., 2]) / 2   # 白は0、赤は大きい
        left = redness[:, : a.shape[1] // 2].mean()
        right = redness[:, a.shape[1] // 2:].mean()
        return left > right + 40

    assert left_is_red(base), "試験画像の左が赤でない"
    assert not left_is_red(rotate_quarters(base, 2)), "180°で赤が右へ移っていない"
    assert left_is_red(rotate_quarters(base, 4)), "4回転で元に戻っていない"

    # 本番の切り出しは refine を使わない（モデルの四隅をそのまま信用する）。
    src = (ROOT / "main.py").read_text()
    assert "refine=False" in src, "main.py の本番切り出しが refine=False になっていない"


check("手動90°回転と本番の refine=False", _manual_rotate)


# 2.12 紙の四隅から台形補正しても、モデルの四隅のズレ分の傾きが文字に残る
#      （2026-11-21 に実機で確認。ユーザー「まだ斜め、文字へ平行に」）。文字・色帯の
#      横線を基準にデスキューして水平へ合わせる。既知の傾きを注入して戻せること、
#      まっすぐな画像を回さないことの両方を見る。
def _deskew():
    from PIL import Image, ImageDraw
    import numpy as np
    from services.imaging import _skew_angle, deskew_text, _encode

    card = Image.new("RGB", (640, 400), "white")
    draw = ImageDraw.Draw(card)
    for y in range(60, 360, 42):                 # 文字行に見立てた横線
        draw.rectangle([60, y, 580, y + 16], fill=(20, 20, 20))

    def detect(img: "Image.Image") -> float:
        pw = 460
        s = img.convert("L").resize((pw, round(img.height * pw / img.width)))
        return _skew_angle(np.asarray(s, dtype=np.float32), 10.0)

    assert abs(detect(card)) < 0.3, "まっすぐな画像を回そうとした"
    for inj in (-4.0, -2.0, 3.0, 5.0):
        tilted = card.rotate(inj, resample=Image.BICUBIC, expand=True,
                             fillcolor=(255, 255, 255))
        got = detect(tilted)                     # 補正角は注入の逆向き
        assert abs(got + inj) <= 0.6, f"{inj}度の傾きを戻せない（検出 {got}）"

    tilted = card.rotate(5.0, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))
    out = deskew_text(_encode(tilted))
    assert abs(detect(Image.open(io.BytesIO(out)))) < 0.6, "deskew 後も傾きが残る"


check("文字を水平に整列（deskew）", _deskew)


# 3. 各ページを Streamlit の中で実際に1回実行する。
#    APP_PASSWORD があるのでログイン画面で止まるが、ページ冒頭の import は全部通る。
def _run_page(path: str):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / path), default_timeout=60).run()
    if at.exception:
        raise RuntimeError(" / ".join(e.message for e in at.exception))


for page in ["main.py", *sorted(str(p) for p in Path("pages").glob("*.py"))]:
    check(f"page {page}", lambda p=page: _run_page(p))

if failures:
    print(f"\n{len(failures)} 件失敗: {', '.join(failures)}")
    sys.exit(1)
print("\nall ok")
