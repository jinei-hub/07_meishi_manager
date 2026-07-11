"""登録済み名刺の一覧・検索・編集・削除・エクスポート。"""

import streamlit as st

from db.models import FIELDS
from db.session import init_db
from services import cards
from services.export import to_csv_bytes, to_vcard_bytes

st.set_page_config(page_title="一覧・検索", page_icon="📋", layout="wide")
init_db()

st.title("📋 名刺一覧・検索")

query = st.text_input("🔍 検索（氏名・会社・メール・住所など）", value="")
results = cards.search(query)

st.caption(f"{len(results)} 件")

# ── エクスポート ────────────────────────────────────────
if results:
    c1, c2, _ = st.columns([1, 1, 4])
    with c1:
        st.download_button(
            "⬇️ CSV",
            data=to_csv_bytes(results),
            file_name="meishi.csv",
            mime="text/csv",
        )
    with c2:
        st.download_button(
            "⬇️ vCard",
            data=to_vcard_bytes(results),
            file_name="meishi.vcf",
            mime="text/vcard",
        )

if not results:
    st.info("該当する名刺がありません。「名刺を登録」ページから追加してください。")
    st.stop()

# ── 一覧テーブル ────────────────────────────────────────
table = [
    {
        "ID": c["id"],
        "氏名": c["name"],
        "会社": c["company"],
        "役職": c["title"],
        "電話": c["phone"] or c["mobile"],
        "メール": c["email"],
    }
    for c in results
]
st.dataframe(table, use_container_width=True, hide_index=True)

# ── まとめて削除（重複の整理） ─────────────────────────
with st.expander("🗑️ まとめて削除（重複の整理）"):
    del_map = {
        f'{c["id"]}: {c["name"] or "(無題)"} / {c["company"]}': c["id"]
        for c in results
    }
    to_delete = st.multiselect("削除する名刺を選択（複数可）", options=list(del_map.keys()))
    if to_delete:
        confirm = st.checkbox(
            f"⚠️ {len(to_delete)} 件を削除します（元に戻せません）",
            key="bulk_del_confirm",
        )
        if st.button("🗑️ 選択した名刺を削除", type="primary", disabled=not confirm):
            for label in to_delete:
                cards.delete(del_map[label])
            st.success(f"{len(to_delete)} 件を削除しました。")
            st.rerun()

# ── 詳細（編集・削除） ──────────────────────────────────
st.divider()
st.subheader("詳細・編集")

id_map = {f'{c["id"]}: {c["name"] or "(無題)"} / {c["company"]}': c["id"] for c in results}
selected_label = st.selectbox("名刺を選択", options=list(id_map.keys()))
card_id = id_map[selected_label]
card = cards.get(card_id)

if card:
    col_img, col_fields = st.columns([1, 1])
    with col_img:
        img_bytes = cards.get_image(card_id)
        if img_bytes:
            st.image(img_bytes, use_container_width=True)
        else:
            st.caption("画像なし")

    with col_fields:
        st.caption("コピーボタンでコピー / 入力欄で修正して更新できます。")
        edited = {}
        for key, label in FIELDS:
            value = card.get(key, "") or ""
            st.markdown(f"**{label}**")
            if value:
                st.code(value, language=None)
            edited[key] = st.text_input(
                f"{label}", value=value, key=f"d_{card_id}_{key}",
                label_visibility="collapsed",
            )
        edited["memo"] = st.text_area("メモ", value=card.get("memo", ""),
                                      key=f"d_{card_id}_memo")

        b1, b2 = st.columns(2)
        with b1:
            if st.button("💾 更新", type="primary", key=f"upd_{card_id}"):
                cards.update(card_id, edited)
                st.success("更新しました。")
                st.rerun()
        with b2:
            if st.button("🗑️ 削除", key=f"del_{card_id}"):
                cards.delete(card_id)
                st.success("削除しました。")
                st.rerun()
