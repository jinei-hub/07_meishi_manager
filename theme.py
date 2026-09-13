"""見た目を会社サイト（dipilot.jp）に寄せる。

色は dipilot-wp/dipilot-theme/assets/css/main.css の :root から取った実測値。
  --dp-blue #008afc / --dp-blue-dark #0071cf / --dp-navy #1a2b4a
  --dp-text #324158 / --dp-gray #bfbfbf / --dp-bg #f5f5f5

■ 和文に明朝は使わない
  本家も欧文だけ Cormorant Garamond で、和文は端末標準のゴシックにしている
  （和文 Web フォントは数MBあり初期表示が遅くなるため）。ここでも同じ判断にする。

■ 配色の大枠は .streamlit/config.toml の [theme] で指定する
  こちらはそれだけでは届かない部分（見出しの色、タイトル下の罫線、
  サイドバーの見え方）を CSS で補う。
"""

from __future__ import annotations

import streamlit as st

BLUE = "#008afc"
BLUE_DARK = "#0071cf"
NAVY = "#1a2b4a"
TEXT = "#324158"
GRAY = "#8a8a8a"
BG = "#f5f5f5"

_CSS = f"""
<style>
  /* 見出しは本家のネイビー。太さを落として詰まった印象を避ける */
  h1, h2, h3 {{ color: {NAVY}; letter-spacing: .02em; }}
  h1 {{ font-size: 1.9rem; font-weight: 700; padding-bottom: .4rem; }}
  /* ページタイトルの下にブランドカラーの細い罫線（本家のセクション区切りの感じ） */
  h1::after {{
    content: ""; display: block; width: 48px; height: 3px;
    background: {BLUE}; margin-top: .5rem; border-radius: 2px;
  }}
  h2 {{ font-size: 1.25rem; margin-top: 1.6rem; }}
  h3 {{ font-size: 1.05rem; }}

  /* 補足文は少し淡く。情報量が多い画面で本文と区別しやすくする */
  [data-testid="stCaptionContainer"] {{ color: {GRAY}; }}

  /* サイドバー: 本家の淡いグレー地に合わせ、選択中を青で示す */
  [data-testid="stSidebarNav"] a[aria-current="page"] {{
    background: rgba(0, 138, 252, .10);
    border-left: 3px solid {BLUE};
    font-weight: 600;
  }}

  /* 主ボタンはブランドブルー。ホバーで濃い方へ */
  .stButton button[kind="primary"], .stFormSubmitButton button[kind="primary"] {{
    background: {BLUE}; border-color: {BLUE};
  }}
  .stButton button[kind="primary"]:hover, .stFormSubmitButton button[kind="primary"]:hover {{
    background: {BLUE_DARK}; border-color: {BLUE_DARK};
  }}

  /* 区切り線は本家と同じ薄さに */
  hr {{ border-color: #e6e6e6; }}
</style>
"""


def apply_theme() -> None:
    """全ページの先頭（set_page_config の直後）で呼ぶ。"""
    st.markdown(_CSS, unsafe_allow_html=True)
