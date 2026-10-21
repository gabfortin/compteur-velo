import csv
import glob
import re
from collections import defaultdict
import os
from tqdm import tqdm
import json
import urllib.request
from datetime import datetime, timedelta, timezone
import math
from statistics import mean, stdev, median
import bixi_stats
from site_format import SITE_DATA_FILE, encode_hour_runs

try:
    with open('version.txt') as _vf:
        _APP_VERSION = _vf.read().strip()
except FileNotFoundError:
    _APP_VERSION = '?'

# Fenêtre glissante de données publiées (data/site.json)
WINDOW_DAYS = 365
# Au-delà de HOURLY_DAYS (comptés depuis la dernière donnée de chaque compteur),
# seuls les totaux journaliers sont publiés — allège les données de ~50 %.
HOURLY_DAYS = 90
# Périodes > HOURLY_DAYS pour lesquelles le profil horaire (heure de pointe) est précalculé
PROFILE_PERIODS = (180, 365)

# Compter le nombre total de lignes pour la barre de progression
total_lines = sum(1 for line in open('cyclistes.csv', encoding='utf-8')) - 1  # Soustraire la ligne d'en-tête

# Lire le fichier CSV et grouper par instance puis par direction
data = defaultdict(lambda: defaultdict(list))
with open('cyclistes.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in tqdm(reader, total=total_lines, desc="Traitement des données"):
        if row['agg_code'] == 'h':
            data[row['instance']][row['direction']].append(row)

# Filtrer les données pour garder les 12 derniers mois
def is_within_window(date_str):
    try:
        clean = re.sub(r'[+-]\d{2}$', '', date_str.strip('"').strip())
        row_date = datetime.fromisoformat(clean)
        cutoff_date = datetime.now() - timedelta(days=WINDOW_DAYS)
        return row_date >= cutoff_date
    except:
        return False

for instance in data.keys():
    for direction in data[instance].keys():
        data[instance][direction] = [row for row in data[instance][direction] if is_within_window(row['periode'])]

# ── Classifier les compteurs sur fût (det-) ───────────────────────────────────
def classify_det_quality(det_data):
    """Classifie chaque compteur det- en groupe A/B/C selon la fraîcheur des données.
    - A : à jour (dernière donnée complète proche du max global)
    - B : délai de publication (données récentes mais incomplètes)
    - C : compteur potentiellement inactif (aucune donnée récente)
    """
    # Normaliser le format de période : '2025-11-04 14:00:00-05' → '2025-11-04'
    def day_of(periode):
        clean = re.sub(r'[+-]\d{2}$', '', periode.strip('"').strip())
        return clean[:10]

    # Date max globale (compteurs det- uniquement)
    all_days = [
        day_of(row['periode'])
        for directions in det_data.values()
        for rows in directions.values()
        for row in rows
    ]
    global_max = max(all_days) if all_days else None

    quality = {}
    for instance, directions in det_data.items():
        # Heures distinctes par jour (toutes directions confondues)
        hours_per_day = defaultdict(set)
        for rows in directions.values():
            for row in rows:
                d = day_of(row['periode'])
                h = row['periode'].strip('"').strip()[11:13]
                hours_per_day[d].add(h)

        last_complete = max((d for d, hrs in hours_per_day.items() if len(hrs) >= 12), default=None)
        last_any      = max(hours_per_day.keys()) if hours_per_day else None

        # Le lag est calculé par rapport à la date du jour (et non au max du
        # jeu de données) : si la source elle-même n'a pas été rafraîchie
        # depuis longtemps, tous les compteurs seraient sinon « à jour » les
        # uns par rapport aux autres alors qu'ils sont en réalité périmés.
        group    = 'A'
        lag_days = None
        if global_max and last_any:
            now = datetime.now()
            lag_any      = (now - datetime.fromisoformat(last_any)).days
            lag_complete = (now - datetime.fromisoformat(last_complete)).days if last_complete else 999
            if lag_any > 30:
                group = 'C'
            elif lag_complete > 7:
                group = 'B'
        else:
            group = 'C'

        if group == 'B' and last_complete:
            lag_days = (datetime.now() - datetime.fromisoformat(last_complete)).days

        quality[instance] = {
            'group':         group,
            'last_complete': last_complete,
            'last_any':      last_any,
            'lag_days':      lag_days,
        }

    groups = {g: sum(1 for q in quality.values() if q['group'] == g) for g in ('A', 'B', 'C')}
    print(f"det- : {len(quality)} compteurs classifiés (A={groups['A']}, B={groups['B']}, C={groups['C']}).")
    return quality

det_quality = classify_det_quality(data)

# ── Intégrer velo-full-2026.csv (compteurs supplémentaires, intervalles 15 min) ──


def load_velo_full(filepath='compteurs.csv', meta_cache_file='velo_meta_cache.json'):
    """Charge compteurs.csv (intervalles 15 min), agrège à l'heure et retourne
    un dict compatible avec data{} : {instance_id: {'N/A': [rows]}}.

    - Les IDs sont préfixés 'vf-' (ex. vf-100041114) pour éviter tout conflit.
    - Les noms des compteurs sont résolus depuis localisation_des_compteurs_velo_update.csv
      et mis en cache dans velo_meta_cache.json.
    - Si le fichier est absent, retourne {} sans erreur.
    """
    if not os.path.exists(filepath):
        print(f"{filepath} introuvable — données velo-full ignorées.")
        return {}

    # Charger le fichier de localisation pour nommer les compteurs (remplace Nominatim)
    localisation_noms = {}
    localisation_file = 'localisation_des_compteurs_velo_update.csv'
    if os.path.exists(localisation_file):
        with open(localisation_file, encoding='utf-8') as f:
            for lrow in csv.DictReader(f):
                localisation_noms[lrow['ID']] = lrow['Nom']
        print(f"Localisation : {len(localisation_noms)} noms chargés depuis {localisation_file}.")

    # Charger le cache de métadonnées
    meta_cache = {}
    if os.path.exists(meta_cache_file):
        with open(meta_cache_file, encoding='utf-8') as f:
            meta_cache = json.load(f)

    # Agréger les passages de 15 min → horaire
    print(f"Chargement {filepath}...")
    counter_info = {}                                       # {cid: {lat, lng}}
    hourly = defaultdict(lambda: defaultdict(int))          # {cid: {hour_key: total}}

    with open(filepath, encoding='utf-8') as f:
        for row in tqdm(csv.DictReader(f), desc="velo-full (agrégation 15 min → h)"):
            cid = row['id_compteur']
            if not row['nb_passages'].strip():
                continue                                    # valeur manquante (fréquent en 2025)
            hh = row['heure'][:2]                           # 'HH' de 'HH:MM:SS'
            hour_key = f"{row['date']} {hh}:00:00"
            hourly[cid][hour_key] += int(row['nb_passages'])
            if cid not in counter_info:
                counter_info[cid] = {
                    'lat': float(row['latitude']),
                    'lng': float(row['longitude'])
                }

    # Résoudre les nouveaux compteurs depuis le fichier de localisation (sans Nominatim)
    new_cids = [cid for cid in counter_info if cid not in meta_cache]
    if new_cids:
        print(f"Résolution de {len(new_cids)} nouveaux compteurs via le fichier de localisation...")
        for cid in new_cids:
            nom = localisation_noms.get(cid, f'Écocompteur {cid}')
            meta_cache[cid] = {'arrondissement': 'Montréal', 'rue_1': nom, 'rue_2': ''}
            print(f"  {cid} → {meta_cache[cid]['rue_1']}")
        with open(meta_cache_file, 'w', encoding='utf-8') as f:
            json.dump(meta_cache, f, ensure_ascii=False, indent=2)
        print(f"  Cache métadonnées sauvegardé → {meta_cache_file}")

    # Mettre à jour rue_1 depuis le CSV de localisation pour tous les compteurs connus
    for cid in meta_cache:
        if cid in localisation_noms:
            meta_cache[cid]['rue_1'] = localisation_noms[cid]

    # Construire la structure compatible avec data{} (fenêtre WINDOW_DAYS, même filtre)
    cutoff = datetime.now() - timedelta(days=WINDOW_DAYS)
    result = {}

    # Date max globale dans le fichier (toutes sources confondues) pour classifier les groupes
    all_day_keys = [hk[:10] for cid in hourly for hk in hourly[cid]]
    global_max_str = max(all_day_keys) if all_day_keys else None

    vf_quality = {}  # {instance_id: {group, last_complete, last_any, lag_days}}

    for cid in sorted(counter_info):
        instance_id = f"vf-{cid}"
        meta = meta_cache.get(cid, {
            'arrondissement': 'Montréal',
            'rue_1': f'Écocompteur {cid}',
            'rue_2': ''
        })

        # Exclure les jours incomplets : le portail publie parfois seulement
        # quelques heures par jour pour les données récentes.  Un jour avec
        # moins de 12 entrées horaires est ignoré pour ne pas fausser les stats
        # et l'heure de pointe dans la vue « 7 derniers jours ».
        hours_per_day = defaultdict(int)
        for hk in hourly[cid]:
            hours_per_day[hk[:10]] += 1
        complete_days = {d for d, n in hours_per_day.items() if n >= 12}

        # ── Classifier ce compteur (Groupe A / B / C) ──────────────────────
        last_complete = max(complete_days) if complete_days else None
        last_any      = max(hours_per_day.keys()) if hours_per_day else None

        # Le lag est calculé par rapport à la date du jour (et non au max du
        # jeu de données) : si la source elle-même n'a pas été rafraîchie
        # depuis longtemps, tous les compteurs seraient sinon « à jour » les
        # uns par rapport aux autres alors qu'ils sont en réalité périmés.
        group    = 'A'
        lag_days = None
        if global_max_str and last_any:
            now = datetime.now()
            lag_any      = (now - datetime.fromisoformat(last_any)).days
            lag_complete = (now - datetime.fromisoformat(last_complete)).days if last_complete else 999
            if lag_any > 30:
                group = 'C'   # Plus de données récentes → inactif / désaffecté
            elif lag_complete > 7:
                group = 'B'   # Données récentes mais incomplètes → délai de publication
        else:
            group = 'C'

        if group == 'B' and last_complete:
            lag_days = (datetime.now() - datetime.fromisoformat(last_complete)).days

        vf_quality[instance_id] = {
            'group':         group,
            'last_complete': last_complete,
            'last_any':      last_any,
            'lag_days':      lag_days,
        }

        rows = []
        for hour_key, total in sorted(hourly[cid].items()):
            if hour_key[:10] not in complete_days:
                continue
            try:
                if datetime.fromisoformat(hour_key) < cutoff:
                    continue
            except Exception:
                continue
            rows.append({
                'periode':        hour_key,
                'volume':         str(total),
                'latitude':       str(counter_info[cid]['lat']),
                'longitude':      str(counter_info[cid]['lng']),
                'arrondissement': meta['arrondissement'],
                'rue_1':          meta['rue_1'],
                'rue_2':          meta.get('rue_2', ''),
                'direction':      'N/A',
            })
        if rows:
            result[instance_id] = {'N/A': rows}

    groups = {g: sum(1 for q in vf_quality.values() if q['group'] == g) for g in ('A', 'B', 'C')}
    print(f"velo-full : {len(result)} compteurs intégrés (A={groups['A']}, B={groups['B']}, C={groups['C']}).")
    return result, vf_quality


vf_data, vf_quality = load_velo_full('compteurs.csv')
data.update(vf_data)

# Détecter les compteurs avec des lacunes significatives dans les données
def has_significant_gaps(instance_data, gap_days=14, missing_ratio=0.20):
    all_dates = set()
    for rows in instance_data.values():
        for row in rows:
            try:
                all_dates.add(datetime.fromisoformat(row['periode'][:10]).date())
            except:
                pass
    if len(all_dates) < 14:
        return False
    min_date, max_date = min(all_dates), max(all_dates)
    total_days = (max_date - min_date).days + 1
    if total_days < 14:
        return False
    if (total_days - len(all_dates)) / total_days > missing_ratio:
        return True
    for a, b in zip(sorted(all_dates), sorted(all_dates)[1:]):
        if (b - a).days > gap_days:
            return True
    return False

gappy_instances = {inst for inst, dirs in data.items() if has_significant_gaps(dirs)}

# ── Météo ─────────────────────────────────────────────────────────────────────

def weather_icon(code):
    if code is None:             return '🌡️'
    if code == 0:                return '☀️'
    if code in (1, 2):           return '🌤️'
    if code == 3:                return '☁️'
    if code in (45, 48):         return '🌫️'
    if code in (51, 53, 55, 56, 57): return '🌦️'
    if code in (61, 63, 65, 66, 67): return '🌧️'
    if code in (71, 73, 75, 77): return '❄️'
    if code in (80, 81, 82):     return '🌦️'
    if code in (85, 86):         return '🌨️'
    if code in (95, 96, 99):     return '⛈️'
    return '🌡️'

def is_bad_weather(w):
    if not w: return False
    return (w.get('precip') or 0) > 15 or (w.get('snow') or 0) > 5 or (w.get('tmax') or 20) < -15

def fetch_weather_data():
    """Météo quotidienne de Montréal via Open-Meteo (archive + forecast récent)."""
    LAT, LNG = 45.5017, -73.5673
    start = (datetime.now() - timedelta(days=WINDOW_DAYS + 2)).strftime('%Y-%m-%d')
    today = datetime.now().strftime('%Y-%m-%d')
    result = {}
    sources = [
        ("https://archive-api.open-meteo.com/v1/archive",
         f"?latitude={LAT}&longitude={LNG}&start_date={start}&end_date={today}"
         f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,weather_code"
         f"&timezone=America%2FToronto"),
        ("https://api.open-meteo.com/v1/forecast",
         f"?latitude={LAT}&longitude={LNG}&past_days=10&forecast_days=0"
         f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,weather_code"
         f"&timezone=America%2FToronto"),
    ]
    for base_url, params in sources:
        try:
            req = urllib.request.Request(base_url + params, headers={"User-Agent": "compteur-velo/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                d = json.loads(resp.read().decode())
            for i, date in enumerate(d['daily']['time']):
                if date > today:
                    continue
                result[date] = {
                    'tmax':  d['daily']['temperature_2m_max'][i],
                    'tmin':  d['daily']['temperature_2m_min'][i],
                    'precip': d['daily']['precipitation_sum'][i] or 0,
                    'snow':   d['daily']['snowfall_sum'][i] or 0,
                    'code':   int(d['daily']['weather_code'][i] or 0),
                }
        except Exception as e:
            print(f"Avertissement météo ({base_url.split('/')[2]}) : {e}")
    for date in result:
        result[date]['icon'] = weather_icon(result[date]['code'])
    print(f"Météo : {len(result)} jours chargés.")
    return result

weather_data = fetch_weather_data()

# Détecter les jours avec un volume anormalement bas (dysfonctionnement probable du compteur)
def detect_anomalies(instance_data, min_ref_days=4, z_threshold=2.5, min_hours=4,
                     ratio_threshold=0.5, adj_ratio_threshold=0.2, adj_window=6,
                     min_expected_total=50, weather_data=None):
    """
    Détecte les jours avec un taux horaire anormalement bas par deux méthodes
    complémentaires, et signale si l'une ou l'autre est déclenchée :

    1. Jour de semaine (z-score) : compare le taux horaire (passages/h) du jour
       à la moyenne (μ) et l'écart-type (σ) des jours complets (≥18h) du même
       jour de semaine. Signale si taux < μ − 2,5σ ET < 50% de μ.

    2. Jours adjacents : compare le taux horaire aux jours complets dans une
       fenêtre de ±adj_window jours. Signale si taux < 25% de la moyenne des
       jours adjacents (≥3 jours de référence requis). Robuste à la saisonnalité.
    """
    daily = defaultdict(lambda: {"total": 0, "hours": 0})
    for rows in instance_data.values():
        for row in rows:
            try:
                d = row['periode'][:10]
                daily[d]["total"] += int(row['volume'])
                daily[d]["hours"] += 1
            except:
                pass

    candidates = {d: v for d, v in daily.items() if v["hours"] >= min_hours}
    full_days  = {d: v for d, v in daily.items() if v["hours"] >= 18}
    if len(full_days) < min_ref_days:
        return {}

    typical_hours = median(v["hours"] for v in full_days.values())
    typical_total = median(v["total"] for v in full_days.values())
    # Seuil dynamique : 25 % de la médiane journalière, plancher absolu à 10
    min_expected_effective = max(10, typical_total * 0.25)

    anomalies = {}
    for d_str, v in candidates.items():
        rate = v["total"] / v["hours"]
        try:
            d_date = datetime.fromisoformat(d_str)
            dow = d_date.weekday()
        except:
            continue

        # Méthode 1 : même jour de semaine
        dow_refs = [fd["total"] / fd["hours"] for ds, fd in full_days.items()
                    if ds != d_str and datetime.fromisoformat(ds).weekday() == dow]
        flagged_dow = False
        mu_dow = 0.0
        if len(dow_refs) >= min_ref_days:
            mu_dow = mean(dow_refs)
            if mu_dow > 0 and rate < mu_dow * ratio_threshold:
                sigma = stdev(dow_refs) if len(dow_refs) >= 2 else 0
                z = (rate - mu_dow) / sigma if sigma > 0 else -99.0
                flagged_dow = z < -z_threshold

        # Méthode 2 : jours adjacents (±adj_window jours)
        adj_refs = []
        for ds, fd in full_days.items():
            if ds == d_str:
                continue
            try:
                delta = abs((datetime.fromisoformat(ds) - d_date).days)
                if delta <= adj_window:
                    adj_refs.append(fd["total"] / fd["hours"])
            except:
                pass
        flagged_adj = False
        mu_adj = 0.0
        if len(adj_refs) >= 4:
            mu_adj = mean(adj_refs)
            cv_adj = stdev(adj_refs) / mu_adj if mu_adj > 0 and len(adj_refs) >= 2 else 1.0
            flagged_adj = mu_adj > 0 and rate < mu_adj * adj_ratio_threshold and cv_adj < 0.6

        if flagged_dow or flagged_adj:
            if weather_data and is_bad_weather(weather_data.get(d_str)):
                continue
            mu_ref = mu_adj if flagged_adj else mu_dow
            expected_total = round(mu_ref * typical_hours)
            if expected_total < min_expected_effective:
                continue
            z_val = -99.0
            if len(dow_refs) >= 2:
                s = stdev(dow_refs)
                if s > 0:
                    z_val = (rate - mu_dow) / s
            anomalies[d_str] = {
                "total": v["total"],
                "expected": expected_total,
                "z_score": round(z_val, 1)
            }

    # Détecter les journées entièrement absentes dans la plage active du compteur
    all_dates = set(daily.keys())
    if all_dates:
        first_date = min(all_dates)
        last_date  = max(all_dates)
        d_iter = datetime.fromisoformat(first_date)
        end_iter = max(datetime.fromisoformat(last_date), datetime.now() - timedelta(days=1))
        while d_iter <= end_iter:
            d_str = d_iter.strftime('%Y-%m-%d')
            if d_str not in all_dates and d_str not in anomalies:
                try:
                    dow = d_iter.weekday()
                    dow_refs_m = [fd["total"] / fd["hours"] for ds, fd in full_days.items()
                                  if datetime.fromisoformat(ds).weekday() == dow]
                    adj_refs_m = [fd["total"] / fd["hours"] for ds, fd in full_days.items()
                                  if 0 < abs((datetime.fromisoformat(ds) - d_iter).days) <= adj_window]
                    mu_ref = 0.0
                    if len(adj_refs_m) >= 4:
                        mu_adj_m = mean(adj_refs_m)
                        cv = stdev(adj_refs_m) / mu_adj_m if mu_adj_m > 0 and len(adj_refs_m) >= 2 else 1.0
                        if mu_adj_m > 0 and cv < 0.6:
                            mu_ref = mu_adj_m
                    if mu_ref == 0 and len(dow_refs_m) >= min_ref_days:
                        mu_ref = mean(dow_refs_m)
                    if mu_ref > 0:
                        expected = round(mu_ref * typical_hours)
                        if expected >= min_expected_effective and not (weather_data and is_bad_weather(weather_data.get(d_str))):
                            anomalies[d_str] = {"total": 0, "expected": expected, "z_score": -99.0}
                except:
                    pass
            d_iter += timedelta(days=1)

    return anomalies

anomaly_data = {inst: det for inst, dirs in data.items()
                if (det := detect_anomalies(dirs, weather_data=weather_data))}

# ── Données historiques (2009–présent) ───────────────────────────────────────
HIST_CACHE_FILE = 'hist_cache.json'

def load_historical_data(historique_dir='historique'):
    """Charge les données historiques.

    Priorité :
      1. hist_cache.json  (pré-calculé, commitable sur GitHub)
      2. dossier historique/ (parsing des CSV bruts, génère aussi hist_cache.json)
      3. Aucune source disponible → retourne des dicts vides

    Retourne:
        global_yearly: {year: total_passages}
        counter_monthly: {'vf-ID': {year: {month: total}}}
    """
    # ── 1. Cache pré-calculé ──────────────────────────────────────────────────
    if os.path.exists(HIST_CACHE_FILE):
        print(f"Historique : cache trouvé → {HIST_CACHE_FILE}")
        with open(HIST_CACHE_FILE, encoding='utf-8') as f:
            cache = json.load(f)
        yearly  = {int(k): v for k, v in cache.get('yearly', {}).items()}
        ctrs    = {int(k): v for k, v in cache.get('yearly_counters', {}).items()}
        monthly = {
            inst: {int(yr): {int(mo): vol for mo, vol in months.items()}
                   for yr, months in yrs.items()}
            for inst, yrs in cache.get('counter_monthly', {}).items()
        }
        print(f"Historique : {len(yearly)} années, {len(monthly)} compteurs (depuis cache).")
        return yearly, ctrs, monthly

    # ── 2. Parsing des CSV bruts ──────────────────────────────────────────────
    if not os.path.isdir(historique_dir):
        print(f"Historique : {historique_dir}/ introuvable et {HIST_CACHE_FILE} absent — ignoré.")
        return {}, {}, {}

    print(f"Historique : parsing de {historique_dir}/ (première exécution — génère {HIST_CACHE_FILE})...")

    # Mapping nom de compteur → ID numérique (depuis localisation historique)
    name_to_id = {}
    loc_file = os.path.join(historique_dir, 'localisation_des_compteurs_velo.csv')
    if os.path.exists(loc_file):
        with open(loc_file, encoding='utf-8', errors='replace') as f:
            for row in csv.DictReader(f):
                nom = (row.get('Nom') or '').strip()
                cid = (row.get('ID') or '').strip()
                if nom and cid:
                    name_to_id[nom] = cid
    # Alias courants dans les CSV historiques (noms de colonnes abrégés)
    _ALIASES = {
        'Berri1':                      '100003032',
        'Rachel / Papineau':           '100003034',
        'PierDup':                     '100003040',
        'Parc':                        '100003042',
        'Brébeuf':                     '100004575',
        'CSC (Côte Sainte-Catherine)': '100003041',
        'Pont_Jacques_Cartier':        '100002880',
        'Pont Jacques-Cartier':        '100002880',
    }
    name_to_id.update(_ALIASES)

    global_yearly    = defaultdict(int)
    yearly_counters  = defaultdict(set)   # {year: set of counter IDs}
    counter_monthly  = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    csv_files = sorted(glob.glob(os.path.join(historique_dir, '*.csv')))
    for filepath in tqdm(csv_files, desc="Historique"):
        basename = os.path.basename(filepath).lower()
        if 'localisation' in basename:
            continue

        with open(filepath, encoding='utf-8', errors='replace') as f:
            first_line = f.readline().strip()

        if 'id_compteur' in first_line:
            # Format long : 2009 (nb_passage) et 2019-2025 (nb_passages)
            col_nb = 'nb_passages' if 'nb_passages' in first_line else 'nb_passage'
            with open(filepath, encoding='utf-8', errors='replace') as f:
                for row in csv.DictReader(f):
                    try:
                        date_str = row['date'][:10]
                        year  = int(date_str[:4])
                        month = int(date_str[5:7])
                        vol   = int(row.get(col_nb) or 0)
                        cid   = (row.get('id_compteur') or '').strip()
                        global_yearly[year] += vol
                        if cid:
                            yearly_counters[year].add(cid)
                            counter_monthly[f'vf-{cid}'][year][month] += vol
                    except (ValueError, KeyError, TypeError):
                        pass

        elif first_line.startswith('Date'):
            # Format large : 2010-2018 (totaux journaliers, colonnes = noms des compteurs)
            with open(filepath, encoding='utf-8', errors='replace') as f:
                reader  = csv.reader(f)
                headers = next(reader)

            # Détecter si la 2e colonne est vide (colonne heure) → décaler de 2
            data_start = 2 if (len(headers) > 1 and not headers[1].strip()) else 1
            col_names  = headers[data_start:]

            with open(filepath, encoding='utf-8', errors='replace') as f:
                next(csv.reader(f))   # sauter l'entête
                reader = csv.reader(f)
                for row in reader:
                    if not row or not row[0].strip():
                        continue
                    date_str = row[0].strip()
                    try:
                        if '-' in date_str:
                            year  = int(date_str[:4])
                            month = int(date_str[5:7])
                        else:
                            parts = date_str.split('/')
                            year  = int(parts[2])
                            month = int(parts[1])
                    except (ValueError, IndexError):
                        continue

                    for i, col_name in enumerate(col_names):
                        col_i = i + data_start
                        if col_i >= len(row):
                            break
                        raw = row[col_i].strip() if col_i < len(row) else ''
                        if not raw or not col_name.strip():
                            continue
                        try:
                            val = int(raw)
                        except ValueError:
                            continue
                        global_yearly[year] += val
                        yearly_counters[year].add(col_name.strip())
                        cid = name_to_id.get(col_name.strip())
                        if cid:
                            counter_monthly[f'vf-{cid}'][year][month] += val

    result_yearly    = dict(global_yearly)
    result_ctrs      = {yr: len(s) for yr, s in yearly_counters.items()}
    result_monthly   = {k: {yr: dict(months) for yr, months in yrs.items()}
                        for k, yrs in counter_monthly.items()}

    # Sauvegarder le cache pour les prochaines exécutions sans historique/
    try:
        with open(HIST_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'yearly': result_yearly, 'yearly_counters': result_ctrs,
                       'counter_monthly': result_monthly},
                      f, ensure_ascii=False)
        print(f"Historique : cache sauvegardé → {HIST_CACHE_FILE}")
    except Exception as e:
        print(f"Historique : impossible de sauvegarder le cache ({e})")

    return result_yearly, result_ctrs, result_monthly


hist_yearly, hist_yearly_counters, hist_counter_monthly = load_historical_data()

# Ajouter les données de l'année courante (depuis les fichiers actifs)
_cur_year = datetime.now().year
_cur_year_instances = set()
for _inst, _directions in data.items():
    for _rows in _directions.values():
        for _row in _rows:
            try:
                _d  = _row['periode'][:10]
                _yr = int(_d[:4])
                _mo = int(_d[5:7])
                _v  = int(_row.get('volume', 0))
                if _yr == _cur_year:
                    hist_yearly[_cur_year] = hist_yearly.get(_cur_year, 0) + _v
                    _cur_year_instances.add(_inst)
                    if _inst not in hist_counter_monthly:
                        hist_counter_monthly[_inst] = {}
                    if _yr not in hist_counter_monthly[_inst]:
                        hist_counter_monthly[_inst][_yr] = {}
                    _mo_dict = hist_counter_monthly[_inst][_yr]
                    _mo_dict[_mo] = _mo_dict.get(_mo, 0) + _v
            except (ValueError, KeyError, TypeError):
                pass

hist_yearly_counters[_cur_year] = len(_cur_year_instances)
print(f"Historique : {len(hist_yearly)} années, {len(hist_counter_monthly)} compteurs.")


# ── Données de la page (data/site.json) ──────────────────────────────────────
# La page (index.html, assets/) est statique : genMap.py ne produit que les
# données, lues par assets/boot.js et assets/app.js (format : site_format.py).
site_data = {
    'version':     _APP_VERSION,
    'generatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%d à %H:%M'),
}

# Grouper par arrondissement (prendre le premier row disponible toutes directions confondues)
def first_row_for(instance):
    for rows in data[instance].values():
        if rows:
            return rows[0]
    return None

by_arrondissement = defaultdict(list)
for instance in data.keys():
    row = first_row_for(instance)
    if row:
        by_arrondissement[row['arrondissement']].append((instance, row))

def counter_label(instance, row):
    directions = sorted(data[instance].keys())
    rue2 = row.get('rue_2', '')
    location = f"{row['rue_1']} & {rue2}" if rue2 else row['rue_1']
    # Pas de direction affichée si plusieurs directions ou direction N/A (velo-full)
    if len(directions) > 1 or directions == ['N/A']:
        return f"{location} ({instance})"
    return f"{location} — {directions[0]} ({instance})"

# Un bloc de graphique par compteur (rendu par assets/boot.js) : [id, emplacement]
site_data['counters'] = []
for instance in data:
    row = first_row_for(instance)
    if row:
        rue2 = row.get('rue_2', '')
        location = (f"{row['arrondissement']} - {row['rue_1']} & {rue2}"
                    if rue2 else f"{row['arrondissement']} - {row['rue_1']}")
        site_data['counters'].append([instance, location])

DIRECTION_COLORS = ['#1DB860', '#29ABE2']
DIRECTION_FILLS  = ['rgba(29,184,96,0.15)', 'rgba(41,171,226,0.15)']

# Données des graphiques et regroupement des compteurs par arrondissement
site_data['HOURLY_DAYS'] = HOURLY_DAYS
site_data['allChartData'] = {}
site_data['allDailyData'] = {}
site_data['hourProfile'] = {}
for instance, directions in data.items():
    if not first_row_for(instance):
        continue
    all_hours = set(row['periode'][:16] for rows in directions.values() for row in rows)
    if not all_hours:
        continue
    # Fenêtres ancrées sur la dernière donnée du compteur (comme le JS)
    max_day = datetime.fromisoformat(max(all_hours)[:10])
    hourly_from = (max_day - timedelta(days=HOURLY_DAYS - 1)).strftime('%Y-%m-%d')
    hour_labels = sorted(h for h in all_hours if h[:10] >= hourly_from)
    all_days    = sorted(set(h[:10] for h in all_hours))
    datasets, daily_series = [], []
    profile = {p: [0] * 24 for p in PROFILE_PERIODS}
    profile_from = {p: (max_day - timedelta(days=p - 1)).strftime('%Y-%m-%d') for p in PROFILE_PERIODS}
    for i, direction in enumerate(sorted(directions.keys())):
        rows = directions[direction]
        if not rows:
            continue
        hour_vol = defaultdict(int)
        day_vol  = defaultdict(int)
        for row in rows:
            v = int(row['volume'])
            hour_vol[row['periode'][:16]] += v
            day_vol[row['periode'][:10]]  += v
            for p in PROFILE_PERIODS:
                if row['periode'][:10] >= profile_from[p]:
                    profile[p][int(row['periode'][11:13])] += v
        datasets.append({
            'label': 'Passages' if direction == 'N/A' else direction,
            'color': DIRECTION_COLORS[i % len(DIRECTION_COLORS)],
            'fill':  DIRECTION_FILLS[i % len(DIRECTION_FILLS)],
            'data':  [hour_vol.get(h) for h in hour_labels]
        })
        daily_series.append([day_vol.get(d) for d in all_days])
    site_data['allChartData'][instance] = {'hours': encode_hour_runs(hour_labels), 'datasets': datasets}
    site_data['allDailyData'][instance] = {'labels': all_days, 'data': daily_series}
    site_data['hourProfile'][instance] = {str(p): v for p, v in profile.items()}

site_data['countersByArrondissement'] = {}
for arrondissement in sorted(by_arrondissement.keys()):
    counters = sorted(by_arrondissement[arrondissement],
                      key=lambda x: (x[0].startswith('vf-'), x[1]['rue_1'], x[1].get('rue_2', '')))
    entries = [{"value": inst,
                "label": ('[Capteur au sol] ' if inst.startswith('vf-') else '[Capteur photo/vidéo] ') + counter_label(inst, row)}
               for inst, row in counters]
    site_data['countersByArrondissement'][arrondissement] = entries

# Localisation des compteurs pour la carte
counter_locations = {}
for instance in data.keys():
    row = first_row_for(instance)
    if row:
        counter_locations[instance] = {
            'lat': float(row['latitude']),
            'lng': float(row['longitude']),
            'label': counter_label(instance, row),
            'arrondissement': row['arrondissement'],
            'type': 'boucle' if instance.startswith('vf-') else 'fut'
        }
site_data['counterLocations'] = counter_locations
site_data['gappyCounters'] = sorted(gappy_instances)
site_data['anomalyDays'] = anomaly_data
all_quality = {**det_quality, **vf_quality}
site_data['vfDataQuality'] = all_quality
weather_js = {d: {k: v for k, v in w.items() if k != 'code'} for d, w in weather_data.items()}
site_data['weatherData'] = weather_js

# Croiser les trajets Bixi avec les compteurs pour valider les anomalies
def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

BIXI_RADIUS_M = 150
bixi_near_counter = defaultdict(lambda: defaultdict(int))
bixi_live = bixi_stats.BixiYearAggregator(
    bixi_stats.load_montreal_polygons() if os.path.exists('bixi.csv') else None)

if os.path.exists('bixi.csv'):
    with open('bixi.csv', encoding='utf-8') as f:
        for row in tqdm(csv.DictReader(f), desc="Données Bixi"):
            bixi_live.add_row(row)
            try:
                slat = float(row['STARTSTATIONLATITUDE'])
                slng = float(row['STARTSTATIONLONGITUDE'])
                elat = float(row['ENDSTATIONLATITUDE'])
                elng = float(row['ENDSTATIONLONGITUDE'])
                date = datetime.fromtimestamp(int(row['STARTTIMEMS']) / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
                nearby = set()
                for inst, loc in counter_locations.items():
                    clat, clng = loc['lat'], loc['lng']
                    if abs(slat - clat) < 0.003 and abs(slng - clng) < 0.004:
                        if _haversine_m(clat, clng, slat, slng) <= BIXI_RADIUS_M:
                            nearby.add(inst)
                    if abs(elat - clat) < 0.003 and abs(elng - clng) < 0.004:
                        if _haversine_m(clat, clng, elat, elng) <= BIXI_RADIUS_M:
                            nearby.add(inst)
                for inst in sorted(nearby):
                    bixi_near_counter[inst][date] += 1
            except Exception:
                pass

site_data['bixiNearby'] = {k: dict(v) for k, v in bixi_near_counter.items()}

# Jours où les trajets Bixi dépassent le volume du compteur
counter_daily_totals = defaultdict(lambda: defaultdict(int))
for inst in bixi_near_counter:
    if inst in data:
        for rows in data[inst].values():
            for row in rows:
                date = row['periode'][:10]
                counter_daily_totals[inst][date] += int(row['volume'])

bixi_exceeds_days = {}
for inst, bixi_days in bixi_near_counter.items():
    exceeds = {}
    cdaily = counter_daily_totals[inst]
    for date, bcount in bixi_days.items():
        ccount = cdaily.get(date, 0)
        if bcount > ccount:
            exceeds[date] = {"counter": ccount, "bixi": bcount}
    if exceeds:
        bixi_exceeds_days[inst] = exceeds

site_data['bixiExceedsDays'] = bixi_exceeds_days

# Statistiques BIXI par année : années passées depuis bixi_cache.json
# (bixi_stats.py), année du fichier bixi.csv calculée ici.
_live_year = bixi_live.live_year()
bixi_years = bixi_stats.merge_live(bixi_stats.load_cache(), bixi_live.summary(_live_year),
                                   _live_year, datetime.now().year)
site_data['bixiYearly'] = bixi_years
# Données historiques
site_data['historicalYearlyTotals'] = hist_yearly
site_data['historicalYearlyCounters'] = hist_yearly_counters
site_data['counterHistoricalData'] = hist_counter_monthly

# ── Vue globale : stats agrégées (compteurs groupes A + B) ────────────────────
_active_g = [i for i in data if all_quality.get(i, {}).get('group') in ('A', 'B')]

_g_daily   = defaultdict(int)
_g_ctr_day = defaultdict(lambda: defaultdict(int))
_g_hour_d  = defaultdict(lambda: defaultdict(int))

for _inst in _active_g:
    for _rows in data[_inst].values():
        for _row in _rows:
            _d = _row['periode'][:10]
            try:
                _v = int(_row['volume'])
            except Exception:
                continue
            _g_daily[_d]           += _v
            _g_ctr_day[_inst][_d]  += _v
            try:
                _g_hour_d[_d][int(_row['periode'][11:13])] += _v
            except Exception:
                pass

def _gstat(dates):
    total    = sum(_g_daily.get(d, 0) for d in dates)
    coverage = sum(1 for i in _active_g if any(_g_ctr_day[i].get(d, 0) > 0 for d in dates))
    i_tots   = {i: sum(_g_ctr_day[i].get(d, 0) for d in dates) for i in _active_g}
    ranked   = sorted(i_tots, key=i_tots.get, reverse=True)
    def _top(inst):
        if not inst: return {'id': None, 'label': '', 'vol': 0}
        r = first_row_for(inst)
        lbl = re.sub(r'\s*\([^)]+\)\s*$', '', counter_label(inst, r)) if r else ''
        return {'id': inst, 'label': lbl, 'vol': i_tots[inst]}
    hrly     = defaultdict(int)
    for d in dates:
        for h, v in _g_hour_d.get(d, {}).items():
            hrly[h] += v
    return {
        'total':     total,
        'coverage':  coverage,
        'active':    len(_active_g),
        'top':       [_top(ranked[i] if i < len(ranked) else None) for i in range(2)],
        'peak_hour': max(hrly, key=hrly.get) if hrly else None,
    }

_FR_MONTHS_SHORT = ['jan.','fév.','mar.','avr.','mai','juin','juil.','août','sep.','oct.','nov.','déc.']
_FR_MONTHS_LONG  = ['janvier','février','mars','avril','mai','juin','juillet','août',
                    'septembre','octobre','novembre','décembre']

def _fmt_d(s):
    dt = datetime.fromisoformat(s)
    return f"{dt.day} {_FR_MONTHS_SHORT[dt.month - 1]}"

_gs = {}
if _g_daily:
    _max_data = max(_g_daily.keys())
    _today    = datetime.now().strftime('%Y-%m-%d')
    # "Hier" = jour civil d'avant aujourd'hui; si données trop récentes (aujourd'hui),
    # on recule d'un jour supplémentaire pour éviter les journées partielles.
    _hier     = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    _hier_dt  = datetime.fromisoformat(_hier)
    _week     = [(_hier_dt - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
    _month    = sorted(d for d in _g_daily if d[:7] == _hier[:7] and d <= _hier)
    # Classement des arrondissements (7 derniers jours)
    _arr_vols = defaultdict(int)
    for _inst in _active_g:
        _arr = counter_locations.get(_inst, {}).get('arrondissement', 'Montréal')
        for _d in _week:
            _arr_vols[_arr] += _g_ctr_day[_inst].get(_d, 0)
    _arr_rank = sorted(_arr_vols.items(), key=lambda x: x[1], reverse=True)[:6]

    # Statut du réseau
    _net = {g: sum(1 for q in all_quality.values() if q.get('group') == g) for g in ('A', 'B', 'C')}
    _net['total'] = len(all_quality)

    _gs = {
        'yesterday': {**_gstat([_hier]),
                      'subtitle': _fmt_d(_hier) + ' ' + _hier[:4]},
        'week':      {**_gstat(_week),
                      'subtitle': _fmt_d(_week[-1]) + ' – ' + _fmt_d(_week[0])},
        'month':     {**_gstat(_month),
                      'subtitle': _FR_MONTHS_LONG[int(_hier[5:7]) - 1].capitalize() + ' ' + _hier[:4]},
        'arr_rank':  [{'name': a, 'vol': v} for a, v in _arr_rank],
        'net_status': _net,
    }
    print(f"Vue globale : {len(_active_g)} compteurs actifs, hier = {_hier}, max données = {_max_data}.")

site_data['globalStats'] = _gs

os.makedirs(os.path.dirname(SITE_DATA_FILE), exist_ok=True)
with open(SITE_DATA_FILE, 'w', encoding='utf-8') as f:
    json.dump(site_data, f, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

print(f"Données générées : {SITE_DATA_FILE} ({os.path.getsize(SITE_DATA_FILE) / 1e6:.1f} Mo)")
