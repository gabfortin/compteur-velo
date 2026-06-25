#!/bin/bash
set -euo pipefail

LOG="[$(date '+%Y-%m-%d %H:%M:%S')]"
WORK_DIR="/tmp/repo"

echo "$LOG ── Début de la mise à jour ──"

# ── Cloner ou mettre à jour le dépôt ──────────────────────────────────────────
if [ -d "$WORK_DIR/.git" ]; then
    echo "$LOG Pull des derniers changements..."
    git -C "$WORK_DIR" pull --quiet
else
    echo "$LOG Clonage du dépôt..."
    git clone --quiet "https://$GITHUB_TOKEN@github.com/$GITHUB_REPO.git" "$WORK_DIR"
fi

cd "$WORK_DIR"

git config user.email "$GIT_EMAIL"
git config user.name "$GIT_NAME"
git remote set-url origin "https://$GITHUB_TOKEN@github.com/$GITHUB_REPO.git"

# ── Télécharger les données (logique partagée avec le workflow GitHub Actions) ─
chmod +x fetch_data.sh
./fetch_data.sh
source .fetch_status.env

# ── Rien de nouveau → on arrête ───────────────────────────────────────────────
if [ "$CSV_CHANGED" = false ] && [ "$BIXI_CHANGED" = false ] && [ "$COMPTEURS_CHANGED" = false ]; then
    echo "$LOG Aucune donnée nouvelle — rien à publier."
    exit 0
fi

# ── Générer le HTML ────────────────────────────────────────────────────────────
echo "$LOG Génération du HTML..."
python3 genMap.py

# ── Incrémenter la version (patch) ────────────────────────────────────────────
if [ -f version.txt ]; then
    CURRENT_VERSION=$(cat version.txt | tr -d '[:space:]')
    IFS='.' read -r V_MAJOR V_MINOR V_PATCH <<< "$CURRENT_VERSION"
    V_PATCH=$((V_PATCH + 1))
    NEW_VERSION="$V_MAJOR.$V_MINOR.$V_PATCH"
    echo "$NEW_VERSION" > version.txt
    echo "$LOG Version incrémentée : $CURRENT_VERSION → $NEW_VERSION"
fi

# ── Valider les données ────────────────────────────────────────────────────────
echo "$LOG Validation des données..."
python3 test_data.py

# ── Publier sur GitHub ─────────────────────────────────────────────────────────
echo "$LOG Vérification des changements..."
git add index.html
# Persister le cache de géocodage Nominatim si de nouveaux compteurs ont été ajoutés
[ -f velo_meta_cache.json ] && git add velo_meta_cache.json
[ -f version.txt ] && git add version.txt

if git diff --staged --quiet; then
    echo "$LOG Aucun changement dans index.html — rien à publier."
else
    git commit -m "Mise à jour automatique — $(date '+%Y-%m-%d')"
    # Retry push (jusqu'à 3 tentatives) pour absorber les erreurs serveur GitHub transitoires
    PUSHED=false
    for attempt in 1 2 3; do
        if git push origin HEAD; then
            PUSHED=true
            break
        fi
        echo "$LOG Push échoué (tentative $attempt/3) — nouvelle tentative dans $((attempt * 15))s..."
        sleep $((attempt * 15))
    done
    if [ "$PUSHED" = true ]; then
        echo "$LOG Publié avec succès sur GitHub."
    else
        echo "$LOG ERREUR : push échoué après 3 tentatives. Le commit est conservé localement."
        exit 1
    fi
fi

echo "$LOG ── Mise à jour terminée ──"
