"""名刺管理アプリ — 登録ページ（エントリ: streamlit run main.py）"""

import io

import streamlit as st
from PIL import Image

import config  # noqa: F401
from camera import CAMERA_MARKER, use_rear_camera
from theme import apply_theme
from auth import require_login
from db.models import FIELDS
from db.session import init_db
from ocr.extract import extract_cards, ExtractError
from services import cards
from services.imaging import probe_size, to_jpeg_bytes

st.set_page_config(page_title="名刺管理", page_icon="📇", layout="wide")

apply_theme()
require_login()

init_db()

st.title("📇 名刺を登録")
st.caption("名刺を撮影またはアップロードすると、AIが項目を読み取り、隣にコピー可能な形で表示します。")

# ── 入力 ───────────────────────────────────────────────
# 「撮る」は端末のカメラアプリを直接開く（camera.py で capture=environment を付ける）。
# 全画面・外カメラ・フル画質で撮れて、ブラウザ内蔵カメラの弱点を全部回避できる。
TYPES = ["jpg", "jpeg", "png", "heic", "heif", "webp"]

cam_file = st.file_uploader(f"📷 {CAMERA_MARKER}（カメラが開きます）", type=TYPES, key="up_cam")
lib_file = st.file_uploader("🖼️ 保存済みの画像から選ぶ", type=TYPES, key="up_lib")
use_rear_camera()
st.caption("「撮る」はタップすると外カメラが全画面で開きます。ピントを合わせてから撮ってください。")

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
            st.session_state["jpeg"] = to_jpeg_bytes(raw)
        except Exception as e:
            st.error(f"画像を読み込めませんでした: {e}")
            st.session_state.pop("jpeg", None)

jpeg = st.session_state.get("jpeg")

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
    if st.button("🔍 読み取る", type="primary", width="stretch"):
        with st.spinner("AIが名刺を読み取っています…"):
            try:
                st.session_state["extracted"] = extract_cards(jpeg)
            except ExtractError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"想定外のエラー: {e}")

# ── 結果表示（画像 + 検出した名刺ごとの編集フォーム） ─────
if jpeg and "extracted" in st.session_state:
    detected = st.session_state["extracted"]

    col_img, col_fields = st.columns([1, 1])
    with col_img:
        st.image(jpeg, caption="アップロードした画像", width="stretch")
    with col_fields:
        if not detected:
            st.warning("名刺を検出できませんでした。別の画像を試すか、手動で入力してください。")
        else:
            st.subheader(f"{len(detected)} 枚の名刺を検出しました")
            st.caption("各項目のコピーボタンでコピー / 入力欄で修正できます。")

    if detected:
        for i, data in enumerate(detected):
            title_txt = data.get("name") or "(氏名不明)"
            company_txt = data.get("company") or ""
            with st.expander(f"名刺 {i + 1}: {title_txt} / {company_txt}", expanded=(len(detected) == 1)):
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
                    cards.create(fields, image_bytes=jpeg)  # 各カードに元画像を保存
                    saved += 1
            st.success(f"{saved} 件を保存しました。「一覧・検索」ページで確認できます。")
            for k in list(st.session_state.keys()):
                if k.startswith("edit_") or k in ("extracted", "jpeg", "_last_raw_len"):
                    st.session_state.pop(k, None)
            st.rerun()
