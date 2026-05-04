FROM python:3.11-slim

ENV TZ=America/Montreal

# Dépendances système
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Supercronic (cron conçu pour Docker)
RUN curl -fsSL https://github.com/aptible/supercronic/releases/download/v0.2.29/supercronic-linux-amd64 \
    -o /usr/local/bin/supercronic && chmod +x /usr/local/bin/supercronic

# Dépendances Python
RUN pip install --no-cache-dir tqdm

# Scripts
WORKDIR /app
COPY update.sh entrypoint.sh crontab /app/
RUN chmod +x /app/update.sh /app/entrypoint.sh

RUN touch /var/log/update.log

ENTRYPOINT ["/app/entrypoint.sh"]
