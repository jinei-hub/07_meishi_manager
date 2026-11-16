"""ホーム画面に追加して「アプリ」として起動できるようにする（PWA 化）。

■ Streamlit では <head> を直接編集できない
  manifest / apple-touch-icon / 各メタタグは <head> に入れないと効かないが、
  Streamlit に <head> を触る引数は無い（st.markdown の HTML は <body> に入る上に
  サニタイズされ、<link rel="manifest"> は無視される）。そこで camera.py と同じ、
  高さ0の不可視コンポーネントから親フレームの document.head に差し込む手口を使う。
  Streamlit は再描画のたびに DOM を作り直すが、head のタグは消えないので一度入れれば残る。
  二重登録を避けるため、各タグに id を付けて「無ければ作る」。

■ アイコン・manifest の配信
  .streamlit/config.toml で enableStaticServing=true にし、static/ 置いたファイルを
  <origin>/app/static/<name> で配る。URL は親フレームの origin から絶対パスで組む
  （ページ（pages/）に移ると相対パスの基準がずれるため、相対では配信先を取り違える）。

■ アイコンを差し替えたら VERSION を上げる
  iPhone はアイコンを強くキャッシュする。URL に ?v=VERSION を付けておき、
  static/ の PNG を入れ替えたらこの数字を 1 つ上げる。これで新しいアイコンを読み直す。

■ これでできること / できないこと（正直に）
  できる: ホーム画面アイコン・全画面起動（アドレスバー無し）・起動時のテーマ色。
  できない: オフライン動作（Streamlit は常にサーバーが要る）。Service Worker は
  置いていない。iOS は自動インストール不可で、初回だけ「共有 → ホーム画面に追加」が要る。
"""

from __future__ import annotations

import json

import streamlit as st

# アイコン/manifest を差し替えたら上げる（iPhone のキャッシュを無効化する）。
VERSION = 1

APP_NAME = "名刺管理"
THEME_COLOR = "#1a2b4a"  # iOS/Android のステータスバー色。theme.py の NAVY と同じ


def enable_pwa() -> None:
    """全ページで（apply_theme 経由で）呼ぶ。head に PWA 用のタグを差し込む。"""
    from streamlit.components.v1 import html

    cfg = json.dumps({"name": APP_NAME, "theme": THEME_COLOR, "v": VERSION})

    js = """
<script>
(function () {
  var cfg = %s;
  var doc;
  try { doc = window.parent.document; } catch (e) { return; }
  // static/ の配信先。origin から絶対パスで作る（pages/ でも基準がずれない）。
  var base = window.parent.location.origin + "/app/static/";
  var q = "?v=" + cfg.v;

  function ensure(tag, id, attrs) {
    var el = doc.getElementById(id);
    if (!el) {
      el = doc.createElement(tag);
      el.id = id;
      doc.head.appendChild(el);
    }
    Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
  }

  ensure("link", "pwa-manifest", { rel: "manifest", href: base + "manifest.json" + q });
  ensure("link", "pwa-apple-icon",
         { rel: "apple-touch-icon", href: base + "apple-touch-icon.png" + q });
  ensure("meta", "pwa-apple-capable",
         { name: "apple-mobile-web-app-capable", content: "yes" });
  ensure("meta", "pwa-web-capable",
         { name: "mobile-web-app-capable", content: "yes" });
  ensure("meta", "pwa-apple-status",
         { name: "apple-mobile-web-app-status-bar-style", content: "default" });
  ensure("meta", "pwa-apple-title",
         { name: "apple-mobile-web-app-title", content: cfg.name });
  ensure("meta", "pwa-theme-color", { name: "theme-color", content: cfg.theme });
})();
</script>
""" % cfg
    html(js, height=0)
