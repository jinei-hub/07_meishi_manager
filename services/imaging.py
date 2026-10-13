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

import numpy as np
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



def crop_quad(jpeg: bytes, quad, margin: float = 0.01, refine: bool = True,
              detail: dict | None = None) -> bytes | None:
    """名刺の四隅から、正面から見た長方形を起こす（台形補正）。使えなければ None。

    quad は ocr/extract.py が返す [{x,y} x4] で、名刺の文字が正立する向きでの
    左上→右上→右下→左下。斜めから撮っても真っ直ぐな「スキャンした」画像になり、
    横倒しに撮った名刺も正しい向きで保存される。

    bbox（軸平行の矩形）と違い、紙の外にある背景が原理的に入らない。
    margin は紙の縁が欠けないための余白で、重心から外へ広げる割合。

    refine=True のとき、読み取りの四隅は「当たり」としてだけ使い、実際の紙の縁を
    画像から探して四隅を引き直す（_snap_quad）。読み取りが返す四隅は数%ずれ、
    しかも4点バラバラにずれるので、そのまま使うと机が残ったり端が欠けたりする。
    縁が見つからなければ読み取りの四隅をそのまま使う（従来どおりの動き）。

    detail に dict を渡すと、縁に吸着できたかを "refined" に入れて返す。
    画面の診断表示用。どちらを通ったか分からないと切り出しの不具合を追えない。
    """
    points = _quad_points(jpeg, quad, margin)
    if points is None:
        return None
    img, corners = points

    # 出力サイズは四辺の実寸から決める（上下・左右それぞれ長い方を採る）
    width = round(max(_dist(corners[0], corners[1]), _dist(corners[3], corners[2])))
    height = round(max(_dist(corners[0], corners[3]), _dist(corners[1], corners[2])))
    if width < 8 or height < 8:
        return None

    if refine:
        better = _refined_corners(img, corners, width, height)
        if detail is not None:
            detail["refined"] = better is not None
        if better is not None:
            corners = better
            width = round(max(_dist(corners[0], corners[1]), _dist(corners[3], corners[2])))
            height = round(max(_dist(corners[0], corners[3]), _dist(corners[1], corners[2])))

    # 元画像より極端に大きくしても情報は増えないので抑える
    width = min(max(width, 8), img.width * 2)
    height = min(max(height, 8), img.height * 2)
    tl, tr, br, bl = corners
    # PIL の QUAD は「左上・左下・右下・右上」の順で元画像の四隅を渡す
    out = img.transform((width, height), Image.QUAD, (*tl, *bl, *br, *tr), Image.BICUBIC)
    return _encode(out)


# 紙の外側をどれだけ余分に見るか（名刺の寸法に対する割合、片側）。
# 読み取りが返す四隅のズレがこの範囲に収まっていれば縁を拾い直せる。
_REFINE_PAD = 0.08
# 縁を探すための下見用画像の最大幅。小さくしても縁の位置は充分に出るので、
# 本番の解像度で探さずに済ませて速くする。
_PROBE_WIDTH = 900


def _feasible_pad(size: tuple[int, int], corners, want: float = _REFINE_PAD) -> float:
    """画像からはみ出さずに広げられる割合を返す。余白を取れなければ 0。

    名刺が写真の端ギリギリに写っていると広げられない。その場合に無理をすると
    画像の外（黒）を紙の縁と誤認するので、素直に補正を諦める。
    """
    w, h = size
    cx = sum(x for x, _ in corners) / 4
    cy = sum(y for _, y in corners) / 4
    limit = 1.0 + 2 * want
    for x, y in corners:
        for delta, room in ((x - cx, w - cx), (cx - x, cx), (y - cy, h - cy), (cy - y, cy)):
            if delta > 0:
                limit = min(limit, room / delta)
    return max(0.0, min(want, (limit - 1.0) / 2))


