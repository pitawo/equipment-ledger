FROM python:3.11.16-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Cloud Run は PORT 環境変数でポートを渡す（既定 8080）。
# $PORT を展開させるためシェル形式で書く（JSON 配列で書くと文字列 "$PORT" のまま渡る）。
# ワーカーは1つに固定する。複数にすると Socket.IO の配信先とメモリ上の
# 見学用サンドボックスがワーカーごとに分かれ、人によって見えるものが変わる。
ENV PORT=8080
CMD gunicorn -w 1 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT app:app
