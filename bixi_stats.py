"""Statistiques annuelles et mensuelles des trajets BIXI.

Deux usages :
  - module importé par genMap.py : BixiYearAggregator agrège l'année courante
    (bixi.csv) ligne par ligne ;
  - script `python3 bixi_stats.py` : construit bixi_cache.json avec les années
    complètes passées (2014 → année précédente), à partir des ZIP publiés sur
    https://bixi.com/en/open-data/. Seules les années absentes du cache sont
    téléchargées, donc l'exécution quotidienne ne fait normalement rien.

Chaque année est résumée deux fois : tout le réseau, et « Montréal » — les
trajets partis d'une station située dans l'un des 19 arrondissements de la
Ville de Montréal (hors Laval, Longueuil, Westmount, Mont-Royal, etc.). Les
stations sont situées avec les limites administratives des données ouvertes de
la Ville ; à défaut, avec la colonne d'arrondissement des fichiers 2022+.

Deux formats de CSV coexistent dans les données ouvertes BIXI :
  - 2014–2021 : start_date, start_station_code (ou emplacement_pk_start),
    end_station_code, duration_sec, is_member ; les noms et coordonnées des
    stations sont dans un fichier Stations_AAAA.csv séparé ;
  - 2022+     : STARTSTATIONNAME, STARTSTATIONARRONDISSEMENT,
    STARTSTATIONLATITUDE/LONGITUDE, ENDSTATIONNAME, STARTTIMEMS, ENDTIMEMS
    (horodatage UTC en ms, pas d'info membre).
"""
import csv
import io
import json
import os
import re
import sys
import tempfile
import unicodedata
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BIXI_CACHE_FILE = 'bixi_cache.json'
BIXI_OPEN_DATA_URL = 'https://bixi.com/en/open-data/'
LIMITES_API_URL = 'https://donnees.montreal.ca/api/3/action/package_show?id=limites-administratives-agglomeration'
LOCAL_DISTRICTS_FILE = 'data/districts-electoraux-2025.json'
FIRST_YEAR = 2014
MTL_TZ = ZoneInfo('America/Montreal')

# Durées retenues pour les moyennes (les trajets hors bornes restent comptés)
MIN_DURATION_S = 60
MAX_DURATION_S = 4 * 3600
# Bornes (minutes) de la répartition des durées
DURATION_BUCKETS_MIN = [10, 20, 30, 45, 60]
TOP_STATIONS = 10

MTL_BOROUGHS = [
    'Ahuntsic-Cartierville', 'Anjou', 'Côte-des-Neiges–Notre-Dame-de-Grâce', 'Lachine', 'LaSalle',
    'Le Plateau-Mont-Royal', 'Le Sud-Ouest', "L'Île-Bizard–Sainte-Geneviève", 'Mercier–Hochelaga-Maisonneuve',
    'Montréal-Nord', 'Outremont', 'Pierrefonds-Roxboro', 'Rivière-des-Prairies–Pointe-aux-Trembles',
    'Rosemont–La Petite-Patrie', 'Saint-Laurent', 'Saint-Léonard', 'Verdun', 'Ville-Marie',
    'Villeray–Saint-Michel–Parc-Extension',
]


def _norm(text):
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z]', '', text)


_MTL_BOROUGHS_NORM = {_norm(b) for b in MTL_BOROUGHS}


