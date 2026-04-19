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
    s = re.sub(r'[+-]\d{2}$', '', s).strip()
    return datetime.fromisoformat(s)

cutoff = datetime.now() - timedelta(days=180)

# csv_data[instance][direction] = [(label, volume), ...]
csv_data = defaultdict(lambda: defaultdict(list))
csv_meta = {}   # instance -> first_row

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
        csv_data[row['instance']][row['direction']].append((label, int(row['volume'])))
        if row['instance'] not in csv_meta:
            csv_meta[row['instance']] = row

# Trier par date
for inst in csv_data:
    for direction in csv_data[inst]:
        csv_data[inst][direction].sort(key=lambda x: x[0])

# Construire les données combinées par instance (union des timestamps, somme des directions)
def combined_for_instance(inst):
    """Retourne (labels_sorted, volumes_summed) toutes directions confondues."""
    totals = defaultdict(int)
    for rows in csv_data[inst].values():
        for label, vol in rows:
            totals[label] += vol
    labels = sorted(totals.keys())
    volumes = [totals[l] for l in labels]
    return labels, volumes

print(f"  Instances trouvées : {len(csv_data)}")
print(f"  Période couverte   : depuis {cutoff.strftime('%Y-%m-%d')}")

# ─── 2. Parser index.html ────────────────────────────────────────────────────

print("\n=== Chargement de index.html ===")

with open('index.html', encoding='utf-8') as f:
    html = f.read()

# Nouvelle structure : allChartData['inst'] = { labels: [...], datasets: [...] }
pattern = r"allChartData\['([^']+)'\]\s*=\s*\{\s*labels:\s*(\[.*?\]),\s*datasets:\s*(\[.*?\])\s*\};"
matches = re.findall(pattern, html, re.DOTALL)

html_data = {}
for inst, labels_json, datasets_json in matches:
    labels = json.loads(labels_json)
    datasets = json.loads(datasets_json)
    # Volumes combinés (somme de toutes les directions à chaque position)
    combined = [
        sum(ds['data'][i] or 0 for ds in datasets)
        for i in range(len(labels))
    ]
    html_data[inst] = {
        'labels':   labels,
        'combined': combined,
        'datasets': datasets,
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
    csv_labels, csv_volumes = combined_for_instance(inst)
    html_labels  = html_data[inst]['labels']
    html_volumes = html_data[inst]['combined']

    check(f"[{inst}] Nombre de points ({len(csv_labels)} attendus)",
          len(html_labels) == len(csv_labels),
          f"HTML={len(html_labels)}, CSV={len(csv_labels)}")

    if len(html_labels) != len(csv_labels):
        continue

    check(f"[{inst}] Labels identiques et triés",
          html_labels == csv_labels,
          f"Premier écart : {next((i for i,(a,b) in enumerate(zip(html_labels,csv_labels)) if a!=b), 'N/A')}")

    check(f"[{inst}] Volumes combinés identiques",
          html_volumes == csv_volumes,
          f"Premier écart : {next((f'i={i} html={a} csv={b}' for i,(a,b) in enumerate(zip(html_volumes,csv_volumes)) if a!=b), 'N/A')}")

    bad_labels = [l for l in html_labels if not label_re.match(l)]
    check(f"[{inst}] Format des labels (YYYY-MM-DD HH:MM)",
          len(bad_labels) == 0,
          f"Labels malformés : {bad_labels[:3]}")

    old = [l for l in html_labels if datetime.fromisoformat(l) < cutoff]
    check(f"[{inst}] Aucune donnée hors période 6 mois",
          len(old) == 0,
          f"{len(old)} entrées trop anciennes, ex: {old[:2]}")

    check(f"[{inst}] Somme des volumes cohérente",
          sum(html_volumes) == sum(csv_volumes),
          f"HTML={sum(html_volumes)}, CSV={sum(csv_volumes)}")

    # Vérifier que le nombre de datasets correspond au nombre de directions
    n_dirs_csv  = len(csv_data[inst])
    n_dirs_html = len(html_data[inst]['datasets'])
    check(f"[{inst}] Nombre de directions ({n_dirs_csv} attendues)",
          n_dirs_html == n_dirs_csv,
          f"HTML={n_dirs_html}, CSV={n_dirs_csv}")

print("\n=== Tests des labels du dropdown ===")

option_re = re.compile(r'<option value="([^"]+)">([^<]+)</option>')
html_options = {m[0]: m[1] for m in option_re.findall(html) if m[0]}

for inst, meta in csv_meta.items():
    if inst not in html_options:
        fail(f"[{inst}] Option présente dans dropdown", "Manquante")
        continue
    label = html_options[inst]
    check(f"[{inst}] Label dropdown contient rue_1",
          meta['rue_1'] in label, f"'{meta['rue_1']}' absent de '{label}'")
    check(f"[{inst}] Label dropdown contient rue_2",
          meta['rue_2'] in label, f"'{meta['rue_2']}' absent de '{label}'")
    # La direction n'apparaît dans le label que pour les compteurs uni-directionnels
    if len(csv_data[inst]) == 1:
        check(f"[{inst}] Label dropdown contient direction",
              meta['direction'] in label, f"'{meta['direction']}' absent de '{label}'")

print("\n=== Tests des optgroups ===")

optgroup_re = re.compile(r'<optgroup label="([^"]+)">')
html_groups = set(optgroup_re.findall(html))
csv_groups  = set(m['arrondissement'] for m in csv_meta.values())

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
    print(f"\033[92mTous les tests sont passés. ({len(html_data)} instances validées)\033[0m")
