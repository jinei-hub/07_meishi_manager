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

## できること（クラウド版）
- スマホ・PCどこからでもアクセス（Macは不要）
- HTTPSなのでアプリ内の「📷 カメラで撮影」も動作
- 名刺データ・画像はNeonに永続保存

## 注意
- Neon無料枠はストレージ0.5GB。名刺画像は縮小保存のため数千枚は入る想定。
- 公開URLを知っている人は誰でもアクセスできる。**URLは共有相手を限定**するか、
  必要ならログイン機能の追加を検討（別途対応可）。
- APIキーはStreamlitのSecretsにのみ置き、コード/GitHubには絶対に書かない。
