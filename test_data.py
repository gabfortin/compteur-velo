"""
Tests de validation : vérifie que les données publiées (data/site.json)
correspondent exactement aux données du fichier cyclistes.csv.
"""
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from site_format import SITE_DATA_FILE, decode_hour_runs, encode_hour_runs

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

# ─── 1. Charger le CSV (données horaires uniquement, 12 derniers mois) ───────

print("\n=== Chargement du CSV ===")

def parse_periode(s):
    s = s.strip('"')
    s = re.sub(r'[+-]\d{2}$', '', s).strip()
    return datetime.fromisoformat(s)

cutoff = datetime.now() - timedelta(days=365)
HOURLY_DAYS = 90   # doit correspondre à genMap.py

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
    """Retourne (labels, volumes, jours, volumes_jour) toutes directions confondues.
    L'horaire est limité aux HOURLY_DAYS derniers jours du compteur (comme genMap.py)."""
    totals = defaultdict(int)
    day_totals = defaultdict(int)
    for rows in csv_data[inst].values():
        for label, vol in rows:
            totals[label] += vol
            day_totals[label[:10]] += vol
    max_day = datetime.fromisoformat(max(totals)[:10])
    hourly_from = (max_day - timedelta(days=HOURLY_DAYS - 1)).strftime('%Y-%m-%d')
    labels = sorted(l for l in totals if l[:10] >= hourly_from)
    volumes = [totals[l] for l in labels]
    days = sorted(day_totals)
    return labels, volumes, days, [day_totals[d] for d in days]

print(f"  Instances trouvées : {len(csv_data)}")
print(f"  Période couverte   : depuis {cutoff.strftime('%Y-%m-%d')}")

# ─── 1b. Recenser les instances velo-full (compteurs.csv, préfixe vf-) ───────

