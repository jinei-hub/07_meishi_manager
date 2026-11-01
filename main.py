"""名刺管理アプリ — 登録ページ（エントリ: streamlit run main.py）"""

import io

import streamlit as st
from PIL import Image

import config  # noqa: F401
from camera import BACK_MARKER, CAMERA_MARKER, use_rear_camera
from theme import apply_theme, PAGE_ICON
from auth import require_login
from db.models import FIELDS
from db.session import init_db
from ocr.extract import current_model, extract_cards, ExtractError
from services import cards
from services.imaging import (crop_bbox, crop_quad, probe_size, rotate_upright,
                              stack_vertical, to_jpeg_bytes)

st.set_page_config(page_title="名刺管理", page_icon=PAGE_ICON, layout="wide")

apply_theme()
require_login()

init_db()

st.title("📇 名刺を登録")
st.caption("名刺を撮影またはアップロードすると、AIが項目を読み取り、隣にコピー可能な形で表示します。")

# ── 入力 ───────────────────────────────────────────────
# 「撮る」は端末のカメラアプリを直接開く（camera.py で capture=environment を付ける）。
# 全画面・外カメラ・フル画質で撮れて、ブラウザ内蔵カメラの弱点を全部回避できる。
TYPES = ["jpg", "jpeg", "png", "heic", "heif", "webp"]

# 保存のたびに番号を繰り上げて、選択欄を新品にする（前の写真を残さない）。
# file_uploader は session_state から値を消せないため、key を変えるのが確実。
_round = st.session_state.get("upload_round", 0)
cam_file = st.file_uploader(
    f"📷 {CAMERA_MARKER}（カメラが開きます）", type=TYPES, key=f"up_cam_{_round}"
)
lib_file = st.file_uploader(
    "🖼️ 保存済みの画像から選ぶ", type=TYPES, key=f"up_lib_{_round}"
)
back_file = st.file_uploader(
    f"📷 {BACK_MARKER}（任意・裏面にも情報がある名刺だけ）", type=TYPES,
    key=f"up_back_{_round}",
)
use_rear_camera()
st.caption(
    "「撮る」はタップすると外カメラが全画面で開きます。ピントを合わせてから撮ってください。"
    "裏面を撮るときは、表・裏とも名刺1枚ずつにしてください。"
)

raw = None
if cam_file is not None:
    raw = cam_file.getvalue()
elif lib_file is not None:
    raw = lib_file.getvalue()

# 画像が変わったら前回の抽出結果をクリア
if raw is not None:
    if st.session_state.get("_last_raw_len") != len(raw):
        st.session_state["_last_raw_len"] = len(raw)
        st.session_state.pop("extracted", None)
        try:
            st.session_state["src_size"] = probe_size(raw)
            st.session_state["jpeg"] = to_jpeg_bytes(raw, model=current_model())
        except Exception as e:
            st.error(f"画像を読み込めませんでした: {e}")
            st.session_state.pop("jpeg", None)

jpeg = st.session_state.get("jpeg")

back_raw = back_file.getvalue() if back_file is not None else None
back_sig = len(back_raw) if back_raw else 0
if st.session_state.get("_last_back_len", 0) != back_sig:
    st.session_state["_last_back_len"] = back_sig
    st.session_state.pop("extracted", None)
    if back_raw:
        try:
            st.session_state["back_jpeg"] = to_jpeg_bytes(back_raw, model=current_model())
        except Exception as e:
            st.error(f"裏面の画像を読み込めませんでした: {e}")
            st.session_state.pop("back_jpeg", None)
    else:
        st.session_state.pop("back_jpeg", None)
back_jpeg = st.session_state.get("back_jpeg")


def _one_side(src: bytes, quad, bbox, text_up) -> tuple[bytes, str]:
    """片面を切り出し、(画像, 使った方式) を返す。方式は画面の診断に出す。

    台形補正を優先する。斜めから撮っても真っ直ぐになり、横倒しに撮った名刺も
    正しい向きになる。四隅が信用できないときだけ従来の bbox に落とす。
    """
    detail: dict = {}
    out = crop_quad(src, quad, detail=detail, text_up=text_up)
    if out is not None:
        snapped = "紙の縁に吸着" if detail.get("refined") else "読み取りの四隅のまま（縁を見つけられず）"
        return out, f"四隅から台形補正 / {snapped}"
    out = crop_bbox(src, bbox)
    if out is not None:
        return rotate_upright(out, text_up), "bbox で切り出し（四隅は使えず）"
    return rotate_upright(src, text_up), "切り出せず写真全体"


def card_image(data: dict) -> tuple[bytes, bool, list[str]]:
    """保存する画像（その人の名刺だけ。裏面があれば表の下に並べる）と、切り出せたか。

    3つめは「表はこの方式」という説明の一覧で、画面の診断表示にだけ使う。
    どの経路を通ったか分からないと、切り出しが効かないときに原因を追えない。
    """
    front, how = _one_side(jpeg, data.get("quad"), data.get("bbox"), data.get("text_up"))
    ways = [f"表: {how}"]
    ok = not how.startswith("切り出せず")
    if not back_jpeg:
        return front, ok, ways
    back, how_b = _one_side(back_jpeg, data.get("back_quad"), data.get("back_bbox"),
                            data.get("back_text_up"))
    ways.append(f"裏: {how_b}")
    ok = ok and not how_b.startswith("切り出せず")
    return stack_vertical(front, back), ok, ways


