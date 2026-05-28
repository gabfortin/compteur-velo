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

CSV_CHANGED=false
BIXI_CHANGED=false
COMPTEURS_CHANGED=false

# ── Récupérer les URLs via l'API CKAN (dataset velos-comptage) ────────────────
# Les deux fichiers (SUM + Éco-Compteur) sont dans le même dataset depuis 2026.
# L'API retourne du JSON structuré — plus fiable que le scraping HTML.
echo "$LOG Interrogation de l'API CKAN pour le dataset velos-comptage..."
CKAN_URLS=$(python3 - <<'EOF'
import urllib.request, json, re, sys
from datetime import datetime

year = datetime.now().year
try:
    # Le slug "velos-comptage" nécessite une auth ; l'UUID est public.
    req = urllib.request.Request(
        "https://donnees.montreal.ca/api/3/action/package_show?id=142ff2e9-7d0a-47d6-b4f6-dfeb97041daf",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())
    if not data.get('success'):
        raise Exception("Réponse API invalide")
    resources = data['result']['resources']

    csv_url = ""
    compteurs_url = ""
    for r in resources:
        url = r.get('url', '')
        if not csv_url and url.lower().endswith('cyclistes.csv'):
            csv_url = url
        if not compteurs_url and re.search(r'comptage_velo_%d.*\.csv$' % year, url, re.IGNORECASE):
            compteurs_url = url

    if not csv_url:
        sys.stderr.write("cyclistes.csv introuvable dans le dataset.\n")
    if not compteurs_url:
        sys.stderr.write("comptage_velo_%d.csv introuvable dans le dataset.\n" % year)

    print(csv_url)
    print(compteurs_url)
except Exception as e:
    sys.stderr.write("Erreur API CKAN : %s\n" % e)
    print("")
    print("")
EOF
)

CSV_URL=$(echo "$CKAN_URLS"     | sed -n '1p')
COMPTEURS_URL=$(echo "$CKAN_URLS" | sed -n '2p')

# ── Télécharger le CSV Détecteurs SUM (cyclistes.csv) ─────────────────────────
if [ -n "$CSV_URL" ]; then
    echo "$LOG URL cyclistes.csv : $CSV_URL"
    OLD_HASH=""
    [ -f cyclistes.csv ] && OLD_HASH=$(md5sum cyclistes.csv | cut -d' ' -f1)
    curl -fsSL \
      -H "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
      -H "Referer: https://donnees.montreal.ca/dataset/velos-comptage" \
      "$CSV_URL" -o cyclistes.csv
    echo "$LOG CSV téléchargé ($(du -sh cyclistes.csv | cut -f1))"
    NEW_HASH=$(md5sum cyclistes.csv | cut -d' ' -f1)
    if [ "$OLD_HASH" != "$NEW_HASH" ]; then
        echo "$LOG Nouveau CSV cyclistes détecté."
        CSV_CHANGED=true
    else
        echo "$LOG CSV cyclistes identique à la version précédente."
    fi
else
    echo "$LOG URL cyclistes.csv introuvable — données existantes conservées."
fi

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
        BIXI_CHANGED=true
    fi
else
    echo "$LOG URL BIXI introuvable — données existantes conservées."
fi

# ── Télécharger le CSV Éco-Compteurs (compteurs.csv) ─────────────────────────
# $COMPTEURS_URL est déjà résolu par l'appel API CKAN ci-dessus.
echo "$LOG Traitement URL compteurs vélo (${YEAR:-$(date +%Y)})..."

if [ -n "$COMPTEURS_URL" ]; then
    echo "$LOG URL trouvée : $COMPTEURS_URL"
    OLD_HASH_C=""
    [ -f compteurs.csv ] && OLD_HASH_C=$(md5sum compteurs.csv | cut -d' ' -f1)
    curl -fsSL \
      -H "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
      -H "Referer: https://donnees.montreal.ca/dataset/velos-comptage" \
      "$COMPTEURS_URL" -o compteurs.csv
    echo "$LOG Fichier téléchargé ($(du -sh compteurs.csv | cut -f1))"
    NEW_HASH_C=$(md5sum compteurs.csv | cut -d' ' -f1)
    if [ "$OLD_HASH_C" != "$NEW_HASH_C" ]; then
        echo "$LOG Nouveau fichier compteurs détecté."
        COMPTEURS_CHANGED=true
    else
        echo "$LOG Fichier compteurs identique à la version précédente."
    fi
else
    echo "$LOG URL compteurs introuvable — données existantes conservées."
fi

# ── Rien de nouveau → on arrête ───────────────────────────────────────────────
if [ "$CSV_CHANGED" = false ] && [ "$BIXI_CHANGED" = false ] && [ "$COMPTEURS_CHANGED" = false ]; then
    echo "$LOG Aucune donnée nouvelle — rien à publier."
    exit 0
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
# Persister le cache de géocodage Nominatim si de nouveaux compteurs ont été ajoutés
[ -f velo_meta_cache.json ] && git add velo_meta_cache.json

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
