#!/bin/bash
# Télécharge cyclistes.csv, bixi.csv et compteurs.csv dans le répertoire courant.
# Ne fait aucune opération git — utilisé à la fois par update.sh (Docker) et
# par le workflow GitHub Actions (.github/workflows/update.yml).
#
# Écrit le résultat dans .fetch_status.env (CSV_CHANGED / BIXI_CHANGED / COMPTEURS_CHANGED)
# pour que l'appelant décide s'il doit régénérer le HTML.
set -euo pipefail

LOG="[$(date '+%Y-%m-%d %H:%M:%S')]"

CSV_CHANGED=false
BIXI_CHANGED=false
COMPTEURS_CHANGED=false

# ── Récupérer les URLs via l'API CKAN (dataset cyclistes) ────────────────────
# Les deux fichiers (SUM + Éco-Compteur) sont dans le même dataset depuis 2026.
# L'API retourne du JSON structuré — plus fiable que le scraping HTML.
echo "$LOG Interrogation de l'API CKAN pour le dataset cyclistes..."
CKAN_URLS=$(python3 - <<'EOF'
import urllib.request, json, re, sys
from datetime import datetime

year = datetime.now().year
try:
    # UUID du dataset (slug actuel : "cyclistes").
    req = urllib.request.Request(
        "https://donnees.montreal.ca/api/3/action/package_show?id=142ff2e9-7d0a-47d6-b4f6-dfeb97041daf",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())
    if not data.get('success'):
        raise Exception("Réponse API invalide")
    resources = data['result']['resources']

    # Depuis oct. 2026, les détecteurs SUM sont publiés par année
    # (« Velos - Comptage AAAA - Ville » → cyclistes_AAAA.csv). L'ancien
    # cyclistes.csv n'est plus mis à jour ; on ne l'utilise qu'en repli.
    def find(pattern):
        for r in resources:
            url = r.get('url', '')
            if re.search(pattern, url, re.IGNORECASE):
                return url
        return ""

    csv_url = find(r'/cyclistes_%d\.csv$' % year)
    if not csv_url:
        sys.stderr.write("cyclistes_%d.csv introuvable — repli sur cyclistes.csv.\n" % year)
        csv_url = find(r'/cyclistes\.csv$')
    compteurs_url = find(r'comptage_velo_%d.*\.csv$' % year)
    # La fenêtre de 12 mois de genMap.py déborde toujours sur l'année
    # précédente : on récupère aussi ces fichiers.
    csv_prev_url       = find(r'/cyclistes_%d\.csv$' % (year - 1))
    compteurs_prev_url = find(r'comptage_velo_%d.*\.csv$' % (year - 1))

    if not csv_url:
        sys.stderr.write("CSV cyclistes introuvable dans le dataset.\n")
    if not compteurs_url:
        sys.stderr.write("comptage_velo_%d.csv introuvable dans le dataset.\n" % year)

    print(csv_url)
    print(compteurs_url)
    print(csv_prev_url)
    print(compteurs_prev_url)
except Exception as e:
    sys.stderr.write("Erreur API CKAN : %s\n" % e)
    print("\n\n\n")
EOF
)

CSV_URL=$(echo "$CKAN_URLS"     | sed -n '1p')
COMPTEURS_URL=$(echo "$CKAN_URLS" | sed -n '2p')
CSV_PREV_URL=$(echo "$CKAN_URLS"  | sed -n '3p')
COMPTEURS_PREV_URL=$(echo "$CKAN_URLS" | sed -n '4p')

# Télécharge $2 (année courante) dans $1, précédé de $3 (année précédente,
# optionnel, en-tête retiré du second fichier) — même schéma de colonnes.
fetch_merged() {
    local out="$1" cur_url="$2" prev_url="$3"
    local ua="User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    local ref="Referer: https://donnees.montreal.ca/dataset/cyclistes"
    curl -fsSL -H "$ua" -H "$ref" "$cur_url" -o "$out.cur"
    if [ -n "$prev_url" ]; then
        echo "$LOG URL année précédente : $prev_url"
        curl -fsSL -H "$ua" -H "$ref" "$prev_url" -o "$out.prev"
        { cat "$out.prev"; tail -n +2 "$out.cur"; } > "$out"
        rm -f "$out.prev" "$out.cur"
    else
        echo "$LOG Fichier de l'année précédente introuvable — année courante seulement."
        mv "$out.cur" "$out"
    fi
}

# ── Télécharger le CSV Détecteurs SUM (cyclistes.csv) ─────────────────────────
# Années courante + précédente fusionnées dans cyclistes.csv.
if [ -n "$CSV_URL" ]; then
    echo "$LOG URL cyclistes (année courante) : $CSV_URL"
    OLD_HASH=""
    [ -f cyclistes.csv ] && OLD_HASH=$(md5sum cyclistes.csv | cut -d' ' -f1)
    fetch_merged cyclistes.csv "$CSV_URL" "$CSV_PREV_URL"
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
    fetch_merged compteurs.csv "$COMPTEURS_URL" "$COMPTEURS_PREV_URL"
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

# ── Exposer le résultat à l'appelant ──────────────────────────────────────────
cat > .fetch_status.env <<EOF
CSV_CHANGED=$CSV_CHANGED
BIXI_CHANGED=$BIXI_CHANGED
COMPTEURS_CHANGED=$COMPTEURS_CHANGED
EOF

echo "$LOG Statut : CSV=$CSV_CHANGED BIXI=$BIXI_CHANGED COMPTEURS=$COMPTEURS_CHANGED"
