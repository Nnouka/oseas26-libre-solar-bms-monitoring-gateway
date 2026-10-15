# syntax=docker/dockerfile:1
# Builds MicroPowerManager with the Libre Solar BMS plugin from upstream, in one build.
# Build context: the repo root.   docker build -f integrations/micropowermanager/docker/Dockerfile --target backend .

ARG MPM_REF=d6632495586a9dc5c4f71decdec33f5f960701b8

# ---- upstream MPM + our plugin overlay -------------------------------------------------------
FROM python:3.12-slim AS mpm-source
ARG MPM_REF
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /mpm
RUN git init -q . \
    && git remote add origin https://github.com/EnAccess/micropowermanager.git \
    && git sparse-checkout set --cone src docker \
    && git fetch -q --depth 1 --filter=blob:none origin "${MPM_REF}" \
    && git checkout -q FETCH_HEAD
COPY integrations/micropowermanager/apply.py /overlay/apply.py
COPY integrations/micropowermanager/backend /overlay/backend
COPY integrations/micropowermanager/frontend /overlay/frontend
RUN python /overlay/apply.py /mpm

# ---- backend image: web, queue worker and scheduler all run from this one image ------------------
FROM php:8.5-apache AS backend
RUN apt-get update && apt-get install -y --no-install-recommends \
        git gosu libfreetype6-dev libjpeg-dev libssl-dev libxml2-dev libzip-dev unzip zlib1g-dev \
        supervisor cron \
    && rm -rf /var/lib/apt/lists/*
RUN docker-php-ext-install bcmath exif gd pcntl pdo pdo_mysql zip
RUN docker-php-ext-configure gd --with-freetype --with-jpeg
RUN docker-php-ext-configure pcntl --enable-pcntl
COPY --from=ghcr.io/php/pie:bin /pie /usr/bin/pie
RUN pie install phpredis/phpredis:6.3.0 && docker-php-ext-enable redis
COPY --from=composer:2.8.8 /usr/bin/composer /usr/local/bin/composer

COPY --from=mpm-source /mpm/docker/config/php/php.ini /usr/local/etc/php/php.ini
COPY --from=mpm-source /mpm/docker/config/apache2/sites-enabled/000-default.conf /etc/apache2/sites-enabled/
RUN a2enmod rewrite
COPY --from=mpm-source /mpm/docker/config/supervisor/ /etc/supervisor/conf.d/
COPY --from=mpm-source /mpm/docker/config/cron/crontab /etc/cron.d/cron
RUN chmod 0644 /etc/cron.d/cron /etc/supervisor \
    && mkdir -p /etc/supervisor/logs/horizon \
    && touch /etc/supervisor/logs/supervisord.log /etc/supervisor/logs/horizon/out.log \
             /etc/supervisor/logs/horizon/err.log /var/log/cron.log

COPY --from=mpm-source /mpm/src/backend /var/www/html
WORKDIR /var/www/html
RUN composer install --no-dev --optimize-autoloader

COPY --from=mpm-source /mpm/docker/entrypoint-backend.sh /usr/local/bin/entrypoint-backend.sh
COPY --from=mpm-source /mpm/docker/entrypoint-queue-worker.sh /usr/local/bin/entrypoint-queue-worker.sh
COPY --from=mpm-source /mpm/docker/entrypoint-scheduler.sh /usr/local/bin/entrypoint-scheduler.sh
RUN chmod +x /usr/local/bin/entrypoint-*.sh
VOLUME ["/var/www/html/storage"]
ENTRYPOINT ["/usr/local/bin/entrypoint-backend.sh"]
EXPOSE 80

# ---- frontend: Vue app built into nginx --------------------------------------------------------------
FROM node:20.18-alpine3.20 AS frontend-build
WORKDIR /app
COPY --from=mpm-source /mpm/src/frontend/package*.json ./
RUN npm install
COPY --from=mpm-source /mpm/src/frontend .
RUN npm run build
# single executable application for runtime env injection (same as upstream DockerfileFrontendProd)
RUN echo '{ "main": "node_modules/@import-meta-env/cli/bin/import-meta-env.js", "output": "sea-prep.blob" }' > sea-config.json \
    && node --experimental-sea-config sea-config.json \
    && cp "$(command -v node)" import-meta-env-alpine \
    && npx postject import-meta-env-alpine NODE_SEA_BLOB sea-prep.blob --sentinel-fuse NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2

FROM nginx:1.27-alpine3.20 AS frontend
RUN apk add libstdc++
COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY --from=frontend-build /app/import-meta-env-alpine /usr/local/bin/
COPY --from=frontend-build /app/.env.example /usr/share/nginx/html/
COPY --from=mpm-source /mpm/docker/entrypoint-frontend-prod.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint-frontend-prod.sh
ENTRYPOINT ["/usr/local/bin/entrypoint-frontend-prod.sh"]
EXPOSE 80
