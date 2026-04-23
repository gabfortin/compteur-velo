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

# ── Télécharger le CSV BIXI (si une nouvelle version est disponible) ───────────
echo "$LOG Recherche de la dernière version des données BIXI..."
BIXI_URL=$(python3 - <<EOF
import urllib.request, re, sys
from datetime import datetime

year = datetime.now().year
try:
    req = urllib.request.Request(
        "https://bixi.com/en/open-data/",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
    pattern = "https://[^ \"]+DonneesOuvertes%s_[0-9]+\\.zip" % year
    matches = re.findall(pattern, html, re.IGNORECASE)
    if matches:
        best = max(matches, key=lambda u: len(re.search("_([0-9]+)\\.zip", u).group(1)))
        print(best)
    else:
        sys.stderr.write("Aucun fichier BIXI %s trouve sur la page.\n" % year)
        sys.exit(1)
except Exception as e:
    sys.stderr.write("Erreur scraping BIXI : %s\n" % e)
    sys.exit(1)
EOF
) || true

if [ -n "$BIXI_URL" ]; then
    BIXI_URL_CACHE=".bixi_url"
    CACHED_URL=""
    [ -f "$BIXI_URL_CACHE" ] && CACHED_URL=$(cat "$BIXI_URL_CACHE")

    if [ "$BIXI_URL" = "$CACHED_URL" ] && [ -f "bixi.csv" ]; then
        echo "$LOG CSV BIXI déjà à jour — conservation du fichier existant."
    else
        echo "$LOG Téléchargement du ZIP BIXI : $BIXI_URL"
        curl -fsSL "$BIXI_URL" -o bixi.zip
        echo "$LOG ZIP téléchargé ($(du -sh bixi.zip | cut -f1)) — extraction..."
        python3 - bixi.zip bixi.csv <<'PYEOF'
import zipfile, csv, sys, io

zip_path, out_path = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(zip_path) as z:
    csv_files = sorted([
        n for n in z.namelist()
        if n.lower().endswith('.csv') and '__MACOSX' not in n
    ])
    if not csv_files:
        sys.stderr.write("Aucun CSV trouvé dans le ZIP BIXI\n")
        sys.exit(1)
    header_written = False
    with open(out_path, 'w', newline='', encoding='utf-8') as out:
        for fname in csv_files:
            with z.open(fname) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding='utf-8-sig'))
                for i, row in enumerate(reader):
                    if i == 0:
                        if not header_written:
                            out.write(','.join(row) + '\n')
                            header_written = True
                    else:
                        out.write(','.join(row) + '\n')
    print(f"Extrait {len(csv_files)} fichier(s) CSV → {out_path}")
PYEOF
        rm -f bixi.zip
        echo "$BIXI_URL" > "$BIXI_URL_CACHE"
        echo "$LOG CSV BIXI prêt ($(du -sh bixi.csv | cut -f1))"
    fi
else
    echo "$LOG URL BIXI introuvable — données existantes conservées."
fi

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
