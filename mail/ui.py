"""お礼メールの下書き作成 UI。pages/1_一覧・検索.py の詳細欄から呼ぶ。

ページ側を薄く保つため、UI ブロックをここに閉じ込めている。

■ session_state の規約
  キーはすべて `mail_` 接頭辞。名刺を切り替えたら関数の冒頭で全部捨てる
  （ウィジェットを作る前に消すこと。作った後に触ると Streamlit が例外を出す）。

■ st.expander ではなく st.toggle で開閉する
  expander の開閉状態は再実行をまたいで保持されるとは限らず、
  生成したプレビューが勝手に畳まれる事故が起きる。toggle なら session_state
  に載るので確実に開いたままになる。
"""

from __future__ import annotations

import streamlit as st

from mail import compose, gmail, templates
from mail.compose import ComposeError
from mail.gmail import GmailError
from services import mail_log


def _reset_if_card_changed(card: dict) -> None:
    """名刺を切り替えたら前の名刺の入力・生成結果を全部捨てる。"""
    if st.session_state.get("mail_card_id") != card["id"]:
        for k in [k for k in st.session_state if k.startswith("mail_")]:
            st.session_state.pop(k, None)
        st.session_state["mail_card_id"] = card["id"]


def _size_label(n: int) -> str:
    return f"{n / 1024 / 1024:.1f}MB" if n >= 1024 * 1024 else f"{n / 1024:.0f}KB"