# ── Limites de la Ville de Montréal ──────────────────────────────────────────
def load_montreal_polygons():
    """Polygones des 19 arrondissements de Montréal.

    Lit LOCAL_DISTRICTS_FILE (districts électoraux, commité dans le dépôt —
    même source que la résolution d'arrondissement des compteurs dans
    genMap.py) plutôt que d'interroger l'API CKAN à chaque génération.

    Retourne [(bbox, anneaux)] ou None si le fichier local est absent.
    """
    if not os.path.exists(LOCAL_DISTRICTS_FILE):
        print(f"BIXI : {LOCAL_DISTRICTS_FILE} introuvable — repli sur la colonne arrondissement.")
        return None
    with open(LOCAL_DISTRICTS_FILE, encoding='utf-8') as f:
        geo = json.load(f)
    polygons = []
    for feat in geo.get('features', []):
        geom = feat.get('geometry') or {}
        polys = geom.get('coordinates', [])
        if geom.get('type') == 'Polygon':
            polys = [polys]
        for rings in polys:
            xs = [p[0] for p in rings[0]]
            ys = [p[1] for p in rings[0]]
            polygons.append(((min(xs), min(ys), max(xs), max(ys)), rings))
    if not polygons:
        print("BIXI : aucun district trouvé dans le fichier local — repli sur la colonne arrondissement.")
        return None
    print(f"BIXI : {len(polygons)} polygones de districts chargés depuis {LOCAL_DISTRICTS_FILE}.")
    return polygons


def _in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_montreal(lat, lon, polygons):
    for (x0, y0, x1, y1), rings in polygons:
        if x0 <= lon <= x1 and y0 <= lat <= y1 and _in_ring(lon, lat, rings[0]) \
                and not any(_in_ring(lon, lat, hole) for hole in rings[1:]):
            return True
    return False


class _Stats:
    """Accumulateur pour un périmètre (tout le réseau ou Montréal)."""

    def __init__(self):
        self.daily = Counter()            # 'AAAA-MM-JJ' -> trajets
        self.hourly = [0] * 24
        self.dur_sum = 0
        self.dur_n = 0
        self.dur_hist = [0] * (len(DURATION_BUCKETS_MIN) + 1)
        self.members = 0
        self.member_known = 0
        self.round_trips = 0
        self.start_stations = Counter()

    def add(self, date, hour, duration_s, start, end, is_member):
        self.daily[date] += 1
        self.hourly[hour] += 1
        if duration_s is not None and MIN_DURATION_S <= duration_s <= MAX_DURATION_S:
            self.dur_sum += duration_s
            self.dur_n += 1
            minutes = duration_s / 60
            i = 0
            while i < len(DURATION_BUCKETS_MIN) and minutes >= DURATION_BUCKETS_MIN[i]:
                i += 1
            self.dur_hist[i] += 1
        if is_member is not None:
            self.member_known += 1
            self.members += is_member
        if start:
            self.start_stations[start] += 1
            if start == end:
                self.round_trips += 1

    def summary(self, station_names, year=None):
        daily = {d: n for d, n in sorted(self.daily.items()) if year is None or d.startswith(str(year))}
        trips = sum(daily.values())
        if not trips:
            return None
        return {
            'trips': trips,
            'daily': daily,
            'hourly': self.hourly,
            'avg_duration_min': round(self.dur_sum / self.dur_n / 60, 1) if self.dur_n else None,
            'duration_hist': self.dur_hist,
            'member_share': round(self.members / self.member_known, 3) if self.member_known else None,
            'round_trip_share': round(self.round_trips / sum(self.daily.values()), 3),
            'stations': len(self.start_stations),
            'top_stations': [[station_names.get(code, code), n]
                             for code, n in self.start_stations.most_common(TOP_STATIONS)],
        }


