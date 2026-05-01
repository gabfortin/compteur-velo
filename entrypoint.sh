#!/bin/bash
set -e

# Rendre les variables d'environnement disponibles au cron
# (le processus cron ne hérite pas des variables du shell parent)
printenv | grep -E "^(GITHUB_TOKEN|GITHUB_REPO|GIT_EMAIL|GIT_NAME)=" \
  | sed 's/^/export /' > /etc/environment

echo "Variables d'environnement chargées."
echo "Exécution initiale au démarrage..."
/app/update.sh 2>&1 | tee -a /var/log/update.log || true

echo "Prochain run planifié : $(awk 'NF {print $1,$2,$3,$4,$5; exit}' /etc/cron.d/velo-cron)"
echo "Démarrage du planificateur cron..."

exec cron -f
