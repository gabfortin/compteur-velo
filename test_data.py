"""
Tests de validation : vérifie que les données dans index.html correspondent
exactement aux données du fichier cyclistes.csv.
"""
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

PASS = "\033[92m PASS\033[0m"
FAIL = "\033[91m FAIL\033[0m"

failures = []

def ok(name):
    print(f"{PASS}  {name}")

def fail(name, detail):
    print(f"{FAIL}  {name}")
    print(f"       → {detail}")
    failures.append(name)

def check(name, condition, detail=""):
    if condition:
        ok(name)
    else:
        fail(name, detail)

# ─── 1. Charger le CSV (données horaires uniquement, 6 derniers mois) ────────

print("\n=== Chargement du CSV ===")

def parse_periode(s):
    s = s.strip('"')
    # Enlever le décalage horaire (-05 / -04)
    s = re.sub(r'[+-]\d{2}$', '', s).strip()
    return datetime.fromisoformat(s)

cutoff = datetime.now() - timedelta(days=180)

csv_data = defaultdict(list)  # instance -> [(label, volume), ...]
csv_meta = {}                  # instance -> first_row

with open('cyclistes.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['agg_code'] != 'h':
            continue
        try:
            dt = parse_periode(row['periode'])
        except Exception:
            continue
        if dt < cutoff:
            continue
        label = row['periode'].strip('"')[:16]
        csv_data[row['instance']].append((label, int(row['volume'])))
        if row['instance'] not in csv_meta:
            csv_meta[row['instance']] = row

# Trier par date (comme le fait genMap.py)
for inst in csv_data:
    csv_data[inst].sort(key=lambda x: x[0])

print(f"  Instances trouvées : {len(csv_data)}")
print(f"  Période couverte   : depuis {cutoff.strftime('%Y-%m-%d')}")

# ─── 2. Parser index.html ────────────────────────────────────────────────────

print("\n=== Chargement de index.html ===")

with open('index.html', encoding='utf-8') as f:
    html = f.read()

# Extraire allChartData[...] = { labels: [...], data: [...] }
pattern = r"allChartData\['([^']+)'\]\s*=\s*\{\s*labels:\s*(\[.*?\]),\s*data:\s*(\[.*?\])\s*\};"
matches = re.findall(pattern, html, re.DOTALL)

html_data = {}
for inst, labels_json, data_json in matches:
    html_data[inst] = {
        'labels': json.loads(labels_json),
        'data':   json.loads(data_json),
    }

print(f"  Instances trouvées : {len(html_data)}")

# ─── 3. Tests ────────────────────────────────────────────────────────────────

print("\n=== Tests de couverture ===")

csv_instances  = set(csv_data.keys())
html_instances = set(html_data.keys())

check("Toutes les instances CSV sont dans le HTML",
      csv_instances == html_instances,
      f"Manquantes dans HTML : {csv_instances - html_instances} | "
      f"En trop dans HTML : {html_instances - csv_instances}")

check("Aucune instance fantôme dans le HTML",
      html_instances.issubset(csv_instances),
      f"Instances inconnues : {html_instances - csv_instances}")

print("\n=== Tests de données par instance ===")

label_re = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')

for inst in sorted(csv_instances & html_instances):
    csv_labels  = [l for l, _ in csv_data[inst]]
    csv_volumes = [v for _, v in csv_data[inst]]
    html_labels  = html_data[inst]['labels']
    html_volumes = html_data[inst]['data']

    # Nombre de points
    check(f"[{inst}] Nombre de points ({len(csv_labels)} attendus)",
          len(html_labels) == len(csv_labels),
          f"HTML={len(html_labels)}, CSV={len(csv_labels)}")

    if len(html_labels) != len(csv_labels):
        continue  # Pas la peine d'aller plus loin pour cet instance

    # Labels identiques et en ordre
    check(f"[{inst}] Labels identiques et triés",
          html_labels == csv_labels,
          f"Premier écart : {next((i for i,(a,b) in enumerate(zip(html_labels,csv_labels)) if a!=b), 'N/A')}")

    # Volumes identiques
    check(f"[{inst}] Volumes identiques",
          html_volumes == csv_volumes,
          f"Premier écart : {next((f'i={i} html={a} csv={b}' for i,(a,b) in enumerate(zip(html_volumes,csv_volumes)) if a!=b), 'N/A')}")

    # Format des labels YYYY-MM-DD HH:MM
    bad_labels = [l for l in html_labels if not label_re.match(l)]
    check(f"[{inst}] Format des labels (YYYY-MM-DD HH:MM)",
          len(bad_labels) == 0,
          f"Labels malformés : {bad_labels[:3]}")

    # Aucune donnée hors période 6 mois
    old = [l for l in html_labels if datetime.fromisoformat(l) < cutoff]
    check(f"[{inst}] Aucune donnée hors période 6 mois",
          len(old) == 0,
          f"{len(old)} entrées trop anciennes, ex: {old[:2]}")

    # Somme des volumes cohérente
    check(f"[{inst}] Somme des volumes cohérente",
          sum(html_volumes) == sum(csv_volumes),
          f"HTML={sum(html_volumes)}, CSV={sum(csv_volumes)}")

print("\n=== Tests des labels du dropdown ===")

# Extraire les options du HTML
option_re = re.compile(r'<option value="([^"]+)">([^<]+)</option>')
html_options = {m[0]: m[1] for m in option_re.findall(html) if m[0]}  # exclut l'option vide

for inst, meta in csv_meta.items():
    if inst not in html_options:
        fail(f"[{inst}] Option présente dans dropdown", f"Manquante")
        continue
    label = html_options[inst]
    expected_rue1 = meta['rue_1']
    expected_rue2 = meta['rue_2']
    expected_dir  = meta['direction']
    check(f"[{inst}] Label dropdown contient rue_1",
          expected_rue1 in label, f"'{expected_rue1}' absent de '{label}'")
    check(f"[{inst}] Label dropdown contient rue_2",
          expected_rue2 in label, f"'{expected_rue2}' absent de '{label}'")
    check(f"[{inst}] Label dropdown contient direction",
          expected_dir in label, f"'{expected_dir}' absent de '{label}'")

print("\n=== Tests des optgroups ===")

optgroup_re = re.compile(r'<optgroup label="([^"]+)">')
html_groups = set(optgroup_re.findall(html))
csv_groups  = set(meta['arrondissement'] for meta in csv_meta.values())

check("Tous les arrondissements sont des optgroups",
      csv_groups == html_groups,
      f"Manquants : {csv_groups - html_groups} | En trop : {html_groups - csv_groups}")

# ─── Résumé ──────────────────────────────────────────────────────────────────

print("\n" + "="*50)
if failures:
    print(f"\033[91m{len(failures)} test(s) échoué(s) :\033[0m")
    for f in failures:
        print(f"  • {f}")
    sys.exit(1)
else:
    total = html # just to count
    passed = len(re.findall(r'allChartData', html))
    print(f"\033[92mTous les tests sont passés. ({passed} instances validées)\033[0m")
