# 07_meishi_manager — 名刺管理アプリ（Sansan風）

## 概要
名刺を撮影/アップロードすると Claude Vision が氏名・会社・役職・連絡先・住所を
自動抽出し、写真の隣に項目ごとにコピー可能な形で表示するWebアプリ。
登録名刺は一覧・検索・編集・削除でき、CSV / vCard でエクスポートできる。
さらに、登録した相手あての「お礼メール」を3パターンから選んで **Gmail の下書き**として作れる
（作るのは下書きのみ。このアプリから送信することは無い）。

## 技術スタック
- UI: Streamlit（マルチページ）
- 抽出: Anthropic Claude Vision（structured outputs / JSON schema、複数名刺を一度に検出）
- 保存: SQLAlchemy。ローカルは SQLite、クラウドは Neon(PostgreSQL)。`DATABASE_URL` で切替
- 画像: Pillow（+ pillow-heif で HEIC 対応）。**画像はDBにBLOB保存**（クラウドでも消えない）
- 設定: `config.py` が `.env`（ローカル）と `st.secrets`（Streamlit Cloud）を環境変数へ橋渡し
- メール: Gmail API（スコープは `gmail.compose` のみ）。文面はテンプレ、微調整だけ Claude

## ディレクトリ
```
main.py                 エントリ（登録ページ）: streamlit run main.py
config.py               .env / st.secrets を環境変数へ橋渡し
auth.py                 アプリ全体のパスワードロック（全ページ先頭で require_login）
theme.py                会社サイト(dipilot.jp)に寄せた配色・CSS（全ページ先頭で apply_theme）
ocr/extract.py          画像 → 名刺リスト（Claude Vision、複数枚対応）
db/models.py            MeishiCard モデル（image=BLOB）・FIELDS（項目キーと日本語ラベル）
db/session.py           init_db / SessionLocal（SQLite/Postgres両対応）
services/cards.py       CRUD（画像はDBにBLOB保存）
services/export.py      CSV / vCard 生成
services/imaging.py     画像を JPEG bytes に正規化（EXIF補正・縮小・HEIC）
services/mail_log.py    お礼メール下書きの作成履歴（mail_draft_logs）
mail/templates.py       お礼メール3パターンの文面（文言を直すならここ）
mail/compose.py         自由記述の指示を Claude で反映（テンプレの最小編集）
mail/gmail.py           OAuth・EmailMessage 組み立て・下書き作成
mail/ui.py              一覧ページに差し込む UI ブロック（render_mail_section）
tools/gmail_auth.py     初回OAuth（Macで1回だけ実行）→ refresh_token を出力
pages/1_一覧・検索.py    一覧/検索/編集/削除/エクスポート + お礼メール下書き
pages/2_お礼メール履歴.py  下書き作成の履歴（絞り込み・相手ごとの回数）
pages/3_設定.py          APIキー状況・アクセス制限・Gmail接続テスト・件数・使い方
data/                   ローカルSQLite用（meishi.db、gitignore）
DEPLOY.md               クラウド公開手順（Neon + Streamlit Cloud）
```

## クラウド公開
スマホから常時利用するには DEPLOY.md を参照（Neon の PostgreSQL + Streamlit Community Cloud）。
Secrets に `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` / `DATABASE_URL` を設定する。
Gmail を使う場合は `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GMAIL_REFRESH_TOKEN` も追加。

## セットアップ / 起動
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # ANTHROPIC_API_KEY を記入（02_sns_analyser の値を流用可）
streamlit run main.py --server.port 8502   # 8501 は 02_sns_analyser が使う
```

## 環境変数（.env）
- `ANTHROPIC_API_KEY` … 必須。名刺の読み取りに使用
- `ANTHROPIC_MODEL` … 既定 `claude-opus-4-8`
- `DATABASE_URL` … 既定 `sqlite:///data/meishi.db`
- `APP_PASSWORD` … **クラウドでは必須**。アプリを開くのに要るパスワード。
  未設定だと URL を知る誰でも名刺データを閲覧できる（画面に警告が出続ける）。