class BixiYearAggregator:
    """Accumule les statistiques d'une année de trajets BIXI (réseau + Montréal)."""

    def __init__(self, polygons=None):
        self.all = _Stats()
        self.mtl = _Stats()
        self.polygons = polygons
        self.station_names = {}           # code -> nom (ancien format)
        self.station_coords = {}          # code -> (lat, lon) (ancien format)
        self._in_mtl = {}                 # station -> bool | None
        self._hour_cache = {}             # heure UTC (ms // 3.6e6) -> (date, heure) locales

    def _local(self, ms):
        key = ms // 3600000
        hit = self._hour_cache.get(key)
        if hit is None:
            dt = datetime.fromtimestamp(key * 3600, tz=timezone.utc).astimezone(MTL_TZ)
            hit = (dt.strftime('%Y-%m-%d'), dt.hour)
            self._hour_cache[key] = hit
        return hit

    def _is_montreal(self, station, lat, lon, borough):
        hit = self._in_mtl.get(station)
        if hit is None and station not in self._in_mtl:
            if self.polygons and lat is not None:
                hit = _in_montreal(lat, lon, self.polygons)
            elif borough:
                hit = _norm(borough) in _MTL_BOROUGHS_NORM
            self._in_mtl[station] = hit
        return hit

    def add_row(self, row):
        """Ajoute une ligne de CSV (dict) — détecte le format automatiquement."""
        try:
            if 'STARTTIMEMS' in row:
                start_ms = int(float(row['STARTTIMEMS']))
                end_raw = row.get('ENDTIMEMS')
                duration = (int(float(end_raw)) - start_ms) / 1000 if end_raw else None
                date, hour = self._local(start_ms)
                start = (row.get('STARTSTATIONNAME') or '').strip()
                end = (row.get('ENDSTATIONNAME') or '').strip()
                member = None
                try:
                    lat, lon = float(row['STARTSTATIONLATITUDE']), float(row['STARTSTATIONLONGITUDE'])
                except (KeyError, ValueError, TypeError):
                    lat = lon = None
                borough = row.get('STARTSTATIONARRONDISSEMENT')
            else:
                start_date = row['start_date']
                date, hour = start_date[:10], int(start_date[11:13])
                start = (row.get('start_station_code') or row.get('emplacement_pk_start') or '').strip()
                end = (row.get('end_station_code') or row.get('emplacement_pk_end') or '').strip()
                dur_raw = row.get('duration_sec')
                duration = float(dur_raw) if dur_raw else None
                member_raw = (row.get('is_member') or '').strip()
                member = int(float(member_raw)) if member_raw else None
                lat, lon = self.station_coords.get(start, (None, None))
                borough = None
        except (KeyError, ValueError, TypeError, IndexError):
            return
        self.all.add(date, hour, duration, start, end, member)
        if self._is_montreal(start, lat, lon, borough):
            self.mtl.add(date, hour, duration, start, end, member)

    def add_station_row(self, row):
        """Ligne d'un fichier Stations_AAAA.csv (ancien format)."""
        code = (row.get('code') or row.get('pk') or row.get('Code') or '').strip()
        name = (row.get('name') or row.get('Name') or '').strip()
        if code and name:
            self.station_names[code] = name
        try:
            self.station_coords[code] = (float(row['latitude']), float(row['longitude']))
        except (KeyError, ValueError, TypeError):
            pass

    def live_year(self):
        """Année majoritaire des trajets (bixi.csv ne contient qu'une année)."""
        years = Counter()
        for d, n in self.all.daily.items():
            years[d[:4]] += n
        return years.most_common(1)[0][0] if years else None

    def summary(self, year=None):
        """Résumé sérialisable ; `year` écarte les trajets d'autres années."""
        out = self.all.summary(self.station_names, year)
        if out:
            out['mtl'] = self.mtl.summary(self.station_names, year)
        return out


# ── Lecture des ZIP de données ouvertes ──────────────────────────────────────
def _iter_csv_members(zf):
    """Parcourt les CSV d'un ZIP, y compris ceux de ZIP imbriqués."""
    for name in sorted(zf.namelist()):
        if '__MACOSX' in name:
            continue
        low = name.lower()
        if low.endswith('.zip'):
            with zf.open(name) as f, zipfile.ZipFile(io.BytesIO(f.read())) as inner:
                yield from _iter_csv_members(inner)
        elif low.endswith('.csv'):
            yield zf, name


