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

# ── Trouver l'URL du CSV sur le portail de données ouvertes ───────────────────
echo "$LOG Recherche de l'URL du CSV..."
CSV_URL=$(python3 - <<'EOF'
import urllib.request, re, sys
try:
    html = urllib.request.urlopen("https://donnees.montreal.ca/dataset/cyclistes").read().decode("utf-8")
    m = re.search(r'href="([^"]+)"[^>]*title="T\u00e9l\u00e9charger"', html, re.DOTALL)
    if not m:
        # Essai avec l'attribut dans l'autre ordre
        m = re.search(r'title="T\u00e9l\u00e9charger"[^>]*href="([^"]+)"', html, re.DOTALL)
    if m:
        print(m.group(1))
    else:
        print("", end="")
        sys.stderr.write("URL du CSV introuvable sur la page.\n")
        sys.exit(1)
except Exception as e:
    sys.stderr.write(f"Erreur lors du scraping : {e}\n")
    sys.exit(1)
EOF
)
echo "$LOG URL trouvée : $CSV_URL"

# ── Télécharger le nouveau CSV ─────────────────────────────────────────────────
echo "$LOG Téléchargement du CSV..."
curl -fsSL "$CSV_URL" -o cyclistes.csv
echo "$LOG CSV téléchargé ($(du -sh cyclistes.csv | cut -f1))"

# ── Générer le HTML ────────────────────────────────────────────────────────────
echo "$LOG Génération du HTML..."
python3 genMap.py

# ── Valider les données ────────────────────────────────────────────────────────
echo "$LOG Validation des données..."
python3 test_data.py

# ── Publier sur GitHub ─────────────────────────────────────────────────────────
echo "$LOG Vérification des changements..."
git add index.html

if git diff --staged --quiet; then
    echo "$LOG Aucun changement dans index.html — rien à publier."
else
    git commit -m "Mise à jour automatique — $(date '+%Y-%m-%d')"
    git push origin HEAD
    echo "$LOG Publié avec succès sur GitHub."
fi

echo "$LOG ── Mise à jour terminée ──"
