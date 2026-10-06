"""アップロード/撮影画像を JPEG bytes に正規化する（HEIC対応込み）。

■ なぜ「Claude が見るサイズ」ちょうどに縮めるのか
  Claude は大きい画像を内部で縮小してから見る。縮小されると、返ってくる
  座標（名刺の位置）は縮小後の画像基準になり、こちらの画像とズレる。
  あらかじめ同じサイズにしておけば座標をそのまま使える。
  計算式は公式ドキュメントの reference implementation をそのまま実装したもの
  （2026-09-13 に確認）。
    - 辺の上限: 高解像度ティア 2576px / 標準ティア 1568px
    - 視覚トークン上限: ceil(w/28) * ceil(h/28) が 4784 / 1568 以下
  写真は普通トークン上限の方が先に効く（4:3 なら約 2236x1677 になる）。
"""

from __future__ import annotations

import io
import math

from PIL import Image

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass  # HEIC非対応環境でもjpg/pngは動く


# 高解像度ティア（Claude 4.7 以降）/ 標準ティア（それ以前）
HIGH_RES = (2576, 4784)
STANDARD = (1568, 1568)
_HIGH_RES_KEYS = ("opus-4-7", "opus-4-8", "opus-5", "sonnet-5", "fable-5", "mythos-5")


def tier_limits(model: str | None) -> tuple[int, int]:
    """モデル名から (辺の上限, 視覚トークン上限) を返す。"""
    name = (model or "").lower()
    return HIGH_RES if any(k in name for k in _HIGH_RES_KEYS) else STANDARD


def count_image_tokens(width: int, height: int) -> int:
    """28x28 のパッチ1枚が1トークン。"""
    return math.ceil(width / 28) * math.ceil(height / 28)


def resized_size(width: int, height: int, max_edge: int, max_tokens: int) -> tuple[int, int]:
    """Claude が縮小した後のサイズ（公式の reference implementation）。"""

    def fits(w: int, h: int) -> bool:
        return (
            math.ceil(w / 28) * 28 <= max_edge
            and math.ceil(h / 28) * 28 <= max_edge
            and count_image_tokens(w, h) <= max_tokens
        )

    if fits(width, height):
        return (width, height)
    if height > width:
        rh, rw = resized_size(height, width, max_edge, max_tokens)
        return (rw, rh)

    aspect = width / height
    lo, hi = 1, width  # lo は必ず収まる / hi は必ず収まらない
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if fits(mid, max(round(mid / aspect), 1)):
            lo = mid
        else:
            hi = mid
    return (lo, max(round(lo / aspect), 1))


def probe_size(raw: bytes) -> tuple[int, int]:
    """元画像の (幅, 高さ)。撮影品質を画面に出すために使う。"""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            return img.size
    except Exception:
        return (0, 0)


def _open_upright(raw: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw))
    try:
        from PIL import ImageOps

        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def _encode(img: Image.Image) -> bytes:
    out = io.BytesIO()
    # subsampling=0 (4:4:4) で色のにじみを抑える。文字の読み取りに効く。
    # 強い圧縮は文字を潰すと公式ドキュメントも注意しているので quality は高め。
    img.convert("RGB").save(out, format="JPEG", quality=92, subsampling=0)
    return out.getvalue()


def to_jpeg_bytes(raw: bytes, model: str | None = None) -> bytes:
    """EXIF回転補正のうえ、Claude が見るサイズちょうどに縮めた JPEG を返す。"""
    img = _open_upright(raw)
    max_edge, max_tokens = tier_limits(model)
    target = resized_size(img.size[0], img.size[1], max_edge, max_tokens)
    if target != img.size:
        # LANCZOS: 既定の縮小より文字のエッジが残る
        img = img.resize(target, Image.LANCZOS)
    return _encode(img)


def crop_bbox(jpeg: bytes, bbox: dict, margin: float = 0.04) -> bytes | None:
    """名刺1枚分を切り出す。bbox が使えなければ None。

    bbox は to_jpeg_bytes が返した画像のピクセル座標 {x1,y1,x2,y2}。
    margin は切り出しの余白（枠ぎりぎりだと端が欠けるため）。
    """
    if not isinstance(bbox, dict):
        return None
    try:
        x1, y1, x2, y2 = (int(bbox[k]) for k in ("x1", "y1", "x2", "y2"))
    except (KeyError, TypeError, ValueError):
        return None

    img = Image.open(io.BytesIO(jpeg))
    w, h = img.size
    x1, x2 = sorted((max(0, x1), min(w, x2)))
    y1, y2 = sorted((max(0, y1), min(h, y2)))
    bw, bh = x2 - x1, y2 - y1
    # 小さすぎる/画像全体に近いものは信用しない（誤検出で変な切り出しをしない）
    if bw < w * 0.08 or bh < h * 0.08:
        return None

    mx, my = int(bw * margin), int(bh * margin)
    box = (max(0, x1 - mx), max(0, y1 - my), min(w, x2 + mx), min(h, y2 + my))
    return _encode(img.crop(box))



