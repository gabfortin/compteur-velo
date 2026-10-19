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

try:
    with open('version.txt') as _vf:
        _APP_VERSION = _vf.read().strip()
except FileNotFoundError:
    _APP_VERSION = '?'

# Fenêtre glissante de données intégrées au HTML
WINDOW_DAYS = 365
# Au-delà de HOURLY_DAYS (comptés depuis la dernière donnée de chaque compteur),
# seuls les totaux journaliers sont intégrés — allège le HTML de ~50 %.
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

# Générer le HTML
html_parts = ['''<html lang="fr">
<head>
    <meta charset="utf-8">
    <script data-goatcounter="https://gabfortin.goatcounter.com/count"
            async src="//gc.zgo.at/count.js"></script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <title>Compteurs Vélo Montréal</title>
    <link rel="icon" type="image/png" href="favico.png">
    <link rel="apple-touch-icon" href="favico.png">
    <meta name="description" content="Passages de cyclistes à Montréal, compteur par compteur : graphiques horaires et journaliers, carte et historique depuis 2009, à partir des données ouvertes de la Ville. Mis à jour chaque jour.">
    <meta name="author" content="Gabriel Fortin">
    <meta name="theme-color" content="#1DB860">
    <link rel="canonical" href="https://velo.gabfortin.com/">
    <!-- Aperçu lors du partage (Open Graph / Twitter) -->
    <meta property="og:type" content="website">
    <meta property="og:site_name" content="Compteurs Vélo Montréal">
    <meta property="og:locale" content="fr_CA">
    <meta property="og:url" content="https://velo.gabfortin.com/">
    <meta property="og:title" content="Compteurs Vélo Montréal">
    <meta property="og:description" content="Passages de cyclistes, compteur par compteur, mis à jour chaque jour à partir des données ouvertes de Montréal.">
    <meta property="og:image" content="https://velo.gabfortin.com/og-image.png">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta property="og:image:alt" content="Compteurs Vélo Montréal — passages de cyclistes, compteur par compteur">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="Compteurs Vélo Montréal">
    <meta name="twitter:description" content="Passages de cyclistes, compteur par compteur, mis à jour chaque jour à partir des données ouvertes de Montréal.">
    <meta name="twitter:image" content="https://velo.gabfortin.com/og-image.png">
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "WebSite", "name": "Compteurs Vélo Montréal",
     "url": "https://velo.gabfortin.com/", "inLanguage": "fr-CA",
     "description": "Passages de cyclistes à Montréal, compteur par compteur, à partir des données ouvertes de la Ville.",
     "author": {"@type": "Person", "name": "Gabriel Fortin", "url": "https://gabfortin.com"}}
    </script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">
    <style>
        :root {
            --accent: #1DB860;
            --accent-strong: #129a4c;
            --accent-soft: rgba(29,184,96,0.12);
            --accent-ring: rgba(29,184,96,0.22);
            --text: #0f1f17;
            --text-2: #3f5248;
            --muted: #6b7d74;
            --faint: #9aa9a1;
            --glass: rgba(255,255,255,0.52);
            --glass-strong: rgba(255,255,255,0.72);
            --glass-soft: rgba(255,255,255,0.34);
            --glass-border: rgba(255,255,255,0.75);
            --hairline: rgba(15,40,28,0.08);
            --blur: saturate(180%) blur(22px);
            --shadow: 0 1px 1px rgba(15,40,28,0.03), 0 10px 36px -8px rgba(15,40,28,0.12);
            --shadow-lg: 0 2px 2px rgba(15,40,28,0.03), 0 24px 60px -16px rgba(15,40,28,0.20);
            --inner: inset 0 1px 0 rgba(255,255,255,0.85);
            --r-lg: 22px;
            --r-md: 14px;
            --r-sm: 10px;
            --ease: cubic-bezier(.22,.68,0,1);
        }
        * { box-sizing: border-box; }
        html { background: #eaf3ee; }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-feature-settings: 'cv11', 'ss01', 'tnum';
            -webkit-font-smoothing: antialiased;
            background: transparent;
            color: var(--text);
            margin: 0;
            padding: 0 12px 12px;
            min-height: 100vh;
            position: relative;
        }
        /* Fond « mesh » : taches de couleur floues fixes derrière les surfaces vitrées */
        body::before {
            content: '';
            position: fixed;
            inset: -10vmax;
            z-index: -2;
            background:
                radial-gradient(38vmax 38vmax at 12% 8%,  rgba(29,184,96,0.38), transparent 62%),
                radial-gradient(34vmax 34vmax at 88% 12%, rgba(41,171,226,0.30), transparent 62%),
                radial-gradient(40vmax 40vmax at 78% 88%, rgba(139,92,246,0.18), transparent 62%),
                radial-gradient(36vmax 36vmax at 8% 92%,  rgba(250,204,21,0.20), transparent 62%),
                linear-gradient(160deg, #f3faf6 0%, #e9f4f1 50%, #eef1f8 100%);
            animation: meshDrift 28s ease-in-out infinite alternate;
        }
        body::after {
            content: '';
            position: fixed;
            inset: 0;
            z-index: -1;
            pointer-events: none;
            opacity: 0.35;
            background-image: radial-gradient(rgba(15,40,28,0.07) 1px, transparent 1px);
            background-size: 22px 22px;
            -webkit-mask-image: linear-gradient(to bottom, #000, transparent 70%);
                    mask-image: linear-gradient(to bottom, #000, transparent 70%);
        }
        @keyframes meshDrift {
            from { transform: translate3d(0,0,0) scale(1); }
            to   { transform: translate3d(-2vmax,1.5vmax,0) scale(1.06); }
        }
        ::selection { background: rgba(29,184,96,0.25); }
        @keyframes fadeDown {
            from { opacity: 0; transform: translateY(-14px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes fadeUp {
            from { opacity: 0; transform: translateY(16px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes fadeIn {
            from { opacity: 0; }
            to   { opacity: 1; }
        }
        /* Surface vitrée partagée */
        .container, .stat-card, .global-card, .global-section, #topbar,
        .source-card, #map-legend, #combineSection {
            -webkit-backdrop-filter: var(--blur);
                    backdrop-filter: var(--blur);
        }
        .site-header {
            text-align: center;
            padding: 36px 16px 26px;
            max-width: 1200px;
            margin: 0 auto;
            animation: fadeDown 0.6s var(--ease) both;
        }
        .site-header img {
            box-shadow: 0 12px 32px -8px rgba(15,40,28,0.35), 0 0 0 1px rgba(255,255,255,0.8) !important;
        }
        h1 {
            font-size: 28px;
            font-weight: 800;
            letter-spacing: -0.035em;
            margin: 0 0 8px 0;
            background: linear-gradient(120deg, #0f1f17 30%, #129a4c 100%);
            -webkit-background-clip: text;
                    background-clip: text;
            color: transparent;
        }
        .subtitle {
            color: var(--text-2);
            font-size: 13px;
            margin: 0;
            font-weight: 400;
            line-height: 1.5;
        }
        .subtitle a { color: var(--accent-strong); text-decoration: underline; }
        @media (max-width: 767px) {
            .subtitle { display: none; }
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: var(--glass);
            border-radius: var(--r-lg);
            padding: 16px;
            border: 1px solid var(--glass-border);
            box-shadow: var(--shadow-lg), var(--inner);
            animation: fadeUp 0.55s 0.08s var(--ease) both;
        }
        .select-wrapper {
            position: relative;
            margin-bottom: 18px;
        }
        .select-wrapper::after {
            content: '';
            position: absolute;
            right: 18px;
            top: 50%;
            width: 7px; height: 7px;
            border-right: 2px solid var(--muted);
            border-bottom: 2px solid var(--muted);
            transform: translateY(-70%) rotate(45deg);
            pointer-events: none;
        }
        select {
            width: 100%;
            padding: 14px 44px 14px 16px;
            border: 1px solid var(--glass-border);
            border-radius: var(--r-md);
            background: var(--glass-strong);
            color: var(--text);
            font: inherit;
            font-size: 15px;
            font-weight: 500;
            box-shadow: 0 1px 2px rgba(15,40,28,0.05), var(--inner);
            -webkit-appearance: none;
            -moz-appearance: none;
            appearance: none;
            cursor: pointer;
            transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
            outline: none;
        }
        select:hover { background: rgba(255,255,255,0.85); }
        select:focus {
            border-color: rgba(29,184,96,0.55);
            box-shadow: 0 0 0 4px var(--accent-ring);
        }
        .stats-row {
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            margin-bottom: 18px;
        }
        .stat-card {
            background: var(--glass-soft);
            border: 1px solid var(--glass-border);
            border-radius: var(--r-md);
            padding: 14px 8px;
            text-align: center;
            box-shadow: var(--inner);
            transition: transform 0.25s var(--ease), box-shadow 0.25s, background 0.25s;
        }
        .stat-card:hover {
            transform: translateY(-2px);
            background: var(--glass-strong);
            box-shadow: var(--shadow), var(--inner);
        }
        .stat-value {
            font-size: 20px;
            font-weight: 750;
            letter-spacing: -0.02em;
            color: var(--accent-strong);
            line-height: 1.2;
        }
        .stat-label {
            font-size: 10px;
            color: var(--muted);
            margin-top: 4px;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-weight: 600;
        }
        .day-label {
            text-align: center;
            font-size: 13px;
            color: var(--accent-strong);
            font-weight: 600;
            margin: -6px 0 14px 0;
            animation: fadeIn 0.3s ease both;
        }
        .table-container { display: none; }
        .table-container.visible {
            display: block;
            animation: fadeIn 0.35s ease both;
        }
        h2 {
            color: var(--text);
            margin: 16px 0 12px 0;
            font-size: 18px;
            font-weight: 700;
            letter-spacing: -0.02em;
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
        }
        h2::before {
            content: '';
            display: inline-block;
            width: 8px;
            height: 8px;
            background: var(--accent);
            border-radius: 50%;
            box-shadow: 0 0 0 4px var(--accent-soft);
            flex-shrink: 0;
        }
        canvas {
            max-width: 100%;
            height: 280px;
            margin-bottom: 16px;
        }
        p { margin: 8px 0; font-weight: normal; font-size: 14px; color: var(--text-2); }
        p strong { color: var(--accent-strong); }
        .watermark {
            text-align: center;
            padding: 16px 10px;
            color: var(--faint);
            font-size: 12px;
            margin-top: 30px;
            border-top: 1px solid var(--hairline);
        }
        .watermark a { color: var(--muted); text-decoration: none; transition: color 0.2s; }
        .watermark a:hover { color: var(--accent-strong); }
        .version-badge {
            position: fixed;
            bottom: 8px;
            right: 12px;
            font-size: 10px;
            color: rgba(100,120,110,0.45);
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            z-index: 999;
            pointer-events: none;
            user-select: none;
        }
        /* Boutons : pastilles vitrées, état actif en vert plein */
        .period-btn, .dir-btn, .hist-btn, .bixi-btn, .toggle-select, .combine-select, .hist-year-select {
            font-family: inherit;
            border: 1px solid var(--glass-border);
            background: var(--glass-soft);
            color: var(--text-2);
            box-shadow: 0 1px 2px rgba(15,40,28,0.04), var(--inner);
            transition: background 0.2s, color 0.2s, box-shadow 0.25s, transform 0.2s var(--ease), border-color 0.2s;
        }
        .period-btn:hover, .dir-btn:hover, .hist-btn:hover, .bixi-btn:hover {
            background: var(--glass-strong);
            color: var(--text);
        }
        .period-btn.active, .dir-btn.active, .hist-btn.active {
            background: linear-gradient(180deg, #24c96b, #149a4f);
            color: #fff;
            border-color: rgba(255,255,255,0.35);
            box-shadow: 0 6px 18px -6px rgba(20,154,79,0.6), inset 0 1px 0 rgba(255,255,255,0.35);
        }
        .period-buttons {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-top: 14px;
            margin-bottom: 10px;
        }
        #datepickerWrapper {
            text-align: center;
            margin-bottom: 18px;
            display: none;
        }
        #specificDatePicker {
            padding: 10px 16px;
            border: 1px solid var(--glass-border);
            border-radius: 999px;
            font: inherit;
            font-size: 14px;
            font-weight: 600;
            color: var(--accent-strong);
            background: var(--glass-strong);
            cursor: pointer;
            outline: none;
            box-shadow: var(--shadow), var(--inner);
            transition: border-color 0.2s, box-shadow 0.2s;
        }
        #specificDatePicker:focus {
            box-shadow: 0 0 0 4px var(--accent-ring);
        }
        .period-btn {
            padding: 11px 12px;
            border-radius: 999px;
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            text-align: center;
        }
        .period-btn:active, .dir-btn:active, .hist-btn:active, .bixi-btn:active { transform: scale(0.97); }
        .desktop-only { display: none; }
        .mobile-only { display: block; }
        #chart-map-layout {
            display: flex;
            flex-direction: column;
            gap: 16px;
        }
        #chart-area { min-width: 0; }
        #map-wrapper {
            position: relative;
            width: 100%;
            height: 300px;
            flex-shrink: 0;
            isolation: isolate;
        }
        #map {
            width: 100%;
            height: 100%;
            border-radius: var(--r-md);
            box-shadow: var(--shadow);
            border: 1px solid var(--glass-border);
            overflow: hidden;
        }
        #cyclosm-btn {
            position: absolute;
            top: 10px;
            right: 10px;
            z-index: 1000;
            background: rgba(255,255,255,0.75);
            -webkit-backdrop-filter: var(--blur);
                    backdrop-filter: var(--blur);
            border: 1px solid var(--glass-border);
            border-radius: 999px;
            padding: 6px 12px;
            font: inherit;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-2);
            cursor: pointer;
            box-shadow: var(--shadow);
            transition: background 0.2s, color 0.2s;
        }
        #cyclosm-btn.active {
            background: var(--accent);
            color: #fff;
            border-color: rgba(255,255,255,0.4);
        }
        .leaflet-container { font-family: inherit !important; }
        .leaflet-bar { border: none !important; box-shadow: var(--shadow) !important; border-radius: 12px !important; overflow: hidden; }
        .leaflet-bar a { background: rgba(255,255,255,0.85) !important; -webkit-backdrop-filter: var(--blur); backdrop-filter: var(--blur); color: var(--text) !important; }
        .leaflet-popup-content-wrapper, .leaflet-popup-tip {
            background: rgba(255,255,255,0.88) !important;
            -webkit-backdrop-filter: var(--blur);
                    backdrop-filter: var(--blur);
            box-shadow: var(--shadow-lg) !important;
        }
        .leaflet-popup-content-wrapper { border-radius: var(--r-md) !important; }
        @media (min-width: 768px) {
            #chart-map-layout { display: grid; grid-template-columns: 1fr 320px; gap: 20px; }
            #map-wrapper { height: 100%; min-height: 300px; }
            #map { height: 100%; }
        }
        @media (max-width: 767px) {
            #dataWarning { font-size: 11px; padding: 7px 10px; gap: 7px; }
            #anomalyWarning { font-size: 11px; padding: 7px 10px; gap: 7px; }
        }
        .toggle-select {
            padding: 7px 12px;
            border-radius: var(--r-sm);
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            outline: none;
            width: 100%;
        }
        .toggle-select:focus, .combine-select:focus, .hist-year-select:focus { box-shadow: 0 0 0 4px var(--accent-ring); border-color: rgba(29,184,96,0.55); }
        .dir-toggle {
            display: flex;
            gap: 6px;
            margin-bottom: 14px;
            justify-content: flex-end;
        }
        .dir-btn {
            padding: 6px 13px;
            border-radius: 999px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
        }
        .bixi-btn {
            padding: 6px 13px;
            color: #c03030;
            border-radius: 999px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
        }
        .bixi-btn.active {
            background: linear-gradient(180deg, #ef4444, #c81e1e);
            color: #fff;
            border-color: rgba(255,255,255,0.35);
            box-shadow: 0 6px 18px -6px rgba(200,30,30,0.55), inset 0 1px 0 rgba(255,255,255,0.3);
        }
        #combineSection {
            display: none;
            margin-bottom: 12px;
            padding: 10px 14px;
            background: var(--glass-soft);
            border-radius: var(--r-md);
            border: 1px solid var(--glass-border);
            box-shadow: var(--inner);
        }
        #combineSectionInner {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }
        #combineSectionInner label {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-2);
            white-space: nowrap;
        }
        .combine-select {
            flex: 1;
            min-width: 200px;
            padding: 7px 12px;
            border-radius: var(--r-sm);
            font-size: 13px;
            color: var(--text);
            cursor: pointer;
            outline: none;
        }
        .combine-clear-btn {
            padding: 5px 12px;
            border: 1px solid rgba(220,38,38,0.25);
            border-radius: 999px;
            background: rgba(220,38,38,0.07);
            color: #dc2626;
            cursor: pointer;
            font: inherit;
            font-size: 12px;
            font-weight: 600;
            white-space: nowrap;
            display: none;
        }
        .combine-clear-btn:hover { background: rgba(220,38,38,0.14); }
        #dataWarning, #anomalyWarning, #vfQualityWarning {
            display: none;
            align-items: flex-start;
            gap: 10px;
            padding: 11px 14px;
            border-radius: var(--r-md);
            font-size: 13px;
            line-height: 1.5;
            margin-bottom: 14px;
            -webkit-backdrop-filter: var(--blur);
                    backdrop-filter: var(--blur);
        }
        #dataWarning, #anomalyWarning, #vfQualityWarning.vf-warn-lag {
            background: rgba(255,244,214,0.6);
            border: 1px solid rgba(245,158,11,0.32);
            color: #8a5a06;
        }
        #vfQualityWarning.vf-warn-stopped {
            background: rgba(254,226,226,0.6);
            border: 1px solid rgba(239,68,68,0.3);
            color: #991b1b;
        }
        #dataWarning .warn-icon, #anomalyWarning .warn-icon, #vfQualityWarning .warn-icon { font-size: 16px; flex-shrink: 0; margin-top: 1px; }
        #anomalyWarning .anomaly-body { flex: 1; }
        #noDataMsg {
            display: none;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 10px;
            padding: 48px 24px;
            border: 1.5px dashed rgba(15,40,28,0.14);
            background: var(--glass-soft);
            border-radius: var(--r-md);
            color: var(--muted);
            font-size: 15px;
            font-weight: 500;
            text-align: center;
            margin-bottom: 16px;
        }
        #noDataMsg span.icon { font-size: 36px; }
        .bixi-exceeds-badge {
            display: inline-block;
            background: rgba(220,38,38,0.10);
            color: #b91c1c;
            border: 1px solid rgba(220,38,38,0.25);
            border-radius: 999px;
            padding: 0 7px;
            font-size: 11px;
            font-weight: 600;
            margin-left: 5px;
            vertical-align: middle;
        }
        .counter-type-badge {
            font-size: 10px;
            font-weight: 600;
            padding: 3px 9px;
            border-radius: 999px;
            vertical-align: middle;
            margin-left: 2px;
            letter-spacing: 0.02em;
            white-space: nowrap;
            text-decoration: none;
            cursor: pointer;
            transition: opacity 0.15s;
        }
        .counter-type-badge:hover { opacity: 0.75; }
        .counter-type-fut, .mbadge-fut {
            background: rgba(41,171,226,0.12);
            color: #0369a1;
            border: 1px solid rgba(41,171,226,0.25);
        }
        .counter-type-boucle, .mbadge-boucle {
            background: rgba(139,92,246,0.12);
            color: #6d28d9;
            border: 1px solid rgba(139,92,246,0.25);
        }
        #map-legend {
            position: absolute;
            bottom: 10px;
            left: 10px;
            z-index: 1000;
            background: rgba(255,255,255,0.7);
            border: 1px solid var(--glass-border);
            border-radius: 12px;
            padding: 8px 11px;
            font-size: 11px;
            font-weight: 500;
            color: var(--text-2);
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 5px;
            box-shadow: var(--shadow);
            pointer-events: none;
        }
        .legend-item { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
        .legend-dot {
            display: inline-block;
            width: 9px; height: 9px;
            border-radius: 50%;
            flex-shrink: 0;
        }
        .anomaly-info-btn {
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 18px;
            height: 18px;
            border-radius: 50%;
            background: rgba(245,158,11,0.2);
            color: #8a5a06;
            font-size: 11px;
            font-weight: 700;
            flex-shrink: 0;
            margin-top: 1px;
            position: relative;
        }
        .anomaly-info-btn::after {
            content: attr(data-tooltip);
            position: absolute;
            top: calc(100% + 8px);
            right: 0;
            background: rgba(15,25,20,0.82);
            -webkit-backdrop-filter: var(--blur);
                    backdrop-filter: var(--blur);
            color: #fff;
            font-size: 12px;
            font-weight: 400;
            line-height: 1.55;
            padding: 10px 13px;
            border-radius: 12px;
            width: 290px;
            white-space: normal;
            text-align: left;
            pointer-events: none;
            opacity: 0;
            transition: opacity 0.18s;
            z-index: 200;
        }
        .anomaly-info-btn:hover::after,
        .anomaly-info-btn:focus::after { opacity: 1; }
        @media (min-width: 768px) {
            .desktop-only { display: block; }
            .mobile-only { display: none; }
            body { padding: 0 20px 20px; }
            h1 { font-size: 40px; }
            .subtitle { font-size: 14px; }
            .container { padding: 28px; }
            select { font-size: 16px; }
            h2 { font-size: 21px; margin: 20px 0 12px 0; }
            canvas { height: 300px; margin-bottom: 20px; }
            p { font-size: 15px; margin: 10px 0; font-weight: 600; }
            .watermark { padding: 20px 10px; font-size: 13px; margin-top: 40px; }
            .period-buttons {
                display: flex; flex-wrap: wrap; gap: 4px;
                justify-content: center; margin: 18px auto 22px;
                grid-template-columns: unset;
                width: fit-content;
                padding: 5px;
                border-radius: 999px;
                background: rgba(15,40,28,0.05);
                border: 1px solid rgba(255,255,255,0.6);
                box-shadow: inset 0 1px 2px rgba(15,40,28,0.06);
            }
            .period-buttons .period-btn {
                background: transparent; border-color: transparent; box-shadow: none;
            }
            .period-buttons .period-btn:hover { background: rgba(255,255,255,0.6); }
            .period-buttons .period-btn.active {
                background: #fff; color: var(--accent-strong); border-color: rgba(255,255,255,0.9);
                box-shadow: 0 2px 8px -2px rgba(15,40,28,0.18);
            }
            .period-btn { padding: 9px 18px; font-size: 13.5px; flex: 0 1 auto; }
            .stats-row { gap: 14px; }
            .stat-card { padding: 18px 10px; }
            .stat-value { font-size: 28px; }
            .stat-label { font-size: 11px; }
        }
        /* ── Historique ── */
        #hist-btns {
            display: none;
            gap: 6px;
            margin-bottom: 14px;
            flex-wrap: wrap;
        }
        .hist-btn {
            padding: 7px 14px;
            border-radius: 999px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
            white-space: nowrap;
        }
        #hist-panel {
            display: none;
            margin-top: 6px;
            padding: 14px 0 4px;
            border-top: 1px solid var(--hairline);
            animation: fadeIn 0.3s ease both;
        }
        #hist-panel-title {
            font-size: 11px;
            font-weight: 700;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 10px;
        }
        #hist-compare-year-wrap {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 10px;
        }
        #hist-compare-year-wrap label {
            font-size: 12px;
            color: var(--muted);
            white-space: nowrap;
        }
        .hist-year-select {
            padding: 6px 12px;
            border-radius: var(--r-sm);
            font-size: 13px;
            color: var(--text);
            cursor: pointer;
            outline: none;
        }
        #hist-compare-vs {
            font-size: 12px;
            font-weight: 700;
            color: var(--faint);
            flex-shrink: 0;
        }
        #hist-view-toggle {
            display: flex;
            gap: 6px;
            margin-bottom: 10px;
        }
        /* ── Topbar : barre flottante vitrée ── */
        #topbar {
            position: sticky;
            top: 10px;
            height: 54px;
            max-width: 1200px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 8px 0 8px;
            margin: 10px auto 0;
            background: rgba(255,255,255,0.58);
            border: 1px solid var(--glass-border);
            border-radius: 18px;
            z-index: 1100;
            box-shadow: var(--shadow), var(--inner);
            animation: fadeDown 0.5s var(--ease) both;
        }
        #topbar-nav { display: flex; align-items: center; gap: 2px; }
        .nav-link {
            text-decoration: none;
            font-size: 13px;
            font-weight: 550;
            color: var(--text-2);
            padding: 8px 14px;
            border-radius: 12px;
            transition: background 0.25s, color 0.25s, box-shadow 0.25s;
        }
        .nav-link:hover { background: rgba(255,255,255,0.6); color: var(--text); }
        .nav-link.active {
            background: #fff;
            color: var(--accent-strong);
            box-shadow: 0 2px 8px -2px rgba(15,40,28,0.15);
        }
        button.nav-link { background: none; border: none; cursor: pointer; font-family: inherit; }
        button.nav-link.active { background: #fff; }
        /* ── Page Méthodologie ───────────────────────────────────────── */
        #methodo-page { max-width: 820px; margin: 0 auto; padding: 32px 16px 60px; }
        .methodo-hero { text-align: center; margin-bottom: 40px; }
        .methodo-hero h2 { font-size: 30px; font-weight: 800; letter-spacing: -0.03em; color: var(--text); margin-bottom: 8px; justify-content: center; }
        .methodo-hero h2::before { display: none; }
        .methodo-hero p { font-size: 15px; color: var(--muted); line-height: 1.6; font-weight: 400; }
        .methodo-section {
            margin-bottom: 22px;
            padding: 22px 22px 14px;
            background: var(--glass);
            border: 1px solid var(--glass-border);
            border-radius: var(--r-lg);
            box-shadow: var(--shadow), var(--inner);
            -webkit-backdrop-filter: var(--blur);
                    backdrop-filter: var(--blur);
        }
        .methodo-section > h3 { font-size: 16px; font-weight: 700; letter-spacing: -0.01em; color: var(--text); margin: 0 0 16px;
            padding-bottom: 10px; border-bottom: 1px solid var(--hairline); display: flex; align-items: center; gap: 8px; }
        .methodo-section p { font-size: 14px; color: var(--text-2); line-height: 1.75; margin-bottom: 10px; font-weight: 400; }
        .methodo-section ul { font-size: 14px; color: var(--text-2); line-height: 1.75; padding-left: 22px; margin-bottom: 12px; }
        .methodo-section li { margin-bottom: 3px; }
        .source-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
        @media (max-width: 600px) { .source-grid { grid-template-columns: 1fr; } }
        .source-card { background: var(--glass-soft); border: 1px solid var(--glass-border); border-radius: var(--r-md);
            padding: 16px; display: flex; flex-direction: column; gap: 6px; box-shadow: var(--inner); }
        .source-card-top { display: flex; align-items: center; gap: 10px; }
        .source-card-icon { font-size: 22px; flex-shrink: 0; }
        .source-card-title { font-size: 14px; font-weight: 700; color: var(--text); }
        .source-card-sub { font-size: 12px; color: var(--faint); }
        .source-card-desc { font-size: 13px; color: var(--muted); line-height: 1.6; }
        .source-card-desc code { background: rgba(15,40,28,0.06) !important; }
        .source-card-link { font-size: 12px; color: var(--accent-strong); text-decoration: none; font-weight: 600; }
        .source-card-link:hover { text-decoration: underline; }
        .mbadge { font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
        .mbadge-bixi   { background: rgba(220,38,38,0.09);  color: #b91c1c; border: 1px solid rgba(220,38,38,0.25); }
        .mbadge-meteo  { background: rgba(251,191,36,0.12); color: #b45309; border: 1px solid rgba(251,191,36,0.28); }
        .algo-box { background: rgba(220,252,231,0.55); border: 1px solid rgba(34,197,94,0.25); border-radius: var(--r-sm);
            padding: 14px 16px; margin-bottom: 12px; font-size: 13.5px; color: #166534; line-height: 1.75; }
        .algo-box strong { color: #15803d; }
        .algo-box code { background: rgba(21,128,61,0.1); border-radius: 4px; padding: 1px 5px; font-size: 12px; }
        .algo-warn { background: rgba(254,243,199,0.6); border: 1px solid rgba(245,158,11,0.3); border-radius: var(--r-sm);
            padding: 14px 16px; margin-bottom: 12px; font-size: 13.5px; color: #92400e; line-height: 1.75; }
        .pipeline-steps { counter-reset: step; list-style: none; padding: 0; }
        .pipeline-steps li { counter-increment: step; display: flex; align-items: flex-start;
            gap: 14px; padding: 12px 0; border-bottom: 1px solid var(--hairline); font-size: 14px; color: var(--text-2); }
        .pipeline-steps li:last-child { border-bottom: none; }
        .pipeline-steps li::before { content: counter(step); background: linear-gradient(180deg, #24c96b, #149a4f); color: #fff;
            font-size: 11px; font-weight: 700; width: 22px; height: 22px; border-radius: 50%;
            display: flex; align-items: center; justify-content: center; flex-shrink: 0; margin-top: 1px;
            box-shadow: 0 4px 10px -3px rgba(20,154,79,0.6); }
        #topbar-profile {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 4px 4px 4px 10px;
            border-radius: 999px;
            text-decoration: none;
            color: var(--text-2);
            transition: color 0.25s, background 0.25s;
        }
        #topbar-profile:hover { color: var(--text); background: rgba(255,255,255,0.6); }
        #topbar-profile:hover img { box-shadow: 0 0 0 2px var(--accent); }
        #topbar-profile-name { font-size: 13px; font-weight: 500; display: none; }
        .nav-label-short { display: inline; }
        .nav-label-full  { display: none; }
        #topbar-profile img {
            width: 34px;
            height: 34px;
            border-radius: 50%;
            object-fit: cover;
            display: block;
            flex-shrink: 0;
            box-shadow: 0 0 0 2px rgba(255,255,255,0.9), 0 2px 8px rgba(15,40,28,0.15);
            transition: box-shadow 0.25s;
        }
        @media (min-width: 768px) {
            #topbar { top: 14px; margin-top: 14px; padding: 0 8px 0 10px; }
            #topbar-profile-name { display: inline; }
            .nav-label-short { display: none; }
            .nav-label-full  { display: inline; }
        }
        /* ── Vue globale ── */
        #global-page { max-width: 1200px; margin: 0 auto; padding: 0 12px 40px; }
        #global-cards { display: grid; grid-template-columns: 1fr; gap: 14px; margin-bottom: 20px; }
        .global-card {
            background: var(--glass);
            border: 1px solid var(--glass-border);
            border-radius: var(--r-lg);
            padding: 22px 22px 20px;
            box-shadow: var(--shadow), var(--inner);
            transition: transform 0.25s var(--ease), box-shadow 0.25s, background 0.25s;
        }
        .global-card:hover {
            transform: translateY(-3px);
            background: var(--glass-strong);
            box-shadow: var(--shadow-lg), var(--inner);
        }
        .global-card-title {
            font-size: 11px; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.08em; color: var(--muted); margin-bottom: 2px;
        }
        .global-card-date {
            font-size: 12px; color: var(--faint); margin-bottom: 10px;
        }
        .global-card-total {
            font-size: 36px; font-weight: 800; letter-spacing: -0.035em; color: var(--accent-strong); line-height: 1.05;
        }
        .global-card-sub {
            font-size: 11px; color: var(--faint); text-transform: uppercase;
            letter-spacing: 0.06em; margin-top: 4px; margin-bottom: 14px;
        }
        .global-card-meta { display: flex; flex-direction: column; gap: 5px; font-size: 13px; color: var(--text-2); }
        .global-card-meta strong { color: var(--text); }
        .global-card-clickable { cursor: pointer; }
        .global-card-cta {
            margin-top: 14px; font-size: 12px; font-weight: 600;
            color: var(--accent-strong); letter-spacing: 0.01em;
        }
        .global-top-row {
            display: flex; align-items: baseline; gap: 6px;
            cursor: pointer; border-radius: 8px; padding: 3px 6px; margin: 0 -6px;
            transition: background 0.15s;
        }
        .global-top-row:hover { background: rgba(255,255,255,0.7); }
        .global-top-rank { font-size: 13px; flex-shrink: 0; }
        .global-top-vol { margin-left: auto; font-size: 12px; color: var(--muted); white-space: nowrap; }
        #global-bottom {
            display: grid; grid-template-columns: 1fr; gap: 14px; margin-bottom: 20px;
        }
        .global-section {
            background: var(--glass); border: 1px solid var(--glass-border); border-radius: var(--r-lg);
            padding: 20px 22px; box-shadow: var(--shadow), var(--inner);
        }
        .global-section-title {
            font-size: 13px; font-weight: 700; color: var(--text);
            margin-bottom: 16px; display: flex; align-items: center; gap: 7px;
        }
        .arr-row {
            display: flex; align-items: center; gap: 10px;
            font-size: 13px; margin-bottom: 10px; color: var(--text-2);
        }
        .arr-name { width: 160px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex-shrink: 0; }
        .arr-bar-wrap { flex: 1; height: 8px; background: rgba(15,40,28,0.06); border-radius: 999px; overflow: hidden; }
        .arr-bar { height: 100%; background: linear-gradient(to right, #7ee8a2, #1DB860); border-radius: 999px; }
        .arr-vol { font-size: 11px; color: var(--muted); white-space: nowrap; min-width: 52px; text-align: right; }
        .net-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; text-align: center; }
        .net-val { font-size: 32px; font-weight: 800; letter-spacing: -0.03em; line-height: 1.1; }
        .net-label { font-size: 11px; color: var(--muted); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.06em; }
        /* ── Vélo d\'hiver — thème glace ── */
        #hiver-page {
            max-width: 1200px; margin: 0 auto; padding: 0 12px 40px; position: relative;
            --accent: #0EA5D8;
            --accent-strong: #0A7FAE;
            --accent-soft: rgba(14,165,216,0.12);
            --accent-ring: rgba(14,165,216,0.22);
            --glass: rgba(233,248,253,0.55);
            --glass-strong: rgba(233,248,253,0.75);
            --glass-soft: rgba(233,248,253,0.34);
            --glass-border: rgba(255,255,255,0.8);
        }
        #hiver-page::before {
            content: \'\';
            position: fixed;
            inset: -10vmax;
            z-index: -2;
            background:
                radial-gradient(38vmax 38vmax at 15% 8%,  rgba(56,189,248,0.35), transparent 62%),
                radial-gradient(34vmax 34vmax at 88% 12%, rgba(14,165,233,0.28), transparent 62%),
                radial-gradient(40vmax 40vmax at 78% 90%, rgba(99,179,237,0.20), transparent 62%),
                radial-gradient(36vmax 36vmax at 8% 92%,  rgba(165,243,252,0.32), transparent 62%),
                linear-gradient(160deg, #eef9fd 0%, #e4f3fb 50%, #eaf2fb 100%);
            animation: meshDrift 28s ease-in-out infinite alternate;
        }
        #hiver-page h1 {
            background: linear-gradient(120deg, #0f1f17 30%, #0A7FAE 100%);
            -webkit-background-clip: text;
                    background-clip: text;
        }
        #nav-hiver.active { color: #0A7FAE; }
        #hiver-cards { display: grid; grid-template-columns: 1fr; gap: 14px; margin-bottom: 20px; }
        .hiver-chart-row { display: flex; flex-direction: column; gap: 20px; }
        .hiver-chart-col { flex: 1; min-width: 0; }
        .hiver-chart-col > div:first-child { font-size: 12px; color: var(--muted); margin-bottom: 6px; text-align: center; }
        .hiver-chart-canvas { position: relative; height: 220px; }
        .net-grid-4 { grid-template-columns: repeat(2, 1fr); }
        @media (min-width: 768px) {
            #global-page { padding: 0 20px 40px; }
            #global-cards { grid-template-columns: repeat(3, 1fr); }
            .global-card-total { font-size: 42px; }
            #global-bottom { grid-template-columns: 2fr 1fr; }
            .arr-name { width: 200px; }
            #hiver-page { padding: 0 20px 40px; }
            #hiver-cards { grid-template-columns: repeat(4, 1fr); }
            .hiver-chart-row { flex-direction: row; }
            .net-grid-4 { grid-template-columns: repeat(4, 1fr); }
        }
        /* Écran de chargement — visible pendant le téléchargement des données inline */
        #loading-screen {
            position: fixed; inset: 0; z-index: 10000;
            display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px;
            background:
                radial-gradient(40vmax 40vmax at 20% 15%, rgba(29,184,96,0.30), transparent 60%),
                radial-gradient(36vmax 36vmax at 85% 85%, rgba(41,171,226,0.25), transparent 60%),
                #eef6f2;
            color: var(--text-2);
            transition: opacity 0.4s ease;
        }
        #loading-screen.done { opacity: 0; pointer-events: none; }
        .loading-wheel {
            width: 48px; height: 48px; border-radius: 50%;
            border: 4px solid rgba(29,184,96,0.15); border-top-color: var(--accent);
            animation: loading-spin 0.9s linear infinite;
        }
        .loading-title { font-size: 18px; font-weight: 700; letter-spacing: -0.02em; color: var(--text); }
        .loading-sub   { font-size: 13px; color: var(--muted); margin-top: -10px; }
        @keyframes loading-spin { to { transform: rotate(360deg); } }
        @media (prefers-reduced-motion: reduce) {
            .loading-wheel { animation-duration: 2.5s; }
            body::before { animation: none; }
        }
        /* Repli si backdrop-filter non supporté : surfaces plus opaques */
        @supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
            :root { --glass: rgba(255,255,255,0.88); --glass-soft: rgba(255,255,255,0.7); }
            #topbar { background: rgba(255,255,255,0.94); }
        }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
</head>
<body>
    <div id="loading-screen" role="status" aria-live="polite">
        <div class="loading-wheel"></div>
        <div class="loading-title">Compteurs Vélo Montréal</div>
        <div class="loading-sub">Chargement des données des 12 derniers mois…</div>
    </div>
    <script>
        // Repli : ne jamais laisser l'écran de chargement bloquer la page (ex. erreur JS)
        window.addEventListener("load", () => setTimeout(() => {
            const ld = document.getElementById("loading-screen");
            if (ld) ld.remove();
        }, 500));
    </script>
    <nav id="topbar">
        <div id="topbar-nav">
            <button class="nav-link active" id="nav-compteurs" onclick="showView(\'main\')">Compteurs</button>
            <button class="nav-link" id="nav-global" onclick="showView(\'global\')"><span class="nav-label-full">Vue Globale</span><span class="nav-label-short">Global</span></button>
            <button class="nav-link" id="nav-hiver" onclick="showView(\'hiver\')"><span class="nav-label-full">Vélo d\'hiver</span><span class="nav-label-short">Hiver</span></button>
            <button class="nav-link" id="nav-methodo" onclick="showView(\'methodo\')">Méthodologie</button>
        </div>
        <a href="https://gabfortin.com" id="topbar-profile" title="gabfortin.com">
            <span id="topbar-profile-name">gabfortin.com</span>
            <img src="img/Gabriel%20Fortin.png" alt="Gabriel Fortin">
        </a>
    </nav>
    <div class="site-header" id="main-header">
        <img src="favico.png" alt="Logo" style="width:72px;height:72px;border-radius:18px;margin-bottom:12px;box-shadow:0 4px 16px rgba(0,0,0,0.1);">
        <h1>Compteurs Vélo Montréal</h1>
    </div>
    <div class="container" id="main-container">
        <div class="select-wrapper desktop-only">
        <select id="counterSelectDesktop">
            <option value="">Sélectionnez un compteur</option>
''']

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

