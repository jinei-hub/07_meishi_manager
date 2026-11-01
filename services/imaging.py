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
from PIL import Image, ImageFilter

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



def _up_vector(text_up) -> tuple[float, float] | None:
    return {"top": (0.0, -1.0), "right": (1.0, 0.0),
            "bottom": (0.0, 1.0), "left": (-1.0, 0.0)}.get(text_up)


def _orient_quad(quad, text_up):
    """四隅の順序を、text_up（文字の上が写真のどちらか）に合わせて並べ直す。

    読み取りは四隅の位置は返せても、横倒しの名刺で「どの角が左上か」を取り違える
    ことがある（2026-10-23 に実機で発生。横向きのまま保存された）。向きは別の項目で
    答えさせ、順序はこちらで決める。text_up が使えなければ quad をそのまま返す。
    """
    up = _up_vector(text_up)
    if up is None or not isinstance(quad, (list, tuple)) or len(quad) != 4:
        return quad
    try:
        pts = [(float(p["x"]), float(p["y"])) for p in quad]
    except (KeyError, TypeError, ValueError):
        return quad

    area = sum(pts[i][0] * pts[(i + 1) % 4][1] - pts[(i + 1) % 4][0] * pts[i][1]
               for i in range(4))
    order = list(quad)
    if area < 0:                    # 反時計回りなら時計回りに直す（鏡像にしない）
        order = [order[0], order[3], order[2], order[1]]
        pts = [pts[0], pts[3], pts[2], pts[1]]

    def agreement(start: int) -> float:
        """start を左上としたときの「上辺の外向き」と up の一致度（1が完全一致）。"""
        (ax, ay), (bx, by) = pts[start], pts[(start + 1) % 4]
        length = _dist((ax, ay), (bx, by)) or 1.0
        return ((by - ay) * up[0] - (bx - ax) * up[1]) / length

    # 45度に置いた名刺は text_up が2方向のどちらとも言えるので、読み取りの順序が
    # text_up と矛盾しない（ずれが約53度以内）ならそちらを優先する。
    best = 0 if agreement(0) >= 0.6 else max(range(4), key=agreement)
    return order[best:] + order[:best]


def rotate_upright(jpeg: bytes, text_up) -> bytes:
    """文字の上が写真の text_up 側にある画像を正立させる（bbox 切り出し用）。"""
    angle = {"right": 90, "bottom": 180, "left": 270}.get(text_up)
    if angle is None:
        return jpeg
    img = Image.open(io.BytesIO(jpeg))
    return _encode(img.rotate(angle, expand=True))


