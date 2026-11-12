FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DIREITO_DATABASE=/var/lib/direito/direito.sqlite3 \
    FORWARDED_ALLOW_IPS=127.0.0.1
WORKDIR /srv/app
# Debian trixie security update DSA-6613-1 for the pinned Python base.
RUN apt-get -o APT::Update::Error-Mode=any update \
    && apt-get install -y --no-install-recommends zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 portal \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin portal \
    && install -d -m 0700 -o portal -g portal /var/lib/direito /srv/public-data
COPY requirements-bootstrap.lock requirements-public.lock ./
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements-bootstrap.lock \
    && python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements-public.lock \
    && python -m pip uninstall -y setuptools
COPY app/*.py app/
COPY web/index.html web/styles.css web/app.js web/bandeira-brasil.svg web/favicon.svg web/
COPY data/catalog.json data/catalog.json
COPY data/guides.json data/guides.json
COPY data/topics.json data/topics.json
COPY data/norm_types.json data/norm_types.json
COPY data/official_portals.json data/official_portals.json
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/ready', timeout=3).close()"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--no-access-log", "--limit-concurrency", "64", "--backlog", "128", "--timeout-keep-alive", "5", "--timeout-graceful-shutdown", "10"]

FROM runtime AS public
LABEL org.opencontainers.image.title="Direito Claro" org.opencontainers.image.version="0.25.0"
ENV DIREITO_PUBLIC_READ_ONLY=1 DIREITO_DATABASE=/srv/public-data/public.sqlite3 \
    DIREITO_DEPLOYMENT_MODE=staging

FROM runtime AS development
COPY scripts/*.py scripts/