def _bilinear(quad, u: float, v: float) -> tuple[float, float]:
    """PIL の QUAD と同じ対応で、起こした画像の相対座標を元画像の座標に戻す。

    quad は (左上, 右上, 右下, 左下)。u,v は 0〜1。
    """
    tl, tr, br, bl = quad
    return tuple((1 - u) * (1 - v) * a + u * (1 - v) * b + u * v * c + (1 - u) * v * d
                 for a, b, c, d in zip(tl, tr, br, bl))


def _refined_corners(img: Image.Image, corners, width: int, height: int):
    """紙の縁を画像から探して四隅を引き直す。信用できなければ None。

    手順は「広めに起こす → 四辺それぞれの縁を複数点でなぞる → 直線に当てはめる →
    交点を四隅にする」。読み取りのズレで残る傾きまで取れるのが、軸に平行な
    枠で詰めるだけの方法との違い。
    """
    pad = _feasible_pad(img.size, corners)
    if pad < 0.02:  # 余白が取れない＝紙の外が見えないので縁を探せない
        return None

    scale = 1.0 + 2 * pad
    cx = sum(x for x, _ in corners) / 4
    cy = sum(y for _, y in corners) / 4
    big = [(cx + (x - cx) * scale, cy + (y - cy) * scale) for x, y in corners]

    # 下見はこの解像度で充分（縁の位置は 1px 未満の精度まで要らない）
    pw = min(_PROBE_WIDTH, round(width * scale))
    ph = max(8, round(pw * (height * scale) / (width * scale)))
    if pw < 40 or ph < 40:
        return None
    btl, btr, bbr, bbl = big
    probe = img.transform((pw, ph), Image.QUAD, (*btl, *bbl, *bbr, *btr), Image.BICUBIC)

    uv = _snap_quad(probe, pad)
    if uv is None:
        return None
    found = [_bilinear(big, u, v) for u, v in uv]

    # 元の見積りから大きく外れたら、縁ではなく印刷などを拾っている
    for a, b in zip(found, corners):
        if _dist(a, b) > max(width, height) * 2.5 * pad:
            return None
    new_w = max(_dist(found[0], found[1]), _dist(found[3], found[2]))
    new_h = max(_dist(found[0], found[3]), _dist(found[1], found[2]))
    if not (0.80 * width <= new_w <= 1.25 * width and 0.80 * height <= new_h <= 1.25 * height):
        return None
    return found


def _snap_quad(probe: Image.Image, pad: float):
    """下見画像から紙の四隅を探し、0〜1 の相対座標で返す。無理なら None。

    紙と机の明るさの差を手がかりにする。差が無い（白い机に白い名刺など）場合は
    None を返し、呼び出し側が読み取りの四隅をそのまま使う。
    """
    gray = np.asarray(probe.convert("L"), dtype=np.float32)
    h, w = gray.shape
    edge = max(1, int(min(w, h) * 0.03))
    center = float(gray[int(h * 0.3):int(h * 0.7), int(w * 0.3):int(w * 0.7)].mean())
    border = float(np.concatenate([
        gray[:edge, :].ravel(), gray[-edge:, :].ravel(),
        gray[:, :edge].ravel(), gray[:, -edge:].ravel(),
    ]).mean())
    if abs(center - border) < 12.0:  # 縁が見えない
        return None
    level = (center + border) / 2
    bright_inside = center > border

    # 四辺それぞれ。left/right は x 方向に、top/bottom は y 方向に走査する。
    # 走査は必ず外側から始める（紙の内側の印刷より先に紙の縁に当たるように）。
    lines = {}
    for side in ("left", "right", "top", "bottom"):
        vertical = side in ("left", "right")
        along = h if vertical else w            # 縁に沿う方向の長さ
        across = w if vertical else h           # 縁を横切る方向の長さ
        band = max(4, int(across * 2 * pad / (1 + 2 * pad)))
        samples = []
        for t in np.linspace(0.18, 0.82, 25):   # 角の近くは外して真ん中だけ見る
            i = int(round(t * (along - 1)))
            strip = gray[i, :] if vertical else gray[:, i]
            hit = _cross(strip, band, level, bright_inside, from_start=side in ("left", "top"))
            if hit is not None:
                samples.append((i, hit))
        if len(samples) < 15:                   # 6割も縁を拾えないなら信用しない
            return None
        line = _fit_line(samples)
        if line is None:
            return None
        slope, intercept = line
        if abs(slope) > 0.25:                   # 14度以上傾いていたら拾い間違い
            return None
        lines[side] = (slope, intercept)

    # 「縁に沿う座標 → 横切る座標」の直線同士を交差させて四隅を出す
    corners = []
    for vert, horz in (("left", "top"), ("right", "top"), ("right", "bottom"), ("left", "bottom")):
        a, b = lines[vert]      # x = a*y + b
        c, d = lines[horz]      # y = c*x + d
        denom = 1 - a * c
        if abs(denom) < 1e-6:
            return None
        x = (a * d + b) / denom
        y = c * x + d
        if not (-w * 0.1 <= x <= w * 1.1 and -h * 0.1 <= y <= h * 1.1):
            return None
        corners.append((x / w, y / h))
    return corners


