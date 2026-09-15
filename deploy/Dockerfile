# syntax=docker/dockerfile:1
# Only explicit source/build inputs enter this image. No dot-env, private data,
# founder output, browser profile, database or tunnel credentials are copied.
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web/index.html web/tsconfig.json web/tsconfig.app.json web/tsconfig.node.json web/vite.config.ts ./
COPY web/src ./src
COPY web/public ./public
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
ENV VITE_APP_MODE=beta
RUN test -n "$VITE_SUPABASE_URL" && test -n "$VITE_SUPABASE_ANON_KEY" && npm run build

FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MOBILE_WEB_DIST=/app/web/dist
WORKDIR /app
RUN groupadd --gid 10001 pursuit && useradd --uid 10001 --gid pursuit --no-create-home pursuit
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir '.[web]'
COPY --from=frontend /build/web/dist ./web/dist
COPY scripts/privacy_worker.py ./scripts/privacy_worker.py
USER 10001:10001
EXPOSE 8843
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8843/readyz', timeout=4)"
CMD ["uvicorn", "jobagent.mobile.web:app", "--host", "0.0.0.0", "--port", "8843", "--workers", "1", "--no-access-log", "--no-proxy-headers"]
