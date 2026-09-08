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
pages/2_設定.py          APIキー状況・Gmail接続テスト・件数・下書き履歴・使い方
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
streamlit run main.py
```

## 環境変数（.env）
- `ANTHROPIC_API_KEY` … 必須。名刺の読み取りに使用
- `ANTHROPIC_MODEL` … 既定 `claude-opus-4-8`
- `DATABASE_URL` … 既定 `sqlite:///data/meishi.db`
- `APP_PASSWORD` … **クラウドでは必須**。アプリを開くのに要るパスワード。
  未設定だと URL を知る誰でも名刺データを閲覧できる（画面に警告が出続ける）。
- `MAIL_SIGNATURE` … 任意。メール署名。改行は `\n` で1行に書く。
  未設定なら会社名・メール・Web だけの署名になる（住所と電話はコードに置かない）。
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
- Streamlit Cloud を private リポジトリで動かすには GitHub の `repo` スコープが要る。
  承認していない状態で private にすると clone に失敗してアプリが落ちる（2026-09-08 に発生）。

## 設計メモ / 規約
- 抽出項目（追加時はここを直す）: `db/models.py` の `FIELDS` が唯一の定義源。
  UI表示・CSVヘッダ・編集フォームはすべて `FIELDS` を参照する。
- Claude呼び出しは `02_sns_analyser/analysis/report.py` のパターンを踏襲
  （`thinking=adaptive` / `output_config` の json_schema / 例外別ハンドリング）。
- 読み取れない項目は空文字 `""`。値の捏造は system プロンプトで禁止。
- 画像は API コスト・処理時間のため長辺2000pxに縮小してから送信。

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
