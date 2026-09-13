"""お礼メールの下書きを作った履歴。

「いつ・誰に・どのパターンで作ったか」を後から追えるようにする。
記録は下書きを作った時点で services/mail_log.py が残している。
"""

import streamlit as st

import config  # noqa: F401  .env / st.secrets を環境変数へ（最初に実行）
from auth import require_login
from db.session import init_db
from mail import gmail, templates
from services import cards, mail_log
from theme import apply_theme

st.set_page_config(page_title="お礼メール履歴", page_icon="✉️", layout="wide")

apply_theme()
require_login()
init_db()

st.title("✉️ お礼メール履歴")
st.caption("作成した下書きの記録です。送信したかどうかまでは分かりません（Gmail で確認してください）。")

rows = mail_log.recent(200)
if not rows:
    st.info("まだ作成した下書きはありません。「一覧・検索」で名刺を選ぶと作成できます。")
    st.stop()

# ── 絞り込み ────────────────────────────────────────────
c1, c2 = st.columns([2, 1])
with c1:
    query = st.text_input("🔍 絞り込み（宛先・件名）", value="")
with c2:
    labels = ["すべて"] + [p["label"] for p in templates.PATTERNS]
    chosen = st.selectbox("パターン", options=labels)

def _match(r: dict) -> bool:
    if chosen != "すべて" and r["pattern_label"] != chosen:
        return False
    q = query.strip()
    if not q:
        return True
    return q in (r["to_email"] or "") or q in (r["subject"] or "")

shown = [r for r in rows if _match(r)]

m1, m2 = st.columns(2)
m1.metric("表示中", f"{len(shown)} 件")
m2.metric("全期間の作成数", f"{mail_log.count()} 件")

st.dataframe(
    [
        {
            "作成日時": r["created_at_jst"],
            "宛先": r["to_email"],
            "パターン": r["pattern_label"],
            "件名": r["subject"],
        }
        for r in shown
    ],
    width="stretch",
    hide_index=True,
)

st.markdown(f"[Gmail の下書きを開く]({gmail.DRAFTS_URL})")

# ── 相手ごとの件数（誰に何回出したか）────────────────────
with st.expander("相手ごとの作成回数"):
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["to_email"] or "(宛先なし)"] = counts.get(r["to_email"] or "(宛先なし)", 0) + 1
    # 名刺の氏名・会社を引き当てて分かりやすくする
    by_email = {c["email"]: c for c in cards.list_all() if c.get("email")}
    st.dataframe(
        [
            {
                "宛先": mail,
                "氏名": (by_email.get(mail) or {}).get("name", ""),
                "会社": (by_email.get(mail) or {}).get("company", ""),
                "作成回数": n,
            }
            for mail, n in sorted(counts.items(), key=lambda kv: -kv[1])
        ],
        width="stretch",
        hide_index=True,
    )
