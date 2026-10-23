"""
Audit des données publiées (data/site.json) contre les fichiers sources.

Chaque valeur est recalculée ici à partir des fichiers bruts, avec un code
indépendant de genMap.py, puis comparée à data/site.json. Quand la source fournit
ses propres agrégats (totaux journaliers, mensuels et annuels de la Ville), ils
servent de référence externe : nos totaux doivent y être identiques.

    python3 test_sources.py              # après fetch_data.sh et genMap.py
    python3 test_sources.py --profond    # + historique et BIXI des années passées,
                                         #   retéléchargés depuis les portails (long)

Les « avertissements » signalent des limites de méthode (ex. réseau de compteurs
différent d'une année à l'autre) : ils ne bloquent pas la publication.
"""
import csv
import io
import json
import math
import os
import re
import sys
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from site_format import SITE_DATA_FILE, decode_hour_runs

PASS = "\033[92m PASS\033[0m"
FAIL = "\033[91m FAIL\033[0m"
WARN = "\033[93m AVERT.\033[0m"
MTL = ZoneInfo('America/Montreal')
PROFOND = '--profond' in sys.argv

failures, warnings = [], []
n_checks = 0


def check(name, condition, detail="", quiet=False):
    """Une vérification ; `quiet` : n'affiche que les échecs (vérifications en série)."""
    global n_checks
    n_checks += 1
    if condition:
        if not quiet:
            print(f"{PASS}  {name}")
    else:
        print(f"{FAIL}  {name}")
        print(f"       → {detail}")
        failures.append(name)
    return condition


def warn(name, detail):
    print(f"{WARN}  {name}")
    print(f"       → {detail}")
    warnings.append(name)


def summary(title, start):
    n = n_checks - start[0]
    f = len(failures) - start[1]
    print(f"{PASS if not f else FAIL}  {title} — {n} vérifications" + (f", {f} en échec" if f else ""))


def mark():
    return (n_checks, len(failures))


def first_diff(a, b):
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            return f"{k} : site={a.get(k)} source={b.get(k)}"
    return "—"


def shift(d, k):
    return (date.fromisoformat(d) + timedelta(days=k)).isoformat()


# ── Données publiées ─────────────────────────────────────────────────────────

with open(SITE_DATA_FILE, encoding='utf-8') as f:
    SITE = json.load(f)
WINDOW_START = datetime.fromisoformat(SITE['windowStart'])
HOURLY_DAYS = SITE['HOURLY_DAYS']
GEN_UTC = datetime.strptime(SITE['generatedAt'], '%Y-%m-%d à %H:%M')
GEN_DATE = GEN_UTC.date().isoformat()
CUR_YEAR = GEN_UTC.year


def site_daily(inst):
    d = SITE['allDailyData'][inst]
    out = {}
    for i, day in enumerate(d['labels']):
        vals = [s[i] for s in d['data'] if s[i] is not None]
        if vals:
            out[day] = sum(vals)
    return out


def site_hourly(inst):
    c = SITE['allChartData'][inst]
    out = {}
    for i, lbl in enumerate(decode_hour_runs(c['hours'])):
        vals = [ds['data'][i] for ds in c['datasets'] if ds['data'][i] is not None]
        if vals:
            out[lbl] = sum(vals)
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 1. Détecteurs SUM (cyclistes.csv)
# ═════════════════════════════════════════════════════════════════════════════
print("\n=== 1. Source cyclistes.csv (détecteurs SUM de la Ville) ===")
start = mark()

SUM_HEADER = ['agg_code', 'instance', 'longitude', 'latitude', 'arrondissement', 'rue_1', 'rue_2',
              'numeroVoie', 'direction', 'periode', 'volume', 'vitesseMoyenne']
lane_hours = Counter()                       # (instance, direction, voie, heure) → nb de lignes
lane_h_day = defaultdict(int)                # (instance, direction, voie, jour) → somme des heures
lane_f_day = defaultdict(int)                # idem, quarts d'heure
ville_d = defaultdict(int)                   # (instance, direction, voie, jour) → total publié
ville_m = {}                                 # (instance, direction, voie, mois) → total publié
ville_y = {}
sum_hourly = defaultdict(lambda: defaultdict(int))   # instance → heure → total (fenêtre)
sum_meta = defaultdict(set)                  # instance → {(lat, lng, arrondissement, rue_1, rue_2)}
sum_hours_by_day = defaultdict(lambda: defaultdict(set))
bad_rows = Counter()
max_hour_vol = (0, None)
latest_h = ''