def crop_quad(jpeg: bytes, quad, margin: float = 0.01, refine: bool = True,
              detail: dict | None = None, text_up: str | None = None) -> bytes | None:
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

    text_up は文字の上が写真のどちら側か。渡すと四隅の順序をこれに合わせて直す。
    """
    points = _quad_points(jpeg, _orient_quad(quad, text_up), margin)
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
_REFINE_PAD = 0.10
# 四隅より内側をどれだけ探すか。読み取りの四隅は紙より1割以上大きいことがある
# （2026-10-23 に実機で下に約1割の机が残った）。狭いと縁が範囲外になり、吸着を
# 諦めて机ごと保存してしまう。
_REFINE_INNER = 0.20
# 縁を探すための下見用画像の最大幅。小さくしても縁の位置は充分に出るので、
# 本番の解像度で探さずに済ませて速くする。
_PROBE_WIDTH = 640


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
    pad = _REFINE_PAD
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

    # 名刺が写真の端に寄っていると、広げた範囲は写真の外に出る。そこは紙ではない
    # 印を付けて探す。諦めると、写真いっぱいに撮った名刺で机や隣の名刺が残る。
    shot = Image.new("L", img.size, 255).transform(
        (pw, ph), Image.QUAD, (*btl, *bbl, *bbr, *btr), Image.NEAREST)
    uv = _snap_quad(probe, pad, np.asarray(shot) > 0)
    if uv is None:
        return None
    found = [_bilinear(big, u, v) for u, v in uv]

    # 元の見積りから大きく外れたら、縁ではなく印刷などを拾っている
    for a, b in zip(found, corners):
        if _dist(a, b) > max(width, height) * 2.5 * pad:
            return None
    new_w = max(_dist(found[0], found[1]), _dist(found[3], found[2]))
    new_h = max(_dist(found[0], found[3]), _dist(found[1], found[2]))
    if not (0.65 * width <= new_w <= 1.25 * width and 0.65 * height <= new_h <= 1.25 * height):
        return None
    return found


def _otsu(values: "np.ndarray") -> float | None:
    """値を2つの山に分ける閾値（大津の方法）。ほぼ一色で分けられなければ None。"""
    lo, hi = float(values.min()), float(values.max())
    if hi - lo < 1e-6:
        return None
    counts, edges = np.histogram(values, bins=64, range=(lo, hi))
    weight = np.cumsum(counts, dtype=np.float64)
    centers = (edges[:-1] + edges[1:]) / 2
    mass = np.cumsum(counts * centers, dtype=np.float64)
    below, above = weight[:-1], weight[-1] - weight[:-1]
    usable = (below > 0) & (above > 0)
    if not usable.any():
        return None
    between = np.zeros_like(below)
    between[usable] = (below[usable] * above[usable]
                       * (mass[:-1][usable] / below[usable]
                          - (mass[-1] - mass[:-1][usable]) / above[usable]) ** 2)
    return float(edges[1:][int(between.argmax())])


def _paper_by_color(rgb: "np.ndarray", shot: "np.ndarray"):
    """色味で紙を見分ける。机と色味が同じなら None。

    影は明るさを大きく変えても色味はあまり変えない。明るさの閾値だと、影の中の紙を
    机と取り違える（2026-10-23 に実機で発生）。r/b の比率で比べれば影に引きずられない。
    外周をそのまま平均すると、隣の名刺が写っているときに紙の色を机と誤認するので
    （2026-11-01 に実機で発生）、外周を2色に分けて紙から遠い方を机とする。
    """
    total = rgb.sum(axis=2) + 30.0   # 暗い画素で比率が暴れないよう底上げする
    r, b = rgb[..., 0] / total, rgb[..., 2] / total
    h, w = r.shape
    mid = (slice(int(h * 0.3), int(h * 0.7)), slice(int(w * 0.3), int(w * 0.7)))
    # 暗い画素の色味はノイズで、黒い机を紙と誤認する（2026-11-01 に実機で発生）。
    # 影の中の紙は明るさ6割ほどなので、それより十分暗いものだけ紙の候補から外す。
    # ただし色がはっきりしていれば残す（影の中にある紺の帯などを切り落とさない）。
    gray = rgb.mean(axis=2)
    bright = (gray > float(np.median(gray[mid])) * 0.35) | (rgb.max(axis=2) - rgb.min(axis=2) > 40)
    # 机の色は外周の帯だけから決める。全体から決めると、名刺の中の濃い色帯を机と誤認する。
    edge = max(2, int(min(h, w) * 0.07))
    outer = np.ones((h, w), dtype=bool)
    outer[edge:-edge, edge:-edge] = False
    usable = outer & shot & bright
    if usable.sum() < outer.sum() * 0.02:
        return None
    from_paper = np.hypot(r - float(np.median(r[mid])), b - float(np.median(b[mid])))
    cut = _otsu(from_paper[usable])
    if cut is None:
        return None
    desk = usable & (from_paper > cut)
    if desk.sum() < outer.sum() * 0.02:
        return None
    # 「紙の色に近い」ではなく「机の色から遠い」で判定する。逆にすると、端に色帯の
    # ある名刺で帯を机とみなして切り落としてしまう。
    desk_r, desk_b = float(np.median(r[desk])), float(np.median(b[desk]))
    gap = float(np.hypot(float(np.median(r[mid])) - desk_r,
                         float(np.median(b[mid])) - desk_b))
    if gap < 0.03:
        return None
    return bright & (np.hypot(r - desk_r, b - desk_b) > gap / 2)


def _paper_by_brightness(rgb: "np.ndarray", shot: "np.ndarray"):
    """明るさで紙を見分ける。白い机に白い名刺など、差が無ければ None。"""
    gray = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    h, w = gray.shape
    center = float(gray[int(h * 0.3):int(h * 0.7), int(w * 0.3):int(w * 0.7)].mean())
    level = _otsu(gray[shot])
    if level is None:
        return None
    other = gray[shot & (gray < level)] if center > level else gray[shot & (gray >= level)]
    if other.size < gray.size * 0.02 or abs(center - float(other.mean())) < 12.0:  # 縁が見えない
        return None
    return gray >= level if center > level else gray < level


def _card_region(paper: "np.ndarray") -> "np.ndarray":
    """中央の紙とつながった領域だけを残す。隣に写った名刺はここで消える。

    1枚の写真に複数枚を並べると、外から走査したときに隣の名刺へ先に当たる。
    行ごとに numpy で伝播させるので、下見画像なら1画素ずつ辿るより桁で速い。
    """
    # 色帯と紙の境目には1〜2画素の判定漏れが出て、帯が中央から切り離される。
    # 隣の名刺との隙間より十分細い途切れだけ埋める。
    mask = Image.fromarray(paper.astype(np.uint8) * 255)
    paper = np.asarray(mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))) > 0
    h, w = paper.shape
    reach = np.zeros_like(paper)
    box = (slice(int(h * 0.4), int(h * 0.6)), slice(int(w * 0.4), int(w * 0.6)))
    reach[box] = paper[box]

    def spread(y: int, near: "np.ndarray") -> None:
        row = paper[y]
        ids = np.cumsum(~row)            # 紙が連続する区間は同じ番号になる
        touched = np.zeros(int(ids[-1]) + 1, dtype=bool)
        touched[ids[row & (near | reach[y])]] = True
        reach[y] = row & touched[ids]

    for _ in range(8):                   # 回り込む形でも数往復で収束する
        before = int(reach.sum())
        for y in range(1, h):
            spread(y, reach[y - 1])
        for y in range(h - 2, -1, -1):
            spread(y, reach[y + 1])
        if int(reach.sum()) == before:
            break
    return reach


def _snap_quad(probe: Image.Image, pad: float, shot: "np.ndarray"):
    """下見画像から紙の四隅を探し、0〜1 の相対座標で返す。無理なら None。

    shot は写真の内側なら True のマスク。まず色味で探し、机と色味が同じ（黒い机に
    白い名刺など）なら明るさで探す。どちらでも見つからなければ None を返し、
    呼び出し側が読み取りの四隅をそのまま使う。
    """
    rgb = np.asarray(probe.convert("RGB").filter(ImageFilter.GaussianBlur(1.5)),
                     dtype=np.float32)
    for find_paper in (_paper_by_color, _paper_by_brightness):
        paper = find_paper(rgb, shot)
        if paper is None:
            continue
        corners = _trace_edges(_card_region(paper & shot), pad)
        if corners is not None:
            return corners
    return None


def _trace_edges(paper: "np.ndarray", pad: float):
    """紙の画素マスクから四辺をなぞり、四隅を返す。なぞれなければ None。"""
    h, w = paper.shape
    # 四辺それぞれ。left/right は x 方向に、top/bottom は y 方向に走査する。
    # 走査は必ず外側から始める（紙の内側の印刷より先に紙の縁に当たるように）。
    lines = {}
    for side in ("left", "right", "top", "bottom"):
        vertical = side in ("left", "right")
        along = h if vertical else w            # 縁に沿う方向の長さ
        across = w if vertical else h           # 縁を横切る方向の長さ
        band = max(4, int(across * (pad + _REFINE_INNER) / (1 + 2 * pad)))
        samples = []
        for t in np.linspace(0.18, 0.82, 25):   # 角の近くは外して真ん中だけ見る
            i = int(round(t * (along - 1)))
            strip = paper[i, :] if vertical else paper[:, i]
            hit = _cross(strip, band, from_start=side in ("left", "top"))
            if hit is not None:
                samples.append((i, hit))
        if len(samples) < 15:                   # 6割も縁を拾えないなら信用しない
            return None
        # 走査の開始点からもう紙なら、紙の外を見ていない（隣の名刺とくっついている等）
        start = 0.0 if side in ("left", "top") else float(across - 1)
        if sum(hit == start for _, hit in samples) > len(samples) / 2:
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


def _cross(strip: "np.ndarray", band: int, from_start: bool):
    """1本の走査線で、外側から見て最初に紙へ切り替わる位置を返す。"""
    n = len(strip)
    band = min(band, n)
    inside = strip[:band] if from_start else strip[n - band:][::-1]
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
