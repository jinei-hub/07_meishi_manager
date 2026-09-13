"""「撮る」ボタンで端末のカメラアプリを直接開かせる。

■ なぜ st.camera_input を使わないか
  ブラウザ内蔵カメラは (1) 解像度が低く名刺の小さな文字が潰れる
  (2) ピントを合わせられない (3) 既定が内カメラ (4) 小さな枠の中でしか見えない。
  どれも Streamlit 側から制御できない（2026-09-13 に実機で確認）。

■ 代わりにやっていること
  file_uploader が描く <input type="file"> に capture="environment" を付ける。
  こうするとタップした瞬間に端末のカメラアプリが開く:
    - 全画面（iPhone の画面いっぱい）
    - 外カメラ（environment = 背面）
    - センサーの実力どおりの解像度、タップでピント合わせも可能
  「写真を撮る」を選ぶ一手間も無くなる。

  属性の付与は JS でしかできないため（Streamlit に指定する引数が無い）、
  高さ0の不可視コンポーネントから親フレームの DOM を触る。
  Streamlit は再描画のたびに DOM を作り直すので MutationObserver で付け直す。
"""

from __future__ import annotations

import json

import streamlit as st

CAMERA_MARKER = "名刺を撮る"


def use_rear_camera(marker: str = CAMERA_MARKER) -> None:
    """ラベルに marker を含む file_uploader を「カメラ直結」にする。"""
    from streamlit.components.v1 import html

    js = """
<script>
(function () {
  var marker = %s;
  function apply() {
    var doc;
    try { doc = window.parent.document; } catch (e) { return; }
    doc.querySelectorAll('[data-testid="stFileUploader"]').forEach(function (box) {
      var input = box.querySelector('input[type="file"]');
      if (!input) return;
      if ((box.innerText || '').indexOf(marker) !== -1) {
        // environment = 外カメラ。タップで即カメラが全画面で開く
        input.setAttribute('capture', 'environment');
        input.setAttribute('accept', 'image/*');
      } else {
        // もう一方（保存済みの画像を選ぶ方）はカメラに固定しない
        input.removeAttribute('capture');
      }
    });
  }
  apply();
  // Streamlit は再描画のたびに DOM を作り直すので、付け直し続ける
  try {
    new MutationObserver(apply).observe(window.parent.document.body,
                                        {childList: true, subtree: true});
  } catch (e) {}
  [100, 300, 800, 2000].forEach(function (t) { setTimeout(apply, t); });
})();
</script>
""" % json.dumps(marker)
    html(js, height=0)