def aggregate_zip(path, year, polygons=None):
    agg = BixiYearAggregator(polygons)
    with zipfile.ZipFile(path) as zf:
        members = list(_iter_csv_members(zf))
        # Stations d'abord (ancien format) pour nommer et situer les codes
        trip_members = []
        for z, name in members:
            with z.open(name) as f:
                header = io.TextIOWrapper(f, encoding='utf-8-sig', errors='replace').readline().lower()
            if 'start_date' in header or 'starttimems' in header:
                trip_members.append((z, name))
            elif 'latitude' in header and ('code' in header or 'pk' in header):
                with z.open(name) as f:
                    for row in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig', errors='replace')):
                        agg.add_station_row(row)
        for z, name in trip_members:
            print(f"  {name}", flush=True)
            with z.open(name) as f:
                for row in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig', errors='replace')):
                    agg.add_row(row)
    unknown = sum(1 for v in agg._in_mtl.values() if v is None)
    if unknown:
        print(f"  {unknown} station(s) non situées (ni coordonnées ni arrondissement).")
    return agg.summary(year)


def find_zip_urls():
    """{année: url} des ZIP publiés sur la page de données ouvertes BIXI."""
    req = urllib.request.Request(BIXI_OPEN_DATA_URL, headers={'User-Agent': 'Mozilla/5.0'})
    html = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', errors='ignore')
    by_year = defaultdict(list)
    for url in set(re.findall(r'https://[^\s"\'<>]+?\.zip', html, re.IGNORECASE)):
        m = re.search(r'(20\d\d)', url.rsplit('/', 1)[-1])
        if m:
            by_year[int(m.group(1))].append(url)
    # Plusieurs versions d'une même année : la plus récente a le nom le plus long
    # (ex. DonneesOuvertes2024_0102…1112.zip)
    return {y: max(urls, key=lambda u: len(u.rsplit('/', 1)[-1])) for y, urls in by_year.items()}


def load_cache(path=BIXI_CACHE_FILE):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return {}


def merge_live(cache, live_summary, live_year, current_year):
    """Ajoute l'année de bixi.csv au cache (sans écraser une année complète)."""
    years = dict(cache)
    if live_summary and live_year and live_year not in years:
        live_summary['partial'] = int(live_year) == current_year
        years[live_year] = live_summary
    for y in years.values():
        y.pop('source', None)
    return dict(sorted(years.items()))


def update_cache(path=BIXI_CACHE_FILE):
    cache = load_cache(path)
    current_year = datetime.now(MTL_TZ).year
    # Une année sans résumé « Montréal » date d'avant le filtre : on la refait
    missing = [y for y in range(FIRST_YEAR, current_year) if 'mtl' not in cache.get(str(y), {})]
    if not missing:
        print("BIXI : cache historique complet — rien à télécharger.")
        return
    print(f"BIXI : années manquantes dans le cache : {missing}")
    try:
        urls = find_zip_urls()
    except Exception as e:
        print(f"BIXI : page de données ouvertes inaccessible ({e}) — cache inchangé.")
        return
    polygons = load_montreal_polygons()
    for year in missing:
        url = urls.get(year)
        if not url:
            print(f"BIXI {year} : aucun ZIP trouvé sur la page.")
            continue
        print(f"BIXI {year} : téléchargement de {url}", flush=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
                tmp_path = tmp.name
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp_path, 'wb') as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
            summary = aggregate_zip(tmp_path, year, polygons)
        except Exception as e:
            print(f"BIXI {year} : échec ({e})")
            continue
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        if not summary:
            print(f"BIXI {year} : aucun trajet reconnu dans le ZIP.")
            continue
        summary['source'] = url.rsplit('/', 1)[-1]
        cache[str(year)] = summary
        mtl = summary['mtl']['trips'] if summary.get('mtl') else 0
        print(f"BIXI {year} : {summary['trips']:,} trajets, dont {mtl:,} partis de Montréal.")
        # Sauvegarde après chaque année : un échec plus loin ne perd rien
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(dict(sorted(cache.items())), f, ensure_ascii=False, separators=(',', ':'))