def crop_quad(jpeg: bytes, quad, margin: float = 0.01) -> bytes | None:
    """名刺の四隅から、正面から見た長方形を起こす（台形補正）。使えなければ None。

    quad は ocr/extract.py が返す [{x,y} x4] で、名刺の文字が正立する向きでの
    左上→右上→右下→左下。斜めから撮っても真っ直ぐな「スキャンした」画像になり、
    横倒しに撮った名刺も正しい向きで保存される。

    bbox（軸平行の矩形）と違い、紙の外にある背景が原理的に入らない。
    margin は紙の縁が欠けないための余白で、重心から外へ広げる割合。
    """
    points = _quad_points(jpeg, quad, margin)
    if points is None:
        return None
    img, (tl, tr, br, bl) = points

    # 出力サイズは四辺の実寸から決める（上下・左右それぞれ長い方を採る）
    width = round(max(_dist(tl, tr), _dist(bl, br)))
    height = round(max(_dist(tl, bl), _dist(tr, br)))
    if width < 8 or height < 8:
        return None
    # 元画像より極端に大きくしても情報は増えないので抑える
    width = min(width, img.width * 2)
    height = min(height, img.height * 2)

    # PIL の QUAD は「左上・左下・右下・右上」の順で元画像の四隅を渡す
    data = (*tl, *bl, *br, *tr)
    out = img.transform((width, height), Image.QUAD, data, Image.BICUBIC)
    return _encode(out)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _quad_points(jpeg: bytes, quad, margin: float):
    """quad を検証して (画像, 四隅) にする。信用できなければ None。

    読み取り結果をそのまま座標に使うので、形・範囲・大きさを必ず確かめる。
    潰れた四角形や画像全体に近いものは誤検出として捨てる（bbox と同じ方針）。
    """
    if not isinstance(quad, (list, tuple)) or len(quad) != 4:
        return None
    try:
        pts = [(float(p["x"]), float(p["y"])) for p in quad]
    except (KeyError, TypeError, ValueError):
        return None

    img = Image.open(io.BytesIO(jpeg))
    w, h = img.size

    # 重心から外へ広げて余白を付ける（紙の縁が欠けるのを防ぐ）
    cx = sum(x for x, _ in pts) / 4
    cy = sum(y for _, y in pts) / 4
    scale = 1.0 + max(0.0, margin)
    pts = [(cx + (x - cx) * scale, cy + (y - cy) * scale) for x, y in pts]
    # はみ出しは画像内に収める（四隅が画像外と推定されることがある）
    pts = [(min(max(x, 0.0), w), min(max(y, 0.0), h)) for x, y in pts]

    tl, tr, br, bl = pts
    # 小さすぎるものは誤検出とみなす
    side_w = max(_dist(tl, tr), _dist(bl, br))
    side_h = max(_dist(tl, bl), _dist(tr, br))
    if side_w < w * 0.08 or side_h < h * 0.08:
        return None
    # 潰れている・順序がおかしいものを弾く（四角形の面積が外接矩形の半分未満）
    area = abs(sum(pts[i][0] * pts[(i + 1) % 4][1] - pts[(i + 1) % 4][0] * pts[i][1]
                   for i in range(4))) / 2
    bw = max(x for x, _ in pts) - min(x for x, _ in pts)
    bh = max(y for _, y in pts) - min(y for _, y in pts)
    if bw <= 0 or bh <= 0 or area < bw * bh * 0.5:
        return None
    return img, (tl, tr, br, bl)


def stack_vertical(top: bytes, bottom: bytes, gap: int = 24) -> bytes:
    """表面の上に裏面を縦に並べた1枚の JPEG を作る。

    DB の画像列は1つしかない（Alembic 未導入で列を足せない）ため、
    表と裏を1枚にまとめて保存する。スマホは縦スクロールなので縦並びにする。
    拡大すると文字がぼやけるので、幅は狭い方に揃える。
    """
    a = Image.open(io.BytesIO(top)).convert("RGB")
    b = Image.open(io.BytesIO(bottom)).convert("RGB")
    width = min(a.width, b.width)

    def fit(img: Image.Image) -> Image.Image:
        if img.width == width:
            return img
        return img.resize((width, max(1, round(img.height * width / img.width))),
                          Image.LANCZOS)

    a, b = fit(a), fit(b)
    canvas = Image.new("RGB", (width, a.height + gap + b.height), "white")
    canvas.paste(a, (0, 0))
    canvas.paste(b, (0, a.height + gap))
    return _encode(canvas)


def thumbnail_bytes(jpeg: bytes, width: int = 160, height: int | None = None) -> bytes:
    """一覧に並べる小さな JPEG を作る。

    height を渡すと、その大きさの枠に収めた画像を返す（はみ出させず、余りは白地）。
    一覧では名刺ごとに縦横比が違うため、枠を揃えないと行の高さがバラバラになる。

    保存してある画像は Claude に読ませる解像度（長辺2000px超）のままなので、
    一覧に何十枚も並べると表示も転送も重い。幅を揃えて軽くする。
    一覧で文字を読む必要はないので quality は低めでよい。
    """
    img = Image.open(io.BytesIO(jpeg))
    # JPEG は draft() を使うと「間引きながらデコード」できる（1/2・1/4・1/8）。
    # 等倍でデコードしてから縮小すると、Streamlit Cloud 無料枠の弱いCPU
    # （最小 0.078 コア）では1枚に数百ms〜秒かかり、一覧が開けなくなる
    # （2026-10-07 に本番で発生）。先に小さく読むと桁で速くなる。
    # draft は load 前にしか効かず、JPEG以外では黙って無視される。
    img.draft("RGB", (width, height or width))
    if img.mode != "RGB":
        img = img.convert("RGB")

    if height is None:
        if img.width > width:
            img = img.resize((width, max(1, round(img.height * width / img.width))),
                             Image.LANCZOS)
        canvas = img
    else:
        # 枠に収まるまで縮めて、中央に置く（縦長の名刺も横長の名刺も同じ枠に収まる）
        ratio = min(width / img.width, height / img.height)
        if ratio < 1:
            img = img.resize((max(1, round(img.width * ratio)),
                              max(1, round(img.height * ratio))), Image.LANCZOS)
        canvas = Image.new("RGB", (width, height), "white")
        canvas.paste(img, ((width - img.width) // 2, (height - img.height) // 2))

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=75)
    return out.getvalue()
