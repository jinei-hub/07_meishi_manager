"""名刺管理アプリ — 登録ページ（エントリ: streamlit run main.py）"""

import streamlit as st

import config  # noqa: F401  .env / st.secrets を環境変数へ（最初に実行）
from db.models import FIELDS
from db.session import init_db
from ocr.extract import extract_cards, ExtractError
from services import cards
from services.imaging import to_jpeg_bytes

st.set_page_config(page_title="名刺管理", page_icon="📇", layout="wide")

init_db()

st.title("📇 名刺を登録")
st.caption("名刺を撮影またはアップロードすると、AIが項目を読み取り、隣にコピー可能な形で表示します。")

# ── 入力 ───────────────────────────────────────────────
tab_cam, tab_up = st.tabs(["📷 カメラで撮影", "🖼️ 画像をアップロード"])
with tab_cam:
    cam_img = st.camera_input("名刺を撮影", key="cam")
with tab_up:
    up_img = st.file_uploader(
        "名刺画像を選択（jpg / png / heic）",
        type=["jpg", "jpeg", "png", "heic", "heif"],
        key="upload",
    )

raw = None
if cam_img is not None:
    raw = cam_img.getvalue()
elif up_img is not None:
    raw = up_img.getvalue()

# 画像が変わったら前回の抽出結果をクリア
if raw is not None:
    if st.session_state.get("_last_raw_len") != len(raw):
        st.session_state["_last_raw_len"] = len(raw)
        st.session_state.pop("extracted", None)
        try:
            st.session_state["jpeg"] = to_jpeg_bytes(raw)
        except Exception as e:
            st.error(f"画像を読み込めませんでした: {e}")
            st.session_state.pop("jpeg", None)

jpeg = st.session_state.get("jpeg")

if jpeg:
    if st.button("🔍 読み取る", type="primary"):
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
        st.image(jpeg, caption="アップロードした画像", use_container_width=True)
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