# ── Trajets par arrondissement, mois par mois (page Arrondissements) ─────────
# Chaque station est rattachée à l'arrondissement que BIXI lui donne
# (colonnes STARTSTATIONARRONDISSEMENT / ENDSTATIONARRONDISSEMENT, format
# 2022+), ramené au nom officiel de la Ville en ignorant accents et tirets
# (« Rosemont - La Petite-Patrie » → « Rosemont–La Petite-Patrie »). Colonne
# vide : polygones des districts électoraux, comme pour les compteurs. Hors des
# 19 arrondissements (Westmount, Longueuil…), le nom de BIXI est gardé tel quel.
#
# Les années passées sont résumées une fois dans ARR_CACHE_FILE (années
# courante − 2 et − 1, assez pour 12 mois glissants et les 12 mois d'avant) ;
# l'année en cours vient de bixi.csv, dans genMap.py. Les mois de plusieurs
# fichiers s'additionnent : un trajet n'est publié que dans un seul fichier.
ARR_CACHE_FILE = 'bixi_arr_cache.json'
ARR_TOP_STATIONS = 10
ARR_TOP_DEST = 6
ARR_UNKNOWN = 'Non précisé'
_BOROUGH_BY_NORM = {_norm(b): b for b in MTL_BOROUGHS}


def load_arrondissement_polygons():
    """[(arrondissement, bbox, anneaux)] des districts électoraux, ou []."""
    if not os.path.exists(LOCAL_DISTRICTS_FILE):
        return []
    with open(LOCAL_DISTRICTS_FILE, encoding='utf-8') as f:
        geo = json.load(f)
    out = []
    for feat in geo.get('features', []):
        geom = feat.get('geometry') or {}
        polys = geom.get('coordinates', [])
        if geom.get('type') == 'Polygon':
            polys = [polys]
        for rings in polys:
            xs = [p[0] for p in rings[0]]
            ys = [p[1] for p in rings[0]]
            out.append((feat['properties']['NOM_ARR'], (min(xs), min(ys), max(xs), max(ys)), rings))
    return out


def _new_arr_rec():
    return {'dep': 0, 'arr': 0, 'int': 0, 'daily': {}, 'hourly': [0] * 24,
            'dur': [0, 0], 'dest': {}, 'st': {}}


class BixiArrAggregator:
    """Trajets partis de / arrivés dans chaque arrondissement, par mois."""

    def __init__(self, polygons=None):
        self.months = {}                  # 'AAAA-MM' -> {arrondissement: rec}
        self.polygons = polygons or []
        self._where = {}                  # (station, arrondissement BIXI) -> (nom, dans Montréal ?)
        self._hour_cache = {}
        self.last_date = None

    def _local(self, ms):
        key = ms // 3600000
        hit = self._hour_cache.get(key)
        if hit is None:
            dt = datetime.fromtimestamp(key * 3600, tz=timezone.utc).astimezone(MTL_TZ)
            hit = (dt.strftime('%Y-%m-%d'), dt.hour)
            self._hour_cache[key] = hit
        return hit

    def _borough(self, station, raw, lat, lon):
        key = (station, raw)
        hit = self._where.get(key)
        if hit is None:
            raw = (raw or '').strip()
            name = _BOROUGH_BY_NORM.get(_norm(raw)) if raw else None
            if name is None and not raw and self.polygons:
                try:
                    la, lo = float(lat), float(lon)
                    for nom, (x0, y0, x1, y1), rings in self.polygons:
                        if x0 <= lo <= x1 and y0 <= la <= y1 and _in_ring(lo, la, rings[0]) \
                                and not any(_in_ring(lo, la, h) for h in rings[1:]):
                            name = nom
                            break
                except (TypeError, ValueError):
                    pass
            hit = (name, True) if name else (raw or ARR_UNKNOWN, False)
            self._where[key] = hit
        return hit

    def _rec(self, month, borough):
        m = self.months.setdefault(month, {})
        rec = m.get(borough)
        if rec is None:
            rec = m[borough] = _new_arr_rec()
        return rec

    def add_row(self, row):
        try:
            start_ms = int(float(row['STARTTIMEMS']))
        except (KeyError, ValueError, TypeError):
            return
        date, hour = self._local(start_ms)
        try:
            duration = (int(float(row['ENDTIMEMS'])) - start_ms) / 1000
        except (KeyError, ValueError, TypeError):
            duration = None
        start = (row.get('STARTSTATIONNAME') or '').strip()
        end = (row.get('ENDSTATIONNAME') or '').strip()
        sb, s_in = self._borough(start, row.get('STARTSTATIONARRONDISSEMENT'),
                                 row.get('STARTSTATIONLATITUDE'), row.get('STARTSTATIONLONGITUDE'))
        eb, e_in = self._borough(end, row.get('ENDSTATIONARRONDISSEMENT'),
                                 row.get('ENDSTATIONLATITUDE'), row.get('ENDSTATIONLONGITUDE'))
        if not end:
            eb, e_in = ARR_UNKNOWN, False
        month = date[:7]
        if self.last_date is None or date > self.last_date:
            self.last_date = date
        if s_in:
            rec = self._rec(month, sb)
            rec['dep'] += 1
            day = date[8:]
            rec['daily'][day] = rec['daily'].get(day, 0) + 1
            rec['hourly'][hour] += 1
            if duration is not None and MIN_DURATION_S <= duration <= MAX_DURATION_S:
                rec['dur'][0] += int(duration)
                rec['dur'][1] += 1
            rec['dest'][eb] = rec['dest'].get(eb, 0) + 1
            if start:
                rec['st'][start] = rec['st'].get(start, 0) + 1
            if e_in and eb == sb:
                rec['int'] += 1
        if e_in:
            self._rec(month, eb)['arr'] += 1