def _cross(strip: "np.ndarray", band: int, level: float, bright_inside: bool, from_start: bool):
    """1本の走査線で、外側から見て最初に明るさが紙の側へ切り替わる位置を返す。"""
    n = len(strip)
    band = min(band, n)
    seg = strip[:band] if from_start else strip[n - band:][::-1]
    inside = seg >= level if bright_inside else seg <= level
    # ノイズ1画素で決めない。3画素続けて紙側になった最初の点を縁とみなす。
    run = np.convolve(inside.astype(np.float32), np.ones(3, np.float32), mode="valid")
    idx = np.flatnonzero(run >= 3)
    if idx.size == 0:
        return None
    pos = int(idx[0])
    return float(pos) if from_start else float(n - 1 - pos)


def _fit_line(samples):
    """(沿う座標, 横切る座標) の点列に直線を当てはめる。外れ値は捨てる。"""
    a = np.asarray(samples, dtype=np.float64)
    x, y = a[:, 0], a[:, 1]
    for _ in range(2):
        if len(x) < 8:
            return None
        slope, intercept = np.polyfit(x, y, 1)
        resid = np.abs(y - (slope * x + intercept))
        keep = resid <= max(2.0, 3.0 * float(np.median(resid)))
        if keep.all():
            break
        x, y = x[keep], y[keep]
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


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
    # 小さすぎるものは誤検出とみなす。画像の短辺と比べる（長辺と比べると
    # 縦長の写真に横向きで置かれた名刺を誤って弾く）。
    side_w = max(_dist(tl, tr), _dist(bl, br))
    side_h = max(_dist(tl, bl), _dist(tr, br))
    short_edge = min(w, h)
    if side_w < short_edge * 0.08 or side_h < short_edge * 0.08:
        return None

    # 潰れた四角形・順序が狂って自己交差しているものを弾く。
    #
    # ここは以前「面積が軸平行の外接矩形の半分未満なら却下」としていたが誤りだった。
    # 外接矩形は傾けると必ず膨らむので、45度前後に置いた名刺は四隅が正しくても
    # 比が 0.47 まで落ち、必ず却下されていた（2026-10-12 に実測。35〜55度と
    # 125〜145度が全滅）。代わりに四辺の実寸から期待される面積と比べる。
    # これなら傾きで値が変わらず、本当に潰れた形だけを弾ける。
    area = abs(sum(pts[i][0] * pts[(i + 1) % 4][1] - pts[(i + 1) % 4][0] * pts[i][1]
                   for i in range(4))) / 2
    expected = ((_dist(tl, tr) + _dist(bl, br)) / 2) * ((_dist(tl, bl) + _dist(tr, br)) / 2)
    if expected <= 0 or area < expected * 0.5:
        return None

    # 凸四角形でなければ四隅の順序が狂っている（台形補正が破綻する）
    cross = []
    for i in range(4):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % 4]
        cx2, cy2 = pts[(i + 2) % 4]
        cross.append((bx - ax) * (cy2 - by) - (by - ay) * (cx2 - bx))
    if not (all(c > 0 for c in cross) or all(c < 0 for c in cross)):
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
