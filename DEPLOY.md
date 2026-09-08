# スマホでも常時使う — クラウド公開手順（Neon + Streamlit Cloud）

Macを起動していなくても、スマホから常にアクセスできるようにする手順です。
所要時間は約15〜20分。すべて無料・クレジットカード不要。

構成:
- 公開: **Streamlit Community Cloud**（無料）
- データ保存: **Neon**（無料のPostgres）
- 画像・名刺データはすべてNeonに保存されるので、再デプロイしても消えません。

---

## ステップ1: Neon で無料データベースを作る（約5分）
1. https://neon.tech にアクセス →「Sign up」→ GitHubアカウントでログイン
2. プロジェクトを作成（名前は `meishi` など、リージョンは Tokyo/Singapore など近い所）
3. 作成後に表示される **Connection string（接続文字列）** をコピー
   - `postgresql://....neon.tech/neondb?sslmode=require` の形
   - 「Pooled connection」でOK。**この文字列は後で使うのでメモ**（パスワードを含むので他人に見せない）

---

## ステップ2: コードを GitHub に上げる（約5分）
このフォルダ `07_meishi_manager` をGitHubのリポジトリにpushします（プライベートでOK）。
既存のバックアップ体制を使う場合はそれに追加。手動なら:

```
cd "/Users/jinei/Desktop/Claude Code/07_meishi_manager"
git init
git add .
git commit -m "名刺管理アプリ"
# GitHubで空のリポジトリを作成し、その後:
git remote add origin https://github.com/<あなた>/meishi-manager.git
git branch -M main
git push -u origin main
```

⚠️ `.env` と `.streamlit/secrets.toml` は `.gitignore` 済みなので、
APIキーやDBパスワードはGitHubには上がりません（安全）。

---

## ステップ3: Streamlit Community Cloud で公開（約5分）
1. https://share.streamlit.io にアクセス → GitHubでログイン
2. 「Create app」→ 先ほどのリポジトリを選択
   - Branch: `main`
   - Main file path: `main.py`
3. 「Advanced settings」→「Secrets」に、以下を**値を入れて**貼り付け:
   ```
   ANTHROPIC_API_KEY = "sk-ant-（あなたのキー）"
   ANTHROPIC_MODEL = "claude-opus-4-8"
   DATABASE_URL = "（ステップ1でコピーしたNeonの接続文字列）"
   ```
4. 「Deploy」を押す → 数分でビルド完了。`https://xxxx.streamlit.app` のURLが発行される
5. そのURLをスマホのホーム画面に追加すればアプリのように使えます

---

## ステップ3.5: パスワードロック（必須）

このリポジトリは公開されているため、**アプリ側にパスワードを掛けないと
URLを知っている人が誰でも名刺データを閲覧できます**。必ず設定してください。

Streamlit Cloud のアプリ →「⋮」→ Settings →「Secrets」に1行追加して Save:

```toml
APP_PASSWORD = "（自分で決めた強めのパスワード）"
```

設定できたかは「⚙️ 設定」ページの「アクセス制限」で確認できます。
`パスワードロック: 有効` と出ていればOKです。

あわせて、メール署名（住所・電話）もリポジトリではなく Secrets に置きます:

```toml
MAIL_SIGNATURE = "--\n\n_______________________________\n\n株式会社DiPilot 奥河 鎮映 / Jinei Okugawa\n\nAddress：〒000-0000 ...\nTEL：000-0000-0000\nEmail：jinei@dipilot.jp\nWeb：https://dipilot.jp/\n_______________________________"
```

改行は `\n` と書いて**1行に収めてください**（TOMLの複数行は壊れやすいため）。
未設定でも動きますが、署名が会社名・メール・Webだけの簡易版になります。

---

## ステップ4: Gmail のお礼メール下書き（任意・あとから追加でOK）

名刺の相手あてに「お礼メール」の下書きを Gmail に作る機能を使う場合だけ必要です。
設定しなくても名刺の登録・一覧・検索は普通に動きます。

### 4-1. Google Cloud コンソール（ブラウザ・約5分）
1. https://console.cloud.google.com/ を開き、右上のアカウントが **jinei@dipilot.jp** か確認
2. 上部のプロジェクト選択で **`alien-device-499902-n3`** を選ぶ（04_invoice_uploader と同じもの）
3. 「APIとサービス」→「ライブラリ」→ `Gmail API` を検索 →「**有効にする**」
4. 「APIとサービス」→「**OAuth同意画面**」で **User type** を確認する
   - 「**内部**」と出ていれば ✅ そのままでよい
   - 「**外部**」＋「**テスト**」の場合は、ログイン状態が **7日で切れる**。
     「**本番環境に公開**」を押しておく（自分専用アプリなので警告は続行してよい）
5. 「データアクセス」→「スコープを追加または削除」→ `gmail.compose` にチェック →「更新」→「保存」
6. OAuth クライアント ID は**既存のものをそのまま使う**（新規作成もリダイレクトURI追加も不要）

### 4-2. Mac で1回だけ認証する（ターミナルに打つ）
```bash
cd "/Users/jinei/Desktop/Claude Code/07_meishi_manager"
source .venv/bin/activate
pip install -r requirements.txt
python3 tools/gmail_auth.py
```
ブラウザが開くので **jinei@dipilot.jp** を選び、「Gmail の下書きの作成」を**許可**。
成功するとターミナルに、`.env` 用と Streamlit Secrets 用の3行が表示されます。

### 4-3. 値を登録する
- **ローカル用**: `.env` の末尾に、表示された `GOOGLE_CLIENT_ID=` 以下の3行を貼る（ファイルに書く）
- **クラウド用**: Streamlit Cloud のアプリ →「⋮」→ Settings →「Secrets」に、
  TOML 形式で表示された3行を既存の下に貼って **Save**（画面に貼る）

```toml
GOOGLE_CLIENT_ID = "xxxxxxxx.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "GOCSPX-xxxxxxxx"
GMAIL_REFRESH_TOKEN = "1//0exxxxxxxx"
```

⚠️ この3つの値は GitHub にも、チャットやメールにも貼らないこと。

### 4-4. 動作確認
アプリの「⚙️ 設定」ページ →「🔌 Gmail 接続テスト」→ `✅ 接続中: jinei@dipilot.jp` が出れば完了。
以降は「📋 一覧・検索」で名刺を選び、詳細欄の「✉️ お礼メール」から下書きを作れます。

> このアプリが持つ権限は「下書きの作成」だけです。アプリからメールを送信することはありません。
> 送信は Gmail で下書きを開き、内容を確認したうえでご自身で行ってください。

---

## できること（クラウド版）
- スマホ・PCどこからでもアクセス（Macは不要）
- HTTPSなのでアプリ内の「📷 カメラで撮影」も動作
- 名刺データ・画像はNeonに永続保存

## 注意
- Neon無料枠はストレージ0.5GB。名刺画像は縮小保存のため数千枚は入る想定。
- 公開URLを知っている人は誰でもアクセスできる。**URLは共有相手を限定**するか、
  必要ならログイン機能の追加を検討（別途対応可）。
- APIキーはStreamlitのSecretsにのみ置き、コード/GitHubには絶対に書かない。
- Gmail の認証が切れたら（設定ページの接続テストでエラーが出たら）、
  `python3 tools/gmail_auth.py` をやり直して `GMAIL_REFRESH_TOKEN` を入れ直す。
  頻繁に切れる場合は、OAuth同意画面が「外部＋テスト」のままになっている。
