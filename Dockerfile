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
#
# ワーカーは1つに固定する。複数にすると Socket.IO の配信先とメモリ上の
# 見学用サンドボックスがワーカーごとに分かれ、人によって見えるものが変わる。
#
# スレッドは 100。Socket.IO の接続は1本がスレッドを1つ占有し続けるため、
# スレッド数がそのまま同時に見られる人数の上限になる。Flask-SocketIO 公式も
# gthread ワーカーでは --threads 100 を例示している。Cloud Run の同時実行数は
# 既定 80 なので、それを下回らない値にしておく。
ENV PORT=8080
CMD gunicorn -w 1 --threads 100 --timeout 60 --bind 0.0.0.0:$PORT app:app