def crop_diagnosis(data: dict, ways: list[str]) -> None:
    """切り出しの内訳を折りたたんで出す。読み取り結果の生の座標も見せる。"""
    with st.expander("切り出しの詳細（うまくいかないときに開く）"):
        for line in ways:
            st.markdown(f"- {line}")
        src = Image.open(io.BytesIO(jpeg)).size
        st.caption(f"読み取りに送った画像: {src[0]}×{src[1]}px")
        for key in ("quad", "bbox", "text_up", "back_quad", "back_bbox", "back_text_up"):
            value = data.get(key)
            if value is None and key.startswith("back") and not back_jpeg:
                continue
            st.markdown(f"**{key}**")
            st.code("（返ってこなかった）" if value is None else repr(value), language=None)


# 撮影品質を見せる（低解像度のまま読ませて精度が出ない、を防ぐ）
if jpeg:
    sw, sh = st.session_state.get("src_size", (0, 0))
    if sw and sh:
        long_side = max(sw, sh)
        sent = Image.open(io.BytesIO(jpeg)).size
        if long_side < 1200:
            st.warning(
                f"この画像は {sw}×{sh}px と小さめです。文字が潰れて読み取り精度が落ちます。"
                "端末のカメラアプリで撮り直すことをおすすめします。",
                icon="🔍",
            )
        else:
            st.caption(f"元の画像 {sw}×{sh}px → 送信 {sent[0]}×{sent[1]}px")

if jpeg:
    read_label = "🔍 表と裏を読み取る" if back_jpeg else "🔍 読み取る"
    if st.button(read_label, type="primary", width="stretch"):
        with st.spinner("AIが名刺を読み取っています…"):
            try:
                st.session_state["extracted"] = extract_cards(jpeg, back_image_bytes=back_jpeg)
            except ExtractError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"想定外のエラー: {e}")

# ── 結果表示（画像 + 検出した名刺ごとの編集フォーム） ─────
if jpeg and "extracted" in st.session_state:
    detected = st.session_state["extracted"]

    col_img, col_fields = st.columns([1, 1])
    with col_img:
        st.image(jpeg, caption="表面" if back_jpeg else "アップロードした画像", width="stretch")
        if back_jpeg:
            st.image(back_jpeg, caption="裏面", width="stretch")
    with col_fields:
        if not detected:
            st.warning("名刺を検出できませんでした。別の画像を試すか、手動で入力してください。")
        else:
            st.subheader(f"{len(detected)} 枚の名刺を検出しました")
            if back_jpeg and len(detected) > 1:
                st.warning("裏面を使うときは、表・裏とも名刺1枚ずつで撮ってください。"
                           "複数枚だと表と裏の組み合わせを取り違えるおそれがあります。")
            st.caption("各項目のコピーボタンでコピー / 入力欄で修正できます。")

    if detected:
        for i, data in enumerate(detected):
            title_txt = data.get("name") or "(氏名不明)"
            company_txt = data.get("company") or ""
            with st.expander(f"名刺 {i + 1}: {title_txt} / {company_txt}", expanded=(len(detected) == 1)):
                # 複数枚を1枚の写真で撮っても、保存されるのはその人の名刺だけ
                img, ok, ways = card_image(data)
                caption = "この名刺として保存されます" + ("（上: 表 / 下: 裏）" if back_jpeg else "")
                st.image(img, caption=caption, width=320)
                if not ok:
                    st.caption("一部の切り出しに失敗したため、その面は写真全体を保存します。")
                crop_diagnosis(data, ways)
                for key, label in FIELDS:
                    value = data.get(key, "") or ""
                    st.markdown(f"**{label}**")
                    if value:
                        st.code(value, language=None)  # 右上にコピーボタン
                    st.text_input(
                        f"{label}（修正用）", value=value, key=f"edit_{i}_{key}",
                        label_visibility="collapsed",
                    )
                st.text_area("メモ", value="", key=f"edit_{i}_memo")

        if st.button("💾 検出した名刺をすべて保存", type="primary"):
            saved = 0
            for i in range(len(detected)):
                fields = {key: st.session_state.get(f"edit_{i}_{key}", "") for key, _ in FIELDS}
                fields["memo"] = st.session_state.get(f"edit_{i}_memo", "")
                # 全項目が空のカードはスキップ
                if any((fields.get(k) or "").strip() for k, _ in FIELDS):
                    # その名刺の部分だけを保存する。切り出せなければ写真全体。
                    img, _ok, _ways = card_image(detected[i])
                    cards.create(fields, image_bytes=img)
                    saved += 1
            st.success(f"{saved} 件を保存しました。「一覧・検索」ページで確認できます。")
            for k in list(st.session_state.keys()):
                if k.startswith("edit_") or k in ("extracted", "jpeg", "back_jpeg",
                                                  "_last_raw_len", "_last_back_len",
                                                  "src_size"):
                    st.session_state.pop(k, None)
            # 選択欄も空にする（次の名刺をすぐ撮れるように）
            st.session_state["upload_round"] = _round + 1
            st.rerun()
