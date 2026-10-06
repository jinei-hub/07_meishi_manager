"""登録済み名刺の一覧・検索・編集・削除・エクスポート。

■ 一覧の見た目は Sansan の名刺一覧に寄せている（2026-10-07）
  - 1件を1行のカードにし、サムネイル / 会社名 / 氏名 / 部署・役職 / 連絡先 / 住所 を並べる。
    表形式（st.dataframe）をやめたのは、名刺は項目が多く横スクロールになるため。
  - キーワード1本の横断検索に加えて、項目別の「詳細検索」（AND）を持つ。
  - 「N件中 x〜y件目」とページ送りを出す。

■ 画像は画面に出すぶんだけ読む
  画像はDBにBLOBで入っており1枚0.5〜1.5MBある。全件のサムネイルを毎回作ると
  無料枠（Neon 0.5GB / Streamlit Cloud 1GB）をすぐ食う。そこで
  - 一覧の取得自体は画像を引かない（db/models.py の image は deferred）
  - サムネイルは「表示中のページの分」だけ作り、st.cache_data で作り直さない
"""

import html
import math

import streamlit as st

import config  # noqa: F401
from theme import apply_theme
from auth import require_login
from db.models import FIELDS
from db.session import init_db
from mail.ui import render_mail_section
from services import cards
from services.export import to_csv_bytes, to_vcard_bytes
from services.imaging import thumbnail_bytes

st.set_page_config(page_title="一覧・検索", page_icon="📋", layout="wide")

apply_theme()
require_login()
init_db()

# 詳細検索に出す項目。キーは services/cards.py の SEARCHABLE に含まれるものだけ。
ADV_FIELDS = [
    ("company", "会社名"),
    ("name", "氏名"),
    ("department", "部署"),
    ("title", "役職"),
    ("email", "メール"),
    ("phone", "電話"),
    ("address", "住所"),
    ("memo", "メモ"),
]
SORT_OPTIONS = ["登録が新しい順", "登録が古い順", "会社名順", "氏名順"]
PER_PAGE_OPTIONS = [10, 20, 50, 100]


@st.cache_data(show_spinner=False, max_entries=600)
def thumb(card_id: int) -> bytes | None:
    """一覧用サムネイル。card_id ごとに1回だけ作る（画像は後から変わらない）。

    壊れた画像が1枚あっても一覧全体を落とさない。一覧は何十枚も並べるので、
    1件の不具合でページが開けなくなる方が困る（その行は「画像なし」になる）。
    """
    raw = cards.get_image(card_id)
    if not raw:
        return None
    try:
        return thumbnail_bytes(raw)
    except Exception:
        return None


def esc(value: str | None) -> str:
    """名刺の中身は読み取り結果なので、HTMLに埋める前に必ずエスケープする。"""
    return html.escape(value or "")


def sort_rows(rows: list[dict], how: str) -> list[dict]:
    """空の項目は末尾へ回す（並べ替えても空欄が先頭に来ないように）。"""
    if how == "登録が古い順":
        return list(reversed(rows))
    if how == "会社名順":
        return sorted(rows, key=lambda c: (c["company"] or "￿", c["name"] or ""))
    if how == "氏名順":
        return sorted(rows, key=lambda c: (c["name"] or "￿", c["company"] or ""))
    return rows  # 登録が新しい順。DB側で created_at desc 済み


st.title("📋 名刺一覧・検索")

# ── 検索 ────────────────────────────────────────────────
query = st.text_input(
    "🔍 キーワード検索",
    placeholder="氏名・会社・メール・住所など（入力すると自動で絞り込みます）",
    key="q",
)

adv: dict = st.session_state.setdefault("adv", {})
nonce: int = st.session_state.setdefault("adv_nonce", 0)

with st.expander("🔎 詳細検索（項目を指定して絞り込む）", expanded=bool(adv)):
    with st.form("adv_form"):
        cols = st.columns(4)
        entered = {
            key: cols[i % 4].text_input(label, value=adv.get(key, ""),
                                        key=f"adv_{key}_{nonce}")
            for i, (key, label) in enumerate(ADV_FIELDS)
        }
        b1, b2, _ = st.columns([2, 2, 5])
        do_search = b1.form_submit_button("🔍 この条件で検索", type="primary",
                                          width="stretch")
        do_clear = b2.form_submit_button("条件をクリア", width="stretch")

    if do_search:
        st.session_state.adv = {k: v for k, v in entered.items() if v.strip()}
        st.session_state.page = 1
        st.rerun()
    if do_clear:
        # 入力欄を空に戻す。ウィジェットのキーを変えて作り直す（state を直接触らない）
        st.session_state.adv = {}
        st.session_state.adv_nonce = nonce + 1
        st.session_state.page = 1
        st.rerun()

results = cards.search(query, adv)

# 検索条件が変わったら1ページ目へ。5ページ目のまま絞り込むと空振りに見えるため。
condition = (query, tuple(sorted(adv.items())))
if st.session_state.get("last_condition") != condition:
    st.session_state.last_condition = condition
    st.session_state.page = 1

if adv:
    labels = {key: label for key, label in ADV_FIELDS}
    st.caption("絞り込み中： " + " ／ ".join(f"{labels[k]}「{v}」" for k, v in adv.items()))