- `MAIL_SIGNATURE` … メール署名。**API で作った下書きには Gmail の署名設定は適用されない**
  （Gmail の署名は画面から「作成」したときにブラウザが挿入するもの。2026-09-10 に実機で確認）。
  本文に署名を入れるにはここに設定する。未設定なら署名なし。改行は `\n` で1行に書く。
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GMAIL_REFRESH_TOKEN`
  … 任意。お礼メールの下書き作成に使う。`python3 tools/gmail_auth.py` で取得する。
  未設定でも名刺の登録・一覧・検索は普通に動く（メール機能だけが無効になる）。

## セキュリティ（このリポジトリは公開されている）
- **リポジトリに個人情報を書かない。** 署名の住所・電話は `MAIL_SIGNATURE`、
  パスワードは `APP_PASSWORD` と、すべて環境変数に逃がす。
  `mail/templates.py` の `DEFAULT_SIGNATURE` に載せてよいのは会社サイトに出ている情報だけ。
- **名刺データは取引先の個人情報。** 全ページの先頭で `require_login()` を呼ぶこと。
  ページを追加したら忘れずに入れる（`st.set_page_config` の直後）。
- `auth.py` の突き合わせは必ず bytes で行う。`hmac.compare_digest` は
  非ASCII文字列を受け付けず、日本語のパスワードでクラッシュする。
- **Streamlit Cloud はアプリに Cookie を渡さない**（`st.context.cookies` が常に空。
  2026-09-13 に実測）。そのため Cookie でログインを長期保持する方式は使えない。
  保持はサーバ側セッションが生きている間だけで、アプリ再起動で切れる。
  恒久的に入力を省くなら `st.login()`（OIDC）への移行が必要。
- `APP_PASSWORD` を変えると全端末のログインが即座に無効になる（端末紛失時の対処）。
- Streamlit Cloud を private リポジトリで動かすには GitHub の `repo` スコープが要る。
  承認していない状態で private にすると clone に失敗してアプリが落ちる（2026-09-08 に発生）。
- **`.streamlit/config.toml` に `port` を書かないこと。** 配色（`[theme]`）を
  クラウドへ届けるためこのファイルはコミットするが、`port = 8502` を入れると
  クラウドがそのポートで起動してヘルスチェックに失敗し
  `Oh no. Error running app.` になる（2026-09-09 に発生）。
  ローカルで 8502 を使うときは起動時に渡す: `streamlit run main.py --server.port 8502`

## 設計メモ / 規約
- 抽出項目（追加時はここを直す）: `db/models.py` の `FIELDS` が唯一の定義源。
  UI表示・CSVヘッダ・編集フォームはすべて `FIELDS` を参照する。
- Claude呼び出しは `02_sns_analyser/analysis/report.py` のパターンを踏襲
  （`thinking=adaptive` / `output_config` の json_schema / 例外別ハンドリング）。
- 読み取れない項目は空文字 `""`。値の捏造は system プロンプトで禁止。
- **撮影は端末のカメラアプリを使わせる。** `st.camera_input`（ブラウザ内蔵カメラ）は
  解像度が低くピントも合わせられず、名刺の小さな文字が潰れる。`st.file_uploader` なら
  スマホで「写真を撮る」を選べてセンサーの実力で撮れるので、そちらを主動線にしている。
  内蔵カメラは PC 用に toggle の裏へ。
  **`st.camera_input` を `st.expander` の中に置かないこと。** expander の中身は
  畳んだ状態でも読み込まれるため、隠れたままカメラの起動に失敗し、開いても
  再試行されない（＝映らない。2026-09-13 に実機で発生）。toggle なら ON にした
  時点で初めて表示された状態で読み込まれるので確実に起動する。
- **送信サイズはモデルの上限に合わせる（長辺 2576px）。** Claude 4.7 以降は
  高解像度ティアで長辺 2576px / 視覚トークン 4784 まで扱える（それ未満に縮めると
  自分で解像度を捨てることになる。2000px だった頃はこれで損をしていた）。
  公式の上限は `services/imaging.py` の `MAX_SIDE` に根拠コメント付きで置いてある。
- JPEG は `quality=92, subsampling=0`。強い圧縮は文字を潰すと公式ドキュメントも警告している。
- 低解像度（長辺1200px未満）の画像は、読み取る前に画面で警告する。

### 見た目（theme.py / .streamlit/config.toml）
- 配色は会社サイトの実測値。出典は `dipilot-wp/dipilot-theme/assets/css/main.css` の `:root`
  （`--dp-blue #008afc` / `--dp-navy #1a2b4a` / `--dp-text #324158` / `--dp-bg #f5f5f5`）。
- **和文に明朝を使わない。** 本家も欧文だけ Cormorant Garamond で、和文は端末標準の
  ゴシック（和文 Web フォントは数MBで初期表示が遅くなるため）。同じ判断を踏襲する。
- 大枠の色は `config.toml` の `[theme]`、そこで届かない部分（見出し色・タイトル下の
  罫線・サイドバーの選択表示）だけ `theme.py` の CSS で補う。

### お礼メール（mail/）
- **送信は絶対にしない**。担保は4層:
  1. スコープは `gmail.compose` のみ（`gmail.send` を要求しない）
  2. `mail/` に `drafts().send` / `messages().send` を書かない。
     レビュー基準: `grep -rn "messages()\.send\|drafts()\.send" mail/` のヒットが
     **`mail/gmail.py` 冒頭の説明コメント2行だけ**であること（実行コードは0件）
  3. UI のボタンラベルに「送信」の語を使わない
  4. 実際の送信は人が Gmail で下書きを開いて自分で押す
- 文面の定義源は `mail/templates.py` の `PATTERNS`。文言・署名を直すならこのファイルだけ。
  UI（パターン固有の入力欄）は `PATTERNS[*]["inputs"]` を読んで自動で組み立てる。
- **自由記述が空なら Claude を呼ばない**（コスト・レイテンシ・「指示していないのに
  文面が変わる」事故の防止）。テンプレだけで完結して成立させること。
- `mail/compose.py` は「ゼロから書かせる」のではなく「最小限の編集」。
  日付・金額・約束事は指示に明記されたものだけを書かせ、署名は一字一句保持させる。
- 生成結果は必ず編集可能なプレビューに入れ、**そこの現在値**を下書きにする（承認ゲート）。
- google 系ライブラリは**関数内 import**。トップレベルにすると未インストール環境で
  ページ全体が落ちる（`ocr/extract.py` の anthropic と同じ理由）。
- `mail_draft_logs` は新テーブルなので `create_all()` で自動作成される。
  Alembic 未導入のため、**既存テーブルへの列追加はできない**（手動 ALTER が必要）。
  項目を足したくなったら新テーブルを切る方が安全。
- `st.expander` ではなく `st.toggle` で開閉する（expander の開閉状態は再実行をまたいで
  保持されるとは限らず、生成したプレビューが勝手に畳まれるため）。

## 注意
- Vision 呼び出しは1名刺=1リクエストで API コストが発生する。
- **OAuth 同意画面が「外部 + テスト」だと refresh_token が7日で失効する。**
  「内部」または「本番環境に公開」にしておくこと。失効時は設定ページの
  「🔌 Gmail 接続テスト」で検知できる。
- ローカルSQLite前提。複数PC共有は将来課題（Google Drive同期等、ROADMAP参照）。
