# One image for the API and the worker (deploy/compose.yaml runs it with different commands).
# The web app is built in the first stage and served by the API from web/dist.

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build


FROM python:3.14-slim
# git: requirements.txt installs the gateway client from GitHub. stockfish: the chess engine lane.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git stockfish \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPORTS_FOLLOW_STOCKFISH=/usr/games/stockfish
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini ./
COPY migrations/ migrations/
COPY sports_follow/ sports_follow/
COPY --from=web /web/dist web/dist
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
CMD ["uvicorn", "sports_follow.server:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
