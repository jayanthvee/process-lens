# ProcessLens, deployable: one image, two start commands (see services/deploy.py and
# internal/docs/DEPLOY.md). The web app is built in a Node stage because the API serves
# apps/web/dist (services/api/main.py, WEB_DIST) and nothing may be built at start-up.

# ---- Stage 1: the web app ---------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /build
COPY apps/web/package.json apps/web/package-lock.json ./apps/web/
RUN npm ci --prefix apps/web
COPY apps/web ./apps/web
RUN npm run build --prefix apps/web

# ---- Stage 2: the runtime ---------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# The project installs from pyproject.toml (no dev extras: no Playwright, no browsers); the
# packages/ tree rides on PYTHONPATH, the way the checkout itself runs.
COPY pyproject.toml README.md ./
COPY services ./services
COPY packages ./packages
COPY db ./db
COPY scripts ./scripts
COPY --from=web /build/apps/web/dist ./apps/web/dist

RUN pip install --no-cache-dir .

# Run as a normal user; PORT is what the platform gives us (Railway sets it).
RUN useradd --create-home --uid 10001 processlens
USER processlens
ENV PORT=8000
EXPOSE 8000

CMD ["python", "-m", "services.deploy", "api"]
