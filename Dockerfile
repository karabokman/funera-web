# syntax=docker/dockerfile:1
# Stage 1: build the stylesheet and copy the self-hosted font files.
FROM node:22-slim AS assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY src/static ./src/static
RUN npm run fonts:copy && npm run css:build

# Stage 2: the application image.
FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY src ./src
COPY --from=assets /build/src/static/css ./src/static/css
COPY --from=assets /build/src/static/fonts ./src/static/fonts
RUN useradd --system --no-create-home --uid 10001 funera && mkdir -p /app/staticfiles && chown funera /app/staticfiles
USER funera
WORKDIR /app/src
# collectstatic needs valid production settings, but not real secrets: build-time placeholders only.
RUN SECRET_KEY=build-only-placeholder-key-that-is-long-enough-0123456789 ALLOWED_HOSTS=build.invalid \
    SITE_URL=https://build.invalid python manage.py collectstatic --noinput
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=4).status == 200 else 1)"
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
