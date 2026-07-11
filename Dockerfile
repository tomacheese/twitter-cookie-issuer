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

# ベースイメージに非rootユーザー (pwuser) が存在するか確認し、
# 存在しなければ作成したうえで /data・/app の書き込み権限を与える。
RUN id pwuser || useradd -m pwuser
RUN mkdir -p /data/screenshots /data/cookies && chown -R pwuser:pwuser /data /app
USER pwuser

# patchright は $HOME 配下 (例: ~/.cache/ms-playwright) にブラウザ
# キャッシュを持つため、実行時ユーザーである pwuser に切り替えた後に
# インストールする (root で取得すると pwuser 実行時に見つからない)。
RUN patchright install chromium

ENTRYPOINT ["/usr/bin/tini", "--", "./entrypoint.sh"]
