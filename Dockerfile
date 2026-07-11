FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /app

# ベースイメージに xvfb が含まれていない場合に備えてインストールする。
# tini は PID 1 として xvfb-run (シグナル/ゾンビ処理を正しく行わない) を
# ラップし、`docker run --init` なしでもハングしないようにするために導入する。
# バージョンは固定しない (Ubuntu noble のアーカイブは旧バージョンを保持しない
# ため、固定するとアーカイブの更新でビルドが壊れる方が実害が大きいと判断)。
# hadolint ignore=DL3008
RUN apt-get update && \
    apt-get install -y --no-install-recommends xvfb tini && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# /data 配下 (once モードのスクリーンショット保存先等) を作成する。
# ホスト側ボリュームマウント時のUID/GID不一致によるパーミッションエラーを
# 避けるため、rootユーザーのまま実行する (/data の所有権調整が不要になる)。
RUN mkdir -p /data/screenshots /data/cookies

RUN patchright install chromium

ENTRYPOINT ["/usr/bin/tini", "--", "./entrypoint.sh"]