def render_mail_section(card: dict) -> None:
    """名刺1件に対するお礼メールの下書き作成 UI を描画する。"""
    _reset_if_card_changed(card)

    st.subheader("✉️ お礼メール")
    if not st.toggle("お礼メールの下書きを作る", key="mail_open"):
        st.caption("この相手あてのお礼メールを、Gmail の下書きとして作成できます（送信はしません）。")
        return

    # ── 二重作成のガード ────────────────────────────────
    history = mail_log.list_for_card(card["id"])
    allow_create = True
    if history:
        last = history[0]
        st.warning(
            f"この名刺には既に下書きを作成済みです"
            f"（{last['created_at_jst']} / {last['pattern_label']}）。"
        )
        allow_create = st.checkbox("それでも新しく下書きを作る", key="mail_again")

    # ── 宛先 ────────────────────────────────────────────
    to = st.text_input(
        "宛先", value=(card.get("email") or ""), key="mail_to",
        placeholder="taro@example.co.jp",
    )
    cc = st.text_input(
        "CC（任意・カンマ区切り）", key="mail_cc",
        placeholder="jiro@example.co.jp, saburo@example.co.jp",
    )
    if not (card.get("email") or "").strip():
        st.info(
            "この名刺にはメールアドレスがありません。上の「詳細・編集」で入力して"
            "💾 更新するか、この宛先欄に直接入力してください。"
        )

    # ── パターン選択 ────────────────────────────────────
    pattern_id = st.radio(
        "パターン",
        options=[p["id"] for p in templates.PATTERNS],
        format_func=lambda pid: templates.get(pid)["label"],
        key="mail_pattern",
    )
    pattern = templates.get(pattern_id)
    st.caption(pattern["hint"])

    # ── パターン固有の入力 ──────────────────────────────
    inputs: dict[str, str] = {}
    for spec in pattern["inputs"]:
        widget_key = f"mail_in_{pattern_id}_{spec['key']}"
        kwargs = {
            "value": spec.get("default", ""),
            "key": widget_key,
            "placeholder": spec.get("placeholder", ""),
        }
        if spec.get("multiline"):
            inputs[spec["key"]] = st.text_area(spec["label"], height=110, **kwargs)
        else:
            inputs[spec["key"]] = st.text_input(spec["label"], **kwargs)

    # ── 添付（文面より先に確定させる） ──────────────────
    # パターンAの本文が「本メールに添付いたしました」/「追ってご送付します」で
    # 分岐するため、生成時点で添付の有無が決まっている必要がある。
    files = st.file_uploader(
        "📎 添付ファイル（複数可）", accept_multiple_files=True, key="mail_files",
    )
    attachments = [(f.name, f.getvalue()) for f in (files or [])]
    total_bytes = sum(len(b) for _n, b in attachments)
    if attachments:
        limit = gmail.MAX_ATTACHMENT_BYTES
        msg = f"{len(attachments)} 件 / 合計 {_size_label(total_bytes)}"
        if total_bytes > limit:
            st.error(f"{msg} — 上限 {_size_label(limit)} を超えています。")
        else:
            st.caption(msg)

    # ── 自由記述 ────────────────────────────────────────
    instruction = st.text_area(
        "自由記述（任意）", key="mail_instruction", height=90,
        placeholder="例: 契約は10/19開始で。石橋専務にも宛ててください。",
    )
    st.caption("自由記述を書いたときだけ AI が文面を調整します。空欄ならテンプレートそのままです。")

    # ── 文面を作る ──────────────────────────────────────
    if st.button("✍️ 文面を作る", type="primary", key="mail_gen"):
        try:
            subject, body = templates.render(
                pattern_id, card, inputs, has_attachment=bool(attachments),
            )
            if instruction.strip():
                with st.spinner("AIが文面を調整しています…"):
                    subject, body = compose.refine(
                        subject, body, instruction, card, pattern["label"],
                    )
        except ComposeError as e:
            st.error(str(e))
        except Exception as e:  # noqa: BLE001  UI を落とさない
            st.error(f"想定外のエラー: {e}")
        else:
            # ウィジェットを作る前に代入する（作った後に触ると Streamlit が例外を出す）
            st.session_state["mail_subject"] = subject
            st.session_state["mail_body"] = body
            st.session_state.pop("mail_done", None)  # 作り直したら作成済みを解除
            st.rerun()

    if "mail_body" not in st.session_state:
        return

    # ── プレビュー（承認ゲート: ここを直した内容がそのまま下書きになる） ──
    st.divider()
    st.caption("内容を確認し、必要なら直してから下書きを作成してください。")
    # value= は渡さない。session_state を唯一の真実にする（両方渡すと警告が出る）
    subject_val = st.text_input("件名", key="mail_subject")
    body_val = st.text_area("本文", key="mail_body", height=420)

    # ── 下書き作成 ──────────────────────────────────────
    reasons = []
    if not gmail.looks_like_email(to):
        reasons.append("宛先を入力してください")
    if not allow_create:
        reasons.append("既に作成済みです（上のチェックを入れると作成できます）")
    if total_bytes > gmail.MAX_ATTACHMENT_BYTES:
        reasons.append("添付が上限を超えています")
    if st.session_state.get("mail_done"):
        reasons.append("この文面の下書きは作成済みです")

    if st.button("📮 Gmail下書きを作成", type="primary",
                 disabled=bool(reasons), key="mail_create"):
        try:
            with st.spinner("Gmail に下書きを作成しています…"):
                draft_id = gmail.create_draft(
                    to, subject_val, body_val, attachments, cc,
                )
        except GmailError as e:
            st.error(str(e))
        except Exception as e:  # noqa: BLE001
            st.error(f"想定外のエラー: {e}")
        else:
            mail_log.add(card["id"], pattern_id, subject_val, to, draft_id)
            st.session_state["mail_done"] = draft_id
            st.rerun()

    if reasons:
        st.caption(" / ".join(reasons))
    st.caption("下書きを作るだけで、送信は行いません。送信は Gmail で内容を確認してからご自身で行ってください。")

    if draft_id := st.session_state.get("mail_done"):
        st.success("✅ Gmail の下書きを作成しました。送信はしていません。")
        st.markdown(f"[Gmail の下書きを開く]({gmail.DRAFTS_URL})")
        st.caption(f"下書きID: {draft_id}")
