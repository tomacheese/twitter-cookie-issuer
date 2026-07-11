# twitter-cookie-issuer

Twitter/X にログインし、`ct0` / `auth_token` クッキーを自動取得してローカルファイルに保存するツール。

[book000/twitter-rss](https://github.com/book000/twitter-rss) 等、`ct0` / `auth_token` クッキーで
Twitter/X を認証する仕組みに対して、失効しがちなこれらのクッキーを手動取得する運用を自動化することが目的。
クッキーを GitHub Secrets 等へ反映する処理は本リポジトリのスコープ外 (別途シェルスクリプト等で行う想定)。

## 採用方式

X の現行のボット判定 (CDP 検知 + Castle.io によるデバイスフィンガープリンティング) を、
`curl_cffi` のみのブラウザレスな実装で突破することは実用的に不可能と判断した。
そのため、[patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python)
(Chrome DevTools Protocol の検知を回避するパッチが当たった Playwright 互換フォーク) を用いて、
実際の Chromium で x.com のログインページを操作し、ログイン完了後にブラウザの Cookie ストアから
`ct0` / `auth_token` を取得する。

なぜこの方式に至ったか、他に試して失敗した手法の詳細な調査ログは非公開 (ローカルの研究メモとして
運用者の手元にのみ保持しており、本リポジトリには含めていない)。

## セットアップ (Docker)

```bash
docker build -t twitter-cookie-issuer .
```

## 実行 (once モード)

1コンテナ = 1アカウントのログイン試行を行い、成功すると `/data/cookies.json` に
`ct0` / `auth_token` を保存する。特別なフラグ (`--init` 等) は不要。

```bash
docker run --rm \
  -e TWITTER_USERNAME=... -e TWITTER_PASSWORD=... \
  -e TWITTER_EMAIL=... \
  -e TWITTER_OTP_SECRET=... \
  -e HTTP_PROXY=... -e HTTPS_PROXY=... \
  -e TZ=Asia/Tokyo \
  -v "$(pwd)/data:/data" \
  twitter-cookie-issuer
```

| 変数名 | 必須 | 説明 |
|---|---|---|
| `TWITTER_USERNAME` | ✅ | ログインに使うユーザー名 |
| `TWITTER_PASSWORD` | ✅ | パスワード |
| `TWITTER_EMAIL` | 省略可 | 追加の本人確認 (email/phone) 画面が出た場合に使用 |
| `TWITTER_OTP_SECRET` | 2FA 有効時のみ必須 | TOTP の Base32 シークレット |
| `HTTP_PROXY` / `HTTPS_PROXY` | 省略可 | 複数アカウントを別 IP から実行したい場合のみ |
| `TZ` | **強く推奨** | ブラウザに報告させるタイムゾーン。実行ホストの実際の所在地 (= 送信元 IP のジオロケーション) に合わせること (詳細は下記「既知の制約」参照) |

既に有効な `/data/cookies.json` があれば、ブラウザによる再ログインは行わずそのまま
再利用する。

複数アカウントを同一 IP・同一環境から短時間に連続してログインさせると不正検知を
誘発することを確認済み (詳細は下記「既知の制約」参照)。

## 実行 (daemon モード)

HTTP サーバーとして常駐し、リクエストごとに異なるアカウントでログインできる。
**エンドポイント自体に認証機構はないため、外部から直接アクセスできない
ネットワーク構成 (docker network の内部限定公開等) で運用すること。
リクエストボディに生パスワードが乗り、レスポンスに `ct0` / `auth_token` が
乗るため、この前提が崩れると認証情報・トークンが漏洩する。**

```bash
docker run --rm -d \
  -e MODE=daemon -p 8080:8080 \
  -v "$(pwd)/data:/data" \
  twitter-cookie-issuer

curl -X POST http://localhost:8080/login \
  -H "Content-Type: application/json" \
  -d '{"username": "...", "password": "...", "otp_secret": "..."}'
```

成功時は `{"status": "ok", "ct0": "...", "auth_token": "..."}` を返しつつ
`/data/cookies/{username}.json` にも保存する。

失敗時のレスポンスは以下の通り:

| ステータスコード | 状況 |
|---|---|
| `400` | `username`/`password` 等の必須項目が欠如している |
| `409` | 他のログイン処理を実行中 (排他制御により1度に1件のみ処理) |
| `500` | ログイン処理自体が失敗 (message とスクリーンショットのパスを含む) |

また `GET /healthz` はプロセスの生存確認用エンドポイントで、常に `200`
`{"status": "ok"}` を返す。

待受ポートは環境変数 `PORT` (デフォルト `8080`) で変更可能。変更する場合は
`docker run -e PORT=... -p <PORT>:<PORT> ...` のように `-e`/`-p` を揃えて指定する。

## 手動検証手順

1. `docker build` でイメージをビルドする。
2. 実アカウントの認証情報を使い once モードで `docker run` し、
   `/data/cookies.json` が生成されることを確認する。
3. 同条件で再実行し、cookie 再利用によりブラウザログインがスキップされ
   即座に結果が返ることをログ出力で確認する。
4. daemon モードで起動し、`curl` で `POST /login` を叩いて応答・
   `/data/cookies/{username}.json` の生成を確認する。
5. 意図的に誤ったパスワードを渡し、失敗時のスクリーンショットが
   `/data/screenshots/` に保存され、適切なステータスコード/メッセージが
   返ることを確認する。
6. コンテナ起動直後に `起動しました (MODE: once)` または
   `起動しました (MODE: daemon)` のログが出力されることを確認する。

## 既知の制約

- **ベースイメージ既定のタイムゾーンは UTC のため、必ず `-e TZ=...` を
  指定すること。** ベースイメージ (`mcr.microsoft.com/playwright/python`)
  は `tzdata` こそ導入済みだが、`/etc/localtime` が UTC のままになって
  いる。この状態だとブラウザの `Intl.DateTimeFormat` が `UTC` を報告し、
  送信元 IP のジオロケーション (実際の実行ホスト所在地) と食い違う。
  IP とタイムゾーンの不一致は不正検知システムの代表的なシグナルであり
  ([Castle blog](https://blog.castle.io/how-to-detect-browser-time-zone-using-javascript/))、
  ログインブロックの一因になり得ることを確認済み。`-e TZ=Asia/Tokyo` のように
  実行ホストの実所在地に合わせた値を渡せば、Dockerfile 側の変更なしに
  `zoneinfo` データベースから正しく解決される。
- 複数アカウントを同一 IP・同一環境から短時間に連続してログインさせると、
  X 側の不正検知がクレデンシャルスタッフィングのような挙動とみなし、
  汎用エラーでブロックすることを確認済み。複数アカウントを本格運用する場合は
  アカウントごとに異なる `HTTP_PROXY`/`HTTPS_PROXY` を指定すること。
- daemon モードのエンドポイントには認証機構がないため、ネットワーク分離
  必須 (上記参照)。

## ディレクトリ構成

```
src/
  __init__.py   # パッケージマーカー (中身なし)
  __main__.py   # CLIエントリーポイント (once/daemon モード分岐、`python -m src` で起動)
  config.py     # onceモード用の環境変数読み込み
  login.py      # patchright によるログイン・cookie再利用判定
Dockerfile
entrypoint.sh   # xvfb-run 経由で python -m src を起動するラッパー
.dockerignore
requirements.txt
```

調査過程で作成した不採用実装・検証スクリプト群 (`trash/`) と、ボット判定回避手法の
詳細な調査ログ (`KNOWLEDGE.md`) は `.gitignore` で除外されている。運用者のローカル
環境には残るが、手法の詳細を公開リポジトリに含めないよう git 管理には含めていない。

## ライセンス・免責事項

[MIT License](./LICENSE) の下で公開しているが、本ツールは Twitter/X のボット判定を
回避する自動化を行うものであり、**利用は Twitter/X の利用規約に抵触する可能性がある**。
本ツールの利用によって生じたアカウント停止・利用規約違反等の一切の結果について、
作者は責任を負わない。利用は自己責任で行うこと。
