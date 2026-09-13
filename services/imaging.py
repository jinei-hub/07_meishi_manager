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