for arrondissement in sorted(by_arrondissement.keys()):
    html_parts.append(f'<optgroup label="{arrondissement}">')
    # Fût en premier, boucles ensuite — dans chaque groupe, tri alphabétique
    sorted_counters = sorted(by_arrondissement[arrondissement],
                             key=lambda x: (x[0].startswith('vf-'), x[1]['rue_1'], x[1].get('rue_2', '')))
    for instance, row in sorted_counters:
        label = counter_label(instance, row)
        prefix = '[Éco-Compteur] ' if instance.startswith('vf-') else '[SUM] '
        html_parts.append(f'<option value="{instance}">{prefix}{label}</option>')
    html_parts.append('</optgroup>')

html_parts.append('''
        </select>
        </div>
        <div class="mobile-only">
        <div class="select-wrapper">
        <select id="arrondissementSelect">
            <option value="">Sélectionnez un arrondissement</option>
''')

for arrondissement in sorted(by_arrondissement.keys()):
    html_parts.append(f'<option value="{arrondissement}">{arrondissement}</option>')

html_parts.append('''
        </select>
        </div>
        <div class="select-wrapper" id="counterSelectWrapper" style="visibility:hidden">
        <select id="counterSelectMobile">
            <option value="">Sélectionnez un compteur</option>
        </select>
        </div>
        </div>
        <div class="period-buttons desktop-only">
            <button class="period-btn" data-days="0">Jour spécifique</button>
            <button class="period-btn active" data-days="7">7 derniers jours</button>
            <button class="period-btn" data-days="30">Dernier mois</button>
            <button class="period-btn" data-days="90">3 derniers mois</button>
            <button class="period-btn" data-days="180">6 derniers mois</button>
            <button class="period-btn" data-days="365">12 derniers mois</button>
        </div>
        <div class="select-wrapper mobile-only" style="margin-top:14px;margin-bottom:14px;">
            <select id="periodSelectMobile">
                <option value="0">Jour spécifique</option>
                <option value="7" selected>7 derniers jours</option>
                <option value="30">Dernier mois</option>
                <option value="90">3 derniers mois</option>
                <option value="180">6 derniers mois</option>
                <option value="365">12 derniers mois</option>
            </select>
        </div>
        <div id="datepickerWrapper"><input type="date" id="specificDatePicker"></div>
        <div id="dayLabel" class="day-label" style="display:none"></div>
        <div class="stats-row" id="statsRow" style="display:none">
            <div class="stat-card">
                <div class="stat-value" id="statTotal">—</div>
                <div class="stat-label">Passages totaux</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="statAvg">—</div>
                <div class="stat-label">Moy. par jour</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="statPeak">—</div>
                <div class="stat-label">Heures de pointe</div>
            </div>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;min-height:30px;gap:8px;">
            <div id="viewToggle" style="display:none;flex-direction:row;gap:6px;">
                <button class="dir-btn active" id="btnTimeline">Dans le temps</button>
                <button class="dir-btn" id="btnDaily">Par jour</button>
            </div>
            <div id="viewToggleMobile" style="display:none;flex:1;">
                <select id="viewSelectMobile" class="toggle-select">
                    <option value="timeline" selected>Dans le temps</option>
                    <option value="daily">Par jour</option>
                </select>
            </div>
            <div style="display:flex;gap:6px;margin-left:auto;align-items:center;">
                <div id="bixiToggle" style="display:none;">
                    <button class="bixi-btn active" id="btnBixi">Bixi</button>
                </div>
                <div id="dirToggle" style="display:none;flex-direction:row;gap:6px;">
                    <button class="dir-btn" id="btnSeparate">Par direction</button>
                    <button class="dir-btn active" id="btnCombined">Combiné</button>
                </div>
                <div id="dirToggleMobile" style="display:none;">
                    <select id="dirSelectMobile" class="toggle-select">
                        <option value="combined" selected>Combiné</option>
                        <option value="separate">Par direction</option>
                    </select>
                </div>
            </div>
        </div>
        <div id="combineSection">
          <div id="combineSectionInner">
            <label>⊕ Combiner avec :</label>
            <select id="combineSelect" class="combine-select">
              <option value="">— Choisir un compteur —</option>
            </select>
            <button id="btnClearCombine" class="combine-clear-btn">✕ Retirer</button>
          </div>
        </div>
        <div id="hist-btns">
            <button class="hist-btn" id="btnHistCompare">📊 Comparer avec une année</button>
            <button class="hist-btn" id="btnHistTimeline">📅 Historique multi-années</button>
        </div>
        <div id="hist-panel">
            <div id="hist-panel-title"></div>
            <!-- Panneau comparaison -->
            <div id="hist-compare-panel" style="display:none;">
                <div id="hist-compare-year-wrap">
                    <select id="hist-year-a" class="hist-year-select"></select>
                    <span id="hist-compare-vs">vs</span>
                    <select id="hist-year-b" class="hist-year-select"></select>
                </div>
                <div style="position:relative;height:200px;"><canvas id="hist-canvas-compare"></canvas></div>
            </div>
            <!-- Panneau historique multi-années -->
            <div id="hist-timeline-panel" style="display:none;">
                <div id="hist-view-toggle">
                    <button class="dir-btn active" id="btnHistAnnual">Annuel</button>
                    <button class="dir-btn" id="btnHistMonthly">Mensuel</button>
                </div>
                <div style="position:relative;height:220px;"><canvas id="hist-canvas-timeline"></canvas></div>
            </div>
        </div>
        <div id="chart-map-layout">
        <div id="chart-area">
        <div id="dataWarning"><span class="warn-icon">⚠️</span><span>Des interruptions ont été détectées dans les données de ce compteur. Certaines périodes peuvent être sous-estimées — interpréter les chiffres avec prudence.</span></div>
        <div id="anomalyWarning">
            <span class="warn-icon">⚠️</span>
            <div class="anomaly-body">
                <strong class="desktop-only">Données potentiellement erronées</strong><strong class="mobile-only">Données suspectes</strong><span class="desktop-only"> — volumes anormalement bas détectés :</span><br>
                <div id="anomalyDetails" style="margin-top:3px;font-size:12px;line-height:1.7;"></div>
            </div>
            <span class="anomaly-info-btn" tabindex="0" onclick="showView('methodo')" data-tooltip="Voir la méthodologie de détection">ℹ</span>
        </div>
        <div id="vfQualityWarning"><span class="warn-icon" id="vfQualityIcon"></span><span id="vfQualityText"></span></div>
        <div id="noDataMsg"><span class="icon">🚴</span>Aucune donnée disponible pour cette période.</div>
''')

