FROM python:3.11-slim

ENV TZ=America/Montreal

# Dépendances système
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    cron \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Dépendances Python
RUN pip install --no-cache-dir tqdm

# Scripts
WORKDIR /app
COPY update.sh entrypoint.sh /app/
RUN chmod +x /app/update.sh /app/entrypoint.sh

# Cron : tous les jours à 08h15 heure de Montréal (America/Montreal)
RUN echo "15 8 * * * root . /etc/environment && /app/update.sh >> /var/log/update.log 2>&1" \
    > /etc/cron.d/velo-cron \
    && chmod 0644 /etc/cron.d/velo-cron

RUN touch /var/log/update.log

ENTRYPOINT ["/app/entrypoint.sh"]