def _add_arr_rec(tgt, rec):
    for k in ('dep', 'arr', 'int'):
        tgt[k] += rec[k]
    for i in range(24):
        tgt['hourly'][i] += rec['hourly'][i]
    tgt['dur'][0] += rec['dur'][0]
    tgt['dur'][1] += rec['dur'][1]
    for k in ('daily', 'dest', 'st'):
        for key, n in rec[k].items():
            tgt[k][key] = tgt[k].get(key, 0) + n


def merge_arr_months(*sources):
    """Additionne les mois de plusieurs fichiers ({mois: {arrondissement: rec}})."""
    out = {}
    for months in sources:
        for month, boroughs in months.items():
            for b, rec in boroughs.items():
                _add_arr_rec(out.setdefault(month, {}).setdefault(b, _new_arr_rec()), rec)
    return out


def _shift_month(month, k):
    y, m = int(month[:4]), int(month[5:7]) - 1 + k
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def arr_window(months, last_date, years_available):
    """Résumé des 12 derniers mois disponibles, par arrondissement.

    `months` : mois fusionnés ; `last_date` : dernier jour avec des trajets ;
    `years_available` : années dont un fichier BIXI a été lu (pour savoir si
    les 12 mois précédents sont couverts).
    """
    if not last_date:
        return None
    end = last_date[:7]
    window = [_shift_month(end, k) for k in range(-11, 1)]
    prev = [_shift_month(end, k) for k in range(-23, -11)]
    prev_ok = all(int(m[:4]) in years_available for m in prev)
    boroughs = {}
    for b in MTL_BOROUGHS:
        tot = _new_arr_rec()
        daily = {}
        for m in window:
            rec = months.get(m, {}).get(b)
            if not rec:
                continue
            _add_arr_rec(tot, rec)
            for dd, n in rec['daily'].items():
                daily[f"{m}-{dd}"] = n
        if not tot['dep'] and not tot['arr']:
            continue
        week = [n for d, n in daily.items() if datetime.strptime(d, '%Y-%m-%d').weekday() < 5]
        weekend = [n for d, n in daily.items() if datetime.strptime(d, '%Y-%m-%d').weekday() >= 5]
        record = max(sorted(daily.items()), key=lambda kv: kv[1]) if daily else None
        dest = sorted(((k, n) for k, n in tot['dest'].items() if k != b), key=lambda kv: (-kv[1], kv[0]))
        top = sorted(tot['st'].items(), key=lambda kv: (-kv[1], kv[0]))
        out = {
            'dep': tot['dep'], 'arr': tot['arr'], 'int': tot['int'],
            'days': len(daily),
            'week': [sum(week), len(week)], 'weekend': [sum(weekend), len(weekend)],
            'record': list(record) if record else None,
            'hourly': tot['hourly'],
            'avgDurationMin': round(tot['dur'][0] / tot['dur'][1] / 60, 1) if tot['dur'][1] else None,
            'monthly': [[m, months.get(m, {}).get(b, {}).get('dep', 0), months.get(m, {}).get(b, {}).get('arr', 0)]
                        for m in window],
            'stations': len(tot['st']),
            'topStations': [[k, n] for k, n in top[:ARR_TOP_STATIONS]],
            'topDest': [[k, n] for k, n in dest[:ARR_TOP_DEST]],
        }
        if prev_ok:
            out['prevDep'] = sum(months.get(m, {}).get(b, {}).get('dep', 0) for m in prev)
        boroughs[b] = out
    return {'start': window[0], 'end': end, 'lastDate': last_date, 'prevStart': prev[0],
            'prevCovered': prev_ok, 'boroughs': boroughs}


