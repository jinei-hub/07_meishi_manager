"""アップロード/撮影画像を JPEG bytes に正規化する（HEIC対応込み）。"""

from __future__ import annotations

import io

from PIL import Image

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass  # HEIC非対応環境でもjpg/pngは動く


def to_jpeg_bytes(raw: bytes, max_side: int = 2000) -> bytes:
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

    # APIコスト・処理時間のため長辺を制限
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))

    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()
