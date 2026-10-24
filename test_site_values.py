"""
Vérifie que chaque chiffre affiché par la page correspond aux données publiées.

Pour chaque page (Compteurs, Vue globale, Arrondissements, Vélo d'hiver, BIXI),
les valeurs sont recalculées ici, en Python, directement à partir de
data/site.json et selon la définition affichée à l'utilisateur — sans réutiliser
les calculs de assets/app.js — puis comparées au texte réellement affiché dans
le navigateur (Playwright). Seules les fonctions de mise en forme de la page
(dates en français) sont réutilisées pour construire les libellés attendus.

    pip install playwright && python3 -m playwright install chromium
    python3 test_site_values.py

Mêmes variables d'environnement facultatives que test_site.py (CHROMIUM_PATH, CDN_DIR).
"""
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from playwright.sync_api import sync_playwright

from site_format import SITE_DATA_FILE, decode_hour_runs
from test_site import open_page, start_server

PASS = "\033[92m PASS\033[0m"
FAIL = "\033[91m FAIL\033[0m"
WARN = "\033[93m AVERT.\033[0m"
failures, warnings = [], []
n_checks = 0


def warn(name, detail):
    """Limite de méthode connue : signalée, mais ne bloque pas la publication."""
    print(f"{WARN}  {name}")
    print(f"       → {detail}")
    warnings.append(name)


def check(name, condition, detail=""):
    global n_checks
    n_checks += 1
    if not condition:
        print(f"{FAIL}  {name}")
        print(f"       → {detail}")
        failures.append(name)


def section(title, before):
    """Résumé d'une section : nombre de vérifications et d'échecs."""
    n_fail = len(failures) - before[1]
    n = n_checks - before[0]
    status = PASS if not n_fail else FAIL
    print(f"{status}  {title} — {n} vérifications" + (f", {n_fail} en échec" if n_fail else ""))


def mark():
    return (n_checks, len(failures))


# ── Utilitaires ──────────────────────────────────────────────────────────────

