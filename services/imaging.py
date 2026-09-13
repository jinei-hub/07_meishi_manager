"""アップロード/撮影画像を JPEG bytes に正規化する（HEIC対応込み）。"""

from __future__ import annotations

import io

from PIL import Image

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass  # HEIC非対応環境でもjpg/pngは動く


# Claude 4.7 以降は「高解像度ティア」で、長辺 2576px / 視覚トークン 4784 まで
# そのまま扱える（これを超えると API 側で縮小される）。
# 2026-09-13 に公式ドキュメントで確認。名刺の小さな文字を読ませたいので、
# 捨てずに上限いっぱいまで送る。
MAX_SIDE = 2576


def probe_size(raw: bytes) -> tuple[int, int]:
    """元画像の (幅, 高さ)。撮影品質を画面に出すために使う。"""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            return img.size
    except Exception:
        return (0, 0)


def to_jpeg_bytes(raw: bytes, max_side: int = MAX_SIDE) -> bytes:
    """任意の画像bytesをEXIF回転補正・長辺縮小の上でJPEG bytesに変換。"""
    img = Image.open(io.BytesIO(raw))

    # EXIFの向きを反映
    try:
        from PIL import ImageOps

        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # モデルが扱える上限まで落とす（それ以上送っても API 側で縮小されるだけ）
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        # LANCZOS: 既定の縮小より文字のエッジが残る
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    out = io.BytesIO()
    # subsampling=0 (4:4:4) で色のにじみを抑える。文字の読み取りに効く。
    # 強い圧縮は文字を潰すと公式ドキュメントも注意しているので quality は高めに。
    img.convert("RGB").save(out, format="JPEG", quality=92, subsampling=0)
    return out.getvalue()
