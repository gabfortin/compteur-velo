#!/bin/bash
set -e

echo "Exécution initiale au démarrage..."
/app/update.sh 2>&1 | tee -a /var/log/update.log || true

echo "Prochain run planifié : $(awk 'NF {print $1,$2,$3,$4,$5; exit}' /app/crontab)"
echo "Démarrage du planificateur cron..."

exec supercronic /app/crontab