def js_fixed1(x):
    """Number.prototype.toFixed(1) : arrondi à 0,5 près en s'éloignant de zéro, sur la valeur binaire exacte."""
    return float(Decimal(x).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def jround(x):
    """Math.round de JavaScript (arrondi vers +∞ à .5), et non l'arrondi bancaire de Python."""
    return math.floor(x + 0.5)


_NUM_RE = re.compile(r'[-−]?\d+(?:[.,]\d+)?')


def nums(text):
    """Nombres d'un texte formaté fr-CA (espaces fines insécables, virgule décimale)."""
    t = re.sub(r'(?<=\d)[   ](?=\d)', '', text or '')
    return [float(m.replace('−', '-').replace(',', '.')) for m in _NUM_RE.findall(t)]


def num(text):
    found = nums(text)
    return found[0] if found else None


def iso(d):
    return d.strftime('%Y-%m-%d')


def shift(d, k):
    return iso(date.fromisoformat(d) + timedelta(days=k))


def weekday(d):
    """0 = lundi … 6 = dimanche."""
    return date.fromisoformat(d).weekday()


def cards(page, container):
    return page.evaluate("""sel => [...document.querySelectorAll(sel + ' .global-card')].map(c => ({
        title: (c.querySelector('.global-card-title') || {}).innerText || '',
        date:  (c.querySelector('.global-card-date')  || {}).innerText || '',
        total: (c.querySelector('.global-card-total') || {}).innerText || '',
        meta:  (c.querySelector('.global-card-meta')  || {}).innerText || '',
    }))""", container)


def table_rows(page, container):
    return page.evaluate("""sel => [...document.querySelectorAll(sel + ' tbody tr')]
        .map(tr => [...tr.children].map(td => td.innerText.trim()))""", container)


def chart_data(page, key, dataset=0):
    return page.evaluate("""([k, i]) => charts[k] ? {
        labels: charts[k].data.labels, data: charts[k].data.datasets[i].data } : null""", [key, dataset])


def close(a, b, tol=0.5):
    return a is not None and b is not None and abs(a - b) <= tol


# ── Données publiées ─────────────────────────────────────────────────────────

with open(SITE_DATA_FILE, encoding='utf-8') as f:
    SITE = json.load(f)

DAILY = SITE['allDailyData']
WEATHER = SITE['weatherData']
ANOM = SITE['anomalyDays']
QUALITY = SITE['vfDataQuality']
LOC = SITE['counterLocations']
HOURLY_DAYS = SITE['HOURLY_DAYS']


def counter_daily(inst):
    """Total quotidien d'un compteur, toutes directions (jours sans aucune donnée exclus)."""
    d = DAILY[inst]
    out = {}
    for i, day in enumerate(d['labels']):
        vals = [s[i] for s in d['data'] if s[i] is not None]
        if vals:
            out[day] = sum(vals)
    return out


def counter_hourly(inst):
    """{étiquette horaire: total toutes directions} sur la fenêtre horaire."""
    c = SITE['allChartData'][inst]
    labels = decode_hour_runs(c['hours'])
    out = {}
    for i, lbl in enumerate(labels):
        vals = [ds['data'][i] for ds in c['datasets'] if ds['data'][i] is not None]
        if vals:
            out[lbl] = sum(vals)
    return out


# ── Spécifications (ce que chaque chiffre affiché est censé représenter) ────

def spec_counter_stats(inst, days):
    """Statistiques du panneau Compteurs pour les `days` derniers jours du compteur :
    total, moyenne par jour de données, deux heures de pointe (≥ 3 h d'écart)."""
    daily = counter_daily(inst)
    max_day = max(daily)
    start = shift(max_day, -(days - 1))
    in_days = [d for d in daily if d >= start]
    total = sum(daily[d] for d in in_days)
    n_days = len(in_days)
    hour_tot = defaultdict(int)
    if days > HOURLY_DAYS:
        for h, v in enumerate(SITE['hourProfile'][inst][str(days)]):
            hour_tot[h] += v
    else:
        for lbl, v in counter_hourly(inst).items():
            if lbl[:10] >= start:
                hour_tot[int(lbl[11:13])] += v
    hour_tot = {h: v for h, v in hour_tot.items() if v}
    return total, n_days, hour_tot


def spec_arr(arr):
    ids = [c['value'] for c in SITE['countersByArrondissement'][arr] if c['value'] in DAILY]
    per = {i: counter_daily(i) for i in ids}
    span = {i: (min(per[i]), max(per[i])) if per[i] else None for i in ids}
    dates = sorted({d for i in ids for d in per[i]})

    def expected(i, d):
        return any(shift(d, -k) in per[i] or shift(d, k) in per[i] for k in range(1, 8))

    totals, complete = {}, []
    for d in dates:
        tot, ok, n = 0, True, 0
        for i in ids:
            sp = span[i]
            if not sp or d < sp[0] or d > sp[1]:
                continue
            v = per[i].get(d)
            if v is not None:
                tot += v
            elif not expected(i, d):
                continue
            n += 1
            if v is None or ANOM.get(i, {}).get(d) or d in sp:
                ok = False
        totals[d] = tot
        if ok and n:
            complete.append(d)
    strict = len(complete) >= 14
    days = complete if strict else [d for d in dates if totals[d] > 0]
    avg = sum(totals[d] for d in days) / len(days) if days else 0
    counters = sorted(({'id': i, 'avg': sum(per[i].values()) / len(per[i]) if per[i] else 0,
                        'stopped': QUALITY.get(i, {}).get('group') == 'C'} for i in ids),
                      key=lambda c: -c['avg'])
    return {'ids': ids, 'per': per, 'totals': totals, 'days': days, 'avg': avg,
            'strict': strict, 'counters': counters}


ARR_WEATHER = [
    ('Jours secs', lambda w: w['precip'] < 1 and w['snow'] < 0.5 and w['tmax'] >= 10),
    ('Pluie', lambda w: w['precip'] >= 5 and w['snow'] < 0.5),
    ('Neige', lambda w: w['snow'] >= 1),
    ('Froid', lambda w: w['tmax'] < -10),
]


def winter_season(d):
    y, m = int(d[:4]), int(d[5:7])
    a = y if m >= 11 else y - 1
    return f"{a}–{(a + 1) % 100:02d}"


def is_winter(d):
    return int(d[5:7]) in (12, 1, 2, 3)


def spec_winter(daily_map, today):
    """Statistiques de la saison d'hiver (déc.–mars) la plus récente terminée
    (à défaut, la saison en cours)."""
    winter = {d: v for d, v in daily_map.items() if is_winter(d)}
    seasons = sorted({winter_season(d) for d in winter})
    current = winter_season(today) if is_winter(today) else None
    done = [s for s in seasons if s != current]
    target = done[-1] if done else (seasons[-1] if seasons else None)
    days = {d: v for d, v in winter.items() if winter_season(d) == target}
    cats = {k: [] for k in ('doux', 'pluie', 'froid', 'neige')}
    dow = defaultdict(list)
    dow_t = defaultdict(list)
    snow_total = snow_ridden = 0
    for d, v in days.items():
        dow[weekday(d)].append(v)
        w = WEATHER.get(d)
        if not w:
            continue
        if w['tmax'] is not None:
            dow_t[weekday(d)].append(w['tmax'])
        snow, precip, tmax = w.get('snow') or 0, w.get('precip') or 0, w['tmax']
        if snow >= 1:
            cats['neige'].append(v)
            snow_total += 1
            snow_ridden += v > 0
        elif tmax is not None and tmax <= -10:
            cats['froid'].append(v)
        elif precip > 5:
            cats['pluie'].append(v)
        else:
            cats['doux'].append(v)
    ridden = [(d, v) for d, v in days.items() if v > 0 and d in WEATHER]
    coldest = min((WEATHER[d]['tmin'] for d, _ in ridden), default=None)
    snowiest = max((WEATHER[d]['snow'] for d, _ in ridden if WEATHER[d]['snow'] > 0), default=None)
    busiest = max(days.values(), default=None)
    return {
        'season': target, 'days': days, 'total': sum(days.values()), 'n_days': len(days),
        'cats': cats, 'snow_total': snow_total, 'snow_ridden': snow_ridden,
        'dow_avg': [jround(sum(dow[i]) / len(dow[i])) if dow[i] else None for i in range(7)],
        'dow_temp': [js_fixed1(sum(dow_t[i]) / len(dow_t[i])) if dow_t[i] else None for i in range(7)],
        'coldest': coldest,
        'coldest_dates': {d for d, v in ridden if WEATHER[d]['tmin'] == coldest},
        'snowiest': snowiest,
        'snowiest_dates': {d for d, v in ridden if WEATHER[d]['snow'] == snowiest},
        'busiest': busiest,
        'busiest_dates': {d for d, v in days.items() if v == busiest},
    }


# ── Pages ────────────────────────────────────────────────────────────────────

def test_counters_page(page):
    print("\n=== Page Compteurs : statistiques de chaque compteur ===")
    before = mark()
    for inst in sorted(DAILY):
        page.evaluate("id => { document.getElementById('counterSelectDesktop').value = id; selectCounter(id); }", inst)
        for days in (7, 30, 90, 180, 365):
            page.evaluate("d => filterDataByPeriod(d)", days)
            got = page.evaluate("""() => ({
                total: document.getElementById('statTotal').dataset.value,
                avg: document.getElementById('statAvg').dataset.value,
                peak: document.getElementById('statPeak').innerText })""")
            total, n_days, hour_tot = spec_counter_stats(inst, days)
            tag = f"[{inst} · {days} j]"
            check(f"{tag} Total", num(got['total']) == total, f"affiché {got['total']}, attendu {total}")
            exp_avg = jround(total / n_days) if n_days else 0
            check(f"{tag} Moyenne par jour", num(got['avg']) == exp_avg,
                  f"affiché {got['avg']}, attendu {exp_avg} ({total} / {n_days} jours de données)")
            if hour_tot:
                best = max(hour_tot.values())
                peak_hours = nums(got['peak'].split('\n')[0])
                ok = bool(peak_hours) and hour_tot.get(int(peak_hours[0])) == best
                check(f"{tag} Heure de pointe", ok,
                      f"affiché {got['peak']!r}, maximum {best} à {[h for h, v in hour_tot.items() if v == best]}")
                avgs = [x for x in nums(got['peak'].split('\n')[-1])]
                if ok and avgs:
                    check(f"{tag} Passages/heure à la pointe", avgs[0] == jround(best / n_days),
                          f"affiché {avgs[0]}, attendu {jround(best / n_days)}")
    section("Total, moyenne et heure de pointe de chaque compteur sur 7, 30, 90, 180 et 365 jours", before)


def test_global_page(page):
    print("\n=== Vue globale ===")
    before = mark()
    page.evaluate("showView('global')")
    page.wait_for_timeout(300)
    gs = SITE['globalStats']
    shown = cards(page, '#global-cards')
    for card, key in zip(shown, ('yesterday', 'week', 'month')):
        s = gs[key]
        check(f"[{key}] Total", num(card['total']) == s['total'], f"{card['total']} ≠ {s['total']}")
        check(f"[{key}] Période", card['date'] == s['subtitle'], f"{card['date']!r} ≠ {s['subtitle']!r}")
        meta = card['meta']
        check(f"[{key}] Couverture", f"{s['coverage']} / {s['active']}" in re.sub(r'[  ]', ' ', meta),
              meta)
        for t in s['top']:
            if t['id']:
                check(f"[{key}] Top compteur {t['label']}", t['label'] in meta and
                      any(n == t['vol'] for n in nums(meta)), meta)
        if s['peak_hour'] is not None:
            check(f"[{key}] Heure de pointe", f"{s['peak_hour']}h–{s['peak_hour'] + 1}h" in meta, meta)
    check("Trois cartes (hier, 7 jours, mois)", len(shown) == 3, f"{len(shown)} cartes")

    rows = page.evaluate("""() => [...document.querySelectorAll('#global-arr .arr-row')]
        .map(r => [r.querySelector('.arr-name').innerText, r.querySelector('.arr-vol').innerText])""")
    exp = [[a['name'], a['vol']] for a in gs['arr_rank']]
    check("Classement des arrondissements", [[n, num(v)] for n, v in rows] == exp, f"{rows} ≠ {exp}")
    net = [num(x) for x in page.evaluate(
        "() => [...document.querySelectorAll('#global-net .net-val')].map(e => e.innerText)")]
    n = gs['net_status']
    check("Statut du réseau", net == [n['A'], n['B'], n['C']], f"{net} ≠ {[n['A'], n['B'], n['C']]}")
    check("Statut du réseau : total", f"{n['total']} compteurs" in page.inner_text('#global-net'))

    sol, fut = chart_data(page, '__global_yearly__', 0), chart_data(page, '__global_yearly__', 1)
    exp_years = sorted(SITE['historicalYearlyTotals'], key=int)
    check("Graphique annuel : années", sol and [str(y) for y in sol['labels']] == exp_years,
          f"{sol and sol['labels']} ≠ {exp_years}")
    check("Graphique annuel : capteurs au sol + photo/vidéo = total de l'année",
          sol and fut and [a + b for a, b in zip(sol['data'], fut['data'])] ==
          [SITE['historicalYearlyTotals'][y] for y in exp_years])
    # Part photo/vidéo : années tirées des données récentes seulement (les totaux des
    # années du portail ne comptent que les capteurs au sol)
    live = {str(y) for y in SITE.get('historicalLiveYears', [])}
    det = defaultdict(int)
    for inst, years in SITE['counterHistoricalData'].items():
        if inst.startswith('det-'):
            for y, months in years.items():
                if y in live:
                    det[y] += sum(months.values())
    check("Graphique annuel : part des capteurs photo/vidéo", fut and fut['data'] == [det[y] for y in exp_years],
          f"{fut and fut['data']} ≠ {[det[y] for y in exp_years]}")
    section("Cartes, classement, statut du réseau et évolution annuelle", before)


def test_arr_page(page):
    print("\n=== Arrondissements (chaque arrondissement) ===")
    before = mark()
    page.evaluate("showView('arr')")
    page.wait_for_timeout(500)
    names = page.evaluate("() => [...document.querySelectorAll('#arrPageSelect option')].map(o => o.value)")
    exp_names = sorted((a for a, cs in SITE['countersByArrondissement'].items() if any(c['value'] in DAILY for c in cs)))
    check("Liste des arrondissements", sorted(names) == exp_names, f"{names} ≠ {exp_names}")
    for arr in names:
        page.select_option('#arrPageSelect', arr)
        page.wait_for_timeout(150)
        s = spec_arr(arr)
        tag = f"[{arr}]"
        shown = cards(page, '#arr-cards')
        total = sum(sum(p.values()) for p in s['per'].values())
        check(f"{tag} Passages (12 mois) = somme de tous les compteurs", num(shown[0]['total']) == total,
              f"{shown[0]['total']} ≠ {total}")
        n_fut = sum(1 for i in s['ids'] if LOC[i]['type'] == 'fut')
        check(f"{tag} Nombre de compteurs",
              nums(shown[0]['meta'])[:3] == [len(s['ids']), n_fut, len(s['ids']) - n_fut], shown[0]['meta'])
        check(f"{tag} Moyenne par jour", num(shown[1]['total']) == jround(s['avg']),
              f"{shown[1]['total']} ≠ {jround(s['avg'])}")
        if s['days']:
            wk = [s['totals'][d] for d in s['days'] if weekday(d) < 5]
            we = [s['totals'][d] for d in s['days'] if weekday(d) >= 5]
            exp = [jround(sum(wk) / len(wk)) if wk else 0, jround(sum(we) / len(we)) if we else 0]
            check(f"{tag} Semaine / fin de semaine", nums(shown[1]['meta'])[:2] == exp, f"{shown[1]['meta']} ≠ {exp}")
            best = max(s['days'], key=lambda d: s['totals'][d])
            check(f"{tag} Journée record (valeur)", num(shown[2]['total']) == s['totals'][best],
                  f"{shown[2]['total']} ≠ {s['totals'][best]}")
            best_dates = [d for d in s['days'] if s['totals'][d] == s['totals'][best]]
            labels = [page.evaluate("d => arrFmtDay(d)", d) for d in best_dates]
            check(f"{tag} Journée record (date)", shown[2]['date'] in labels, f"{shown[2]['date']} ∉ {labels}")
        top = s['counters'][0]
        check(f"{tag} Compteur le plus achalandé (moyenne)", num(shown[3]['total']) == jround(top['avg']),
              f"{shown[3]['total']} ≠ {jround(top['avg'])}")
        tied = [c['id'] for c in s['counters'] if jround(c['avg']) == jround(top['avg'])]
        check(f"{tag} Compteur le plus achalandé (nom)", shown[3]['date'] in [LOC[i]['label'] for i in tied],
              shown[3]['date'])

        rows = page.evaluate("""() => [...document.querySelectorAll('#arr-counters .arr-row')]
            .map(r => r.querySelector('.arr-vol').innerText)""")
        exp_rows = ['inactif' if c['stopped'] else jround(c['avg']) for c in s['counters']]
        got_rows = ['inactif' if r == 'inactif' else num(r) for r in rows]
        check(f"{tag} Liste des compteurs (moyennes par jour)", got_rows == exp_rows, f"{got_rows} ≠ {exp_rows}")

        weather = page.evaluate("""() => [...document.querySelectorAll('#arr-weather-grid > div')].map(d => ({
            val: d.querySelector('.net-val').innerText, sub: d.querySelector('.arr-weather').innerText }))""")
        for (label, test), w in zip(ARR_WEATHER, weather):
            lst = [d for d in s['days'] if d in WEATHER and test(WEATHER[d])]
            m = sum(s['totals'][d] for d in lst) / len(lst) if lst else None
            check(f"{tag} Météo « {label} » : moyenne", (num(w['val']) == jround(m)) if m is not None else '—' in w['val'],
                  f"{w['val']} ≠ {m}")
            check(f"{tag} Météo « {label} » : nombre de jours", num(w['sub']) == len(lst), f"{w['sub']} ≠ {len(lst)}")
            if m is not None and s['avg']:
                diff = jround((m - s['avg']) / s['avg'] * 100)
                check(f"{tag} Météo « {label} » : écart à la moyenne", nums(w['sub'])[1] == diff,
                      f"{w['sub']} ≠ {diff} %")

        ranked = sorted(s['days'], key=lambda d: -s['totals'][d])
        for sel, lst, name in (('#arr-top-days', ranked[:10], 'les plus'), ('#arr-low-days', ranked[-10:][::-1], 'les moins')):
            got = [(num(r[1])) for r in table_rows(page, sel)]
            check(f"{tag} Jours {name} achalandés (valeurs)", got == [s['totals'][d] for d in lst],
                  f"{got} ≠ {[s['totals'][d] for d in lst]}")
            rows_txt = table_rows(page, sel)
            for r, d in zip(rows_txt, lst):
                tied = [x for x in s['days'] if s['totals'][x] == s['totals'][d]]
                fmt = [page.evaluate("d => arrFmtDay(d)", x) for x in tied]
                if r[0] not in fmt:
                    check(f"{tag} Jours {name} achalandés (date)", False, f"{r[0]} ∉ {fmt}")
                    break
                w = WEATHER.get(d)
                if w and s['avg']:
                    p = (s['totals'][d] - s['avg']) / s['avg'] * 100
                    exp_p = abs(jround(p))
                    check(f"{tag} {d} écart à la moyenne", abs(num(r[2])) == exp_p, f"{r[2]} ≠ {exp_p} %")
                    shown_t = nums(r[3])[:2]
                    check(f"{tag} {d} météo affichée", shown_t == [jround(w['tmax']), jround(w['tmin'])],
                          f"{r[3]} ≠ {w['tmax']} / {w['tmin']}")

        monthly = chart_data(page, '__arr_monthly__')
        by_m = defaultdict(list)
        for d in s['days']:
            by_m[d[:7]].append(s['totals'][d])
        exp_m = [sum(v) / len(v) for _, v in sorted(by_m.items())]
        check(f"{tag} Moyenne par mois (graphique)", monthly and len(monthly['data']) == len(exp_m) and
              all(close(a, b, 1e-6) for a, b in zip(monthly['data'], exp_m)), f"{monthly and monthly['data']} ≠ {exp_m}")
        note = page.inner_text('#arr-days-note')
        if s['strict']:
            check(f"{tag} Note : nombre de jours comparables", f"Classement sur {len(s['days'])} jours" in note, note)
    section(f"Cartes, liste des compteurs, météo, jours record et moyennes mensuelles — {len(names)} arrondissements",
            before)


def test_hiver_page(page, today):
    print("\n=== Vélo d'hiver ===")
    before = mark()
    page.evaluate("showView('hiver')")
    page.wait_for_timeout(500)
    glob = defaultdict(int)
    for inst in LOC:
        if inst in DAILY:
            for d, v in counter_daily(inst).items():
                glob[d] += v
    w = spec_winter(glob, today)
    shown = cards(page, '#hiver-cards')
    fmt_date = lambda d: page.evaluate(
        "d => new Date(d + 'T00:00:00').toLocaleDateString('fr-CA', { day: 'numeric', month: 'long', year: 'numeric' })", d)
    check("Saison couverte : décembre à mars", w['season'] is not None, "aucune journée d'hiver")
    check("Total de l'hiver", num(shown[0]['total']) == w['total'], f"{shown[0]['total']} ≠ {w['total']}")
    check("Nombre de jours d'hiver", num(shown[0]['meta']) == w['n_days'], f"{shown[0]['meta']} ≠ {w['n_days']}")
    check("Saison indiquée sur la carte", w['season'] and (w['season'][:4] in shown[0]['date']), shown[0]['date'])
    check("Jour le plus froid : température", close(num(shown[1]['total']), w['coldest'], 0.05),
          f"{shown[1]['total']} ≠ {w['coldest']}")
    check("Jour le plus froid : date", shown[1]['date'] in [fmt_date(d) for d in w['coldest_dates']], shown[1]['date'])
    check("Jour le plus froid : étiqueté « min »", 'MIN' in shown[1]['total'].upper(), shown[1]['total'])
    maxes = {WEATHER[d]['tmax'] for d in w['coldest_dates']}
    sub = page.evaluate("() => document.querySelectorAll('#hiver-cards .global-card-sub')[1].innerText")
    check("Jour le plus froid : maximum de la journée entre parenthèses",
          any(close(num(sub), m, 0.05) for m in maxes) and 'MAX' in sub.upper(), f"{sub} ∉ {maxes}")
    cold_v = {w['days'][d] for d in w['coldest_dates']}
    check("Jour le plus froid : passages", num(shown[1]['meta']) in cold_v, f"{shown[1]['meta']} ∉ {cold_v}")
    check("Record hivernal : valeur", num(shown[2]['total']) == w['busiest'], f"{shown[2]['total']} ≠ {w['busiest']}")
    check("Record hivernal : date", shown[2]['date'] in [fmt_date(d) for d in w['busiest_dates']], shown[2]['date'])
    pct = jround(w['snow_ridden'] / w['snow_total'] * 100) if w['snow_total'] else 0
    check("Jours de neige pédalés (%)", num(shown[3]['total']) == pct, f"{shown[3]['total']} ≠ {pct}")
    check("Jours de neige pédalés (n / total)", nums(shown[3]['meta'])[:2] == [w['snow_ridden'], w['snow_total']],
          f"{shown[3]['meta']} ≠ {w['snow_ridden']} / {w['snow_total']}")

    # Saisons : déc. (année précédente) + janv.–mars depuis l'historique ; la saison des
    # cartes depuis les données quotidiennes (tous les compteurs, décembre compris)
    trend = {'boucle': defaultdict(int), 'fut': defaultdict(int)}
    for inst, years in SITE['counterHistoricalData'].items():
        for y, months in years.items():
            for m, v in months.items():
                s = winter_season(f"{y}-{int(m):02d}-01")
                if int(m) in (12, 1, 2, 3) and s != w['season']:
                    trend['fut' if inst.startswith('det-') else 'boucle'][s] += v
    for inst in LOC:
        if inst in DAILY:
            for d, v in counter_daily(inst).items():
                if is_winter(d) and winter_season(d) == w['season']:
                    trend['fut' if inst.startswith('det-') else 'boucle'][w['season']] += v
    sol, fut = chart_data(page, '__hiver_trend__', 0), chart_data(page, '__hiver_trend__', 1)
    exp_seasons = sorted(set(trend['boucle']) | set(trend['fut']))
    check("Graphique des saisons : saisons", sol and sol['labels'] == exp_seasons, f"{sol and sol['labels']}")
    check("Graphique des saisons : capteurs au sol", sol and sol['data'] == [trend['boucle'][s] for s in exp_seasons],
          f"{sol and sol['data']}")
    check("Graphique des saisons : capteurs photo/vidéo", fut and fut['data'] == [trend['fut'][s] for s in exp_seasons],
          f"{fut and fut['data']}")
    if sol and fut and w['season'] in exp_seasons:
        i = exp_seasons.index(w['season'])
        check("Saison des cartes = même saison dans le graphique", sol['data'][i] + fut['data'][i] == w['total'],
              f"carte {w['total']:,}, graphique {sol['data'][i] + fut['data'][i]:,} pour {w['season']}")

    # Explorer un compteur l'hiver : moyenne combinée puis chaque compteur
    options = page.evaluate("() => [...document.querySelectorAll('#hiverCounterSelect option')].map(o => o.value)")
    for inst in options:
        page.select_option('#hiverCounterSelect', inst)
        page.wait_for_timeout(60)
        dm = glob if inst == '__all__' else counter_daily(inst)
        ws = spec_winter(dm, today)
        tag = f"[hiver {inst}]"
        dow = chart_data(page, '__hiver_dow_passages__')
        check(f"{tag} Passages moyens par jour de semaine", dow and dow['data'] == ws['dow_avg'],
              f"{dow and dow['data']} ≠ {ws['dow_avg']}")
        tmp = chart_data(page, '__hiver_dow_temp__')
        check(f"{tag} Température max. moyenne par jour", tmp and all(
            (a is None and b is None) or close(a, b, 1e-9) for a, b in zip(tmp['data'], ws['dow_temp'])),
              f"{tmp and tmp['data']} ≠ {ws['dow_temp']}")
        brk = page.evaluate("""() => [...document.querySelectorAll('#hiver-weather-breakdown > div')]
            .map(d => [d.querySelector('.net-val').innerText, d.lastElementChild.innerText])""")
        for (val, cnt), key in zip(brk, ('doux', 'pluie', 'froid', 'neige')):
            lst = ws['cats'][key]
            exp = jround(sum(lst) / len(lst)) if lst else None
            check(f"{tag} Type de journée « {key} »", (num(val) == exp if exp is not None else '—' in val)
                  and num(cnt) == len(lst), f"{val} ({cnt}) ≠ {exp} ({len(lst)} jours)")
        rec = page.inner_text('#hiver-records')
        if ws['busiest']:
            check(f"{tag} Fait marquant : record", ws['busiest'] in nums(rec), rec)
        if ws['coldest'] is not None:
            check(f"{tag} Fait marquant : jour le plus froid", any(close(x, ws['coldest'], 0.05) for x in nums(rec)), rec)
            check(f"{tag} Fait marquant : maximum du jour le plus froid",
                  any(close(x, WEATHER[d]['tmax'], 0.05) for d in ws['coldest_dates'] for x in nums(rec)), rec)
        if ws['snowiest'] is not None:
            check(f"{tag} Fait marquant : jour le plus enneigé", any(close(x, ws['snowiest'], 0.05) for x in nums(rec)), rec)
    page.select_option('#hiverCounterSelect', '__all__')

    # BIXI l'hiver
    seasons = defaultdict(int)
    months = defaultdict(set)
    for y, s in SITE['bixiYearly'].items():
        for d, n in s['daily'].items():
            if is_winter(d):
                seasons[winter_season(d)] += n
    if seasons:
        top = max(seasons.values())
        served = {k for k, v in seasons.items() if v >= top * 0.01}
        for y, s in SITE['bixiYearly'].items():
            for d in s['daily']:
                if is_winter(d) and winter_season(d) in served:
                    months[d[:7]].add(d)
        last = max(seasons)
        bc = cards(page, '#hiver-bixi-cards')
        check("BIXI l'hiver : dernière saison", last in bc[0]['title'] and num(bc[0]['total']) == seasons[last],
              f"{bc[0]['title']} {bc[0]['total']} ≠ {last} {seasons[last]}")
        avg = sum(seasons[k] for k in served) / len(months)
        check("BIXI l'hiver : moyenne des mois hivernaux", num(bc[1]['total']) == jround(avg),
              f"{bc[1]['total']} ≠ {jround(avg)} ({sum(seasons[k] for k in served)} trajets / {len(months)} mois)")
    section(f"Cartes, saisons, BIXI l'hiver et explorateur ({len(options)} choix)", before)


def test_bixi_page(page):
    print("\n=== BIXI ===")
    before = mark()
    page.evaluate("showView('bixi')")
    page.wait_for_timeout(500)
    for scope in ('all', 'mtl'):
        page.click(f'#bixi-scope button[data-scope="{scope}"]')
        page.wait_for_timeout(400)
        data = {}
        for y, s in SITE['bixiYearly'].items():
            d = s.get('mtl') if scope == 'mtl' else s
            if d and d.get('trips'):
                data[y] = {**d, 'partial': s.get('partial')}
        years = sorted(data)
        tag = f"[{'réseau' if scope == 'all' else 'Montréal'}]"
        for y, d in data.items():
            check(f"{tag} {y} : trajets = somme des jours", d['trips'] == sum(d['daily'].values()),
                  f"{d['trips']} ≠ {sum(d['daily'].values())}")
        complete = [y for y in years if not data[y]['partial']]
        cur = [y for y in years if data[y]['partial']]
        shown = cards(page, '#bixi-cards')
        i = 0

        def pct(a, b):
            return (a - b) / b * 100

        if complete:
            y = complete[-1]
            check(f"{tag} Carte « Année {y} »", y in shown[i]['title'] and num(shown[i]['total']) == data[y]['trips'],
                  f"{shown[i]['title']} {shown[i]['total']}")
            if str(int(y) - 1) in data:
                p = pct(data[y]['trips'], data[str(int(y) - 1)]['trips'])
                check(f"{tag} Croissance {y}", close(abs(num(shown[i]['meta'])), abs(p), 0.051),
                      f"{shown[i]['meta']} ≠ {p:.1f} %")
            i += 1
        if cur:
            y = cur[-1]
            last = max(data[y]['daily'])
            check(f"{tag} Carte « {y} à ce jour »", num(shown[i]['total']) == data[y]['trips'], shown[i]['total'])
            prev = data.get(str(int(y) - 1))
            if prev:
                ytd = sum(n for dd, n in prev['daily'].items() if dd[5:] <= last[5:])
                p = pct(data[y]['trips'], ytd)
                check(f"{tag} {y} vs même période l'an dernier", close(abs(num(shown[i]['meta'])), abs(p), 0.051),
                      f"{shown[i]['meta']} ≠ {p:.1f} % ({data[y]['trips']} vs {ytd} au {last[5:]})")
            i += 1
        best = max(((dd, n) for y in years for dd, n in data[y]['daily'].items()), key=lambda x: x[1])
        check(f"{tag} Record en une journée", num(shown[i]['total']) == best[1], f"{shown[i]['total']} ≠ {best}")
        i += 1
        if len(complete) >= 2:
            g = data[complete[-1]]['trips'] / data[complete[0]]['trips']
            check(f"{tag} Croissance depuis {complete[0]}", close(num(shown[i]['total']), g, 0.051),
                  f"{shown[i]['total']} ≠ ×{g:.1f}")

        rows = table_rows(page, '#bixi-annual-table')
        check(f"{tag} Tableau annuel : une ligne par année", len(rows) == len(years), f"{len(rows)} ≠ {len(years)}")
        for r, y in zip(rows, years[::-1]):
            d = data[y]
            monthly = [0] * 12
            for dd, n in d['daily'].items():
                monthly[int(dd[5:7]) - 1] += n
            ok = (num(r[0]) == int(y) and num(r[1]) == d['trips']
                  and num(r[3]) == jround(d['trips'] / len(d['daily']))
                  and num(r[6]) == d['stations'])
            check(f"{tag} Tableau annuel {y} : trajets, moyenne/jour, stations", ok, r)
            mois = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sep", "Oct", "Nov", "Déc"]
            check(f"{tag} Tableau annuel {y} : mois record", r[7] == mois[monthly.index(max(monthly))], r)
            prev = data.get(str(int(y) - 1))
            if prev:
                ref = (sum(n for dd, n in prev['daily'].items() if dd[5:] <= max(d['daily'])[5:])
                       if d['partial'] else prev['trips'])
                p = pct(d['trips'], ref)
                check(f"{tag} Tableau annuel {y} : croissance", close(abs(num(r[2])), abs(p), 0.051),
                      f"{r[2]} ≠ {p:.1f} %")
            if d.get('member_share') is not None:
                check(f"{tag} Tableau annuel {y} : membres", num(r[5]) == jround(d['member_share'] * 100), r)

        for y in years:
            page.select_option('#bixiYearSelect', y)
            page.wait_for_timeout(80)
            d = data[y]
            prev = data.get(str(int(y) - 1))
            monthly = defaultdict(list)
            for dd, n in d['daily'].items():
                monthly[int(dd[5:7])].append((dd, n))
            got = {r[0].rstrip('*'): r for r in table_rows(page, '#bixi-monthly-table')}
            mois = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sep", "Oct", "Nov", "Déc"]
            for m, lst in sorted(monthly.items()):
                r = got.get(mois[m - 1])
                if r is None:
                    continue
                tot = sum(n for _, n in lst)
                ok = num(r[1]) == tot and num(r[3]) == jround(tot / len(lst))
                bd, bn = max(lst, key=lambda x: x[1])
                ok = ok and nums(r[4])[-1] == bn
                check(f"{tag} {y} {mois[m - 1]} : trajets, moyenne/jour, jour record", ok, f"{r} (attendu {tot}, record {bn})")
            hourly = chart_data(page, '__bixi_hour__')
            check(f"{tag} {y} : trajets par heure", hourly and hourly['data'] == d['hourly'])
            check(f"{tag} {y} : heures = total des trajets", sum(d['hourly']) == d['trips'],
                  f"{sum(d['hourly'])} ≠ {d['trips']}")
            stations = page.evaluate("""() => [...document.querySelectorAll('#bixi-top-stations .arr-row')]
                .map(r => [r.querySelector('.arr-name').innerText, r.querySelector('.arr-vol').innerText])""")
            check(f"{tag} {y} : stations les plus populaires",
                  [[n, num(v)] for n, v in stations] == [[n, v] for n, v in d['top_stations']])
    page.click('#bixi-scope button[data-scope="all"]')
    section("Cartes, tableau annuel et détail de chaque année (réseau et Montréal)", before)


def main():
    server, base = start_server()
    cdn_dir = os.environ.get('CDN_DIR')
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or None)
        page = open_page(browser, base, {'width': 1366, 'height': 900}, cdn_dir, errors)
        # Les compteurs animés affichent la valeur finale immédiatement pour la lecture
        page.evaluate("""() => { window.animateCount = (el, target) => {
            if (!el) return; el.dataset.value = target; el.textContent = target.toLocaleString('fr-CA'); }; }""")
        today = page.evaluate("() => { const d = new Date(); return d.getFullYear() + '-' + "
                              "String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }")
        test_counters_page(page)
        test_global_page(page)
        test_arr_page(page)
        test_hiver_page(page, today)
        test_bixi_page(page)
        check("Aucune erreur JavaScript", not errors, errors[:3])
        browser.close()
    server.shutdown()

    print("\n" + "=" * 50)
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
    print(f"\033[92mLes {n_checks} chiffres vérifiés correspondent aux données publiées.\033[0m")


if __name__ == '__main__':
    main()
