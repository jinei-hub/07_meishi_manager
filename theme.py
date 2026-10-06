"""見た目を会社サイト（dipilot.jp）に寄せる。

色は dipilot-wp/dipilot-theme/assets/css/main.css の :root から取った実測値。
  --dp-blue #008afc / --dp-blue-dark #0071cf / --dp-navy #1a2b4a
  --dp-text #324158 / --dp-gray #bfbfbf / --dp-bg #f5f5f5

■ 和文に明朝は使わない
  本家も欧文だけ Cormorant Garamond で、和文は端末標準のゴシックにしている
  （和文 Web フォントは数MBあり初期表示が遅くなるため）。ここでも同じ判断にする。

■ 配色の大枠は .streamlit/config.toml の [theme] で指定する
  こちらはそれだけでは届かない部分（見出しの色、タイトル下の罫線、
  サイドバーの見え方、一覧の名刺カード行）を CSS で補う。
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

# ロゴは会社サイト（dipilot-wp/dipilot-theme/assets/images/logo.png）と同じもの。
# このリポジトリは公開されているが、ロゴは会社サイトに出ている公開物なので置いてよい。
ASSETS = Path(__file__).resolve().parent / "assets"
LOGO = ASSETS / "logo.png"            # 横長のロゴ（サイドバー上部）
LOGO_MARK = ASSETS / "logo-mark.png"  # マークのみ（サイドバーを畳んだとき）
SITE = "https://dipilot.jp/"

# ブラウザのタブに出すアイコン。サイドバーのナビはファイル名で表示されるため、
# ここを変えてもページの並びの見え方は変わらない（タブだけがDiPilotになる）。
PAGE_ICON = str(LOGO_MARK) if LOGO_MARK.is_file() else "📇"

BLUE = "#008afc"
BLUE_DARK = "#0071cf"
NAVY = "#1a2b4a"
TEXT = "#324158"
GRAY = "#8a8a8a"
MUTED = "#6b7a90"
BG = "#f5f5f5"
BORDER = "#e6e6e6"

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
  hr {{ border-color: {BORDER}; }}

  /* ── 一覧の名刺カード行 ──────────────────────────────
     Sansan の一覧に寄せる: 会社名を小さく青、氏名を大きく濃く、
     連絡先と住所はアイコン付きで淡く。視線が氏名→連絡先の順に流れるようにする。 */
  .dp-company {{
    font-size: .8rem; font-weight: 600; color: {BLUE_DARK};
    overflow-wrap: anywhere;
  }}
  .dp-name {{
    font-size: 1.08rem; font-weight: 700; color: {NAVY};
    margin: .1rem 0 .15rem; overflow-wrap: anywhere;
  }}
  .dp-role {{ font-size: .82rem; color: {TEXT}; overflow-wrap: anywhere; }}
  .dp-meta {{
    font-size: .8rem; color: {MUTED}; margin-top: .35rem;
    display: flex; flex-wrap: wrap; gap: .2rem 1.1rem;
  }}
  .dp-meta span {{ overflow-wrap: anywhere; }}
  .dp-empty {{ color: #bfbfbf; }}

  /* 登録日（Sansan の「名刺交換日」にあたる位置） */
  .dp-date {{ font-size: .72rem; color: {MUTED}; line-height: 1.5; text-align: right; }}
  .dp-date b {{ font-size: .85rem; color: {TEXT}; font-weight: 600; }}

  /* 画像が無い名刺のプレースホルダ（行の高さを揃えて一覧のガタつきを防ぐ） */
  .dp-noimg {{
    display: flex; align-items: center; justify-content: center;
    height: 72px; border: 1px dashed {BORDER}; border-radius: 4px;
    color: #bfbfbf; font-size: .72rem; background: {BG};
  }}

  /* 一覧の上に出す結果件数 */
  .dp-count {{ font-size: .85rem; color: {MUTED}; padding-top: .35rem; }}
  .dp-count b {{ color: {NAVY}; font-size: 1.1rem; }}

  /* サイドバー下部の社名。どのページでも会社名が目に入るようにする */
  .dp-foot {{
    margin-top: 1.2rem; padding-top: .9rem; border-top: 1px solid {BORDER};
    font-size: .74rem; color: {MUTED}; line-height: 1.7;
  }}
  .dp-foot b {{ color: {NAVY}; font-size: .84rem; font-weight: 700; }}
  .dp-foot a {{ color: {BLUE_DARK}; text-decoration: none; }}
  .dp-foot a:hover {{ text-decoration: underline; }}

  /* 枠付きコンテナ（一覧の行・検索パネル）はホバーでブランドカラーに寄せる */
  [data-testid="stVerticalBlockBorderWrapper"]:hover {{
    border-color: rgba(0, 138, 252, .45);
  }}

  /* 折りたたみ見出しもホバーでブランドカラーに */
  [data-testid="stExpander"] summary:hover {{ color: {BLUE_DARK}; }}

  /* 名刺画像は角を丸めて枠を付け、紙の名刺らしく見せる */
  [data-testid="stImage"] img {{ border-radius: 4px; border: 1px solid {BORDER}; }}
</style>
"""


def apply_theme() -> None:
    """全ページの先頭（set_page_config の直後）で呼ぶ。

    配色の CSS に加えて、会社ロゴとサイドバー下部の社名を出す。
    ロゴが見つからない場合も画面は出す（アプリが開けない方が困るため）。
    """
    st.markdown(_CSS, unsafe_allow_html=True)

    if LOGO.is_file():
        st.logo(
            str(LOGO),
            size="large",
            link=SITE,
            icon_image=str(LOGO_MARK) if LOGO_MARK.is_file() else None,
        )

    st.sidebar.markdown(
        '<div class="dp-foot">名刺管理<br><b>株式会社DiPilot</b><br>'
        f'<a href="{SITE}" target="_blank" rel="noopener">dipilot.jp</a></div>',
        unsafe_allow_html=True,
    )