for instance, directions in tqdm(data.items(), desc="Génération HTML"):
    row = first_row_for(instance)
    if row:
        rue2 = row.get('rue_2', '')
        location = (f"{row['arrondissement']} - {row['rue_1']} & {rue2}"
                    if rue2 else f"{row['arrondissement']} - {row['rue_1']}")
        is_boucle  = instance.startswith('vf-')
        type_label = 'Éco-Compteur' if is_boucle else 'Détecteur SUM'
        type_cls   = 'boucle' if is_boucle else 'fut'
        type_url   = 'https://donnees.montreal.ca/dataset/cyclistes'
        html_parts.append(f'<div id="{instance}" class="table-container">')
        html_parts.append(f'<h2>Compteur {instance} <a href="{type_url}" target="_blank" rel="noopener" class="counter-type-badge counter-type-{type_cls}">{type_label}</a></h2>')
        html_parts.append(f'<p><strong>Emplacement:</strong> {location}</p>')
        html_parts.append(f'<canvas id="chart-{instance}"></canvas>')
        html_parts.append('</div>')

html_parts.append('''
        </div>
        <div id="map-wrapper">
            <div id="map"></div>
            <button id="cyclosm-btn" title="Afficher les pistes cyclables">🚲 Pistes cyclables</button>
            <div id="map-legend">
                <span class="legend-item"><span class="legend-dot" style="background:#1DB860;box-shadow:0 0 0 1.5px #fff;"></span>Compteur actif</span>
                <span class="legend-item"><span class="legend-dot" style="background:#F59E0B;box-shadow:0 0 0 1.5px #fff;"></span>Délai de données</span>
                <span class="legend-item"><span class="legend-dot" style="background:#EF4444;box-shadow:0 0 0 1.5px #fff;"></span>Compteur inactif</span>
                <span class="legend-item"><span class="legend-dot" style="background:#29ABE2;box-shadow:0 0 0 1.5px #fff;"></span>Sélectionné</span>
            </div>
        </div>
        </div>
    </div>
    <script>
        const allChartData = {};   // horaire, HOURLY_DAYS derniers jours
        const allDailyData = {};   // totaux journaliers, fenêtre complète
        const hourProfile  = {};   // {période: [24 totaux par heure]} pour périodes > HOURLY_DAYS
        const markers = {};
        let map;
        const chartData = {};
        const charts = {};
        const countersByArrondissement = {};
        let specificDate = null;
        let combinedInstance = null;
''')

DIRECTION_COLORS = ['#1DB860', '#29ABE2']
DIRECTION_FILLS  = ['rgba(29,184,96,0.15)', 'rgba(41,171,226,0.15)']

# Ajouter les données des graphiques et le mapping arrondissement → compteurs
html_parts.append(f"const HOURLY_DAYS = {HOURLY_DAYS};\n")
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
    html_parts.append(f"allChartData['{instance}'] = {{ labels: {json.dumps(hour_labels)}, datasets: {json.dumps(datasets)} }};\n")
    html_parts.append(f"allDailyData['{instance}'] = {{ labels: {json.dumps(all_days)}, data: {json.dumps(daily_series)} }};\n")
    html_parts.append(f"hourProfile['{instance}'] = {json.dumps({str(p): v for p, v in profile.items()})};\n")

for arrondissement in sorted(by_arrondissement.keys()):
    counters = sorted(by_arrondissement[arrondissement],
                      key=lambda x: (x[0].startswith('vf-'), x[1]['rue_1'], x[1].get('rue_2', '')))
    entries = [{"value": inst,
                "label": ('[Éco-Compteur] ' if inst.startswith('vf-') else '[SUM] ') + counter_label(inst, row)}
               for inst, row in counters]
    html_parts.append(f"countersByArrondissement[{json.dumps(arrondissement)}] = {json.dumps(entries)};\n")

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
html_parts.append(f"const counterLocations = {json.dumps(counter_locations)};\n")
html_parts.append(f"const gappyCounters = new Set({json.dumps(sorted(gappy_instances))});\n")
html_parts.append(f"const anomalyDays = {json.dumps(anomaly_data)};\n")
all_quality = {**det_quality, **vf_quality}
html_parts.append(f"const vfDataQuality = {json.dumps(all_quality)};\n")
weather_js = {d: {k: v for k, v in w.items() if k != 'code'} for d, w in weather_data.items()}
html_parts.append(f"const weatherData = {json.dumps(weather_js)};\n")

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

if os.path.exists('bixi.csv'):
    with open('bixi.csv', encoding='utf-8') as f:
        for row in tqdm(csv.DictReader(f), desc="Données Bixi"):
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
                for inst in nearby:
                    bixi_near_counter[inst][date] += 1
            except Exception:
                pass

html_parts.append(f"const bixiNearby = {json.dumps({k: dict(v) for k, v in bixi_near_counter.items()})};\n")

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

html_parts.append(f"const bixiExceedsDays = {json.dumps(bixi_exceeds_days)};\n")
# Données historiques
html_parts.append(f"const historicalYearlyTotals = {json.dumps(hist_yearly, ensure_ascii=False)};\n")
html_parts.append(f"const historicalYearlyCounters = {json.dumps(hist_yearly_counters, ensure_ascii=False)};\n")
html_parts.append(f"const counterHistoricalData = {json.dumps(hist_counter_monthly, ensure_ascii=False)};\n")

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

html_parts.append(f"const globalStats = {json.dumps(_gs, ensure_ascii=False)};\n")