def load_arr_cache(path=ARR_CACHE_FILE):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return {}


def aggregate_zip_arr(path, polygons=None):
    agg = BixiArrAggregator(polygons)
    with zipfile.ZipFile(path) as zf:
        for z, name in _iter_csv_members(zf):
            with z.open(name) as f:
                header = io.TextIOWrapper(f, encoding='utf-8-sig', errors='replace').readline().lower()
            if 'starttimems' not in header:
                continue
            print(f"  {name}", flush=True)
            with z.open(name) as f:
                for row in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig', errors='replace')):
                    agg.add_row(row)
    return agg


def update_arr_cache(path=ARR_CACHE_FILE):
    """Résume par arrondissement les deux années précédentes.

    Une année est refaite si le ZIP publié par BIXI a changé de nom (ex. ZIP
    partiel remplacé par l'année complète) ; les années trop anciennes sont
    retirées.
    """
    cache = load_arr_cache(path)
    current_year = datetime.now(MTL_TZ).year
    needed = [current_year - 2, current_year - 1]
    try:
        urls = find_zip_urls()
    except Exception as e:
        print(f"BIXI arrondissements : page inaccessible ({e}) — cache inchangé.")
        return
    polygons = load_arrondissement_polygons()
    changed = False
    for y in list(cache):
        if int(y) not in needed:
            del cache[y]
            changed = True
    for year in needed:
        url = urls.get(year)
        if not url:
            print(f"BIXI arrondissements {year} : aucun ZIP trouvé sur la page.")
            continue
        fname = url.rsplit('/', 1)[-1]
        if cache.get(str(year), {}).get('source') == fname:
            print(f"BIXI arrondissements {year} : à jour ({fname}).")
            continue
        print(f"BIXI arrondissements {year} : téléchargement de {url}", flush=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
                tmp_path = tmp.name
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp_path, 'wb') as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
            agg = aggregate_zip_arr(tmp_path, polygons)
        except Exception as e:
            print(f"BIXI arrondissements {year} : échec ({e})")
            continue
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        if not agg.months:
            print(f"BIXI arrondissements {year} : aucun trajet reconnu (format 2022+ attendu).")
            continue
        cache[str(year)] = {'source': fname, 'lastDate': agg.last_date, 'months': agg.months}
        changed = True
        print(f"BIXI arrondissements {year} : {len(agg.months)} mois, jusqu'au {agg.last_date}.")
    if changed:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(dict(sorted(cache.items())), f, ensure_ascii=False, separators=(',', ':'))


if __name__ == '__main__':
    update_cache(sys.argv[1] if len(sys.argv) > 1 else BIXI_CACHE_FILE)
    update_arr_cache()
