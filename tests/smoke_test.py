"""起動スモークテスト: python tests/smoke_test.py（リポジトリ直下で実行）

クラウドと同じ依存を入れた環境で「アプリが起動画面まで辿り着けるか」だけを見る。
2026-09-26 に SQLAlchemy 2.1 の既定ドライバ変更で本番が落ちた。こういう
「上流の更新で import / 接続準備の段階で落ちる」事故を、依存を上げる前に捕まえる。

外部には一切接続しない（DB は URL を解釈してドライバを読み込むところまで、
API キーはダミー）。GitHub Actions からも .env 無しでそのまま走る。
"""

from __future__ import annotations

import importlib
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
        for key in (["bbox", "quad"] + (["back_bbox", "back_quad"] if with_back else [])):
            assert key in item["properties"], f"スキーマに {key} が無い"
            assert key in item["required"], f"{key} が required に無い"
    src = (ROOT / "ocr" / "extract.py").read_text()
    for key in ("bbox", "quad", "back_bbox", "back_quad"):
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