html_parts.append('''
        function themeColor(color) { return color; }

        function parseLabel(label) {
            return new Date(label.replace(' ', 'T'));
        }

        function getMaxDate(labels) {
            let max = null;
            labels.forEach(label => {
                const d = parseLabel(label);
                if (!max || d > max) max = d;
            });
            return max;
        }

        let displayMode = 'combined';
        let viewMode = 'timeline';
        let showBixi = false;
        let histMode    = null;
        let histSubMode = 'annual';

        function dateStr(d) {
            return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        }

        function maxDateStr(instance) {
            const maxDate = getMaxDate(allChartData[instance].labels);
            return maxDate ? dateStr(maxDate) : null;
        }

        function buildFilteredData(instance, days) {
            // Au-delà de HOURLY_DAYS, seules les données journalières existent
            // (la vue timeline est alors désactivée).
            if (days > HOURLY_DAYS) return buildDailyData(instance, days);
            const allLabels = allChartData[instance].labels;
            const allDatasets = allChartData[instance].datasets;
            // Ancrer la fenêtre sur la date max propre à ce compteur,
            // pour éviter qu'un écart de fraîcheur entre sources (ex. det- vs vf-)
            // ne décale la fenêtre vers une période sans données.
            const instMax = getMaxDate(allLabels) || globalMaxDate;

            let indices;
            if (days === 0) {
                if (!specificDate) return { labels: [], datasets: [] };
                indices = allLabels.map((l, i) => l.startsWith(specificDate) ? i : -1).filter(i => i >= 0);
            } else {
                if (!instMax) return { labels: [], datasets: [] };
                const cutoffDate = new Date(instMax.getFullYear(), instMax.getMonth(), instMax.getDate() - (days - 1));
                indices = allLabels.map((l, i) => parseLabel(l) >= cutoffDate ? i : -1).filter(i => i >= 0);
            }

            // Pour la vue 7 jours, générer la grille horaire complète et remplir
            // les heures manquantes avec null (les jours sans données apparaissent comme des gaps)
            if (days === 7 && instMax) {
                const cutoff = new Date(instMax.getFullYear(), instMax.getMonth(), instMax.getDate() - 6);
                cutoff.setHours(0, 0, 0, 0);
                const fullHours = [];
                const cur = new Date(cutoff);
                while (cur <= instMax) {
                    const lbl = `${cur.getFullYear()}-${String(cur.getMonth()+1).padStart(2,'0')}-${String(cur.getDate()).padStart(2,'0')} ${String(cur.getHours()).padStart(2,'0')}:00`;
                    fullHours.push(lbl);
                    cur.setHours(cur.getHours() + 1);
                }
                const labelToIdx = {};
                indices.forEach(i => { labelToIdx[allLabels[i]] = i; });
                const hasMissing = fullHours.some(h => !(h in labelToIdx));
                if (hasMissing) {
                    const isMulti = allDatasets.length > 1;
                    const showCombined = isMulti && displayMode === 'combined';
                    if (showCombined) {
                        return { labels: fullHours, datasets: [{ label: 'Combiné',
                            data: fullHours.map(lbl => lbl in labelToIdx ? allDatasets.reduce((s, ds) => s + (ds.data[labelToIdx[lbl]] || 0), 0) : 0),
                            borderColor: themeColor('#1DB860'), backgroundColor: themeColor('rgba(29,184,96,0.15)'),
                            fill: true, tension: 0.3, borderWidth: 1, pointRadius: 2, pointHoverRadius: 5 }] };
                    }
                    return { labels: fullHours, datasets: allDatasets.map(ds => ({
                        label: ds.label,
                        data: fullHours.map(lbl => lbl in labelToIdx ? ds.data[labelToIdx[lbl]] : 0),
                        borderColor: themeColor(ds.color), backgroundColor: themeColor(ds.fill),
                        fill: !isMulti, tension: 0.3, borderWidth: 1, pointRadius: 2, pointHoverRadius: 5
                    })) };
                }
            }

            const filteredLabels = indices.map(i => allLabels[i]);
            const isMulti = allDatasets.length > 1;
            const showCombined = isMulti && displayMode === 'combined';

            if (showCombined) {
                const combined = indices.map(i =>
                    allDatasets.reduce((sum, ds) => sum + (ds.data[i] || 0), 0)
                );
                return {
                    labels: filteredLabels,
                    datasets: [{ label: 'Combiné', data: combined, borderColor: themeColor('#1DB860'), backgroundColor: themeColor('rgba(29,184,96,0.15)'), fill: true, tension: 0.3, borderWidth: 1, pointRadius: 2, pointHoverRadius: 5 }]
                };
            }

            const isSingle = !isMulti;
            return {
                labels: filteredLabels,
                datasets: allDatasets.map(ds => ({
                    label: ds.label,
                    data: indices.map(i => ds.data[i]),
                    borderColor: themeColor(ds.color),
                    backgroundColor: themeColor(ds.fill),
                    fill: isSingle,
                    tension: 0.3,
                    borderWidth: 1,
                    pointRadius: 2,
                    pointHoverRadius: 5
                }))
            };
        }

        function buildDailyData(instance, days) {
            const daily = allDailyData[instance];
            const allLabels = daily.labels;
            const allDatasets = allChartData[instance].datasets.map((ds, k) => ({ ...ds, data: daily.data[k] }));
            const instMax = getMaxDate(allChartData[instance].labels) || globalMaxDate;
            if (!instMax) return { labels: [], datasets: [] };
            const cutoffStr = dateStr(new Date(instMax.getFullYear(), instMax.getMonth(), instMax.getDate() - (days - 1)));
            const indices = allLabels.map((l, i) => l >= cutoffStr ? i : -1).filter(i => i >= 0);
            const daySet = {};
            indices.forEach(i => { daySet[allLabels[i].slice(0, 10)] = true; });
            let dayList = Object.keys(daySet).sort();

            // Pour la vue 7 jours, générer tous les jours du calendrier
            // (les jours sans données apparaissent comme des barres à 0, potentiellement en rouge)
            if (days === 7 && instMax) {
                const fullRange = [];
                const end = new Date(instMax.getFullYear(), instMax.getMonth(), instMax.getDate());
                const start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - 6);
                const cur = new Date(start);
                while (cur <= end) {
                    fullRange.push(`${cur.getFullYear()}-${String(cur.getMonth()+1).padStart(2,'0')}-${String(cur.getDate()).padStart(2,'0')}`);
                    cur.setDate(cur.getDate() + 1);
                }
                dayList = fullRange;
            }

            const anomInst = (typeof anomalyDays !== 'undefined' && anomalyDays[instance]) ? anomalyDays[instance] : {};
            function barBg(d, base)  { return anomInst[d] ? 'rgba(245,158,11,0.65)' : base; }
            function barBd(d, base)  { return anomInst[d] ? 'rgba(245,158,11,0.9)'  : base; }

            const isMulti = allDatasets.length > 1;
            const showCombined = isMulti && displayMode === 'combined';
            if (!isMulti || showCombined) {
                const totals = {};
                dayList.forEach(d => totals[d] = 0);
                indices.forEach(i => {
                    const day = allLabels[i].slice(0, 10);
                    if (day in totals) allDatasets.forEach(ds => { totals[day] += (ds.data[i] || 0); });
                });
                const defBg = themeColor('rgba(29,184,96,0.75)'), defBd = themeColor('#1DB860');
                return { labels: dayList, datasets: [{ label: showCombined ? 'Combiné' : allDatasets[0].label, data: dayList.map(d => totals[d]),
                    backgroundColor: dayList.map(d => barBg(d, defBg)), borderColor: dayList.map(d => barBd(d, defBd)), borderWidth: 1, borderRadius: 4 }] };
            }
            return {
                labels: dayList,
                datasets: allDatasets.map((ds, i) => {
                    const totals = {};
                    dayList.forEach(d => totals[d] = 0);
                    indices.forEach(idx => {
                        const day = allLabels[idx].slice(0, 10);
                        if (day in totals) totals[day] += (ds.data[idx] || 0);
                    });
                    const defBg = themeColor(i === 0 ? 'rgba(29,184,96,0.75)' : 'rgba(41,171,226,0.75)');
                    const defBd = themeColor(ds.color);
                    return { label: ds.label, data: dayList.map(d => totals[d]),
                        backgroundColor: dayList.map(d => barBg(d, defBg)), borderColor: dayList.map(d => barBd(d, defBd)), borderWidth: 1, borderRadius: 4 };
                })
            };
        }

        function buildCombinedFilteredData(inst1, inst2, days) {
            const d1 = buildFilteredData(inst1, days);
            const d2 = buildFilteredData(inst2, days);
            const map1 = {};
            d1.labels.forEach((lbl, i) => { map1[lbl] = d1.datasets.reduce((s, ds) => s + (ds.data[i] || 0), 0); });
            const map2 = {};
            d2.labels.forEach((lbl, i) => { map2[lbl] = d2.datasets.reduce((s, ds) => s + (ds.data[i] || 0), 0); });
            const allLabels = [...new Set([...d1.labels, ...d2.labels])].sort();
            const combined = allLabels.map(lbl => (map1[lbl] || 0) + (map2[lbl] || 0));
            return { labels: allLabels, datasets: [{ label: 'Total combiné', data: combined,
                borderColor: themeColor('#1DB860'), backgroundColor: themeColor('rgba(29,184,96,0.15)'),
                fill: true, tension: 0.3, borderWidth: 1.5, pointRadius: 2, pointHoverRadius: 5 }] };
        }

        function buildCombinedDailyData(inst1, inst2, days) {
            const d1 = buildDailyData(inst1, days);
            const d2 = buildDailyData(inst2, days);
            const map1 = {};
            d1.labels.forEach((lbl, i) => { map1[lbl] = d1.datasets.reduce((s, ds) => s + (ds.data[i] || 0), 0); });
            const map2 = {};
            d2.labels.forEach((lbl, i) => { map2[lbl] = d2.datasets.reduce((s, ds) => s + (ds.data[i] || 0), 0); });
            const allLabels = [...new Set([...d1.labels, ...d2.labels])].sort();
            const combined = allLabels.map(lbl => (map1[lbl] || 0) + (map2[lbl] || 0));
            const defBg = themeColor('rgba(29,184,96,0.75)'), defBd = themeColor('#1DB860');
            return { labels: allLabels, datasets: [{ label: 'Total combiné', data: combined,
                backgroundColor: defBg, borderColor: defBd, borderWidth: 1, borderRadius: 4 }] };
        }

        const globalMaxDate = (function() {
            let max = null;
            for (let inst in allChartData) {
                const d = getMaxDate(allChartData[inst].labels);
                if (d && (!max || d > max)) max = d;
            }
            return max;
        })();

        function initializeChartData() {
            for (let instance in allChartData) {
                chartData[instance] = buildFilteredData(instance, 7);
            }
        }
        initializeChartData();

        function updateDayLabel(instance) {
            const el = document.getElementById('dayLabel');
            if (currentPeriod === 0 && specificDate) {
                const d = new Date(specificDate + 'T12:00:00');
                const s = d.toLocaleDateString('fr-CA', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
                el.textContent = s.charAt(0).toUpperCase() + s.slice(1);
                el.style.display = 'block';
                return;
            }
            el.style.display = 'none';
        }

        function animateCount(el, target) {
            if (!el) return;
            const duration = 700;
            const start = parseInt(el.textContent.replace(/\\D/g, '')) || 0;
            const t0 = performance.now();
            function step(now) {
                const t = Math.min((now - t0) / duration, 1);
                const eased = 1 - Math.pow(1 - t, 3);
                el.textContent = Math.round(start + (target - start) * eased).toLocaleString('fr-CA');
                if (t < 1) requestAnimationFrame(step);
            }
            requestAnimationFrame(step);
        }

        function updateStats(instance) {
            const statsRow = document.getElementById('statsRow');
            if (!instance) { statsRow.style.display = 'none'; return; }
            let labels, datasets;
            if (combinedInstance && allChartData[combinedInstance]) {
                const combData = buildCombinedFilteredData(instance, combinedInstance, currentPeriod);
                labels = combData.labels;
                datasets = combData.datasets;
            } else {
                if (!chartData[instance]) { statsRow.style.display = 'none'; return; }
                labels = chartData[instance].labels;
                datasets = chartData[instance].datasets;
            }
            // Combine all directions
            const combined = labels.map((_, i) =>
                datasets.reduce((sum, ds) => sum + (ds.data[i] || 0), 0)
            );
            const total = combined.reduce((a, b) => a + b, 0);
            const uniqueDays = new Set(labels.map(l => l.slice(0, 10))).size;
            const avg = uniqueDays > 0 ? Math.round(total / uniqueDays) : 0;
            const hourTotals = {};
            if (currentPeriod > HOURLY_DAYS) {
                // Données journalières seulement : profil horaire précalculé
                [instance, combinedInstance].filter(Boolean).forEach(inst => {
                    const prof = hourProfile[inst] && hourProfile[inst][currentPeriod];
                    if (prof) prof.forEach((v, h) => {
                        if (v) { const k = String(h).padStart(2, '0'); hourTotals[k] = (hourTotals[k] || 0) + v; }
                    });
                });
            } else {
                labels.forEach((label, i) => {
                    const h = label.slice(11, 13);
                    if (h) hourTotals[h] = (hourTotals[h] || 0) + combined[i];
                });
            }
            const peaks = Object.entries(hourTotals).sort((a, b) => b[1] - a[1]);
            const peak1 = peaks[0];
            const peak2 = peak1
                ? peaks.find(p => Math.abs(parseInt(p[0]) - parseInt(peak1[0])) >= 3) || null
                : null;
            statsRow.style.display = 'grid';
            animateCount(document.getElementById('statTotal'), total);
            animateCount(document.getElementById('statAvg'), avg);
            const fmtAvg = p => '~' + Math.round(p[1] / uniqueDays);
            if (peak1) {
                const hours = peak1[0] + 'h' + (peak2 ? ' · ' + peak2[0] + 'h' : '');
                const avgs  = fmtAvg(peak1) + (peak2 ? ' · ' + fmtAvg(peak2) : '') + ' passages/heure';
                document.getElementById('statPeak').innerHTML =
                    hours + '<br><span style="font-size:0.65em;opacity:0.65">' + avgs + '</span>';
            } else {
                document.getElementById('statPeak').textContent = '—';
            }
        }

        function createChart(id) {
            if (!charts[id]) {
                const ctx = document.getElementById('chart-' + id);
                if (ctx) {
                    const isDaily = viewMode === 'daily';
                    let rawData = isDaily ? buildDailyData(id, currentPeriod) : chartData[id];
                    if (combinedInstance && allChartData[combinedInstance]) {
                        rawData = isDaily
                            ? buildCombinedDailyData(id, combinedInstance, currentPeriod)
                            : buildCombinedFilteredData(id, combinedInstance, currentPeriod);
                    }
                    const type = isDaily ? 'bar' : 'line';
                    const isSingle = rawData.datasets.length === 1;

                    const bixiInst = (showBixi && typeof bixiNearby !== 'undefined' && bixiNearby[id]) ? bixiNearby[id] : null;
                    let data = rawData;
                    let hasBixi = false;
                    if (bixiInst) {
                        const bixiVals = rawData.labels.map(lbl => bixiInst[lbl.slice(0, 10)] ?? null);
                        if (bixiVals.some(v => v !== null)) {
                            hasBixi = true;
                            const bixiDs = isDaily
                                ? { label: 'Bixi', data: bixiVals, type: 'bar',
                                    backgroundColor: 'rgba(220,38,38,0.25)', borderColor: 'rgba(220,38,38,0.55)',
                                    borderWidth: 1, borderRadius: 3, yAxisID: 'y', order: 0 }
                                : { label: 'Bixi', data: bixiVals, type: 'line', stepped: 'before',
                                    pointRadius: 0, borderWidth: 1, borderColor: 'rgba(220,38,38,0.55)',
                                    backgroundColor: 'rgba(220,38,38,0.06)', fill: true, tension: 0,
                                    yAxisID: 'y', borderDash: [3, 2], order: 10 };
                            data = { labels: rawData.labels, datasets: [...rawData.datasets, bixiDs] };
                        }
                    }

                    if (!isDaily && isSingle && rawData.datasets[0].fill) {
                        const gradient = ctx.getContext('2d').createLinearGradient(0, 0, 0, 380);
                        gradient.addColorStop(0, themeColor('rgba(29,184,96,0.28)'));
                        gradient.addColorStop(1, 'rgba(0,0,0,0)');
                        rawData.datasets[0].backgroundColor = gradient;
                    }

                    // ── Visualisation des données manquantes ──
                    // Désactivé en mode combiné (les anomalies n'appartiennent qu'au compteur principal)
                    const anomalies = (!combinedInstance && typeof anomalyDays !== 'undefined' && anomalyDays[id]) || {};
                    const missingDaySet = new Set(
                        Object.entries(anomalies).filter(([, v]) => v.total === 0).map(([d]) => d)
                    );
                    const missingPlugin = missingDaySet.size ? {
                        id: 'missingViz',
                        beforeDraw(chart) {
                            if (isDaily) return;
                            const {ctx: c, chartArea: area, scales: {x: xAxis}} = chart;
                            if (!area) return;
                            const labels = chart.data.labels;
                            c.save();
                            c.fillStyle = 'rgba(220,38,38,0.07)';
                            for (let i = 0; i < labels.length - 1; i++) {
                                const d1 = new Date(labels[i].replace(' ', 'T'));
                                const d2 = new Date(labels[i+1].replace(' ', 'T'));
                                if ((d2 - d1) <= 23 * 3600 * 1000) continue;
                                let chk = new Date(d1);
                                chk.setDate(chk.getDate() + 1); chk.setHours(0, 0, 0, 0);
                                let found = false;
                                while (chk < d2) {
                                    if (missingDaySet.has(chk.toISOString().slice(0, 10))) { found = true; break; }
                                    chk.setDate(chk.getDate() + 1);
                                }
                                if (found) {
                                    const x1 = xAxis.getPixelForValue(i), x2 = xAxis.getPixelForValue(i + 1);
                                    c.fillRect(x1, area.top, x2 - x1, area.bottom - area.top);
                                }
                            }
                            c.restore();
                        }
                    } : null;

                    // Barres fantômes (vue par jour) — dataset réel pour que le hover fonctionne
                    if (!combinedInstance && isDaily && missingDaySet.size) {
                        const missingVals = data.labels.map(lbl => { const a = anomalies[lbl]; return (a && a.total === 0) ? a.expected : null; });
                        if (missingVals.some(v => v !== null)) {
                            data = { labels: data.labels, datasets: [...data.datasets, {
                                label: 'Données absentes', data: missingVals, type: 'bar',
                                backgroundColor: 'rgba(220,38,38,0.12)', borderColor: 'rgba(220,38,38,0.55)',
                                borderWidth: 1.5, borderRadius: 3, yAxisID: 'y', order: 1,
                                grouped: false,
                            }]};
                        }
                    }

                    // Points rouges sur les jours suspects (vue dans le temps)
                    if (!isDaily && missingDaySet.size) {
                        data.datasets.forEach(ds => {
                            if (!Array.isArray(ds.data)) return;
                            ds.pointBackgroundColor = data.labels.map(lbl => anomalies[lbl.slice(0, 10)] ? 'rgba(220,38,38,0.85)' : (ds.borderColor || '#1DB860'));
                            ds.pointBorderColor    = data.labels.map(lbl => anomalies[lbl.slice(0, 10)] ? 'rgba(220,38,38,1)'    : (ds.borderColor || '#1DB860'));
                            ds.pointRadius         = data.labels.map(lbl => anomalies[lbl.slice(0, 10)] ? 4 : 2);
                            ds.pointHoverRadius    = 6;
                        });
                    }

                    charts[id] = new Chart(ctx, {
                        type: type,
                        data: data,
                        plugins: missingPlugin ? [missingPlugin] : [],
                        options: {
                            responsive: true,
                            animation: { duration: 500, easing: 'easeInOutQuart' },
                            plugins: {
                                legend: { display: data.datasets.length > 1 },
                                tooltip: {
                                    backgroundColor: 'rgba(10,40,20,0.88)',
                                    titleColor: '#7ee8a2',
                                    bodyColor: '#fff',
                                    padding: 10,
                                    cornerRadius: 8,
                                    callbacks: {
                                        label: function(item) {
                                            if (item.dataset.label === 'Données absentes')
                                                return ' Attendu : ~' + Math.round(item.raw).toLocaleString('fr-CA') + ' passages';
                                            return ' ' + (item.dataset.label || 'Passages') + ' : ' + item.formattedValue;
                                        },
                                        title: function(items) {
                                            const label = items[0].label;
                                            if (isDaily) {
                                                const d = new Date(label + 'T12:00:00');
                                                return d.toLocaleDateString('fr-CA', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
                                            }
                                            const d = parseLabel(label);
                                            return d.toLocaleDateString('fr-CA', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' })
                                                + ' · ' + d.toLocaleTimeString('fr-CA', { hour: '2-digit', minute: '2-digit' });
                                        },
                                        afterBody: function(items) {
                                            const label = items[0].label;
                                            const dateKey = label.slice(0, 10);
                                            const lines = [];
                                            const w = (typeof weatherData !== 'undefined') ? weatherData[dateKey] : null;
                                            if (w) {
                                                lines.push('');
                                                let wline = w.icon + '  ' + (w.tmax !== null ? Math.round(w.tmax) + '°C' : '?');
                                                if (w.tmin !== null) wline += ' / ' + Math.round(w.tmin) + '°C';
                                                lines.push(wline);
                                                if (w.precip > 0.5) lines.push('🌧  ' + w.precip.toFixed(1) + ' mm pluie');
                                                if (w.snow > 0.5) lines.push('❄  ' + w.snow.toFixed(1) + ' cm neige');
                                            }
                                            if (!isDaily) {
                                                const anomaly = anomalies[dateKey];
                                                if (anomaly) {
                                                    if (!lines.length) lines.push('');
                                                    lines.push(anomaly.total === 0 ? '⚠ Données manquantes' : '⚠ Volume suspect');
                                                }
                                            }
                                            return lines;
                                        }
                                    }
                                }
                            },
                            scales: {
                                x: {
                                    grid: { color: 'rgba(0,0,0,0.04)' },
                                    border: { display: false },
                                    title: { display: true, text: 'Date', color: '#999', font: { size: 11 } },
                                    ticks: {
                                        color: '#777',
                                        maxTicksLimit: 10,
                                        callback: function(value) {
                                            const label = this.getLabelForValue(value);
                                            if (isDaily) {
                                                const d = new Date(label + 'T12:00:00');
                                                return d.toLocaleDateString('fr-CA', { month: 'short', day: 'numeric' });
                                            }
                                            const d = parseLabel(label);
                                            if (currentPeriod === 0) {
                                                return d.toLocaleTimeString('fr-CA', { hour: '2-digit', minute: '2-digit' });
                                            }
                                            return d.toLocaleDateString('fr-CA', { month: 'short', day: 'numeric' });
                                        }
                                    }
                                },
                                y: {
                                    grid: { color: 'rgba(0,0,0,0.04)' },
                                    border: { display: false },
                                    title: { display: true, text: 'Passages', color: '#999', font: { size: 11 } },
                                    beginAtZero: true,
                                    ticks: { color: '#777' }
                                },
                            }
                        }
                    });
                }
            }
        }

        function updateViewToggle() {
            const show = currentPeriod >= 7 && currentPeriod <= HOURLY_DAYS;
            const isMobile = window.innerWidth < 768;
            document.getElementById('viewToggle').style.display = show && !isMobile ? 'flex' : 'none';
            document.getElementById('viewToggleMobile').style.display = show && isMobile ? 'block' : 'none';
            if (currentPeriod === 0 && viewMode !== 'timeline') {
                viewMode = 'timeline';
                document.getElementById('btnTimeline').classList.add('active');
                document.getElementById('btnDaily').classList.remove('active');
                document.getElementById('viewSelectMobile').value = 'timeline';
            }
        }

        function updateDirToggle(instance) {
            const show = !!(instance && allChartData[instance] && allChartData[instance].datasets.length > 1);
            const isMobile = window.innerWidth < 768;
            document.getElementById('dirToggle').style.display = show && !isMobile ? 'flex' : 'none';
            document.getElementById('dirToggleMobile').style.display = show && isMobile ? 'block' : 'none';
        }

        function updateBixiToggle(instance) {
            document.getElementById('bixiToggle').style.display = 'none';
        }

        document.getElementById('btnBixi').addEventListener('click', function() {
            showBixi = !showBixi;
            this.classList.toggle('active', showBixi);
            const instance = getSelectedCounter();
            if (instance) {
                if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
                createChart(instance);
            }
        });

        function hasDataForPeriod(instance) {
            return instance && chartData[instance] && chartData[instance].labels.length > 0;
        }

        function updateAnomalyWarning(instance) {
            const el = document.getElementById('anomalyWarning');
            el.style.display = 'none';
            document.getElementById('anomalyDetails').innerHTML = '';
            if (!instance || !anomalyDays[instance] || (currentPeriod !== 0 && currentPeriod !== 7)) return;
            const visibleDates = new Set();
            if (currentPeriod === 0 && specificDate) {
                visibleDates.add(specificDate);
            } else if (chartData[instance]) {
                chartData[instance].labels.forEach(l => visibleDates.add(l.slice(0, 10)));
            }
            const found = Object.entries(anomalyDays[instance]).filter(([d]) => visibleDates.has(d));
            if (!found.length) { el.style.display = 'none'; return; }
            const bixi = (typeof bixiNearby !== 'undefined' && bixiNearby[instance]) ? bixiNearby[instance] : {};
            const exceeds = (typeof bixiExceedsDays !== 'undefined' && bixiExceedsDays[instance]) ? bixiExceedsDays[instance] : {};
            const isMobile = window.innerWidth < 768;
            const anomalyLines = found.map(([d, info]) => {
                const dateObj = new Date(d + 'T12:00:00');
                if (isMobile) {
                    const dateStr = dateObj.toLocaleDateString('fr-CA', {day: 'numeric', month: 'short'});
                    const label = info.total === 0 ? 'données manquantes' : `${info.total.toLocaleString('fr-CA')} passages`;
                    return `${dateStr} — ${label}`;
                }
                const dateStr = dateObj.toLocaleDateString('fr-CA', {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'});
                const label = info.total === 0
                    ? `<strong>données manquantes</strong> (attendu ~${info.expected.toLocaleString('fr-CA')} passages)`
                    : `<strong>${info.total.toLocaleString('fr-CA')}</strong> passages (attendu ~${info.expected.toLocaleString('fr-CA')}, z = ${info.z_score})`;
                const bixiCount = bixi[d] || 0;
                const exceedsBadge = exceeds[d]
                    ? `<span class="bixi-exceeds-badge">⚠ Bixi (${bixiCount}) > compteur (${info.total})</span>`
                    : (bixiCount > 0 ? ` — <span style="color:#0085C8">✓ ${bixiCount} trajet${bixiCount > 1 ? 's' : ''} Bixi à proximité</span>` : '');
                return `${dateStr.charAt(0).toUpperCase() + dateStr.slice(1)} : ${label}${exceedsBadge}`;
            });
            const exceedsOnlyDates = Object.keys(exceeds).filter(d => visibleDates.has(d) && !anomalyDays[instance][d]);
            const exceedsOnlyLines = isMobile ? [] : exceedsOnlyDates.map(d => {
                const dateObj = new Date(d + 'T12:00:00');
                const dateStr = dateObj.toLocaleDateString('fr-CA', {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'});
                const {counter, bixi: bcount} = exceeds[d];
                return `${dateStr.charAt(0).toUpperCase() + dateStr.slice(1)} : <strong>${counter.toLocaleString('fr-CA')}</strong> passages comptés<span class="bixi-exceeds-badge">⚠ Bixi (${bcount}) > compteur (${counter})</span>`;
            });
            document.getElementById('anomalyDetails').innerHTML = [...anomalyLines, ...exceedsOnlyLines].join('<br>');
            el.style.display = 'flex';
        }

        function updateVfQualityWarning(instance) {
            const el = document.getElementById('vfQualityWarning');
            el.style.display = 'none';
            el.className = '';
            if (!instance || typeof vfDataQuality === 'undefined' || !vfDataQuality[instance]) return;
            const q = vfDataQuality[instance];
            if (q.group === 'A') return;
            const fmt = d => d
                ? new Date(d + 'T12:00:00').toLocaleDateString('fr-CA', {day: 'numeric', month: 'long', year: 'numeric'})
                : '—';
            const iconEl = document.getElementById('vfQualityIcon');
            const textEl = document.getElementById('vfQualityText');
            if (q.group === 'B') {
                el.className = 'vf-warn-lag';
                iconEl.textContent = '⚠️';
                textEl.innerHTML = `<strong>Délai de publication</strong> — Les données de ce compteur s\'arrêtent au <strong>${fmt(q.last_complete)}</strong>. La Ville de Montréal publie ces données avec environ <strong>${q.lag_days} jours</strong> de délai. Elles seront mises à jour automatiquement.`;
            } else if (q.group === 'C') {
                el.className = 'vf-warn-stopped';
                iconEl.textContent = '⛔';
                textEl.innerHTML = `<strong>Compteur potentiellement inactif</strong> — Aucune donnée reçue depuis le <strong>${fmt(q.last_complete || q.last_any)}</strong>. Ce compteur est peut-être désaffecté ou en réparation.`;
            }
            el.style.display = 'flex';
        }

        function selectCounter(instance) {
            // Reset combine state on counter switch
            combinedInstance = null;
            document.getElementById('btnClearCombine').style.display = 'none';
            const nearbyCount = instance ? populateCombineSelect(instance) : 0;
            document.getElementById('combineSection').style.display = (instance && nearbyCount > 0) ? 'block' : 'none';
            updateHistButtons(instance);

            document.querySelectorAll('.table-container').forEach(c => c.classList.remove('visible'));
            const noDataMsg = document.getElementById('noDataMsg');
            if (instance) {
                document.getElementById(instance).classList.add('visible');
                const hasData = hasDataForPeriod(instance);
                noDataMsg.style.display = hasData ? 'none' : 'flex';
                document.getElementById('dataWarning').style.display = gappyCounters.has(instance) ? 'flex' : 'none';
                const canvas = document.getElementById('chart-' + instance);
                if (canvas) canvas.style.display = hasData ? '' : 'none';
                if (hasData) createChart(instance);
                updateStats(instance);
                updateDayLabel(instance);
                updateViewToggle();
                updateDirToggle(instance);
                updateBixiToggle(instance);
                updateAnomalyWarning(instance);
                updateVfQualityWarning(instance);
            } else {
                noDataMsg.style.display = 'none';
                document.getElementById('dataWarning').style.display = 'none';
                document.getElementById('anomalyWarning').style.display = 'none';
                document.getElementById('vfQualityWarning').style.display = 'none';
                updateStats(null);
                updateDayLabel(null);
                updateDirToggle(null);
                updateBixiToggle(null);
            }
            if (typeof markers !== 'undefined') updateMapSelection(instance);
        }

        function applyViewMode(val) {
            if (viewMode === val) return;
            viewMode = val;
            document.getElementById('btnTimeline').classList.toggle('active', val === 'timeline');
            document.getElementById('btnDaily').classList.toggle('active', val === 'daily');
            document.getElementById('viewSelectMobile').value = val;
            const instance = getSelectedCounter();
            if (instance) {
                if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
                createChart(instance);
            }
        }

        function applyDisplayMode(val) {
            if (displayMode === val) return;
            displayMode = val;
            document.getElementById('btnSeparate').classList.toggle('active', val === 'separate');
            document.getElementById('btnCombined').classList.toggle('active', val === 'combined');
            document.getElementById('dirSelectMobile').value = val;
            const instance = getSelectedCounter();
            if (instance) {
                chartData[instance] = buildFilteredData(instance, currentPeriod);
                if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
                createChart(instance);
                updateStats(instance);
            }
        }

        document.getElementById('btnTimeline').addEventListener('click', () => applyViewMode('timeline'));
        document.getElementById('btnDaily').addEventListener('click', () => applyViewMode('daily'));
        document.getElementById('btnSeparate').addEventListener('click', () => applyDisplayMode('separate'));
        document.getElementById('btnCombined').addEventListener('click', () => applyDisplayMode('combined'));
        document.getElementById('viewSelectMobile').addEventListener('change', function() { applyViewMode(this.value); });
        document.getElementById('dirSelectMobile').addEventListener('change', function() { applyDisplayMode(this.value); });

        document.getElementById('counterSelectDesktop').addEventListener('change', function() {
            selectCounter(this.value);
        });

        document.getElementById('arrondissementSelect').addEventListener('change', function() {
            const arr = this.value;
            const wrapper = document.getElementById('counterSelectWrapper');
            const mobileSelect = document.getElementById('counterSelectMobile');
            mobileSelect.innerHTML = '';
            if (arr && countersByArrondissement[arr]) {
                countersByArrondissement[arr].forEach(c => {
                    const opt = document.createElement('option');
                    opt.value = c.value;
                    opt.textContent = c.label;
                    mobileSelect.appendChild(opt);
                });
                wrapper.style.visibility = 'visible';
                const first = countersByArrondissement[arr][0].value;
                mobileSelect.value = first;
                selectCounter(first);
            } else {
                wrapper.style.visibility = 'hidden';
                selectCounter(null);
            }
        });

        document.getElementById('counterSelectMobile').addEventListener('change', function() {
            selectCounter(this.value);
        });

        let currentPeriod = 7;

        function getSelectedCounter() {
            if (window.innerWidth >= 768) {
                return document.getElementById('counterSelectDesktop').value;
            } else {
                return document.getElementById('counterSelectMobile').value;
            }
        }

        function filterDataByPeriod(days) {
            currentPeriod = days;
            const mPeriod = document.getElementById('periodSelectMobile');
            if (mPeriod) mPeriod.value = String(days);
            document.querySelectorAll('.period-btn').forEach(b => b.classList.toggle('active', parseInt(b.getAttribute('data-days')) === days));
            const wrapper = document.getElementById('datepickerWrapper');
            if (days === 0) {
                const instance = getSelectedCounter();
                if (instance && allChartData[instance]) {
                    specificDate = maxDateStr(instance);
                    const picker = document.getElementById('specificDatePicker');
                    const labels = allChartData[instance].labels;
                    if (labels.length) picker.min = labels[0].slice(0, 10);
                    picker.max = specificDate;
                    picker.value = specificDate;
                }
                wrapper.style.display = 'block';
            } else {
                wrapper.style.display = 'none';
            }
            updateViewToggle();
            if (days >= 30) {
                viewMode = 'daily';
                document.getElementById('btnDaily').classList.add('active');
                document.getElementById('btnTimeline').classList.remove('active');
                document.getElementById('viewSelectMobile').value = 'daily';
            } else {
                viewMode = 'timeline';
                document.getElementById('btnTimeline').classList.add('active');
                document.getElementById('btnDaily').classList.remove('active');
                document.getElementById('viewSelectMobile').value = 'timeline';
            }
            for (let instance in allChartData) {
                chartData[instance] = buildFilteredData(instance, days);
            }
            const selected = getSelectedCounter();
            if (selected) {
                if (charts[selected]) { charts[selected].destroy(); charts[selected] = null; }
                const hasData = hasDataForPeriod(selected);
                const noDataMsg = document.getElementById('noDataMsg');
                noDataMsg.style.display = hasData ? 'none' : 'flex';
                const canvas = document.getElementById('chart-' + selected);
                if (canvas) canvas.style.display = hasData ? '' : 'none';
                if (hasData) createChart(selected);
                updateStats(selected);
                updateDayLabel(selected);
                updateAnomalyWarning(selected);
            }
        }

        (function() {
            const arrondissements = Object.keys(countersByArrondissement);
            if (!arrondissements.length) return;
            const defaultCounter = 'vf-300046833';
            const defaultArr = counterLocations[defaultCounter]?.arrondissement
                || arrondissements[0];

            // Desktop
            document.getElementById('counterSelectDesktop').value = defaultCounter;

            // Mobile
            const arrSelect = document.getElementById('arrondissementSelect');
            arrSelect.value = defaultArr;
            const mobileSelect = document.getElementById('counterSelectMobile');
            countersByArrondissement[defaultArr].forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.value;
                opt.textContent = c.label;
                mobileSelect.appendChild(opt);
            });
            document.getElementById('counterSelectWrapper').style.visibility = 'visible';
            mobileSelect.value = defaultCounter;

            selectCounter(defaultCounter);
        })();

        document.querySelectorAll('.period-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                filterDataByPeriod(parseInt(this.getAttribute('data-days')));
            });
        });

        document.getElementById('periodSelectMobile').addEventListener('change', function() {
            filterDataByPeriod(parseInt(this.value));
        });

        document.getElementById('specificDatePicker').addEventListener('change', function() {
            specificDate = this.value;
            for (let instance in allChartData) {
                chartData[instance] = buildFilteredData(instance, 0);
            }
            const selected = getSelectedCounter();
            if (selected) {
                if (charts[selected]) { charts[selected].destroy(); charts[selected] = null; }
                const hasData = hasDataForPeriod(selected);
                const noDataMsg = document.getElementById('noDataMsg');
                noDataMsg.style.display = hasData ? 'none' : 'flex';
                const canvas = document.getElementById('chart-' + selected);
                if (canvas) canvas.style.display = hasData ? '' : 'none';
                if (hasData) createChart(selected);
                updateStats(selected);
                updateDayLabel(selected);
            }
        });

        // ── Combiner deux compteurs ──
        // Paires exclues du dropdown "Combiner avec" :
        // capteurs non-complémentaires (rues différentes, doublons, Eco-Display, etc.)
        const EXCLUDED_COMBINE_PAIRS = new Set([
            // ── 0–200 m ──
            'vf-100060991|vf-100060992',  // Avenue Westmount + Avenue Lansdowne — rues différentes
            'vf-100052600|vf-100061409',  // Rachel 3 (Angus) + Rachel/Angus — probable doublon
            'vf-100034805|vf-100041114',  // Gouin/Lajeunesse + Eco-Display Parc Stanley
            'vf-100011747|vf-100047030',  // Saint-Antoine/St-Urbain + Viger/St-Urbain — intersections ≠
            'det-00385-01|det-00421-01',  // Canadiens&Peel + René-Lévesque&Peel — points ≠ sur Peel
            // 'vf-300014995|vf-300014996' — REV St-Denis Duluth/Rachel : paire valide
            'vf-100003032|vf-300034853',  // Berri1 + Ontario&Savoie — rues différentes
            'det-00467-01|vf-100011747',  // Viger&St-Urbain + Saint-Antoine/St-Urbain — intersections ≠
            // ── 200–300 m ──
            'det-00967-13|det-00970-01',  // Boucherville&Notre-Dame + Notre-Dame&Curatteau — points ≠
            'det-00789-01|vf-100055268',  // Bourbonnière&Sherbrooke + Rachel/PieIX — rues différentes
            'det-00268-02|vf-300021685',  // L-H-LaFontaine&Perras Est + A25/Gouin — rues différentes
            'det-00268-03|vf-300021685',  // L-H-LaFontaine&Perras Ouest + A25/Gouin — rues différentes
            'vf-100012218|vf-100052603',  // Boyer/Rosemont + Piste des Carrières — voies différentes
            'det-00385-01|det-00402-02',  // Canadiens&Peel + Peel&Saint-Antoine — points ≠ sur Peel
            'det-00399-01|det-00402-01',  // Notre-Dame&Peel Sud + Peel&Saint-Antoine Nord — intersections ≠
            'det-00399-02|det-00402-01',  // Notre-Dame&Peel Nord + Peel&Saint-Antoine Nord — intersections ≠
            'vf-100012217|vf-300014996',  // Rachel/HôteldeVille + REV St-Denis/Rachel — intersections ≠
            'det-01364-01|det-01725-01',  // Charlevoix&Wellington + Gaétan-Laberge&A-15 — rues différentes
        ]);
        function pairKey(a, b) { return [a, b].sort().join('|'); }

        function haversineM(lat1, lng1, lat2, lng2) {
            const R = 6371000, rad = Math.PI / 180;
            const dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
            const a = Math.sin(dLat/2)**2 + Math.cos(lat1*rad)*Math.cos(lat2*rad)*Math.sin(dLng/2)**2;
            return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
        }

        function populateCombineSelect(instance) {
            const sel = document.getElementById('combineSelect');
            sel.innerHTML = '<option value="">— Choisir un compteur —</option>';
            const loc = counterLocations[instance];
            if (!loc) return 0;
            const MAX_DIST = 300; // mètres
            const nearby = Object.entries(counterLocations)
                .filter(([id, l]) => id !== instance
                    && haversineM(loc.lat, loc.lng, l.lat, l.lng) <= MAX_DIST
                    && !EXCLUDED_COMBINE_PAIRS.has(pairKey(instance, id)))
                .sort((a, b) => haversineM(loc.lat, loc.lng, a[1].lat, a[1].lng)
                              - haversineM(loc.lat, loc.lng, b[1].lat, b[1].lng));
            nearby.forEach(([id, l]) => {
                const opt = document.createElement('option');
                opt.value = id;
                const dist = Math.round(haversineM(loc.lat, loc.lng, l.lat, l.lng));
                opt.textContent = `${l.label} (${dist} m)`;
                sel.appendChild(opt);
            });
            return nearby.length;
        }

        document.getElementById('combineSelect').addEventListener('change', function() {
            const instance = getSelectedCounter();
            if (!instance) return;
            combinedInstance = this.value || null;
            document.getElementById('btnClearCombine').style.display = combinedInstance ? 'inline-block' : 'none';
            if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
            createChart(instance);
            updateStats(instance);
        });

        document.getElementById('btnClearCombine').addEventListener('click', function() {
            combinedInstance = null;
            document.getElementById('combineSelect').value = '';
            this.style.display = 'none';
            const instance = getSelectedCounter();
            if (instance) {
                if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
                createChart(instance);
                updateStats(instance);
            }
        });

        // ── Carte Leaflet ──
        const COLOR_DEFAULT       = '#1DB860';
        const COLOR_SELECTED      = '#29ABE2';
        const COLOR_GAPPY         = '#F59E0B';
        const COLOR_BOUCLE        = '#8B5CF6';
        const COLOR_BOUCLE_LAG    = '#F59E0B';   // Groupe B – délai de publication
        const COLOR_BOUCLE_STOPPED = '#EF4444';  // Groupe C – compteur inactif

        function vfGroup(instance) {
            return (typeof vfDataQuality !== 'undefined' && vfDataQuality[instance])
                ? vfDataQuality[instance].group : 'A';
        }

        function markerStyle(selected, gappy, type, group) {
            let base;
            if      (group === 'C') base = COLOR_BOUCLE_STOPPED;  // rouge
            else if (group === 'B') base = COLOR_BOUCLE_LAG;      // orange
            else                    base = COLOR_DEFAULT;           // vert
            return { radius: 9, fillColor: selected ? COLOR_SELECTED : base, color: '#fff', weight: 2, fillOpacity: 0.92 };
        }

        function updateMapSelection(instance) {
            Object.entries(markers).forEach(([id, m]) => {
                const type = counterLocations[id] ? counterLocations[id].type : 'fut';
                m.setStyle(markerStyle(id === instance, gappyCounters.has(id), type, vfGroup(id)));
            });
            if (instance && markers[instance]) markers[instance].bringToFront();
        }

        setTimeout(() => {
            map = L.map('map').setView([45.53, -73.59], 11);
            const osmLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
                maxZoom: 19
            });
            const cyclOsmLayer = L.tileLayer('https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png', {
                attribution: '© <a href="https://www.cyclosm.org">CyclOSM</a> © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
                maxZoom: 20
            });
            osmLayer.addTo(map);
            let cyclOsmActive = false;
            document.getElementById('cyclosm-btn').addEventListener('click', () => {
                cyclOsmActive = !cyclOsmActive;
                if (cyclOsmActive) {
                    map.removeLayer(osmLayer);
                    cyclOsmLayer.addTo(map);
                } else {
                    map.removeLayer(cyclOsmLayer);
                    osmLayer.addTo(map);
                }
                document.getElementById('cyclosm-btn').classList.toggle('active', cyclOsmActive);
            });
            Object.entries(counterLocations).forEach(([instance, loc]) => {
                const gappy = gappyCounters.has(instance);
                const grp = vfGroup(instance);
                const typeIcon = loc.type === 'boucle' ? '⬡ ' : '📍 ';
                const tooltipPrefix = grp === 'C' ? '⛔ '
                                    : grp === 'B' ? '⚠ '
                                    : gappy       ? '⚠ '
                                    : typeIcon;
                const m = L.circleMarker([loc.lat, loc.lng], markerStyle(false, gappy, loc.type, grp))
                    .addTo(map)
                    .bindTooltip(tooltipPrefix + loc.label, { direction: 'top', offset: [0, -6] });
                m.on('click', () => setCounterFromMap(instance));
                markers[instance] = m;
            });
            updateMapSelection(getSelectedCounter());
            map.invalidateSize();
        }, 0);

        function setCounterFromMap(instance) {
            // Mettre à jour le dropdown desktop
            document.getElementById('counterSelectDesktop').value = instance;

            // Mettre à jour les dropdowns mobile
            const arr = counterLocations[instance].arrondissement;
            const arrSelect = document.getElementById('arrondissementSelect');
            arrSelect.value = arr;
            const mobileSelect = document.getElementById('counterSelectMobile');
            mobileSelect.innerHTML = '<option value="">Sélectionnez un compteur</option>';
            if (countersByArrondissement[arr]) {
                countersByArrondissement[arr].forEach(c => {
                    const opt = document.createElement('option');
                    opt.value = c.value; opt.textContent = c.label;
                    mobileSelect.appendChild(opt);
                });
                document.getElementById('counterSelectWrapper').style.visibility = 'visible';
            }
            mobileSelect.value = instance;

            selectCounter(instance);
            map.panTo([counterLocations[instance].lat, counterLocations[instance].lng]);
        }

        // ── Toggle vue Compteurs / Méthodologie ──────────────────────────────
        // ── Vue globale ──────────────────────────────────────────────────────
        function goToCounter(id) {
            if (!id) return;
            showView(\'main\');
            setTimeout(() => setCounterFromMap(id), 0);
        }

        function renderGlobalPage() {
            const s = globalStats;
            if (!s || !s.yesterday) return;

            const fmt = n => (n || 0).toLocaleString(\'fr-CA\');
            const fmtPeak = h => h !== null && h !== undefined ? h + \'h–\' + (h + 1) + \'h\' : \'—\';

            const periods = [
                { key: \'yesterday\', label: \'Hier\',              icon: \'📅\' },
                { key: \'week\',      label: \'7 derniers jours\',   icon: \'📆\' },
                { key: \'month\',     label: \'Ce mois\',            icon: \'🗓️\' },
            ];

            document.getElementById(\'global-cards\').innerHTML = periods.map(p => {
                const d = s[p.key];
                const top1 = d.top && d.top[0];
                const top2 = d.top && d.top[1];
                const clickable = top1 && !!top1.id;
                const topRow = (t, rank) => t && t.id
                    ? \'<span class="global-top-row" onclick="event.stopPropagation();goToCounter(\\\'\' + t.id + \'\\\')">\' +
                      \'<span class="global-top-rank">\' + rank + \'</span>\' +
                      \'<strong>\' + (t.label || \'—\') + \'</strong>\' +
                      \'<span class="global-top-vol">\' + fmt(t.vol) + \'</span>\' +
                      \'</span>\'
                    : \'\';
                return \'<div class="global-card\' + (clickable ? \' global-card-clickable\' : \'\') + \'"\' +
                    (clickable ? \' onclick="goToCounter(\\\'\' + top1.id + \'\\\')" title="Voir \' + (top1.label || \'\') + \'"\' : \'\') + \'>\' +
                    \'<div class="global-card-title">\' + p.icon + \' \' + p.label + \'</div>\' +
                    \'<div class="global-card-date">\' + (d.subtitle || \'\') + \'</div>\' +
                    \'<div class="global-card-total">\' + fmt(d.total) + \'</div>\' +
                    \'<div class="global-card-sub">passages</div>\' +
                    \'<div class="global-card-meta">\' +
                        \'<span>⏰ Pointe : <strong>\' + fmtPeak(d.peak_hour) + \'</strong></span>\' +
                        topRow(top1, \'🥇\') +
                        topRow(top2, \'🥈\') +
                        \'<span>📍 \' + d.coverage + \' / \' + d.active + \' compteurs avec données</span>\' +
                    \'</div>\' +
                    (clickable ? \'<div class="global-card-cta">Voir le top compteur →</div>\' : \'\') +
                \'</div>\';
            }).join(\'\');

            // ── Classement arrondissements ──
            const maxVol = s.arr_rank && s.arr_rank.length ? s.arr_rank[0].vol : 1;
            document.getElementById(\'global-arr\').innerHTML =
                \'<div class="global-section-title">🏙️ Top arrondissements <span style="font-size:11px;font-weight:400;color:#9ca3af;">— 7 derniers jours</span></div>\' +
                (s.arr_rank || []).map(a =>
                    \'<div class="arr-row">\' +
                        \'<span class="arr-name">\' + a.name + \'</span>\' +
                        \'<div class="arr-bar-wrap"><div class="arr-bar" style="width:\' + Math.round(a.vol / maxVol * 100) + \'%"></div></div>\' +
                        \'<span class="arr-vol">\' + fmt(a.vol) + \'</span>\' +
                    \'</div>\'
                ).join(\'\');

            // ── Statut du réseau ──
            const n = s.net_status || {};
            document.getElementById(\'global-net\').innerHTML =
                \'<div class="global-section-title">📡 Statut du réseau</div>\' +
                \'<div class="net-grid">\' +
                    \'<div><div class="net-val" style="color:#1DB860">\' + (n.A || 0) + \'</div><div class="net-label">Actifs</div></div>\' +
                    \'<div><div class="net-val" style="color:#F59E0B">\' + (n.B || 0) + \'</div><div class="net-label">En retard</div></div>\' +
                    \'<div><div class="net-val" style="color:#EF4444">\' + (n.C || 0) + \'</div><div class="net-label">Inactifs</div></div>\' +
                \'</div>\' +
                \'<p style="font-size:11px;color:#9ca3af;margin-top:14px;text-align:center;">\' + (n.total || 0) + \' compteurs au total</p>\';

            // ── Graphique évolution annuelle ──
            if (typeof historicalYearlyTotals !== \'undefined\' && !charts[\'__global_yearly__\']) {
                const years  = Object.keys(historicalYearlyTotals).map(Number).sort();
                const totals = years.map(y => historicalYearlyTotals[y]);
                const curY   = new Date().getFullYear();
                const ctx    = document.getElementById(\'global-yearly-chart\');
                if (ctx) {
                    charts[\'__global_yearly__\'] = new Chart(ctx, {
                        type: \'bar\',
                        data: {
                            labels: years,
                            datasets: [{
                                label: \'Total passages\',
                                data: totals,
                                backgroundColor: years.map(y => y === curY
                                    ? \'rgba(29,184,96,0.45)\'
                                    : \'rgba(29,184,96,0.75)\'),
                                borderColor: years.map(y => y === curY ? \'#17a355\' : \'#1DB860\'),
                                borderWidth: 1,
                                borderRadius: 4,
                            }],
                        },
                        options: {
                            responsive: true,
                            animation: { duration: 500 },
                            plugins: {
                                legend: { display: false },
                                tooltip: {
                                    backgroundColor: \'rgba(10,40,20,0.88)\',
                                    titleColor: \'#7ee8a2\',
                                    bodyColor: \'#fff\',
                                    padding: 10,
                                    cornerRadius: 8,
                                    callbacks: {
                                        label: item => {
                                            const y = Number(item.label);
                                            const n = (typeof historicalYearlyCounters !== \'undefined\' && historicalYearlyCounters[y]) || null;
                                            const suffix = y === curY ? \' (en cours)\' : \'\';
                                            const ctrLine = n ? \'📍 \' + n + \' compteur\' + (n > 1 ? \'s\' : \'\') : \'\';
                                            return [
                                                \' \' + Math.round(item.raw).toLocaleString(\'fr-CA\') + \' passages\' + suffix,
                                                ctrLine,
                                            ].filter(Boolean);
                                        },
                                    },
                                },
                            },
                            scales: {
                                x: {
                                    grid: { color: \'rgba(0,0,0,0.04)\' },
                                    border: { display: false },
                                    ticks: { color: \'#777\', font: { size: 11 } },
                                },
                                y: {
                                    grid: { color: \'rgba(0,0,0,0.04)\' },
                                    border: { display: false },
                                    beginAtZero: true,
                                    ticks: {
                                        color: \'#777\',
                                        callback: v => v >= 1e6
                                            ? (v / 1e6).toFixed(1) + \'M\'
                                            : v >= 1e3 ? (v / 1e3).toFixed(0) + \'k\' : v,
                                    },
                                    title: { display: true, text: \'Passages\', color: \'#999\', font: { size: 11 } },
                                },
                            },
                        },
                    });
                }
            }
        }

        // ── Vélo d\'hiver ──────────────────────────────────────────────────────
        const WINTER_MONTHS_SET = new Set([11, 12, 1, 2, 3]);
        const DOW_LABELS_FR = [\'Lundi\', \'Mardi\', \'Mercredi\', \'Jeudi\', \'Vendredi\', \'Samedi\', \'Dimanche\'];
        const HIVER_CAT_COLORS = { doux: \'#38BDF8\', pluie: \'#2563EB\', froid: \'#8B5CF6\', neige: \'#06B6D4\' };
        let _hiverGlobalDaily = null;

        function isWinterDate(dateStr) {
            return WINTER_MONTHS_SET.has(parseInt(dateStr.slice(5, 7), 10));
        }

        function dailyTotalsFor(instance) {
            const d = allDailyData[instance];
            const out = {};
            if (!d) return out;
            d.labels.forEach((date, i) => {
                let tot = 0;
                d.data.forEach(series => { const v = series[i]; if (v != null) tot += v; });
                out[date] = tot;
            });
            return out;
        }

        function hiverGlobalDaily() {
            if (_hiverGlobalDaily) return _hiverGlobalDaily;
            const out = {};
            Object.keys(counterLocations).forEach(inst => {
                const daily = dailyTotalsFor(inst);
                Object.entries(daily).forEach(([date, v]) => { out[date] = (out[date] || 0) + v; });
            });
            _hiverGlobalDaily = out;
            return out;
        }

        function winterSeasonLabel(dateStr) {
            const y = parseInt(dateStr.slice(0, 4), 10);
            const m = parseInt(dateStr.slice(5, 7), 10);
            return m >= 11
                ? y + \'–\' + String((y + 1) % 100).padStart(2, \'0\')
                : (y - 1) + \'–\' + String(y % 100).padStart(2, \'0\');
        }

        function computeWinterStats(dailyMap) {
            const dowSum = Array(7).fill(0), dowCount = Array(7).fill(0);
            const dowTempSum = Array(7).fill(0), dowTempCount = Array(7).fill(0);
            const cats = {
                doux:  { sum: 0, count: 0 },
                pluie: { sum: 0, count: 0 },
                froid: { sum: 0, count: 0 },
                neige: { sum: 0, count: 0 },
            };
            let coldestRidden = null, snowiestRidden = null, busiest = null;
            let winterTotal = 0, winterDays = 0, snowDaysTotal = 0, snowDaysRidden = 0;

            Object.entries(dailyMap).forEach(([date, v]) => {
                if (!isWinterDate(date)) return;
                winterTotal += v;
                winterDays++;
                const dow = (new Date(date + \'T00:00:00\').getDay() + 6) % 7;
                dowSum[dow] += v;
                dowCount[dow]++;
                if (busiest === null || v > busiest.v) busiest = { date, v };

                const w = weatherData[date];
                if (!w) return;
                if (w.tmax != null) { dowTempSum[dow] += w.tmax; dowTempCount[dow]++; }
                const snow = w.snow || 0, precip = w.precip || 0, tmax = w.tmax;
                if (snow >= 1) {
                    cats.neige.sum += v; cats.neige.count++;
                    snowDaysTotal++;
                    if (v > 0) snowDaysRidden++;
                } else if (tmax != null && tmax <= -10) {
                    cats.froid.sum += v; cats.froid.count++;
                } else if (precip > 5) {
                    cats.pluie.sum += v; cats.pluie.count++;
                } else {
                    cats.doux.sum += v; cats.doux.count++;
                }
                if (v > 0 && (coldestRidden === null || w.tmin < coldestRidden.tmin)) {
                    coldestRidden = { date, tmin: w.tmin, v };
                }
                if (v > 0 && snow > 0 && (snowiestRidden === null || snow > snowiestRidden.snow)) {
                    snowiestRidden = { date, snow, v };
                }
            });

            return {
                avgPassages: dowSum.map((s, i) => dowCount[i] ? Math.round(s / dowCount[i]) : null),
                avgTemp: dowTempSum.map((s, i) => dowTempCount[i] ? +(s / dowTempCount[i]).toFixed(1) : null),
                cats, coldestRidden, snowiestRidden, busiest,
                winterTotal, winterDays, snowDaysTotal, snowDaysRidden,
            };
        }

        function computeHistoricalWinterTrend() {
            const totals = {}, counters = {};
            if (typeof counterHistoricalData === \'undefined\') return { totals, counters };
            Object.entries(counterHistoricalData).forEach(([inst, years]) => {
                Object.entries(years).forEach(([yrStr, months]) => {
                    const yr = parseInt(yrStr, 10);
                    Object.entries(months).forEach(([moStr, vol]) => {
                        const mo = parseInt(moStr, 10);
                        if (![11, 12, 1, 2, 3].includes(mo)) return;
                        const season = mo >= 11
                            ? yr + \'–\' + String((yr + 1) % 100).padStart(2, \'0\')
                            : (yr - 1) + \'–\' + String(yr % 100).padStart(2, \'0\');
                        totals[season] = (totals[season] || 0) + vol;
                        (counters[season] = counters[season] || new Set()).add(inst);
                    });
                });
            });
            return { totals, counters };
        }

        function populateHiverCounterSelect() {
            const sel = document.getElementById(\'hiverCounterSelect\');
            if (!sel || sel.dataset.filled) return;
            sel.dataset.filled = \'1\';
            Object.keys(countersByArrondissement).sort().forEach(arr => {
                const og = document.createElement(\'optgroup\');
                og.label = arr;
                countersByArrondissement[arr].forEach(c => {
                    if (!allDailyData[c.value]) return;
                    const opt = document.createElement(\'option\');
                    opt.value = c.value;
                    opt.textContent = c.label;
                    og.appendChild(opt);
                });
                if (og.children.length) sel.appendChild(og);
            });
            sel.addEventListener(\'change\', function() {
                renderHiverDowCharts(this.value);
            });
        }

        function renderHiverFacts() {
            const el = document.getElementById(\'hiver-cards\');
            if (!el) return;
            const stats = computeWinterStats(hiverGlobalDaily());
            const fmt = n => (n || 0).toLocaleString(\'fr-CA\');
            const fmtDate = d => d ? new Date(d + \'T00:00:00\').toLocaleDateString(\'fr-CA\', { day: \'numeric\', month: \'long\', year: \'numeric\' }) : \'—\';
            const pctSnowRidden = stats.snowDaysTotal ? Math.round(stats.snowDaysRidden / stats.snowDaysTotal * 100) : 0;

            const cards = [
                {
                    icon: \'❄️\', title: "Hiver dernier", date: "Nov. à mars, tous compteurs",
                    total: fmt(stats.winterTotal), sub: \'passages\',
                    meta: "<span>📆 Sur <strong>" + fmt(stats.winterDays) + "</strong> jours de données</span>",
                },
                {
                    icon: \'🥶\', title: "Jour le plus froid pédalé", date: stats.coldestRidden ? fmtDate(stats.coldestRidden.date) : \'—\',
                    total: stats.coldestRidden ? stats.coldestRidden.tmin.toFixed(1) + \'°C\' : \'—\', sub: \'minimum ce jour\',
                    meta: stats.coldestRidden ? "<span>🚲 <strong>" + fmt(stats.coldestRidden.v) + "</strong> passages quand même</span>" : \'\',
                },
                {
                    icon: \'🏆\', title: "Record d\'achalandage hivernal", date: stats.busiest ? fmtDate(stats.busiest.date) : \'—\',
                    total: stats.busiest ? fmt(stats.busiest.v) : \'—\', sub: \'passages en 1 jour\', meta: \'\',
                },
                {
                    icon: \'🌨️\', title: "Jours de neige, pédalés quand même", date: fmt(stats.snowDaysTotal) + \' jours de neige (≥1 cm)\',
                    total: pctSnowRidden + \'%\', sub: \'de ces jours avec des cyclistes\',
                    meta: "<span>🚲 <strong>" + fmt(stats.snowDaysRidden) + "</strong> / " + fmt(stats.snowDaysTotal) + " jours</span>",
                },
            ];

            el.innerHTML = cards.map(c =>
                "<div class=\'global-card\'>" +
                    "<div class=\'global-card-title\'>" + c.icon + \' \' + c.title + "</div>" +
                    "<div class=\'global-card-date\'>" + c.date + "</div>" +
                    "<div class=\'global-card-total\'>" + c.total + "</div>" +
                    "<div class=\'global-card-sub\'>" + c.sub + "</div>" +
                    (c.meta ? "<div class=\'global-card-meta\'>" + c.meta + "</div>" : \'\') +
                "</div>"
            ).join(\'\');
        }

        function renderHiverTrendChart() {
            if (charts[\'__hiver_trend__\']) return;
            const { totals, counters } = computeHistoricalWinterTrend();
            const seasons = Object.keys(totals).sort();
            const ctx = document.getElementById(\'hiver-trend-chart\');
            if (!ctx || !seasons.length) return;
            const todayStr = new Date().toISOString().slice(0, 10);
            const curSeason = isWinterDate(todayStr) ? winterSeasonLabel(todayStr) : null;
            charts[\'__hiver_trend__\'] = new Chart(ctx, {
                type: \'bar\',
                data: {
                    labels: seasons,
                    datasets: [{
                        label: \'Passages (nov–mar)\',
                        data: seasons.map(s => totals[s]),
                        backgroundColor: seasons.map(s => s === curSeason ? \'rgba(14,165,216,0.45)\' : \'rgba(14,165,216,0.75)\'),
                        borderColor: seasons.map(s => s === curSeason ? \'#0A7FAE\' : \'#0EA5D8\'),
                        borderWidth: 1,
                        borderRadius: 4,
                    }],
                },
                options: {
                    responsive: true,
                    animation: { duration: 500 },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: \'rgba(7,33,51,0.90)\',
                            titleColor: \'#8FE0FB\',
                            bodyColor: \'#fff\',
                            padding: 10,
                            cornerRadius: 8,
                            callbacks: {
                                label: item => {
                                    const s = item.label;
                                    const n = counters[s] ? counters[s].size : 0;
                                    const suffix = s === curSeason ? \' (en cours)\' : \'\';
                                    return [
                                        \' \' + Math.round(item.raw).toLocaleString(\'fr-CA\') + \' passages\' + suffix,
                                        n ? \'📍 \' + n + \' compteur\' + (n > 1 ? \'s\' : \'\') : \'\',
                                    ].filter(Boolean);
                                },
                            },
                        },
                    },
                    scales: {
                        x: { grid: { color: \'rgba(0,0,0,0.04)\' }, border: { display: false }, ticks: { color: \'#777\', font: { size: 11 } } },
                        y: {
                            grid: { color: \'rgba(0,0,0,0.04)\' }, border: { display: false }, beginAtZero: true,
                            ticks: { color: \'#777\', callback: v => v >= 1e6 ? (v / 1e6).toFixed(1) + \'M\' : v >= 1e3 ? (v / 1e3).toFixed(0) + \'k\' : v },
                            title: { display: true, text: \'Passages\', color: \'#999\', font: { size: 11 } },
                        },
                    },
                },
            });
        }

        function renderHiverWeatherBreakdown(stats) {
            const el = document.getElementById(\'hiver-weather-breakdown\');
            if (!el) return;
            const fmt = n => (n || 0).toLocaleString(\'fr-CA\');
            const defs = [
                { key: \'doux\',  icon: \'🙂\', label: \'Jours doux (référence)\' },
                { key: \'pluie\', icon: \'🌧️\', label: \'Pluie / verglas (>5 mm)\' },
                { key: \'froid\', icon: \'🥶\', label: \'≤ -10 °C (sans neige)\' },
                { key: \'neige\', icon: \'❄️\', label: \'Neige (≥1 cm)\' },
            ];
            el.innerHTML = defs.map(d => {
                const c = stats.cats[d.key];
                const avg = c.count ? Math.round(c.sum / c.count) : null;
                return "<div>" +
                    "<div class=\'net-val\' style=\'color:" + HIVER_CAT_COLORS[d.key] + "\'>" + (avg !== null ? fmt(avg) : \'—\') + "</div>" +
                    "<div class=\'net-label\'>" + d.icon + \' \' + d.label + "</div>" +
                    "<div style=\'font-size:11px;color:var(--faint);margin-top:2px;\'>" + c.count + (c.count === 1 ? \' jour\' : \' jours\') + "</div>" +
                "</div>";
            }).join(\'\');
        }

        function renderHiverRecords(stats, instance) {
            const el = document.getElementById(\'hiver-records\');
            if (!el) return;
            const fmt = n => (n || 0).toLocaleString(\'fr-CA\');
            const fmtDate = d => d ? new Date(d + \'T00:00:00\').toLocaleDateString(\'fr-CA\', { day: \'numeric\', month: \'long\', year: \'numeric\' }) : \'—\';
            const label = instance === \'__all__\' ? \'tous les compteurs\' : ((counterLocations[instance] && counterLocations[instance].label) || instance);
            const rows = [];
            if (stats.busiest) {
                rows.push("<p>🏆 Journée la plus achalandée de l\'hiver pour <strong>" + label + "</strong> : <strong>" + fmtDate(stats.busiest.date) + "</strong>, avec <strong>" + fmt(stats.busiest.v) + "</strong> passages.</p>");
            }
            if (stats.coldestRidden) {
                rows.push("<p>🥶 Malgré <strong>" + stats.coldestRidden.tmin.toFixed(1) + " °C</strong> le " + fmtDate(stats.coldestRidden.date) + ", <strong>" + fmt(stats.coldestRidden.v) + "</strong> passages ont tout de même été comptés.</p>");
            }
            if (stats.snowiestRidden) {
                rows.push("<p>❄️ Le " + fmtDate(stats.snowiestRidden.date) + ", sous " + stats.snowiestRidden.snow.toFixed(1) + " cm de neige, <strong>" + fmt(stats.snowiestRidden.v) + "</strong> cyclistes ont quand même été comptés.</p>");
            }
            el.innerHTML = "<div class=\'global-section-title\'>📌 Faits marquants — " + label + "</div>" +
                (rows.length ? rows.join(\'\') : "<p style=\'color:var(--faint);font-size:13px;\'>Pas assez de données hivernales pour ce compteur.</p>");
        }

        function renderHiverDowCharts(instance) {
            const dailyMap = instance === \'__all__\' ? hiverGlobalDaily() : dailyTotalsFor(instance);
            const stats = computeWinterStats(dailyMap);
            const labels = DOW_LABELS_FR;

            [\'__hiver_dow_passages__\', \'__hiver_dow_temp__\'].forEach(k => {
                if (charts[k]) { charts[k].destroy(); charts[k] = null; }
            });

            const ctx1 = document.getElementById(\'hiver-dow-chart\');
            if (ctx1) {
                charts[\'__hiver_dow_passages__\'] = new Chart(ctx1, {
                    type: \'bar\',
                    data: {
                        labels,
                        datasets: [{
                            label: \'Passages moyens\',
                            data: stats.avgPassages,
                            backgroundColor: \'rgba(14,165,216,0.75)\',
                            borderColor: \'#0EA5D8\',
                            borderWidth: 1,
                            borderRadius: 4,
                        }],
                    },
                    options: { ..._histChartOpts(\'Passages / jour\'), plugins: { ..._histChartOpts(\'Passages / jour\').plugins, legend: { display: false } } },
                });
            }
            const ctx2 = document.getElementById(\'hiver-temp-chart\');
            if (ctx2) {
                charts[\'__hiver_dow_temp__\'] = new Chart(ctx2, {
                    type: \'line\',
                    data: {
                        labels,
                        datasets: [{
                            label: \'Température max. moyenne\',
                            data: stats.avgTemp,
                            borderColor: \'#22D3EE\',
                            backgroundColor: \'rgba(34,211,238,0.18)\',
                            fill: true,
                            tension: 0.3,
                            borderWidth: 1.5,
                            pointRadius: 3,
                            pointBackgroundColor: \'#22D3EE\',
                        }],
                    },
                    options: { ..._histChartOpts(\'°C\'), plugins: { ..._histChartOpts(\'°C\').plugins, legend: { display: false } } },
                });
            }

            renderHiverWeatherBreakdown(stats);
            renderHiverRecords(stats, instance);
        }

        function getSelectedHiverCounter() {
            const sel = document.getElementById(\'hiverCounterSelect\');
            return sel ? sel.value : \'__all__\';
        }

        function renderHiverPage() {
            populateHiverCounterSelect();
            renderHiverFacts();
            renderHiverTrendChart();
            renderHiverDowCharts(getSelectedHiverCounter());
        }

        // ── Fonctions historique compteur ────────────────────────────────────
        const _FR_MONTHS = [\'Jan\',\'Fév\',\'Mar\',\'Avr\',\'Mai\',\'Juin\',\'Juil\',\'Août\',\'Sep\',\'Oct\',\'Nov\',\'Déc\'];
        const _PALETTE   = [\'#1DB860\',\'#29ABE2\',\'#F59E0B\',\'#EF4444\',\'#8B5CF6\',\'#EC4899\',\'#14B8A6\',\'#F97316\'];

        function histDataForInstance(instance) {
            if (typeof counterHistoricalData === \'undefined\') return null;
            return counterHistoricalData[instance] || null;
        }

        function destroyHistCharts() {
            [\'__hist_compare__\', \'__hist_timeline__\'].forEach(k => {
                if (charts[k]) { charts[k].destroy(); charts[k] = null; }
            });
        }

        function resetHistPanel() {
            destroyHistCharts();
            document.getElementById(\'hist-compare-panel\').style.display  = \'none\';
            document.getElementById(\'hist-timeline-panel\').style.display  = \'none\';
            document.getElementById(\'hist-panel\').style.display           = \'none\';
            document.getElementById(\'btnHistCompare\').classList.remove(\'active\');
            document.getElementById(\'btnHistTimeline\').classList.remove(\'active\');
            histMode = null;
        }

        function updateHistButtons(instance) {
            // Désactivé — fonctionnalité réservée à la Vue Globale pour l\'instant
            document.getElementById(\'hist-btns\').style.display = \'none\';
            resetHistPanel();
        }

        function buildCompareChart(instance, yearA, yearB) {
            destroyHistCharts();
            const hd  = histDataForInstance(instance);
            if (!hd) return;

            const yearTotal = y => Object.values(hd[y] || {}).reduce((a, b) => a + b, 0);
            const curY = new Date().getFullYear();

            const totA = yearTotal(yearA);
            const totB = yearTotal(yearB);

            const labelA = yearA == curY ? String(yearA) + \' (en cours)\' : String(yearA);
            const labelB = yearB == curY ? String(yearB) + \' (en cours)\' : String(yearB);
            const colorA = yearA == curY ? \'rgba(29,184,96,0.75)\'  : \'rgba(41,171,226,0.75)\';
            const colorB = yearB == curY ? \'rgba(29,184,96,0.75)\'  : \'rgba(41,171,226,0.45)\';
            const bdrA   = yearA == curY ? \'#1DB860\'               : \'#29ABE2\';
            const bdrB   = yearB == curY ? \'#1DB860\'               : \'rgba(41,171,226,0.9)\';

            const ctx = document.getElementById(\'hist-canvas-compare\');
            if (!ctx) return;
            charts[\'__hist_compare__\'] = new Chart(ctx, {
                type: \'bar\',
                data: {
                    labels: [labelA, labelB],
                    datasets: [{
                        label: \'Total annuel\',
                        data:  [totA, totB],
                        backgroundColor: [colorA, colorB],
                        borderColor:     [bdrA, bdrB],
                        borderWidth: 1,
                        borderRadius: 6,
                    }],
                },
                options: _histChartOpts(\'Passages\'),
            });
        }

        function buildTimelineChart(instance, subMode) {
            destroyHistCharts();
            const hd = histDataForInstance(instance);
            if (!hd) return;
            const years = Object.keys(hd).map(Number).sort();
            const ctx   = document.getElementById(\'hist-canvas-timeline\');
            if (!ctx) return;

            if (subMode === \'annual\') {
                const totals = years.map(y => Object.values(hd[y] || {}).reduce((a,b) => a+b, 0));
                const curY   = new Date().getFullYear();
                charts[\'__hist_timeline__\'] = new Chart(ctx, {
                    type: \'bar\',
                    data: {
                        labels: years,
                        datasets: [{
                            label: \'Passages totaux\',
                            data:  totals,
                            backgroundColor: years.map(y => y === curY ? \'rgba(29,184,96,0.45)\' : \'rgba(29,184,96,0.75)\'),
                            borderColor: \'#1DB860\',
                            borderWidth: 1,
                            borderRadius: 4,
                        }],
                    },
                    options: _histChartOpts(\'Passages/an\'),
                });
            } else {
                // Mensuel : une ligne par année
                const months   = [1,2,3,4,5,6,7,8,9,10,11,12];
                const datasets = years.map((y, i) => ({
                    label: String(y),
                    data:  months.map(m => (hd[y] || {})[m] || null),
                    borderColor:     _PALETTE[i % _PALETTE.length],
                    backgroundColor: _PALETTE[i % _PALETTE.length] + \'22\',
                    fill: false, tension: 0.3, borderWidth: 1.5, pointRadius: 2,
                }));
                charts[\'__hist_timeline__\'] = new Chart(ctx, {
                    type: \'line\',
                    data: { labels: _FR_MONTHS, datasets },
                    options: { ..._histChartOpts(\'Passages/mois\'), plugins: { ..._histChartOpts(\'Passages/mois\').plugins, legend: { display: true, position: \'bottom\', labels: { boxWidth: 12, font: { size: 11 } } } } },
                });
            }
        }

        function _histChartOpts(yLabel) {
            return {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 400 },
                plugins: {
                    legend: { display: true, position: \'top\', labels: { boxWidth: 12, font: { size: 11 } } },
                    tooltip: {
                        backgroundColor: \'rgba(10,40,20,0.88)\',
                        titleColor: \'#7ee8a2\', bodyColor: \'#fff\',
                        padding: 9, cornerRadius: 7,
                        callbacks: {
                            label: item => \' \' + (item.dataset.label || \'\') + \' : \' +
                                (item.raw !== null ? Math.round(item.raw).toLocaleString(\'fr-CA\') : \'—\'),
                        },
                    },
                },
                scales: {
                    x: { grid: { color: \'rgba(0,0,0,0.04)\' }, border: { display: false }, ticks: { color: \'#777\', font: { size: 11 } } },
                    y: { grid: { color: \'rgba(0,0,0,0.04)\' }, border: { display: false }, beginAtZero: true,
                        ticks: { color: \'#777\', callback: v => v >= 1e6 ? (v/1e6).toFixed(1)+\'M\' : v >= 1e3 ? (v/1e3).toFixed(0)+\'k\' : v },
                        title: { display: true, text: yLabel, color: \'#999\', font: { size: 11 } } },
                },
            };
        }

        function _fillYearSelect(sel, years, selected) {
            sel.innerHTML = \'\';
            years.forEach(y => {
                const opt = document.createElement(\'option\');
                opt.value = y;
                opt.textContent = y;
                sel.appendChild(opt);
            });
            sel.value = selected;
        }

        function populateHistYearSelect(instance) {
            const hd    = histDataForInstance(instance);
            if (!hd) return;
            const curY  = new Date().getFullYear();
            const years = Object.keys(hd).map(Number).sort().reverse();
            const selA  = document.getElementById(\'hist-year-a\');
            const selB  = document.getElementById(\'hist-year-b\');
            _fillYearSelect(selA, years, curY);
            // default année B = année la plus récente avant curY
            const defaultB = years.find(y => y < curY) || years[years.length - 1];
            _fillYearSelect(selB, years, defaultB);
        }

        // ── Boutons historique ──
        document.getElementById(\'btnHistCompare\').addEventListener(\'click\', function() {
            const instance = getSelectedCounter();
            if (!instance) return;
            const wasActive = histMode === \'compare\';
            resetHistPanel();
            if (wasActive) return;
            histMode = \'compare\';
            this.classList.add(\'active\');
            document.getElementById(\'hist-panel-title\').textContent = \'Comparaison annuelle\';
            populateHistYearSelect(instance);
            document.getElementById(\'hist-compare-panel\').style.display = \'block\';
            document.getElementById(\'hist-panel\').style.display = \'block\';
            const yA = parseInt(document.getElementById(\'hist-year-a\').value);
            const yB = parseInt(document.getElementById(\'hist-year-b\').value);
            setTimeout(() => buildCompareChart(instance, yA, yB), 0);
        });

        document.getElementById(\'btnHistTimeline\').addEventListener(\'click\', function() {
            const instance = getSelectedCounter();
            if (!instance) return;
            const wasActive = histMode === \'timeline\';
            resetHistPanel();
            if (wasActive) return;
            histMode = \'timeline\';
            this.classList.add(\'active\');
            document.getElementById(\'hist-panel-title\').textContent = \'Historique multi-années\';
            document.getElementById(\'hist-timeline-panel\').style.display = \'block\';
            document.getElementById(\'hist-panel\').style.display = \'block\';
            setTimeout(() => buildTimelineChart(instance, histSubMode), 0);
        });

        function _onCompareYearChange() {
            const instance = getSelectedCounter();
            if (!instance || histMode !== \'compare\') return;
            const yA = parseInt(document.getElementById(\'hist-year-a\').value);
            const yB = parseInt(document.getElementById(\'hist-year-b\').value);
            buildCompareChart(instance, yA, yB);
        }
        document.getElementById(\'hist-year-a\').addEventListener(\'change\', _onCompareYearChange);
        document.getElementById(\'hist-year-b\').addEventListener(\'change\', _onCompareYearChange);

        document.getElementById(\'btnHistAnnual\').addEventListener(\'click\', function() {
            if (histSubMode === \'annual\') return;
            histSubMode = \'annual\';
            this.classList.add(\'active\');
            document.getElementById(\'btnHistMonthly\').classList.remove(\'active\');
            setTimeout(() => buildTimelineChart(getSelectedCounter(), \'annual\'), 0);
        });

        document.getElementById(\'btnHistMonthly\').addEventListener(\'click\', function() {
            if (histSubMode === \'monthly\') return;
            histSubMode = \'monthly\';
            this.classList.add(\'active\');
            document.getElementById(\'btnHistAnnual\').classList.remove(\'active\');
            setTimeout(() => buildTimelineChart(getSelectedCounter(), \'monthly\'), 0);
        });

        function showView(view) {
            const isMain   = view === \'main\';
            const isGlobal = view === \'global\';
            const isHiver  = view === \'hiver\';
            const isMeth   = view === \'methodo\';
            document.getElementById(\'main-header\').style.display    = isMain   ? \'\' : \'none\';
            document.getElementById(\'main-container\').style.display = isMain   ? \'\' : \'none\';
            document.getElementById(\'global-page\').style.display    = isGlobal ? \'\' : \'none\';
            document.getElementById(\'hiver-page\').style.display     = isHiver  ? \'\' : \'none\';
            document.getElementById(\'methodo-page\').style.display   = isMeth   ? \'\' : \'none\';
            document.getElementById(\'nav-compteurs\').classList.toggle(\'active\', isMain);
            document.getElementById(\'nav-global\').classList.toggle(\'active\', isGlobal);
            document.getElementById(\'nav-hiver\').classList.toggle(\'active\', isHiver);
            document.getElementById(\'nav-methodo\').classList.toggle(\'active\', isMeth);
            if (isGlobal) renderGlobalPage();
            if (isHiver) renderHiverPage();
            window.scrollTo({ top: 0, behavior: \'smooth\' });
        }

        // Masquer l\'écran de chargement après l\'initialisation (carte incluse, setTimeout ci-dessus)
        setTimeout(() => {
            const ld = document.getElementById("loading-screen");
            if (!ld) return;
            ld.classList.add("done");
            setTimeout(() => ld.remove(), 400);
        }, 0);

    </script>

    <!-- ══ Page Méthodologie ═══════════════════════════════════════════════ -->
    <div id="methodo-page" style="display:none">
      <div class="methodo-hero">
        <h2>Méthodologie &amp; Sources de données</h2>
        <p>Comment les données sont collectées, traitées et validées avant d\'être affichées sur cette carte.</p>
      </div>

      <!-- Sources -->
      <div class="methodo-section">
        <h3>📡 Sources de données</h3>
        <div class="source-grid">
          <div class="source-card">
            <div class="source-card-top">
              <span class="source-card-icon">🚦</span>
              <div>
                <div class="source-card-title">Détecteurs SUM <span class="mbadge mbadge-fut">Détecteur SUM</span></div>
                <div class="source-card-sub">Vélos - comptage permanent — Ville de Montréal</div>
              </div>
            </div>
            <div class="source-card-desc">Capteurs installés sur des fûts le long des pistes cyclables. Données horaires bidirectionnelles, identifiants <code style="font-size:11px;background:#f3f4f6;border-radius:3px;padding:1px 4px">det-XXXXX-XX</code>. Publié sur le même portail que les Éco-Compteurs depuis 2026.</div>
            <a class="source-card-link" href="https://donnees.montreal.ca/dataset/cyclistes" target="_blank" rel="noopener">→ Portail données ouvertes Ville ↗</a>
          </div>
          <div class="source-card">
            <div class="source-card-top">
              <span class="source-card-icon">🔵</span>
              <div>
                <div class="source-card-title">Éco-Compteurs <span class="mbadge mbadge-boucle">Éco-Compteur</span></div>
                <div class="source-card-sub">Vélos - comptage sur les pistes cyclables — Ville de Montréal</div>
              </div>
            </div>
            <div class="source-card-desc">Détecteurs inductifs encastrés dans l\'asphalte. Données à intervalles de 15 minutes, sens unique, identifiants <code style="font-size:11px;background:#f3f4f6;border-radius:3px;padding:1px 4px">vf-XXXXXXX</code>. Agrégées à l\'heure pour l\'affichage.</div>
            <a class="source-card-link" href="https://donnees.montreal.ca/dataset/cyclistes" target="_blank" rel="noopener">→ Portail données ouvertes Ville ↗</a>
          </div>
          <div class="source-card">
            <div class="source-card-top">
              <span class="source-card-icon">🚲</span>
              <div>
                <div class="source-card-title">BIXI Montréal <span class="mbadge mbadge-bixi">BIXI</span></div>
                <div class="source-card-sub">bixi.csv — BIXI open data</div>
              </div>
            </div>
            <div class="source-card-desc">Données de trajets BIXI de l\'année courante. Utilisées pour valider les volumes des capteurs fixes situés près de stations BIXI — un dépassement visible est signalé.</div>
            <a class="source-card-link" href="https://bixi.com/en/open-data/" target="_blank" rel="noopener">→ Données ouvertes BIXI ↗</a>
          </div>
          <div class="source-card">
            <div class="source-card-top">
              <span class="source-card-icon">🌤️</span>
              <div>
                <div class="source-card-title">Météo quotidienne <span class="mbadge mbadge-meteo">Météo</span></div>
                <div class="source-card-sub">Open-Meteo API (archive + prévisions)</div>
              </div>
            </div>
            <div class="source-card-desc">Température min/max, précipitations et chutes de neige pour Montréal. Sert exclusivement à exclure les jours de météo sévère de la détection d\'anomalies.</div>
            <a class="source-card-link" href="https://open-meteo.com/" target="_blank" rel="noopener">→ open-meteo.com ↗</a>
          </div>
        </div>
      </div>

      <!-- Détection lacunes -->
      <div class="methodo-section">
        <h3>⚠️ Détection des lacunes</h3>
        <p>Un compteur est marqué comme <em>lacunaire</em> (icône ⚠ sur la carte, bandeau d\'avertissement dans le panneau) lorsque ses données présentent l\'un des deux critères suivants :</p>
        <div class="algo-box">
          <strong>Critère 1 — Interruption longue :</strong> au moins un écart de plus de <code>14 jours consécutifs</code> sans aucune donnée dans la période active du compteur.<br>
          <strong>Critère 2 — Taux de manquants élevé :</strong> plus de <code>20 %</code> des jours dans la plage min–max du compteur sont totalement absents.
        </div>
      </div>

      <!-- Détection anomalies -->
      <div class="methodo-section">
        <h3>🔍 Détection des anomalies</h3>
        <p>Des jours suspects (dysfonctionnement probable du capteur) sont identifiés par deux méthodes complémentaires. Un jour est signalé dès qu\'<em>au moins l\'une</em> se déclenche :</p>
        <div class="algo-box">
          <strong>Méthode 1 — Z-score par jour de semaine :</strong> le taux horaire (passages/h) du jour est comparé à la moyenne <code>μ</code> et l\'écart-type <code>σ</code> de tous les jours complets (≥ 18 h de données) du même jour de semaine. Signalé si <code>taux &lt; μ − 2,5σ</code> ET <code>taux &lt; 50 % de μ</code>.
        </div>
        <div class="algo-box">
          <strong>Méthode 2 — Jours adjacents :</strong> comparaison du taux horaire aux jours complets dans une fenêtre de <code>± 6 jours</code>. Signalé si <code>taux &lt; 20 % de la moyenne adjacente</code> et que le coefficient de variation est &lt; 0,6 (trafic stable autour de la période). Robuste à la saisonnalité.
        </div>
        <div class="algo-box">
          <strong>Jours entièrement absents :</strong> une journée sans aucune donnée dans la plage active du compteur est signalée si le volume attendu (estimé par les méthodes ci-dessus) dépasse <code>25 % de la médiane journalière</code> du compteur (plancher absolu : 10 passages).
        </div>
        <div class="algo-warn">
          <strong>🌧 Exclusion météo :</strong> les jours présentant des conditions sévères sont automatiquement exclus de la détection — pluie &gt; 15 mm, neige &gt; 5 cm, ou température maximale &lt; −15 °C.
        </div>
      </div>

      <!-- Validation BIXI -->
      <div class="methodo-section">
        <h3>🚲 Validation croisée BIXI</h3>
        <p>Pour les compteurs situés à proximité de stations BIXI, le volume journalier BIXI (départs + arrivées) est affiché en superposition. Si le volume BIXI dépasse le volume du capteur pour une journée donnée, la barre est marquée d\'un badge <em>«&nbsp;Bixi &gt; capteur&nbsp;»</em> — signal possible d\'une sous-comptabilisation.</p>
      </div>

      <!-- Fenêtre temporelle -->
      <div class="methodo-section">
        <h3>📅 Fenêtre temporelle</h3>
        <p>Seules les données des <strong>12 derniers mois glissants</strong> (365 jours) sont chargées et affichées. Cette limite s\'applique uniformément aux deux types de capteurs et est recalculée à chaque génération du HTML.</p>
        <p>Le détail <strong>horaire</strong> (vue timeline, jour spécifique) couvre les <strong>90 derniers jours</strong> de chaque compteur ; au-delà, les vues 6 et 12 mois s\'appuient sur les totaux journaliers, et l\'heure de pointe est précalculée à partir des données horaires complètes.</p>
        <p>Pour les Éco-Compteurs, les intervalles de 15 minutes sont agrégés en tranches horaires par simple sommation — la même granularité que les données des Détecteurs SUM.</p>
      </div>

      <!-- Combinaison de compteurs -->
      <div class="methodo-section">
        <h3>⊕ Combinaison de compteurs</h3>
        <p>Lorsqu\'un compteur possède un voisin complémentaire à moins de <strong>300 m</strong>, un sélecteur <em>« Combiner avec… »</em> apparaît sous les contrôles du graphique. Choisir un compteur affiche la <strong>somme des deux flux</strong> en une seule courbe verte, et met à jour les statistiques (total, moyenne, heure de pointe) en conséquence.</p>
        <p>Ce mode est conçu pour les paires de capteurs mesurant des <strong>directions inverses sur le même axe</strong> — typiquement deux capteurs situés de part et d\'autre d\'une piste bidirectionnelle ou d\'une intersection. La liste est filtrée par un algorithme de proximité (distance de Haversine) combiné à une liste d\'exclusion manuelle qui écarte les faux positifs géographiques (rues différentes, doublons, panneaux d\'affichage).</p>
        <div class="algo-box">
          <strong>Paires reconnues (15 au total) :</strong> les compteurs éligibles sont ceux dont la distance au capteur sélectionné est ≤ 300 m <em>et</em> qui ne figurent pas dans la liste d\'exclusion. Le dropdown affiche chaque voisin avec sa distance en mètres, trié du plus proche au plus éloigné.
        </div>
        <p>En mode combiné, la visualisation des anomalies (points rouges, zones grisées, barres fantômes) est désactivée — les anomalies étant définies par capteur individuel, elles ne s\'appliquent pas à un signal synthétique.</p>
      </div>

      <!-- Données historiques -->
      <div class="methodo-section">
        <h3>📈 Données historiques (2009–présent)</h3>
        <p>En complément des 12 derniers mois glissants, le site intègre des données agrégées depuis <strong>2009</strong> pour deux fonctionnalités :</p>
        <ul>
          <li><strong>Vue Globale — évolution annuelle :</strong> diagramme à barres du total de passages pour tous les compteurs confondus, de 2009 à l\'année courante (partielle). L\'année en cours apparaît avec une barre plus claire pour signaler qu\'elle est incomplète.</li>
          <li><strong>Vue Compteur — Comparer &amp; Historique :</strong> deux boutons apparaissent sous le graphique principal pour les compteurs disposant de données antérieures à l\'année courante. Ces boutons sont masqués pour les détecteurs récents sans historique.</li>
        </ul>
        <div class="algo-box">
          <strong>📊 Comparer avec une année :</strong> deux sélecteurs (Année A / Année B) permettent de comparer le total annuel de deux années quelconques. Par défaut, A = année courante, B = année la plus récente dans l\'historique du compteur.
        </div>
        <div class="algo-box">
          <strong>📅 Historique multi-années :</strong> deux sous-modes disponibles :<br>
          — <em>Annuel</em> : une barre par année, total de passages.<br>
          — <em>Mensuel</em> : une courbe Jan–Déc par année (toutes les années disponibles superposées), pour visualiser la saisonnalité.
        </div>
        <p>Les données historiques proviennent de fichiers CSV archivés (dossier <code>historique/</code>, non commité). Elles sont pré-calculées en un fichier cache <code>hist_cache.json</code> (~60 Ko, commité sur GitHub) contenant les totaux mensuels par compteur et par année. À chaque génération du HTML, <code>genMap.py</code> charge ce cache et y ajoute les données de l\'année courante depuis les fichiers live.</p>
        <div class="algo-warn">
          Les totaux annuels ne sont pas directement comparables entre toutes les années : le nombre de compteurs actifs et leur couverture géographique ont évolué depuis 2009 (de quelques capteurs sur Rachel et Berri à plus d\'une centaine aujourd\'hui). Une hausse du total peut refléter l\'ajout de nouveaux compteurs autant qu\'une augmentation réelle du vélo.
        </div>
      </div>
    </div>
    <!-- ══════════════════════════════════════════════════════════════════ -->

    <!-- ══ Page Vue globale ═══════════════════════════════════════════════ -->
    <div id="global-page" style="display:none">
      <div class="site-header" id="global-header">
        <h1>Vue Globale</h1>
        <p class="subtitle" style="display:block">Passages agrégés de tous les compteurs actifs</p>
      </div>
      <div id="global-cards"></div>
      <div class="global-section" style="margin-bottom:20px;">
        <div class="global-section-title" style="margin-bottom:12px;">📈 Évolution annuelle — passages totaux</div>
        <canvas id="global-yearly-chart" style="height:260px;"></canvas>
      </div>
      <div id="global-bottom">
        <div class="global-section" id="global-arr"></div>
        <div class="global-section" id="global-net"></div>
      </div>
    </div>
    <!-- ══════════════════════════════════════════════════════════════════ -->

    <!-- ══ Page Vélo d\'hiver ═══════════════════════════════════════════════ -->
    <div id="hiver-page" style="display:none">
      <div class="site-header" id="hiver-header">
        <h1>❄️ Vélo d\'hiver à Montréal</h1>
        <p class="subtitle" style="display:block">Novembre à mars — le vélo ne s\'arrête pas pour l\'hiver</p>
      </div>
      <div id="hiver-cards"></div>
      <div class="global-section" style="margin-bottom:20px;">
        <div class="global-section-title" style="margin-bottom:12px;">📊 Évolution du vélo d\'hiver au fil des années</div>
        <canvas id="hiver-trend-chart" style="height:260px;"></canvas>
        <p style="font-size:11px;color:#9ca3af;margin-top:8px;">Total des passages de novembre à mars, tous compteurs confondus. Le nombre de compteurs actifs a augmenté au fil des années (voir l\'info-bulle) — une partie de la hausse reflète l\'ajout de nouveaux capteurs plutôt qu\'une hausse équivalente de l\'achalandage.</p>
      </div>
      <div class="global-section" style="margin-bottom:20px;">
        <div class="global-section-title" style="margin-bottom:12px;">🔍 Explorer un compteur l\'hiver</div>
        <div class="select-wrapper" style="max-width:480px;margin-bottom:18px;">
          <select id="hiverCounterSelect">
            <option value="__all__">Tous les compteurs — moyenne combinée</option>
          </select>
        </div>
        <div class="hiver-chart-row">
          <div class="hiver-chart-col">
            <div>Passages moyens par jour de semaine</div>
            <div class="hiver-chart-canvas"><canvas id="hiver-dow-chart"></canvas></div>
          </div>
          <div class="hiver-chart-col">
            <div>Température maximale moyenne par jour</div>
            <div class="hiver-chart-canvas"><canvas id="hiver-temp-chart"></canvas></div>
          </div>
        </div>
        <div style="margin-top:22px;">
          <div class="global-section-title" style="margin-bottom:10px;font-size:12px;">🌡️ Achalandage moyen par type de journée</div>
          <div id="hiver-weather-breakdown" class="net-grid net-grid-4"></div>
        </div>
      </div>
      <div class="global-section" id="hiver-records"></div>
    </div>
    <!-- ══════════════════════════════════════════════════════════════════ -->

    <div class="watermark">
        <p>Développé par <a href="https://www.gabfortin.com" target="_blank">Gabriel Fortin</a> · <a href="confidentialite.html">Confidentialité</a></p>
        <p style="font-size:0.75rem;color:#aaa;margin-top:6px;">⚠️ Ces données proviennent directement des données ouvertes de la Ville de Montréal et de BIXI. Certaines valeurs peuvent être incomplètes ou erronées.</p>
        <p style="font-size:0.75rem;color:#aaa;margin-top:6px;">Pour en savoir plus sur les sources de données et la méthodologie, consultez l\'<a href="#" onclick="showView(\'methodo\');return false;" style="color:rgba(29,184,96,0.7);text-decoration:none;" onmouseover="this.style.color=\'#1DB860\'" onmouseout="this.style.color=\'rgba(29,184,96,0.7)\'">onglet Méthodologie</a>.</p>
        <p style="font-size:0.7rem;color:#aaa;margin-top:4px;">Page mise à jour le ''' + datetime.now(timezone.utc).strftime('%Y-%m-%d à %H:%M') + ''' UTC</p>
    </div>
    <div class="version-badge">v''' + _APP_VERSION + '''</div>
</body>
</html>
''')

html = ''.join(html_parts)

# Écrire le fichier HTML
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("Fichier HTML généré : index.html")