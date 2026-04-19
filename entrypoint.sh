#!/bin/bash
set -e

# Rendre les variables d'environnement disponibles au cron
# (le processus cron ne hérite pas des variables du shell parent)
printenv | grep -E "^(GITHUB_TOKEN|GITHUB_REPO|GIT_EMAIL|GIT_NAME)=" > /etc/environment

echo "Variables d'environnement chargées."
echo "Prochain run : $(grep velo-cron /etc/cron.d/velo-cron | awk '{print $1,$2,$3,$4,$5}')"
echo "Démarrage du planificateur cron..."

exec cron -f