if not results:
    st.info("該当する名刺がありません。条件を変えるか、「名刺を登録」ページから追加してください。")
    st.stop()

# ── 件数・並び替え・エクスポート ────────────────────────
c_cnt, c_sort, c_per, c_csv, c_vcf = st.columns([3, 2, 1.4, 1.2, 1.2],
                                                vertical_alignment="bottom")
with c_sort:
    sort_how = st.selectbox("並び替え", SORT_OPTIONS, key="sort")
with c_per:
    per_page = st.selectbox("表示件数", PER_PAGE_OPTIONS, index=1, key="per_page")
with c_csv:
    st.download_button("⬇️ CSV", data=to_csv_bytes(results), file_name="meishi.csv",
                       mime="text/csv", width="stretch")
with c_vcf:
    st.download_button("⬇️ vCard", data=to_vcard_bytes(results), file_name="meishi.vcf",
                       mime="text/vcard", width="stretch")

rows = sort_rows(results, sort_how)
total = len(rows)
last_page = max(1, math.ceil(total / per_page))
page = min(max(1, st.session_state.setdefault("page", 1)), last_page)
st.session_state.page = page
start, end = (page - 1) * per_page, min(page * per_page, total)

with c_cnt:
    st.markdown(
        f'<div class="dp-count"><b>{total}</b> 件中 {start + 1}〜{end} 件目</div>',
        unsafe_allow_html=True,
    )

# ── 一覧（1件=1行のカード） ─────────────────────────────
for card_row in rows[start:end]:
    with st.container(border=True):
        c_img, c_main, c_date, c_act = st.columns([1.1, 5, 1.1, 1.1],
                                                  vertical_alignment="center")

        with c_img:
            image = thumb(card_row["id"]) if card_row["has_image"] else None
            if image:
                st.image(image, width="stretch")
            else:
                st.markdown('<div class="dp-noimg">画像なし</div>', unsafe_allow_html=True)

        with c_main:
            role = " ／ ".join(p for p in (card_row["department"], card_row["title"]) if p)
            meta = []
            if card_row["phone"] or card_row["mobile"]:
                meta.append(f'<span>☎ {esc(card_row["phone"] or card_row["mobile"])}</span>')
            if card_row["email"]:
                meta.append(f'<span>✉ {esc(card_row["email"])}</span>')
            if card_row["address"]:
                meta.append(f'<span>📍 {esc(card_row["address"])}</span>')

            company = esc(card_row["company"]) or "&nbsp;"
            name = esc(card_row["name"]) or '<span class="dp-empty">(氏名なし)</span>'
            st.markdown(
                f'<div class="dp-company">{company}</div>'
                f'<div class="dp-name">{name}</div>'
                f'<div class="dp-role">{esc(role)}</div>'
                f'<div class="dp-meta">{"".join(meta)}</div>',
                unsafe_allow_html=True,
            )

        with c_date:
            created = card_row.get("created_at")
            st.markdown(
                '<div class="dp-date">登録日<br><b>'
                + (created.strftime("%Y/%m/%d") if created else "—")
                + "</b></div>",
                unsafe_allow_html=True,
            )

        with c_act:
            if st.button("詳細", key=f"open_{card_row['id']}", width="stretch"):
                st.session_state.sel = card_row["id"]

# ── ページ送り ──────────────────────────────────────────
if last_page > 1:
    p_prev, p_now, p_next = st.columns([1, 2, 1], vertical_alignment="center")
    if p_prev.button("◀ 前へ", disabled=page <= 1, width="stretch"):
        st.session_state.page = page - 1
        st.rerun()
    p_now.markdown(
        f'<div class="dp-count" style="text-align:center">{page} / {last_page} ページ</div>',
        unsafe_allow_html=True,
    )
    if p_next.button("次へ ▶", disabled=page >= last_page, width="stretch"):
        st.session_state.page = page + 1
        st.rerun()

# ── まとめて削除（重複の整理） ─────────────────────────
with st.expander("🗑️ まとめて削除（重複の整理）"):
    del_map = {
        f'{c["id"]}: {c["name"] or "(無題)"} / {c["company"]}': c["id"]
        for c in rows
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

id_map = {f'{c["id"]}: {c["name"] or "(無題)"} / {c["company"]}': c["id"] for c in rows}
option_labels = list(id_map)
selected_id = st.session_state.get("sel")
current = next((lb for lb, cid in id_map.items() if cid == selected_id), option_labels[0])

selected_label = st.selectbox("名刺を選択", options=option_labels,
                              index=option_labels.index(current))
card_id = id_map[selected_label]
st.session_state.sel = card_id
card = cards.get(card_id)

if card:
    col_img, col_fields = st.columns([1, 1])
    with col_img:
        img_bytes = cards.get_image(card_id)
        if not img_bytes:
            st.caption("画像なし")
        else:
            try:
                st.image(img_bytes, width="stretch")
            except Exception:
                # 画像が壊れていてもページは開けるようにする（項目の閲覧・編集は続けられる）
                st.warning("画像を表示できません（保存データが壊れています）。")

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

    st.divider()
    render_mail_section(card)