with open('cyclistes.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    check("En-tête de cyclistes.csv", reader.fieldnames == SUM_HEADER, reader.fieldnames)
    for r in reader:
        # Ancien format (cyclistes.csv de repli) : fuseau horaire en suffixe, ex. « -05 »
        a, p = r['agg_code'], re.sub(r'[+-]\d{2}$', '', r['periode'].strip())
        if a not in ('h', 'd', 'm', 'y', 'f'):
            bad_rows['agg_code inconnu'] += 1
            continue
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?', p):
            bad_rows['période illisible'] += 1
            continue
        try:
            v = int(r['volume'])
        except ValueError:
            bad_rows['volume non entier'] += 1
            continue
        if v < 0:
            bad_rows['volume négatif'] += 1
        lane = (r['instance'], r['direction'], r['numeroVoie'])
        if a == 'h':
            lane_hours[lane + (p[:16],)] += 1
            lane_h_day[lane + (p[:10],)] += v
            latest_h = max(latest_h, p[:16])
            if v > max_hour_vol[0]:
                max_hour_vol = (v, (r['instance'], p))
            if datetime.fromisoformat(p) >= WINDOW_START:
                sum_hourly[r['instance']][p[:16]] += v
                sum_hours_by_day[r['instance']][p[:10]].add(p[11:13])
                sum_meta[r['instance']].add((float(r['latitude']), float(r['longitude']), r['arrondissement'],
                                             r['rue_1'], r['rue_2']))
        elif a == 'f':
            lane_f_day[lane + (p[:10],)] += v
        elif a == 'd':
            ville_d[lane + (p[:10],)] += v
        elif a == 'm':
            ville_m[lane + (p[:7],)] = v
        elif a == 'y':
            ville_y[lane + (p[:4],)] = v

check("Toutes les lignes sont lisibles (type, période, volume entier ≥ 0)", not bad_rows, dict(bad_rows))
dups = [k for k, n in lane_hours.items() if n > 1]
check("Aucune heure publiée deux fois pour une même voie", not dups, dups[:5])
check("Somme des heures = total journalier publié par la Ville (chaque voie, chaque jour)",
      all(lane_h_day.get(k) == v for k, v in ville_d.items()) and set(lane_h_day) == set(ville_d),
      first_diff(lane_h_day, ville_d))
check("Somme des quarts d'heure = somme des heures (chaque voie, chaque jour)",
      lane_f_day == lane_h_day, first_diff(lane_f_day, lane_h_day))
cur_month = latest_h[:7]
ville_d_month = defaultdict(int)
for k, v in ville_d.items():
    ville_d_month[k[:3] + (k[3][:7],)] += v
m_bad = {k: (v, ville_d_month.get(k)) for k, v in ville_m.items() if k[3] != cur_month and v != ville_d_month.get(k)}
check("Totaux mensuels de la Ville = somme de ses totaux journaliers (mois terminés)", not m_bad,
      list(m_bad.items())[:5])
y_from_m = defaultdict(int)
for k, v in ville_m.items():
    y_from_m[k[:3] + (k[3][:4],)] += v
check("Totaux annuels de la Ville = somme de ses totaux mensuels", all(y_from_m.get(k) == v for k, v in ville_y.items()),
      first_diff(dict(y_from_m), ville_y))
def dist_m(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def spread_m(points):
    pts = list(points)
    return max((dist_m(*a, *b) for a in pts for b in pts), default=0)


# L'orthographe des rues varie d'un fichier à l'autre : seul un déplacement compte
moved = {i: sorted(m) for i, m in sum_meta.items() if spread_m({(x[0], x[1]) for x in m}) > 25}
check("Aucun détecteur déplacé de plus de 25 m sur la fenêtre", not moved, list(moved.items())[:3])
arr_changed = {i: sorted({x[2] for x in m}) for i, m in sum_meta.items() if len({x[2] for x in m}) > 1}
check("Arrondissement constant pour chaque détecteur", not arr_changed, arr_changed)
if max_hour_vol[0] > 1500:
    warn("Volume horaire très élevé", f"{max_hour_vol[0]} passages en une heure ({max_hour_vol[1]})")
summary("Intégrité de cyclistes.csv et cohérence avec les agrégats de la Ville", start)

# ═════════════════════════════════════════════════════════════════════════════
# 2. Capteurs au sol (compteurs.csv)
# ═════════════════════════════════════════════════════════════════════════════
print("\n=== 2. Source compteurs.csv (capteurs au sol, quarts d'heure) ===")
start = mark()
ECO_HEADER = ['date', 'heure', 'id_compteur', 'nb_passages', 'longitude', 'latitude']
eco_q = Counter()
eco_hour = defaultdict(lambda: defaultdict(int))     # id → 'AAAA-MM-JJ HH:00' → total
eco_hours_of_day = defaultdict(lambda: defaultdict(set))
eco_coords = defaultdict(set)
eco_bad = Counter()
eco_empty_window = 0
with open('compteurs.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    check("En-tête de compteurs.csv", reader.fieldnames == ECO_HEADER, reader.fieldnames)
    for r in reader:
        d, h, cid = r['date'], r['heure'], r['id_compteur']
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', d) or not re.fullmatch(r'\d{2}:(00|15|30|45):00', h):
            eco_bad['date/heure hors grille de 15 min'] += 1
            continue
        hour_key = f"{d} {h[:2]}:00"
        in_window = datetime.fromisoformat(hour_key) >= WINDOW_START
        if in_window:
            eco_q[(cid, d, h)] += 1
        raw = r['nb_passages'].strip()
        if not raw:
            eco_empty_window += in_window
            continue
        try:
            v = int(raw)
        except ValueError:
            eco_bad['nb_passages non entier'] += 1
            continue
        if v < 0:
            eco_bad['nb_passages négatif'] += 1
        eco_hour[cid][hour_key] += v
        eco_hours_of_day[cid][d].add(h[:2])
        eco_coords[cid].add((r['latitude'], r['longitude']))

check("Toutes les lignes sont lisibles (grille de 15 min, entier ≥ 0)", not eco_bad, dict(eco_bad))
eco_dups = [k for k, n in eco_q.items() if n > 1]
check("Aucun quart d'heure publié deux fois (fenêtre)", not eco_dups, eco_dups[:5])
if eco_empty_window:
    warn("Quarts d'heure sans valeur dans la fenêtre", f"{eco_empty_window} valeurs vides, ignorées")
moved = {c: s for c, s in eco_coords.items() if spread_m({(float(a), float(b)) for a, b in s}) > 25}
check("Aucun capteur au sol déplacé de plus de 25 m", not moved, list(moved.items())[:3])

# Recalcul indépendant : un jour n'est retenu que s'il compte ≥ 12 heures de données
# (le portail publie parfois des journées partielles), heures ≥ début de la fenêtre.
eco_daily, eco_hourly, excluded = {}, {}, Counter()
for cid, hours in eco_hour.items():
    complete = {d for d, hs in eco_hours_of_day[cid].items() if len(hs) >= 12}
    kept = {k: v for k, v in hours.items() if datetime.fromisoformat(k) >= WINDOW_START and k[:10] in complete}
    for k, v in hours.items():
        if datetime.fromisoformat(k) >= WINDOW_START and k[:10] not in complete:
            excluded[k[:10]] += v
    if kept:
        inst = f"vf-{cid}"
        eco_hourly[inst] = kept
        days = defaultdict(int)
        for k, v in kept.items():
            days[k[:10]] += v
        eco_daily[inst] = dict(days)
if excluded:
    warn("Journées partielles des capteurs au sol écartées (< 12 h de données)",
         f"{sum(excluded.values())} passages sur {len(excluded)} jours (ex. {sorted(excluded)[-3:]})")

site_vf = {i for i in SITE['allDailyData'] if i.startswith('vf-')}
check("Mêmes capteurs au sol publiés que dans la source", site_vf == set(eco_daily),
      f"en trop : {sorted(site_vf - set(eco_daily))[:5]}, manquants : {sorted(set(eco_daily) - site_vf)[:5]}")
for inst in sorted(site_vf & set(eco_daily)):
    sd = site_daily(inst)
    check(f"[{inst}] Totaux journaliers", sd == eco_daily[inst], first_diff(sd, eco_daily[inst]), quiet=True)
    max_day = max(eco_daily[inst])
    hourly_from = shift(max_day, -(HOURLY_DAYS - 1))
    exp_h = {k: v for k, v in eco_hourly[inst].items() if k[:10] >= hourly_from}
    sh = site_hourly(inst)
    check(f"[{inst}] Données horaires ({HOURLY_DAYS} derniers jours)", sh == exp_h, first_diff(sh, exp_h), quiet=True)
summary("Capteurs au sol : totaux journaliers et horaires recalculés depuis les quarts d'heure", start)

# ═════════════════════════════════════════════════════════════════════════════
# 3. Détecteurs SUM : site.json contre les agrégats officiels de la Ville
# ═════════════════════════════════════════════════════════════════════════════
print("\n=== 3. Détecteurs SUM : données publiées contre les totaux de la Ville ===")
start = mark()
first_day = WINDOW_START.date().isoformat()
ville_inst_day = defaultdict(lambda: defaultdict(int))
for (inst, _, _, d), v in ville_d.items():
    if d > first_day:                      # le 1er jour de la fenêtre est partiel
        ville_inst_day[inst][d] += v
ville_inst_month = defaultdict(lambda: defaultdict(int))
for (inst, _, _, m), v in ville_m.items():
    ville_inst_month[inst][m] += v

site_det = {i for i in SITE['allDailyData'] if i.startswith('det-')}
check("Mêmes détecteurs publiés que dans la source (fenêtre)", site_det == set(sum_hourly),
      f"en trop : {sorted(site_det - set(sum_hourly))}, manquants : {sorted(set(sum_hourly) - site_det)}")
sum_daily = {}
for inst in sorted(site_det & set(sum_hourly)):
    days = defaultdict(int)
    for k, v in sum_hourly[inst].items():
        days[k[:10]] += v
    sum_daily[inst] = dict(days)
    sd = site_daily(inst)
    sd_full = {d: v for d, v in sd.items() if d > first_day}
    vd = {d: v for d, v in ville_inst_day[inst].items() if d in sd_full or v}
    check(f"[{inst}] Totaux journaliers = totaux journaliers de la Ville", sd_full == vd, first_diff(sd_full, vd), quiet=True)
    # Mois entièrement dans la fenêtre et terminés
    by_m = defaultdict(int)
    for d, v in sd.items():
        by_m[d[:7]] += v
    for m, v in by_m.items():
        if m > first_day[:7] and m != cur_month:
            check(f"[{inst}] Total de {m} = total mensuel de la Ville", v == ville_inst_month[inst].get(m),
                  f"site={v}, Ville={ville_inst_month[inst].get(m)}", quiet=True)
summary("Totaux journaliers et mensuels de chaque détecteur identiques à ceux publiés par la Ville", start)

ALL_DAILY = {**sum_daily, **eco_daily}
ALL_HOURLY = {**{i: dict(h) for i, h in sum_hourly.items()}, **eco_hourly}

# ═════════════════════════════════════════════════════════════════════════════
# 4. Profils horaires, localisations, fenêtre
# ═════════════════════════════════════════════════════════════════════════════
print("\n=== 4. Profils horaires, localisations et fenêtre ===")
start = mark()
for inst, daily in ALL_DAILY.items():
    if inst not in SITE['hourProfile']:
        continue
    max_day = max(daily)
    for p in ('180', '365'):
        lo = shift(max_day, -(int(p) - 1))
        prof = [0] * 24
        for k, v in ALL_HOURLY[inst].items():
            if k[:10] >= lo:
                prof[int(k[11:13])] += v
        check(f"[{inst}] Profil horaire {p} j", SITE['hourProfile'][inst][p] == prof,
              f"{SITE['hourProfile'][inst][p]} ≠ {prof}", quiet=True)
for inst, metas in sum_meta.items():
    loc = SITE['counterLocations'].get(inst, {})
    check(f"[{inst}] Localisation", any(dist_m(loc.get('lat', 0), loc.get('lng', 0), m[0], m[1]) <= 25
                                        and loc.get('arrondissement') == m[2] for m in metas),
          f"{loc} ≠ {sorted(metas)}", quiet=True)
    check(f"[{inst}] Nom contient les rues", any(m[3] in loc.get('label', '') and m[4] in loc.get('label', '')
                                                 for m in metas), loc.get('label'), quiet=True)
for cid, coords in eco_coords.items():
    inst = f"vf-{cid}"
    if inst in SITE['counterLocations']:
        loc = SITE['counterLocations'][inst]
        check(f"[{inst}] Localisation", any(dist_m(loc['lat'], loc['lng'], float(a), float(b)) <= 25 for a, b in coords),
              f"{loc} ≠ {coords}", quiet=True)
all_days = [d for i in SITE['allDailyData'] for d in SITE['allDailyData'][i]['labels']]
check("Aucune journée avant le début de la fenêtre ni après la génération",
      min(all_days) >= first_day and max(all_days) <= GEN_DATE, f"{min(all_days)} → {max(all_days)}", quiet=True)
summary("Profils horaires (6 et 12 mois), localisations et bornes de la fenêtre", start)

# ═════════════════════════════════════════════════════════════════════════════
# 5. Statut des compteurs, lacunes, anomalies
# ═════════════════════════════════════════════════════════════════════════════
print("\n=== 5. Statut des compteurs, lacunes et anomalies ===")
start = mark()
# Heure locale de la génération : UTC (GitHub Actions) ou Montréal (poste local)
gen_candidates = [GEN_UTC, GEN_UTC.replace(tzinfo=timezone.utc).astimezone(MTL).replace(tzinfo=None)]


def expected_group(last_any, last_complete):
    groups = set()
    for now in gen_candidates:
        lag_any = (now - datetime.fromisoformat(last_any)).days if last_any else 999
        lag_c = (now - datetime.fromisoformat(last_complete)).days if last_complete else 999
        groups.add('C' if lag_any > 30 else 'B' if lag_c > 7 else 'A')
    return groups


Q = SITE['vfDataQuality']
for inst, by_day in sum_hours_by_day.items():
    last_any = max(by_day)
    last_complete = max((d for d, hs in by_day.items() if len(hs) >= 12), default=None)
    q = Q.get(inst, {})
    check(f"[{inst}] Dernières données", (q.get('last_any'), q.get('last_complete')) == (last_any, last_complete),
          f"{q} ≠ {(last_any, last_complete)}", quiet=True)
    check(f"[{inst}] Statut (actif / en retard / inactif)", q.get('group') in expected_group(last_any, last_complete),
          f"{q.get('group')} ∉ {expected_group(last_any, last_complete)}", quiet=True)
for cid, by_day in eco_hours_of_day.items():
    inst = f"vf-{cid}"
    last_any = max(by_day)
    last_complete = max((d for d, hs in by_day.items() if len(hs) >= 12), default=None)
    q = Q.get(inst, {})
    check(f"[{inst}] Dernières données", (q.get('last_any'), q.get('last_complete')) == (last_any, last_complete),
          f"{q} ≠ {(last_any, last_complete)}", quiet=True)
    check(f"[{inst}] Statut", q.get('group') in expected_group(last_any, last_complete),
          f"{q.get('group')} ∉ {expected_group(last_any, last_complete)}", quiet=True)


def gappy(days):
    ds = sorted(date.fromisoformat(d) for d in days)
    if len(ds) < 14:
        return False
    span = (ds[-1] - ds[0]).days + 1
    if (span - len(ds)) / span > 0.20:
        return True
    return any((b - a).days > 14 for a, b in zip(ds, ds[1:]))


exp_gappy = sorted(i for i, d in ALL_DAILY.items() if gappy(d))
check("Compteurs signalés « lacunaires » (> 20 % de jours manquants ou trou > 14 j)",
      SITE['gappyCounters'] == exp_gappy,
      f"en trop : {sorted(set(SITE['gappyCounters']) - set(exp_gappy))}, manquants : {sorted(set(exp_gappy) - set(SITE['gappyCounters']))}")

W = SITE['weatherData']
for inst, days in SITE['anomalyDays'].items():
    daily = ALL_DAILY.get(inst, {})
    lo = min(daily) if daily else None
    hi = max(max(daily), shift(GEN_DATE, -1)) if daily else None
    for d, a in days.items():
        tag = f"[{inst} {d}] Anomalie"
        if a['total'] == 0:
            check(f"{tag} : journée réellement absente", daily.get(d, 0) == 0, f"source={daily.get(d)}", quiet=True)
        else:
            check(f"{tag} : volume = total du jour", a['total'] == daily.get(d), f"{a['total']} ≠ {daily.get(d)}",
                  quiet=True)
        check(f"{tag} : volume < attendu", a['total'] < a['expected'], a, quiet=True)
        check(f"{tag} : dans la période active du compteur", lo is not None and lo <= d <= hi, (lo, hi), quiet=True)
        w = W.get(d)
        check(f"{tag} : pas un jour de météo extrême (exclus)", not (w and (w['precip'] > 15 or w['snow'] > 5 or w['tmax'] < -15)),
              w, quiet=True)
summary("Statut de chaque compteur, compteurs lacunaires et cohérence des anomalies signalées", start)

# ═════════════════════════════════════════════════════════════════════════════
# 6. Vue globale
# ═════════════════════════════════════════════════════════════════════════════
print("\n=== 6. Vue globale ===")
start = mark()
GS = SITE['globalStats']
active = [i for i in ALL_DAILY if Q.get(i, {}).get('group') in ('A', 'B')]
hier = shift(GEN_DATE, -1)
week = [shift(hier, -k) for k in range(7)]
month = sorted({d for i in active for d in ALL_DAILY[i] if d[:7] == hier[:7] and d <= hier})
for key, dates in (('yesterday', [hier]), ('week', week), ('month', month)):
    s = GS[key]
    tots = {i: sum(ALL_DAILY[i].get(d, 0) for d in dates) for i in active}
    check(f"[{key}] Total des passages", s['total'] == sum(tots.values()), f"{s['total']} ≠ {sum(tots.values())}")
    cov = sum(1 for i in active if any(ALL_DAILY[i].get(d, 0) > 0 for d in dates))
    check(f"[{key}] Compteurs avec données", (s['coverage'], s['active']) == (cov, len(active)),
          f"{(s['coverage'], s['active'])} ≠ {(cov, len(active))}")
    ranked = sorted(tots.values(), reverse=True)
    check(f"[{key}] Deux compteurs les plus achalandés", [t['vol'] for t in s['top']] == ranked[:2] and
          all(tots.get(t['id']) == t['vol'] for t in s['top']), f"{s['top']} ≠ {ranked[:2]}")
    by_h = defaultdict(int)
    for i in active:
        for k, v in ALL_HOURLY[i].items():
            if k[:10] in dates:
                by_h[int(k[11:13])] += v
    best = max(by_h.values()) if by_h else None
    check(f"[{key}] Heure de pointe", s['peak_hour'] is None if best is None else by_h.get(s['peak_hour']) == best,
          f"{s['peak_hour']} ({by_h.get(s['peak_hour'])}) ≠ max {best}")
arr_vol = defaultdict(int)
for i in active:
    arr_vol[SITE['counterLocations'][i]['arrondissement']] += sum(ALL_DAILY[i].get(d, 0) for d in week)
exp_rank = sorted(arr_vol.values(), reverse=True)[:6]
check("Classement des arrondissements (7 jours)",
      [a['vol'] for a in GS['arr_rank']] == exp_rank and all(arr_vol[a['name']] == a['vol'] for a in GS['arr_rank']),
      f"{GS['arr_rank']} ≠ {exp_rank}")
net = Counter(q['group'] for q in Q.values())
check("Statut du réseau", GS['net_status'] == {'A': net['A'], 'B': net['B'], 'C': net['C'], 'total': len(Q)},
      f"{GS['net_status']} ≠ {dict(net)}")
summary("Totaux d'hier, des 7 derniers jours et du mois, pointes, classements, statut du réseau", start)

# ═════════════════════════════════════════════════════════════════════════════
# 7. Historique
# ═════════════════════════════════════════════════════════════════════════════
print("\n=== 7. Historique (2009 → année en cours) ===")
start = mark()
with open('hist_cache.json', encoding='utf-8') as f:
    CACHE = json.load(f)
HY, HC, HM = SITE['historicalYearlyTotals'], SITE['historicalYearlyCounters'], SITE['counterHistoricalData']
LIVE = {str(y) for y in CACHE.get('live_years', [])}
check("Historique publié = hist_cache.json (totaux, compteurs, mois, années récentes)",
      HY == CACHE['yearly'] and HC == CACHE['yearly_counters'] and HM == CACHE['counter_monthly']
      and {str(y) for y in SITE.get('historicalLiveYears', [])} == LIVE)
cur = str(CUR_YEAR)
check(f"{cur} fait partie des années tenues à jour depuis les données récentes", cur in LIVE, sorted(LIVE))

# Mois entièrement compris dans la fenêtre, recalculés depuis les sources (1er jour partiel exclu)
live_months = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
for inst, daily in ALL_DAILY.items():
    for d, v in daily.items():
        live_months[inst][d[:4]][str(int(d[5:7]))] += v


def in_window(y, m):
    return datetime(int(y), int(m), 1) > WINDOW_START


# Cache du dernier commit : référence pour vérifier qu'aucune donnée n'est perdue ni modifiée
prev_path = os.environ.get('HIST_CACHE_PRECEDENT')
try:
    if prev_path:
        with open(prev_path, encoding='utf-8') as f:
            PREV = json.load(f)
    else:
        import subprocess
        PREV = json.loads(subprocess.run(['git', 'show', 'HEAD:hist_cache.json'], capture_output=True,
                                         check=True).stdout)
except Exception as e:
    PREV = None
    warn("Historique non comparé au dernier commit", f"hist_cache.json précédent introuvable ({e})")

for inst, years in live_months.items():
    for y, months in years.items():
        for m, v in months.items():
            if not in_window(y, m):
                continue
            got = CACHE['counter_monthly'].get(inst, {}).get(y, {}).get(m)
            if y in LIVE:
                check(f"[{inst}] {y}-{m} = données récentes", got == v, f"cache={got}, sources={v}", quiet=True)
            elif PREV is not None and m not in PREV['counter_monthly'].get(inst, {}).get(y, {}):
                check(f"[{inst}] {y}-{m} ajouté = données récentes", got == v, f"cache={got}, sources={v}", quiet=True)
for y in LIVE:
    ctrs = [ys[y] for ys in CACHE['counter_monthly'].values() if ys.get(y)]
    check(f"Total {y} = somme des mois de tous les compteurs", CACHE['yearly'].get(y) == sum(sum(m.values()) for m in ctrs),
          f"{CACHE['yearly'].get(y)} ≠ {sum(sum(m.values()) for m in ctrs)}")
    check(f"Nombre de compteurs en {y}", CACHE['yearly_counters'].get(y) == len(ctrs),
          f"{CACHE['yearly_counters'].get(y)} ≠ {len(ctrs)}")
cur_total = sum(v for daily in ALL_DAILY.values() for d, v in daily.items() if d[:4] == cur)
check(f"Total {cur} = somme de tous les compteurs depuis le 1er janvier", HY.get(cur) == cur_total,
      f"{HY.get(cur)} ≠ {cur_total}")

if PREV is not None:
    lost = [(i, y, m) for i, ys in PREV['counter_monthly'].items() for y, ms in ys.items() for m in ms
            if m not in CACHE['counter_monthly'].get(i, {}).get(y, {})]
    check("Aucun mois perdu depuis le dernier commit", not lost, lost[:5])
    check("Aucune année perdue depuis le dernier commit", set(PREV['yearly']) <= set(CACHE['yearly']),
          sorted(set(PREV['yearly']) - set(CACHE['yearly'])))
    frozen = [(y, PREV['yearly'][y], CACHE['yearly'].get(y)) for y in PREV['yearly']
              if y not in LIVE and (PREV['yearly'][y], PREV['yearly_counters'].get(y)) !=
              (CACHE['yearly'].get(y), CACHE['yearly_counters'].get(y))]
    check("Totaux des années du portail inchangés", not frozen, frozen[:5])
    changed = [(i, y, m, v, CACHE['counter_monthly'].get(i, {}).get(y, {}).get(m))
               for i, ys in PREV['counter_monthly'].items() for y, ms in ys.items() for m, v in ms.items()
               if (y not in LIVE or not in_window(y, m)) and CACHE['counter_monthly'].get(i, {}).get(y, {}).get(m) != v]
    check("Mois déjà enregistrés inchangés (années du portail et mois sortis de la fenêtre)", not changed, changed[:5])
summary("Totaux annuels, nombre de compteurs et totaux mensuels par compteur", start)

# ═════════════════════════════════════════════════════════════════════════════
# 8. Météo
# ═════════════════════════════════════════════════════════════════════════════
print("\n=== 8. Météo (Open-Meteo) ===")
start = mark()
check("Météo plausible (tmin ≤ tmax, précipitations et neige ≥ 0)",
      all(w['tmin'] <= w['tmax'] and w['precip'] >= 0 and w['snow'] >= 0 for w in W.values()),
      [d for d, w in W.items() if not (w['tmin'] <= w['tmax'] and w['precip'] >= 0 and w['snow'] >= 0)][:5])
wd = sorted(W)
check("Météo disponible pour chaque jour de la fenêtre", all(d in W for d in sorted(set(all_days))),
      [d for d in sorted(set(all_days)) if d not in W][:5])
try:
    def fetch(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'compteur-velo-tests/1.0'})
        return json.loads(urllib.request.urlopen(req, timeout=30).read())['daily']
    q = ("?latitude=45.5017&longitude=-73.5673&daily=temperature_2m_max,temperature_2m_min,"
         "precipitation_sum,snowfall_sum&timezone=America%2FToronto")
    arch = fetch(f"https://archive-api.open-meteo.com/v1/archive{q}&start_date={wd[0]}&end_date={wd[-1]}")
    fc = fetch(f"https://api.open-meteo.com/v1/forecast{q}&past_days=10&forecast_days=0")
    ref = {}
    for src in (arch, fc):
        for i, d in enumerate(src['time']):
            if src['temperature_2m_max'][i] is not None:
                ref.setdefault(d, []).append((src['temperature_2m_max'][i], src['temperature_2m_min'][i],
                                              src['precipitation_sum'][i] or 0, src['snowfall_sum'][i] or 0))
    bad = []
    for d, w in W.items():
        cands = ref.get(d, [])
        if not any(abs(w['tmax'] - a) <= 0.15 and abs(w['tmin'] - b) <= 0.15 and abs(w['precip'] - c) <= 0.15
                   and abs(w['snow'] - e) <= 0.15 for a, b, c, e in cands):
            bad.append((d, w, cands))
    # Les jours les plus récents sont encore révisés par Open-Meteo
    old_bad = [b for b in bad if b[0] < shift(GEN_DATE, -12)]
    check("Météo identique à Open-Meteo (archive et prévisions récentes)", not old_bad, old_bad[:3])
    if bad and not old_bad:
        warn("Météo des derniers jours révisée depuis la génération", f"{len(bad)} jours (ex. {bad[0][0]})")
except Exception as e:
    warn("Météo non revérifiée (Open-Meteo inaccessible)", str(e))
summary("Valeurs météo plausibles, complètes et identiques à Open-Meteo", start)

# ═════════════════════════════════════════════════════════════════════════════
# 9. BIXI
# ═════════════════════════════════════════════════════════════════════════════
print("\n=== 9. BIXI ===")
start = mark()
BY = SITE['bixiYearly']
for y, s in BY.items():
    for scope, d in (('réseau', s), ('Montréal', s.get('mtl'))):
        if not d:
            continue
        tag = f"[BIXI {y} {scope}]"
        check(f"{tag} trajets = somme des jours", d['trips'] == sum(d['daily'].values()),
              f"{d['trips']} ≠ {sum(d['daily'].values())}", quiet=True)
        check(f"{tag} jours de l'année {y}", all(k[:4] == y for k in d['daily']),
              [k for k in d['daily'] if k[:4] != y][:3], quiet=True)
        check(f"{tag} répartition des durées ≤ trajets", sum(d['duration_hist']) <= d['trips'], "", quiet=True)
        check(f"{tag} stations les plus populaires triées", [n for _, n in d['top_stations']] ==
              sorted((n for _, n in d['top_stations']), reverse=True), "", quiet=True)
        if d.get('member_share') is not None:
            check(f"{tag} part des membres entre 0 et 1", 0 <= d['member_share'] <= 1, d['member_share'], quiet=True)
        if abs(sum(d['hourly']) - d['trips']) > d['trips'] * 0.001:
            check(f"{tag} trajets par heure = trajets de l'année", False,
                  f"{sum(d['hourly'])} ≠ {d['trips']}")
    if s.get('mtl'):
        check(f"[BIXI {y}] Montréal ≤ réseau, chaque jour",
              all(n <= s['daily'].get(k, 0) for k, n in s['mtl']['daily'].items()), "", quiet=True)

if os.path.exists('bixi.csv'):
    loc = SITE['counterLocations']
    near_cache = {}

    def near(lat, lng):
        key = (lat, lng)
        if key not in near_cache:
            la, lo = float(lat), float(lng)
            near_cache[key] = {i for i, c in loc.items() if dist_m(c['lat'], c['lng'], la, lo) <= 150}
        return near_cache[key]

    hour_local = {}
    live_daily, live_hourly = Counter(), [0] * 24
    nearby = defaultdict(Counter)
    unreadable = 0
    with open('bixi.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                ms = int(float(r['STARTTIMEMS']))
            except (KeyError, ValueError):
                unreadable += 1
                continue
            hk = ms // 3600000
            if hk not in hour_local:
                t = datetime.fromtimestamp(hk * 3600, tz=timezone.utc).astimezone(MTL)
                hour_local[hk] = (t.date().isoformat(), t.hour)
            d, h = hour_local[hk]
            live_daily[d] += 1
            live_hourly[h] += 1
            try:
                ids = near(r['STARTSTATIONLATITUDE'], r['STARTSTATIONLONGITUDE']) | \
                      near(r['ENDSTATIONLATITUDE'], r['ENDSTATIONLONGITUDE'])
            except ValueError:
                ids = set()
            for i in ids:
                nearby[i][d] += 1
    live_year = Counter({y: 0 for y in []})
    for d, n in live_daily.items():
        live_year[d[:4]] += n
    ly = live_year.most_common(1)[0][0]
    exp_daily = {d: n for d, n in live_daily.items() if d[:4] == ly}
    s = BY.get(ly, {})
    check(f"[BIXI {ly}] Trajets recomptés depuis bixi.csv (heure de Montréal)", s.get('trips') == sum(exp_daily.values()),
          f"site={s.get('trips')}, bixi.csv={sum(exp_daily.values())}")
    check(f"[BIXI {ly}] Trajets par jour", s.get('daily') == exp_daily, first_diff(s.get('daily', {}), exp_daily))
    check(f"[BIXI {ly}] Trajets par heure", s.get('hourly') == live_hourly, f"{s.get('hourly')} ≠ {live_hourly}")
    if unreadable:
        warn("Trajets BIXI illisibles", f"{unreadable} lignes sans heure de départ")
    exp_near = {i: dict(c) for i, c in nearby.items()}
    check("Trajets BIXI près de chaque compteur (≤ 150 m, jour de Montréal)", SITE['bixiNearby'] == exp_near,
          next((f"{i} : {first_diff(SITE['bixiNearby'].get(i, {}), exp_near.get(i, {}))}"
                for i in sorted(set(SITE['bixiNearby']) | set(exp_near))
                if SITE['bixiNearby'].get(i) != exp_near.get(i)), "—"))
    exp_exceeds = {}
    for i, days in exp_near.items():
        ex = {d: {'counter': ALL_DAILY.get(i, {}).get(d, 0), 'bixi': n} for d, n in days.items()
              if n > ALL_DAILY.get(i, {}).get(d, 0)}
        if ex:
            exp_exceeds[i] = ex
    check("Jours où BIXI dépasse le compteur", SITE['bixiExceedsDays'] == exp_exceeds,
          next((f"{i} : {first_diff(SITE['bixiExceedsDays'].get(i, {}), exp_exceeds.get(i, {}))}"
                for i in sorted(set(SITE['bixiExceedsDays']) | set(exp_exceeds))
                if SITE['bixiExceedsDays'].get(i) != exp_exceeds.get(i)), "—"))
summary("Cohérence des années BIXI, année en cours recomptée et trajets près des compteurs", start)

# ═════════════════════════════════════════════════════════════════════════════
# 10. Audit profond (--profond) : années passées retéléchargées
# ═════════════════════════════════════════════════════════════════════════════
if PROFOND:
    print("\n=== 10. Audit profond : historique et BIXI des années passées ===")
    start = mark()
    req = urllib.request.Request("https://donnees.montreal.ca/api/3/action/package_show?id=142ff2e9-7d0a-47d6-b4f6-dfeb97041daf",
                                 headers={'User-Agent': 'Mozilla/5.0'})
    res = json.loads(urllib.request.urlopen(req, timeout=60).read())['result']['resources']
    eco_urls = {}
    for r in res:
        m = re.search(r'Comptage (\d{4}) - Eco-Compteur', r.get('name', ''))
        if m:
            eco_urls[m.group(1)] = r['url']
    for y in sorted(CACHE['yearly']):
        if y not in eco_urls or y in LIVE:      # années récentes : tous les compteurs, pas seulement le portail
            continue
        req = urllib.request.Request(eco_urls[y], headers={'User-Agent': 'Mozilla/5.0',
                                                           'Referer': 'https://donnees.montreal.ca/dataset/cyclistes'})
        text = urllib.request.urlopen(req, timeout=300).read().decode('utf-8', errors='replace')
        rows = list(csv.reader(io.StringIO(text)))
        head = rows[0]
        total, monthly = 0, defaultdict(lambda: defaultdict(int))
        if 'id_compteur' in head:
            ci, di = head.index('id_compteur'), head.index('date')
            ni = head.index('nb_passages') if 'nb_passages' in head else head.index('nb_passage')
            for r in rows[1:]:
                if len(r) > ni and r[ni].strip():
                    try:
                        v = int(r[ni])
                    except ValueError:
                        continue
                    total += v
                    monthly[f"vf-{r[ci].strip()}"][str(int(r[di][5:7]))] += v
        else:
            start_col = 2 if len(head) > 1 and not head[1].strip() else 1
            named = [c for c in range(start_col, len(head)) if head[c].strip()]
            for r in rows[1:]:
                if not r or not r[0].strip():
                    continue
                for c in named:
                    try:
                        total += int(r[c].strip())
                    except (ValueError, IndexError):
                        pass
        check(f"Historique {y} : total = fichier du portail ({eco_urls[y].rsplit('/', 1)[-1]})",
              CACHE['yearly'][y] == total, f"cache={CACHE['yearly'][y]:,}, portail={total:,}")
        for inst, months in monthly.items():
            cm = CACHE['counter_monthly'].get(inst, {}).get(y)
            if cm is not None:
                check(f"Historique {y} [{inst}] : mois", cm == dict(months), first_diff(cm, dict(months)), quiet=True)
    summary("Historique retéléchargé depuis le portail de la Ville", start)

    # BIXI : années complètes (bixi_cache.json) recomptées depuis les ZIP officiels.
    # BIXI_ANNEES=2024,2025 limite le téléchargement (plusieurs centaines de Mo par année).
    import tempfile
    import bixi_stats
    start = mark()
    wanted = os.environ.get('BIXI_ANNEES')
    wanted = set(wanted.split(',')) if wanted else None
    urls = bixi_stats.find_zip_urls()      # découverte des liens seulement

    def iter_csv(zf):
        for name in zf.namelist():
            if '__MACOSX' in name:
                continue
            if name.lower().endswith('.zip'):
                yield from iter_csv(zipfile.ZipFile(io.BytesIO(zf.read(name))))
            elif name.lower().endswith('.csv'):
                yield zf, name

    for y in sorted(BY):
        if BY[y].get('partial') or (wanted and y not in wanted) or int(y) not in urls:
            continue
        with tempfile.NamedTemporaryFile(suffix='.zip') as tmp:
            req = urllib.request.Request(urls[int(y)], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=300) as resp:
                while chunk := resp.read(1 << 20):
                    tmp.write(chunk)
            tmp.flush()
            daily = Counter()
            with zipfile.ZipFile(tmp.name) as zf:
                for z, name in iter_csv(zf):
                    with z.open(name) as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig', errors='replace'))
                        cols = {c.lower(): c for c in reader.fieldnames or []}
                        if 'starttimems' in cols:
                            col, cache = cols['starttimems'], {}
                            for r in reader:
                                try:
                                    hk = int(float(r[col])) // 3600000
                                except (ValueError, TypeError):
                                    continue
                                if hk not in cache:
                                    cache[hk] = datetime.fromtimestamp(hk * 3600, tz=timezone.utc).astimezone(MTL).date().isoformat()
                                daily[cache[hk]] += 1
                        elif 'start_date' in cols:
                            col = cols['start_date']
                            for r in reader:
                                d = (r[col] or '')[:10]
                                if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d):
                                    daily[d] += 1
            exp = {d: n for d, n in daily.items() if d[:4] == y}
            check(f"BIXI {y} : trajets = ZIP officiel ({urls[int(y)].rsplit('/', 1)[-1]})",
                  BY[y]['trips'] == sum(exp.values()), f"cache={BY[y]['trips']:,}, ZIP={sum(exp.values()):,}")
            check(f"BIXI {y} : trajets par jour", BY[y]['daily'] == exp, first_diff(BY[y]['daily'], exp))
    summary("Années BIXI passées recomptées depuis les ZIP officiels", start)

# ── Résumé ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
if warnings:
    print(f"\033[93m{len(warnings)} avertissement(s) (méthode, non bloquants) :\033[0m")
    for w in warnings:
        print(f"  • {w}")
if failures:
    print(f"\033[91m{len(failures)} vérification(s) échouée(s) sur {n_checks} :\033[0m")
    for f in failures[:40]:
        print(f"  • {f}")
    if len(failures) > 40:
        print(f"  … et {len(failures) - 40} autres")
    sys.exit(1)
print(f"\033[92mLes {n_checks} vérifications sont passées : les données publiées correspondent aux sources.\033[0m")
