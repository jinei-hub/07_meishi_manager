# 07_meishi_manager — 名刺管理アプリ（Sansan風）

## 概要
名刺を撮影/アップロードすると Claude Vision が氏名・会社・役職・連絡先・住所を
自動抽出し、写真の隣に項目ごとにコピー可能な形で表示するWebアプリ。
登録名刺は一覧・検索・編集・削除でき、CSV / vCard でエクスポートできる。

## 技術スタック
- UI: Streamlit（マルチページ）
- 抽出: Anthropic Claude Vision（structured outputs / JSON schema、複数名刺を一度に検出）
- 保存: SQLAlchemy。ローカルは SQLite、クラウドは Neon(PostgreSQL)。`DATABASE_URL` で切替
- 画像: Pillow（+ pillow-heif で HEIC 対応）。**画像はDBにBLOB保存**（クラウドでも消えない）
- 設定: `config.py` が `.env`（ローカル）と `st.secrets`（Streamlit Cloud）を環境変数へ橋渡し

## ディレクトリ
```
main.py                 エントリ（登録ページ）: streamlit run main.py
config.py               .env / st.secrets を環境変数へ橋渡し
ocr/extract.py          画像 → 名刺リスト（Claude Vision、複数枚対応）
db/models.py            MeishiCard モデル（image=BLOB）・FIELDS（項目キーと日本語ラベル）
db/session.py           init_db / SessionLocal（SQLite/Postgres両対応）
services/cards.py       CRUD（画像はDBにBLOB保存）
services/export.py      CSV / vCard 生成
services/imaging.py     画像を JPEG bytes に正規化（EXIF補正・縮小・HEIC）
pages/1_一覧・検索.py    一覧/検索/編集/削除/エクスポート
pages/2_設定.py          APIキー状況・件数・使い方
data/                   ローカルSQLite用（meishi.db、gitignore）
DEPLOY.md               クラウド公開手順（Neon + Streamlit Cloud）
```

## クラウド公開
スマホから常時利用するには DEPLOY.md を参照（Neon の PostgreSQL + Streamlit Community Cloud）。
Secrets に `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` / `DATABASE_URL` を設定する。

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

## 設計メモ / 規約
- 抽出項目（追加時はここを直す）: `db/models.py` の `FIELDS` が唯一の定義源。
  UI表示・CSVヘッダ・編集フォームはすべて `FIELDS` を参照する。
- Claude呼び出しは `02_sns_analyser/analysis/report.py` のパターンを踏襲
  （`thinking=adaptive` / `output_config` の json_schema / 例外別ハンドリング）。
- 読み取れない項目は空文字 `""`。値の捏造は system プロンプトで禁止。
- 画像は API コスト・処理時間のため長辺2000pxに縮小してから送信。

## 注意
- Vision 呼び出しは1名刺=1リクエストで API コストが発生する。
- ローカルSQLite前提。複数PC共有は将来課題（Google Drive同期等、ROADMAP参照）。