vf_instances = set()
if os.path.exists('compteurs.csv'):
    with open('compteurs.csv', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            vf_instances.add(f"vf-{row['id_compteur']}")
    print(f"  Instances vf- (compteurs.csv) : {len(vf_instances)}")

# ─── 2. Charger data/site.json ───────────────────────────────────────────────

print(f"\n=== Chargement de {SITE_DATA_FILE} ===")

with open(SITE_DATA_FILE, encoding='utf-8') as f:
    site = json.load(f)

REQUIRED_KEYS = [
    'version', 'generatedAt', 'counters', 'HOURLY_DAYS', 'allChartData', 'allDailyData',
    'hourProfile', 'countersByArrondissement', 'counterLocations', 'gappyCounters',
    'anomalyDays', 'vfDataQuality', 'weatherData', 'bixiNearby', 'bixiExceedsDays',
    'bixiYearly', 'historicalYearlyTotals', 'historicalYearlyCounters',
    'counterHistoricalData', 'globalStats',
]
missing_keys = [k for k in REQUIRED_KEYS if k not in site]
check("Toutes les clés attendues par la page sont présentes", not missing_keys,
      f"Clés manquantes : {missing_keys}")
check("HOURLY_DAYS identique à genMap.py", site.get('HOURLY_DAYS') == HOURLY_DAYS,
      f"site.json={site.get('HOURLY_DAYS')}, attendu={HOURLY_DAYS}")

site_data = {}
for inst, chart in site['allChartData'].items():
    runs = chart['hours']
    labels = decode_hour_runs(runs)
    datasets = chart['datasets']
    # Les plages doivent être maximales (encodage canonique, comme genMap.py)
    if encode_hour_runs(labels) != runs:
        fail(f"[{inst}] Plages horaires canoniques", "Plages fusionnables ou mal formées")
    if any(len(ds['data']) != len(labels) for ds in datasets):
        fail(f"[{inst}] Longueur des séries horaires", "Séries et étiquettes de longueurs différentes")
    # Volumes combinés (somme de toutes les directions à chaque position)
    combined = [
        sum(ds['data'][i] or 0 for ds in datasets)
        for i in range(len(labels))
    ]
    site_data[inst] = {
        'labels':   labels,
        'combined': combined,
        'datasets': datasets,
    }

print(f"  Instances trouvées : {len(site_data)}")

site_daily = {}
for inst, daily in site['allDailyData'].items():
    labels = daily['labels']
    series = daily['data']
    site_daily[inst] = {
        'labels':   labels,
        'combined': [sum(s[i] or 0 for s in series) for i in range(len(labels))],
    }
print(f"  Instances journalières : {len(site_daily)}")

# ─── 3. Tests ────────────────────────────────────────────────────────────────

print("\n=== Tests de couverture ===")

csv_instances  = set(csv_data.keys())
site_instances = set(site_data.keys())
# Les instances publiées peuvent inclure des vf- (compteurs.csv) en plus de cyclistes.csv
known_instances = csv_instances | vf_instances

check("Toutes les instances cyclistes.csv sont dans les données",
      csv_instances.issubset(site_instances),
      f"Manquantes dans les données : {csv_instances - site_instances}")

check("Aucune instance inconnue dans les données",
      site_instances.issubset(known_instances),
      f"Instances inconnues : {site_instances - known_instances}")

print("\n=== Tests de données par instance ===")

label_re = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')

for inst in sorted(csv_instances & site_instances):
    csv_labels, csv_volumes, csv_days, csv_day_volumes = combined_for_instance(inst)

    daily = site_daily.get(inst)
    check(f"[{inst}] Données journalières présentes", daily is not None)
    if daily:
        check(f"[{inst}] Jours identiques ({len(csv_days)} attendus)",
              daily['labels'] == csv_days,
              f"site.json={len(daily['labels'])}, CSV={len(csv_days)}")
        check(f"[{inst}] Totaux journaliers identiques",
              daily['combined'] == csv_day_volumes,
              f"Premier écart : {next((f'{d} site={a} csv={b}' for d,a,b in zip(csv_days, daily['combined'], csv_day_volumes) if a!=b), 'N/A')}")
    site_labels  = site_data[inst]['labels']
    site_volumes = site_data[inst]['combined']

    check(f"[{inst}] Nombre de points ({len(csv_labels)} attendus)",
          len(site_labels) == len(csv_labels),
          f"site.json={len(site_labels)}, CSV={len(csv_labels)}")

    if len(site_labels) != len(csv_labels):
        continue

    check(f"[{inst}] Labels identiques et triés",
          site_labels == csv_labels,
          f"Premier écart : {next((i for i,(a,b) in enumerate(zip(site_labels,csv_labels)) if a!=b), 'N/A')}")

    check(f"[{inst}] Volumes combinés identiques",
          site_volumes == csv_volumes,
          f"Premier écart : {next((f'i={i} site={a} csv={b}' for i,(a,b) in enumerate(zip(site_volumes,csv_volumes)) if a!=b), 'N/A')}")

    bad_labels = [l for l in site_labels if not label_re.match(l)]
    check(f"[{inst}] Format des labels (YYYY-MM-DD HH:MM)",
          len(bad_labels) == 0,
          f"Labels malformés : {bad_labels[:3]}")

    old = [l for l in site_labels if datetime.fromisoformat(l) < cutoff]
    check(f"[{inst}] Aucune donnée hors période 12 mois",
          len(old) == 0,
          f"{len(old)} entrées trop anciennes, ex: {old[:2]}")

    check(f"[{inst}] Somme des volumes cohérente",
          sum(site_volumes) == sum(csv_volumes),
          f"site.json={sum(site_volumes)}, CSV={sum(csv_volumes)}")

    # Vérifier que le nombre de datasets correspond au nombre de directions
    n_dirs_csv  = len(csv_data[inst])
    n_dirs_site = len(site_data[inst]['datasets'])
    check(f"[{inst}] Nombre de directions ({n_dirs_csv} attendues)",
          n_dirs_site == n_dirs_csv,
          f"site.json={n_dirs_site}, CSV={n_dirs_csv}")

print("\n=== Tests des blocs de graphiques ===")

block_ids = [c[0] for c in site['counters']]
check("Un bloc de graphique par compteur ayant des données",
      set(site_instances).issubset(block_ids),
      f"Sans bloc : {set(site_instances) - set(block_ids)}")
check("Aucun bloc de graphique en double", len(block_ids) == len(set(block_ids)),
      f"Doublons : {sorted({b for b in block_ids if block_ids.count(b) > 1})}")

print("\n=== Tests des labels du dropdown ===")

# Le menu est construit par assets/boot.js à partir de countersByArrondissement
site_options = {c['value']: c['label'] for entries in site['countersByArrondissement'].values() for c in entries}

for inst, meta in csv_meta.items():
    if inst not in site_options:
        fail(f"[{inst}] Option présente dans dropdown", "Manquante")
        continue
    label = site_options[inst]
    check(f"[{inst}] Label dropdown contient rue_1",
          meta['rue_1'] in label, f"'{meta['rue_1']}' absent de '{label}'")
    check(f"[{inst}] Label dropdown contient rue_2",
          meta['rue_2'] in label, f"'{meta['rue_2']}' absent de '{label}'")
    # La direction n'apparaît dans le label que pour les compteurs uni-directionnels
    if len(csv_data[inst]) == 1:
        check(f"[{inst}] Label dropdown contient direction",
              meta['direction'] in label, f"'{meta['direction']}' absent de '{label}'")

print("\n=== Tests des optgroups ===")

site_groups = set(site['countersByArrondissement'])
csv_groups  = set(m['arrondissement'] for m in csv_meta.values())

check("Tous les arrondissements cyclistes.csv sont des optgroups",
      csv_groups.issubset(site_groups),
      f"Manquants dans les données : {csv_groups - site_groups}")
# Les arrondissements supplémentaires viennent des boucles magnétiques (compteurs.csv) — c'est normal

# ─── Résumé ──────────────────────────────────────────────────────────────────

print("\n" + "="*50)
if failures:
    print(f"\033[91m{len(failures)} test(s) échoué(s) :\033[0m")
    for f in failures:
        print(f"  • {f}")
    sys.exit(1)
else:
    print(f"\033[92mTous les tests sont passés. ({len(site_data)} instances validées)\033[0m")
