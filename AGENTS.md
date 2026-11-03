# AGENTS.md

## 言語ルール

- コミットの説明、PR/Issue のタイトルと本文、コードコメント・docstring、ドキュメントは日本語で書く。
- 例外として、次は日本語化の対象にしない。技術的な識別子、引用、実行時のログ・エラーメッセージ、ワークフローの YAML キー・値 (承認依頼コメントの本文 `Environment approval required` とその `body-includes` の一致キーを含む)。
- 日本語と英数字の間には半角スペースを入れる。

## 概要

Twitter/X にログインし、`ct0` / `auth_token` クッキーを取得して保存するツール。bot 判定を回避するため、patchright (CDP 検知回避パッチ入りの Playwright 互換フォーク) で実 Chromium を Xvfb 上の非ヘッドレスで操作する。クッキーの GitHub Secrets 等への反映は本リポジトリのスコープ外。詳細な使い方は `README.md` を参照。

## 開発コマンド

- 依存のインストール: `pip install -r requirements.txt`
- テスト: `python -m unittest discover -s tests -t .`
- Docker イメージのビルド: `docker build -t twitter-cookie-issuer .`
- 実行は Docker 前提 (`entrypoint.sh` が `xvfb-run -a python3 -m src` を起動する)。`MODE` は `once` (既定) または `daemon`。

## アーキテクチャ

- `src/__main__.py`: エントリーポイント。once モード (環境変数の単一アカウント、`/data/cookies.json` に保存) と daemon モード (`POST /login`、`GET /healthz`、`/data/cookies/{username}.json` に保存) を分岐する。
- `src/login.py`: ログイン、保存済み cookie の検証 (VALID / INVALID / INDETERMINATE の 3 状態)、検証結果のメモリキャッシュ。
- `src/config.py`: once モード用の環境変数読み込み。
- `src/error_reporting.py`: Sentry 初期化と `before_send` による機密値のマスク。
- 終了コード: ログイン失敗は `1`、cookie の有効性が判定不能な場合は `2`。daemon モードでは判定不能を `503` で返す。

## 守るべき不変条件

- cookie の有効性が判定不能 (INDETERMINATE) な場合は、フルログインへフォールバックせず、既存の `ct0` / `auth_token` を変更しない。
- `password` / `otp_secret` / `ct0` / `auth_token` / TOTP コードをログ・診断出力・Sentry イベントに出さない。`error_reporting.py` の `_SENSITIVE_KEYS` によるキー名マスクと repr / JSON の正規表現マスクを弱めない。`TWITTER_PASSWORD` / `TWITTER_OTP_SECRET` の実値置換は環境変数で渡した認証情報にしか効かず、daemon モード (認証情報がリクエストボディ) ではキー名マスクと正規表現マスクで守られる。そのため新しい機密変数は `_SENSITIVE_KEYS` に該当するキー名にする。
- `/login` の診断ログ (`_write_diagnostic_log`) には機密値を渡さない。
- daemon モードの `username` は `_USERNAME_PATTERN` で `[A-Za-z0-9_]{1,15}` に制限する。キャッシュのファイル名に直接使うため、パストラバーサルを避ける目的である。
- daemon モードのエンドポイントには認証機構がない。サーバーは `0.0.0.0` で待ち受けるため、docker network の内部限定公開などネットワーク分離を前提とする。この前提を崩す説明や設定を追加しない。
- ログインは `_login_lock` で同時 1 件に制限し、待機は `LOGIN_LOCK_TIMEOUT_SECONDS` で bounded にする。
- 実在アカウントの認証情報、cookie、スクリーンショット、`data/` 配下、`KNOWLEDGE.md`、`trash/` をコミットしない。bot 判定回避手法の詳細な調査ログは公開リポジトリに含めない。
- 実行ホストの実所在地に合う `TZ` を指定する運用を前提とする。IP とタイムゾーンの不一致は不正検知のシグナルになる。
