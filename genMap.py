import csv
import re
from collections import defaultdict
import os
from tqdm import tqdm
import json
from datetime import datetime, timedelta, timezone
import math
from statistics import mean, stdev, median

# Compter le nombre total de lignes pour la barre de progression
total_lines = sum(1 for line in open('cyclistes.csv', encoding='utf-8')) - 1  # Soustraire la ligne d'en-tête

# Lire le fichier CSV et grouper par instance puis par direction
data = defaultdict(lambda: defaultdict(list))
with open('cyclistes.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in tqdm(reader, total=total_lines, desc="Traitement des données"):
        if row['agg_code'] == 'h':
            data[row['instance']][row['direction']].append(row)

# Filtrer les données pour garder les 6 derniers mois
def is_within_last_6_months(date_str):
    try:
        clean = re.sub(r'[+-]\d{2}$', '', date_str.strip('"').strip())
        row_date = datetime.fromisoformat(clean)
        cutoff_date = datetime.now() - timedelta(days=180)
        return row_date >= cutoff_date
    except:
        return False

for instance in data.keys():
    for direction in data[instance].keys():
        data[instance][direction] = [row for row in data[instance][direction] if is_within_last_6_months(row['periode'])]

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

# Détecter les jours avec un volume anormalement bas (dysfonctionnement probable du compteur)
def detect_anomalies(instance_data, min_ref_days=4, z_threshold=2.5, min_hours=4,
                     ratio_threshold=0.5, adj_ratio_threshold=0.2, adj_window=6,
                     min_expected_total=50):
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
            mu_ref = mu_adj if flagged_adj else mu_dow
            expected_total = round(mu_ref * typical_hours)
            if expected_total < min_expected_total:
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
        end_iter = datetime.fromisoformat(last_date)
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
                        if expected >= min_expected_total:
                            anomalies[d_str] = {"total": 0, "expected": expected, "z_score": -99.0}
                except:
                    pass
            d_iter += timedelta(days=1)

    return anomalies

anomaly_data = {inst: det for inst, dirs in data.items()
                if (det := detect_anomalies(dirs))}

# Générer le HTML
html_parts = ['''<html>
<head>
    <meta charset="utf-8">
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-YGPDF0GH27"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', 'G-YGPDF0GH27');
    </script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <title>Compteurs Vélo Montréal</title>
    <link rel="icon" type="image/png" href="favico.png">
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(160deg, #6dcf96 0%, #2a9455 45%, #0d4f2a 100%);
            color: #333;
            margin: 0;
            padding: 0 12px 12px;
            min-height: 100vh;
        }
        @keyframes fadeDown {
            from { opacity: 0; transform: translateY(-18px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes fadeUp {
            from { opacity: 0; transform: translateY(18px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes fadeIn {
            from { opacity: 0; }
            to   { opacity: 1; }
        }
        .site-header {
            text-align: center;
            padding: 28px 16px 22px;
            max-width: 1200px;
            margin: 0 auto;
            animation: fadeDown 0.55s cubic-bezier(.22,.68,0,1.2) both;
        }
        h1 {
            color: #fff;
            font-size: 26px;
            font-weight: 700;
            letter-spacing: -0.5px;
            margin: 0 0 8px 0;
        }
        .subtitle {
            color: rgba(255,255,255,0.72);
            font-size: 13px;
            margin: 0;
            font-weight: 400;
            line-height: 1.5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: rgba(255,255,255,0.97);
            border-radius: 12px;
            padding: 16px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.15);
            border-top: 3px solid #1DB860;
            animation: fadeUp 0.5s 0.1s cubic-bezier(.22,.68,0,1.2) both;
        }
        .select-wrapper {
            position: relative;
            margin-bottom: 18px;
        }
        .select-wrapper::after {
            content: '';
            position: absolute;
            right: 16px;
            top: 50%;
            transform: translateY(-50%);
            width: 0; height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid #1DB860;
            pointer-events: none;
        }
        select {
            width: 100%;
            padding: 13px 40px 13px 14px;
            border: 1.5px solid rgba(29,184,96,0.25);
            border-radius: 8px;
            background: #fff;
            font-size: 15px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.06);
            -webkit-appearance: none;
            -moz-appearance: none;
            appearance: none;
            cursor: pointer;
            transition: border-color 0.2s, box-shadow 0.2s;
            outline: none;
        }
        select:focus {
            border-color: #1DB860;
            box-shadow: 0 0 0 3px rgba(29,184,96,0.15);
        }
        .stats-row {
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            margin-bottom: 18px;
        }
        .stat-card {
            background: linear-gradient(135deg, rgba(29,184,96,0.07), rgba(29,184,96,0.02));
            border: 1.5px solid rgba(29,184,96,0.18);
            border-radius: 10px;
            padding: 12px 8px;
            text-align: center;
            transition: transform 0.2s, border-color 0.2s, box-shadow 0.2s;
        }
        .stat-card:hover {
            transform: translateY(-2px);
            border-color: rgba(29,184,96,0.4);
            box-shadow: 0 4px 12px rgba(29,184,96,0.12);
        }
        .stat-value {
            font-size: 20px;
            font-weight: 700;
            color: #1DB860;
            line-height: 1.2;
        }
        .stat-label {
            font-size: 10px;
            color: #888;
            margin-top: 3px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            font-weight: 500;
        }
        .day-label {
            text-align: center;
            font-size: 13px;
            color: #1a9950;
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
            color: #1a9950;
            margin: 16px 0 12px 0;
            font-size: 18px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        h2::before {
            content: '';
            display: inline-block;
            width: 4px;
            height: 18px;
            background: linear-gradient(to bottom, #1DB860, #17a355);
            border-radius: 2px;
            flex-shrink: 0;
        }
        canvas {
            max-width: 100%;
            height: 280px;
            margin-bottom: 16px;
        }
        p { margin: 8px 0; font-weight: normal; font-size: 14px; }
        p strong { color: #1DB860; }
        .watermark {
            text-align: center;
            padding: 16px 10px;
            color: rgba(29,184,96,0.4);
            font-size: 12px;
            margin-top: 30px;
            border-top: 1px solid rgba(0,0,0,0.07);
        }
        .watermark a { color: rgba(29,184,96,0.6); text-decoration: none; transition: color 0.2s; }
        .watermark a:hover { color: #1DB860; }
        .period-buttons {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-bottom: 10px;
        }
        #datepickerWrapper {
            text-align: center;
            margin-bottom: 18px;
            display: none;
        }
        #specificDatePicker {
            padding: 10px 16px;
            border: 1.5px solid #1DB860;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            color: #1DB860;
            background: #fff;
            cursor: pointer;
            outline: none;
            box-shadow: 0 3px 10px rgba(29,184,96,0.2);
            transition: border-color 0.2s, box-shadow 0.2s;
        }
        #specificDatePicker:focus {
            box-shadow: 0 0 0 3px rgba(29,184,96,0.15);
        }
        .period-btn {
            padding: 11px 12px;
            border: 1.5px solid rgba(29,184,96,0.35);
            background: #fff;
            color: #1DB860;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            transition: all 0.2s ease;
            text-align: center;
            position: relative;
            overflow: hidden;
        }
        .period-btn::before {
            content: '';
            position: absolute;
            inset: 0;
            background: radial-gradient(circle, rgba(29,184,96,0.15) 0%, transparent 70%);
            opacity: 0;
            transition: opacity 0.3s;
        }
        .period-btn:hover::before { opacity: 1; }
        .period-btn:hover {
            border-color: #1DB860;
            transform: translateY(-1px);
            box-shadow: 0 3px 8px rgba(29,184,96,0.2);
        }
        .period-btn:active { transform: scale(0.97); }
        .period-btn.active {
            background: linear-gradient(135deg, #1DB860, #17a355);
            color: #fff;
            border-color: transparent;
            box-shadow: 0 3px 10px rgba(29,184,96,0.35);
        }
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
        }
        #map {
            width: 100%;
            height: 100%;
            border-radius: 10px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.12);
            border: 1.5px solid rgba(29,184,96,0.18);
        }
        #cyclosm-btn {
            position: absolute;
            top: 10px;
            right: 10px;
            z-index: 1000;
            background: #fff;
            border: 1.5px solid #ccc;
            border-radius: 6px;
            padding: 5px 10px;
            font-size: 12px;
            cursor: pointer;
            box-shadow: 0 2px 6px rgba(0,0,0,0.15);
            transition: background 0.2s, border-color 0.2s;
        }
        #cyclosm-btn.active {
            background: #1DB860;
            color: #fff;
            border-color: #1DB860;
        }
        @media (min-width: 768px) {
            #chart-map-layout { display: grid; grid-template-columns: 1fr 320px; gap: 20px; }
            #map-wrapper { height: 100%; min-height: 300px; }
            #map { height: 100%; }
        }
        .dir-toggle {
            display: flex;
            gap: 6px;
            margin-bottom: 14px;
            justify-content: flex-end;
        }
        .dir-btn {
            padding: 5px 12px;
            border: 1.5px solid rgba(29,184,96,0.35);
            background: #fff;
            color: #1DB860;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
            transition: all 0.2s;
        }
        .dir-btn.active {
            background: linear-gradient(135deg, #1DB860, #17a355);
            color: #fff;
            border-color: transparent;
        }
        .bixi-btn {
            padding: 5px 12px;
            border: 1.5px solid rgba(220,38,38,0.35);
            background: #fff;
            color: rgba(220,38,38,0.75);
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
            transition: all 0.2s;
        }
        .bixi-btn.active {
            background: linear-gradient(135deg, rgba(220,38,38,0.8), rgba(185,28,28,0.9));
            color: #fff;
            border-color: transparent;
        }
        #dataWarning {
            display: none;
            align-items: flex-start;
            gap: 10px;
            padding: 10px 14px;
            background: rgba(245,158,11,0.08);
            border: 1.5px solid rgba(245,158,11,0.35);
            border-radius: 8px;
            color: #92620a;
            font-size: 13px;
            line-height: 1.5;
            margin-bottom: 14px;
        }
        #dataWarning .warn-icon { font-size: 16px; flex-shrink: 0; margin-top: 1px; }
        #noDataMsg {
            display: none;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 10px;
            padding: 48px 24px;
            border: 2px dashed rgba(29,184,96,0.25);
            border-radius: 10px;
            color: #aaa;
            font-size: 15px;
            font-weight: 500;
            text-align: center;
            margin-bottom: 16px;
        }
        #noDataMsg span.icon { font-size: 36px; }
        #anomalyWarning {
            display: none;
            align-items: flex-start;
            gap: 10px;
            padding: 10px 14px;
            background: rgba(245,158,11,0.08);
            border: 1.5px solid rgba(245,158,11,0.35);
            border-radius: 8px;
            color: #92620a;
            font-size: 13px;
            line-height: 1.5;
            margin-bottom: 14px;
        }
        #anomalyWarning .warn-icon { font-size: 16px; flex-shrink: 0; margin-top: 1px; }
        #anomalyWarning .anomaly-body { flex: 1; }
        .bixi-exceeds-badge {
            display: inline-block;
            background: rgba(220,38,38,0.10);
            color: #b91c1c;
            border: 1px solid rgba(220,38,38,0.30);
            border-radius: 4px;
            padding: 0px 5px;
            font-size: 11px;
            font-weight: 600;
            margin-left: 5px;
            vertical-align: middle;
        }
        .anomaly-info-btn {
            cursor: help;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 18px;
            height: 18px;
            border-radius: 50%;
            background: rgba(245,158,11,0.2);
            color: #92620a;
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
            background: rgba(20,10,10,0.93);
            color: #fff;
            font-size: 12px;
            font-weight: 400;
            line-height: 1.55;
            padding: 10px 13px;
            border-radius: 8px;
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
            h1 { font-size: 32px; }
            .subtitle { font-size: 14px; }
            .container { padding: 24px; }
            select { font-size: 16px; }
            h2 { font-size: 20px; margin: 20px 0 12px 0; }
            h2::before { height: 20px; }
            canvas { height: 300px; margin-bottom: 20px; }
            p { font-size: 15px; margin: 10px 0; font-weight: bold; }
            .watermark { padding: 20px 10px; font-size: 14px; margin-top: 40px; }
            .period-buttons {
                display: flex; flex-wrap: wrap; gap: 10px;
                justify-content: center; margin-bottom: 20px;
                grid-template-columns: unset;
            }
            .period-btn { padding: 10px 20px; font-size: 14px; flex: 0 1 auto; }
            .stats-row { gap: 14px; }
            .stat-value { font-size: 24px; }
            .stat-label { font-size: 11px; }
        }
        /* ── Thème REV ─────────────────────────────────────────────────────── */
        body, .container, .period-btn, .dir-btn, select, .stat-card,
        .stat-value, h2, .watermark, #specificDatePicker {
            transition: background 0.35s ease, background-color 0.35s ease,
                        border-color 0.35s ease, color 0.35s ease,
                        box-shadow 0.35s ease;
        }
        body.rev-mode {
            background: linear-gradient(160deg, #1a8fd1 0%, #003d7a 100%);
        }
        body.rev-mode .container          { border-top-color: #0072BC; }
        body.rev-mode .select-wrapper::after { border-top-color: #0072BC; }
        body.rev-mode select              { border-color: rgba(0,114,188,0.25); }
        body.rev-mode select:focus        { border-color: #0072BC; box-shadow: 0 0 0 3px rgba(0,114,188,0.15); }
        body.rev-mode .stat-card          { background: linear-gradient(135deg, rgba(0,114,188,0.07), rgba(0,114,188,0.02)); border-color: rgba(0,114,188,0.18); }
        body.rev-mode .stat-card:hover    { border-color: rgba(0,114,188,0.4); box-shadow: 0 4px 12px rgba(0,114,188,0.12); }
        body.rev-mode .stat-value         { color: #0072BC; }
        body.rev-mode .day-label          { color: #005a96; }
        body.rev-mode h2                  { color: #004f85; }
        body.rev-mode h2::before          { background: linear-gradient(to bottom, #0072BC, #005a96); }
        body.rev-mode .period-btn         { border-color: rgba(0,114,188,0.35); color: #0072BC; }
        body.rev-mode .period-btn::before { background: radial-gradient(circle, rgba(0,114,188,0.15) 0%, transparent 70%); }
        body.rev-mode .period-btn:hover   { border-color: #0072BC; box-shadow: 0 3px 8px rgba(0,114,188,0.2); }
        body.rev-mode .period-btn.active  { background: linear-gradient(135deg, #0072BC, #005a96); box-shadow: 0 3px 10px rgba(0,114,188,0.35); color: #fff; }
        body.rev-mode #specificDatePicker { border-color: #0072BC; color: #0072BC; box-shadow: 0 3px 10px rgba(0,114,188,0.2); }
        body.rev-mode #specificDatePicker:focus { box-shadow: 0 0 0 3px rgba(0,114,188,0.15); }
        body.rev-mode .dir-btn            { border-color: rgba(0,114,188,0.35); color: #0072BC; }
        body.rev-mode .dir-btn.active     { background: linear-gradient(135deg, #0072BC, #005a96); color: #fff; }
        body.rev-mode .watermark          { color: rgba(0,114,188,0.4); }
        body.rev-mode .watermark a        { color: rgba(0,114,188,0.6); }
        body.rev-mode .watermark a:hover  { color: #0072BC; }
        body.rev-mode #noDataMsg          { border-color: rgba(0,114,188,0.25); }
        body.rev-mode p strong            { color: #0072BC; }
        #themeToggleBtn:hover             { transform: scale(1.07); }
        #themeToggleBtn:active            { transform: scale(0.96); }
        @keyframes spinIcon {
            from { transform: rotate(0deg) scale(1); }
            50%  { transform: rotate(180deg) scale(1.12); }
            to   { transform: rotate(360deg) scale(1); }
        }
        #themeToggleBtn.spinning {
            animation: spinIcon 0.55s cubic-bezier(0.42, 0, 0.58, 1) forwards;
            pointer-events: none;
        }
        /* ── Top bar ── */
        #topbar {
            height: 50px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 16px 0 20px;
            background: #fff;
            border-bottom: 1px solid #e5e7eb;
            margin: 0 -12px;
            z-index: 900;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }
        #topbar-nav { display: flex; align-items: center; gap: 4px; }
        .nav-link {
            text-decoration: none;
            font-size: 13px;
            font-weight: 500;
            color: #4b5563;
            padding: 6px 12px;
            border-radius: 8px;
            transition: background 0.25s, color 0.25s;
        }
        .nav-link:hover { background: #f1f5f9; color: #111827; }
        .nav-link.active { background: #e8faf0; color: #1DB860; }
        body.rev-mode .nav-link.active { background: #e8f2fc; color: #0072BC; }
        #topbar-profile {
            display: flex;
            align-items: center;
            gap: 8px;
            text-decoration: none;
            color: #4b5563;
            transition: color 0.25s;
        }
        #topbar-profile:hover { color: #111827; }
        #topbar-profile:hover img { box-shadow: 0 0 0 2px #1DB860; }
        body.rev-mode #topbar-profile:hover img { box-shadow: 0 0 0 2px #0072BC; }
        #topbar-profile-name { font-size: 13px; font-weight: 500; }
        #topbar-profile img {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            object-fit: cover;
            display: block;
            flex-shrink: 0;
            box-shadow: 0 0 0 2px #e5e7eb;
            transition: box-shadow 0.25s;
        }
        @media (min-width: 768px) {
            #topbar { margin: 0 -20px; }
        }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
</head>
<body>
    <nav id="topbar">
        <div id="topbar-nav">
            <a href="https://pistes.gabfortin.com" class="nav-link">Pistes</a>
            <a href="/" class="nav-link active">Compteurs</a>
        </div>
        <a href="https://gabfortin.com" id="topbar-profile" title="gabfortin.com">
            <span id="topbar-profile-name">gabfortin.com</span>
            <img src="img/Gabriel%20Fortin.png" alt="Gabriel Fortin">
        </a>
    </nav>
    <div class="site-header">
        <img src="favico.png" alt="Logo" id="themeToggleBtn" title="Basculer vers le thème REV" style="width:72px;height:72px;border-radius:18px;margin-bottom:12px;box-shadow:0 4px 16px rgba(0,0,0,0.2);cursor:pointer;transition:transform 0.15s ease;">
        <h1>Compteurs Vélo Montréal</h1>
        <p class="subtitle">Données de passage de cyclistes à Montréal, tirées du portail de <a href="https://donnees.montreal.ca/dataset/cyclistes" target="_blank" style="color:rgba(255,255,255,0.9);text-decoration:underline;">données ouvertes de la Ville</a>. Validation croisée avec les <a href="https://bixi.com/en/open-data/" target="_blank" style="color:rgba(255,255,255,0.9);text-decoration:underline;">données ouvertes BIXI</a>.</p>
    </div>
    <div class="container">
        <div class="period-buttons">
            <button class="period-btn" data-days="0">Jour spécifique</button>
            <button class="period-btn active" data-days="7">7 derniers jours</button>
            <button class="period-btn" data-days="30">Dernier mois</button>
            <button class="period-btn" data-days="90">3 derniers mois</button>
            <button class="period-btn" data-days="180">6 derniers mois</button>
            <button class="period-btn" data-days="-1">Tout</button>
        </div>
        <div id="datepickerWrapper"><input type="date" id="specificDatePicker"></div>
        <div id="dayLabel" class="day-label" style="display:none"></div>
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
    if len(directions) > 1:
        return f"{row['rue_1']} & {row['rue_2']} ({instance})"
    return f"{row['rue_1']} & {row['rue_2']} — {directions[0]} ({instance})"

for arrondissement in sorted(by_arrondissement.keys()):
    html_parts.append(f'<optgroup label="{arrondissement}">')
    for instance, row in sorted(by_arrondissement[arrondissement], key=lambda x: (x[1]['rue_1'], x[1]['rue_2'])):
        label = counter_label(instance, row)
        html_parts.append(f'<option value="{instance}">{label}</option>')
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
                <div class="stat-label">Heure de pointe</div>
            </div>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;min-height:30px;">
            <div id="viewToggle" style="display:none;flex-direction:row;gap:6px;">
                <button class="dir-btn active" id="btnTimeline">Dans le temps</button>
                <button class="dir-btn" id="btnDaily">Par jour</button>
            </div>
            <div style="display:flex;gap:6px;margin-left:auto;align-items:center;">
                <div id="bixiToggle" style="display:none;">
                    <button class="bixi-btn active" id="btnBixi">Bixi</button>
                </div>
                <div id="dirToggle" style="display:none;flex-direction:row;gap:6px;">
                    <button class="dir-btn" id="btnSeparate">Par direction</button>
                    <button class="dir-btn active" id="btnCombined">Combiné</button>
                </div>
            </div>
        </div>
        <div id="chart-map-layout">
        <div id="chart-area">
        <div id="dataWarning"><span class="warn-icon">⚠️</span><span>Des interruptions ont été détectées dans les données de ce compteur. Certaines périodes peuvent être sous-estimées — interpréter les chiffres avec prudence.</span></div>
        <div id="anomalyWarning">
            <span class="warn-icon">⚠️</span>
            <div class="anomaly-body">
                <strong>Données potentiellement erronées</strong> — volumes anormalement bas détectés :<br>
                <div id="anomalyDetails" style="margin-top:3px;font-size:12px;line-height:1.7;"></div>
            </div>
            <span class="anomaly-info-btn" tabindex="0" data-tooltip="Deux méthodes complémentaires : (1) Z-score par jour de semaine — le taux horaire du jour est comparé à la moyenne (μ) et l'écart-type (σ) des autres mêmes jours de semaine ; signalé si taux &lt; μ−2,5σ ET &lt; 50 % de μ. (2) Jours adjacents — signalé si le taux est &lt; 20 % de la moyenne des jours complets dans ±6 jours (CV &lt; 0,6). Les jours sans aucune donnée dans la plage active du compteur sont aussi signalés (0 passages = données manquantes).">ℹ</span>
        </div>
        <div id="noDataMsg"><span class="icon">🚴</span>Aucune donnée disponible pour cette période.</div>
''')

for instance, directions in tqdm(data.items(), desc="Génération HTML"):
    row = first_row_for(instance)
    if row:
        location = f"{row['arrondissement']} - {row['rue_1']} & {row['rue_2']}"
        html_parts.append(f'<div id="{instance}" class="table-container">')
        html_parts.append(f'<h2>Compteur {instance}</h2>')
        html_parts.append(f'<p><strong>Emplacement:</strong> {location}</p>')
        html_parts.append(f'<canvas id="chart-{instance}"></canvas>')
        html_parts.append('</div>')

html_parts.append('''
        </div>
        <div id="map-wrapper">
            <div id="map"></div>
            <button id="cyclosm-btn" title="Afficher les pistes cyclables">🚲 Pistes cyclables</button>
        </div>
        </div>
    </div>
    <script>
        const allChartData = {};
        const markers = {};
        let map;
        const chartData = {};
        const charts = {};
        const countersByArrondissement = {};
        let specificDate = null;
''')

DIRECTION_COLORS = ['#1DB860', '#29ABE2']
DIRECTION_FILLS  = ['rgba(29,184,96,0.15)', 'rgba(41,171,226,0.15)']

# Ajouter les données des graphiques et le mapping arrondissement → compteurs
for instance, directions in data.items():
    if not first_row_for(instance):
        continue
    all_dates = sorted(set(row['periode'][:16] for rows in directions.values() for row in rows))
    datasets = []
    for i, direction in enumerate(sorted(directions.keys())):
        rows = directions[direction]
        if not rows:
            continue
        date_vol = {row['periode'][:16]: int(row['volume']) for row in rows}
        volumes = [date_vol.get(d) for d in all_dates]
        datasets.append({
            'label': direction,
            'color': DIRECTION_COLORS[i % len(DIRECTION_COLORS)],
            'fill':  DIRECTION_FILLS[i % len(DIRECTION_FILLS)],
            'data':  volumes
        })
    html_parts.append(f"allChartData['{instance}'] = {{ labels: {json.dumps(all_dates)}, datasets: {json.dumps(datasets)} }};\n")

for arrondissement in sorted(by_arrondissement.keys()):
    counters = sorted(by_arrondissement[arrondissement], key=lambda x: (x[1]['rue_1'], x[1]['rue_2']))
    entries = [{"value": inst, "label": counter_label(inst, row)} for inst, row in counters]
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
            'arrondissement': row['arrondissement']
        }
html_parts.append(f"const counterLocations = {json.dumps(counter_locations)};\n")
html_parts.append(f"const gappyCounters = new Set({json.dumps(sorted(gappy_instances))});\n")
html_parts.append(f"const anomalyDays = {json.dumps(anomaly_data)};\n")

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

html_parts.append('''
        const COLOR_MAP_REV = {
            '#1DB860': '#0072BC',
            '#29ABE2': '#1DB860',
            'rgba(29,184,96,0.15)':  'rgba(0,114,188,0.15)',
            'rgba(41,171,226,0.15)': 'rgba(29,184,96,0.15)',
            'rgba(29,184,96,0.75)':  'rgba(0,114,188,0.75)',
            'rgba(41,171,226,0.75)': 'rgba(29,184,96,0.75)',
            'rgba(29,184,96,0.28)':  'rgba(0,114,188,0.28)',
        };
        function themeColor(color) {
            if (!document.body.classList.contains('rev-mode')) return color;
            return COLOR_MAP_REV[color] || color;
        }

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
        let showBixi = true;

        function maxDateStr(instance) {
            const maxDate = getMaxDate(allChartData[instance].labels);
            if (!maxDate) return null;
            const yyyy = maxDate.getFullYear();
            const mm = String(maxDate.getMonth() + 1).padStart(2, '0');
            const dd = String(maxDate.getDate()).padStart(2, '0');
            return `${yyyy}-${mm}-${dd}`;
        }

        function buildFilteredData(instance, days) {
            const allLabels = allChartData[instance].labels;
            const allDatasets = allChartData[instance].datasets;

            let indices;
            if (days === 0) {
                if (!specificDate) return { labels: [], datasets: [] };
                indices = allLabels.map((l, i) => l.startsWith(specificDate) ? i : -1).filter(i => i >= 0);
            } else if (days === -1) {
                indices = allLabels.map((_, i) => i);
            } else {
                if (!globalMaxDate) return { labels: [], datasets: [] };
                const cutoffDate = new Date(globalMaxDate.getFullYear(), globalMaxDate.getMonth(), globalMaxDate.getDate() - (days - 1));
                indices = allLabels.map((l, i) => parseLabel(l) >= cutoffDate ? i : -1).filter(i => i >= 0);
            }

            // Pour la vue 7 jours, générer la grille horaire complète et remplir
            // les heures manquantes avec null (les jours sans données apparaissent comme des gaps)
            if (days === 7 && globalMaxDate) {
                const cutoff = new Date(globalMaxDate.getFullYear(), globalMaxDate.getMonth(), globalMaxDate.getDate() - 6);
                cutoff.setHours(0, 0, 0, 0);
                const fullHours = [];
                const cur = new Date(cutoff);
                while (cur <= globalMaxDate) {
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
            const allLabels = allChartData[instance].labels;
            const allDatasets = allChartData[instance].datasets;
            let indices;
            if (days === -1) {
                indices = allLabels.map((_, i) => i);
            } else {
                if (!globalMaxDate) return { labels: [], datasets: [] };
                const cutoff = new Date(globalMaxDate.getFullYear(), globalMaxDate.getMonth(), globalMaxDate.getDate() - (days - 1));
                indices = allLabels.map((l, i) => parseLabel(l) >= cutoff ? i : -1).filter(i => i >= 0);
            }
            const daySet = {};
            indices.forEach(i => { daySet[allLabels[i].slice(0, 10)] = true; });
            let dayList = Object.keys(daySet).sort();

            // Pour la vue 7 jours, générer tous les jours du calendrier
            // (les jours sans données apparaissent comme des barres à 0, potentiellement en rouge)
            if (days === 7 && globalMaxDate) {
                const fullRange = [];
                const end = new Date(globalMaxDate.getFullYear(), globalMaxDate.getMonth(), globalMaxDate.getDate());
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
            if (!instance || !chartData[instance]) { statsRow.style.display = 'none'; return; }
            const labels = chartData[instance].labels;
            // Combine all directions
            const combined = labels.map((_, i) =>
                chartData[instance].datasets.reduce((sum, ds) => sum + (ds.data[i] || 0), 0)
            );
            const total = combined.reduce((a, b) => a + b, 0);
            const uniqueDays = new Set(labels.map(l => l.slice(0, 10))).size;
            const avg = uniqueDays > 0 ? Math.round(total / uniqueDays) : 0;
            const hourTotals = {};
            labels.forEach((label, i) => {
                const h = label.slice(11, 13);
                if (h) hourTotals[h] = (hourTotals[h] || 0) + combined[i];
            });
            const peak = Object.entries(hourTotals).sort((a, b) => b[1] - a[1])[0];
            statsRow.style.display = 'grid';
            animateCount(document.getElementById('statTotal'), total);
            animateCount(document.getElementById('statAvg'), avg);
            document.getElementById('statPeak').textContent = peak ? peak[0] + 'h' : '—';
        }

        function createChart(id) {
            if (!charts[id]) {
                const ctx = document.getElementById('chart-' + id);
                if (ctx) {
                    const isDaily = viewMode === 'daily';
                    const rawData = isDaily ? buildDailyData(id, currentPeriod) : chartData[id];
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
                    charts[id] = new Chart(ctx, {
                        type: type,
                        data: data,
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
                                        title: function(items) {
                                            const label = items[0].label;
                                            if (isDaily) {
                                                const d = new Date(label + 'T12:00:00');
                                                return d.toLocaleDateString('fr-CA', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
                                            }
                                            const d = parseLabel(label);
                                            return d.toLocaleDateString('fr-CA', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' })
                                                + ' · ' + d.toLocaleTimeString('fr-CA', { hour: '2-digit', minute: '2-digit' });
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
            const toggle = document.getElementById('viewToggle');
            if (currentPeriod >= 7 || currentPeriod === -1) {
                toggle.style.display = 'flex';
            } else {
                toggle.style.display = 'none';
                if (viewMode !== 'timeline') {
                    viewMode = 'timeline';
                    document.getElementById('btnTimeline').classList.add('active');
                    document.getElementById('btnDaily').classList.remove('active');
                }
            }
        }

        function updateDirToggle(instance) {
            const toggle = document.getElementById('dirToggle');
            if (instance && allChartData[instance] && allChartData[instance].datasets.length > 1) {
                toggle.style.display = 'flex';
            } else {
                toggle.style.display = 'none';
            }
        }

        function updateBixiToggle(instance) {
            const hasBixi = instance && typeof bixiNearby !== 'undefined' && bixiNearby[instance] && Object.keys(bixiNearby[instance]).length > 0;
            document.getElementById('bixiToggle').style.display = hasBixi ? 'block' : 'none';
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
            if (!instance || !anomalyDays[instance] || (currentPeriod !== 0 && currentPeriod !== 7)) { el.style.display = 'none'; return; }
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
            const anomalyLines = found.map(([d, info]) => {
                const dateObj = new Date(d + 'T12:00:00');
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
            const exceedsOnlyLines = exceedsOnlyDates.map(d => {
                const dateObj = new Date(d + 'T12:00:00');
                const dateStr = dateObj.toLocaleDateString('fr-CA', {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'});
                const {counter, bixi: bcount} = exceeds[d];
                return `${dateStr.charAt(0).toUpperCase() + dateStr.slice(1)} : <strong>${counter.toLocaleString('fr-CA')}</strong> passages comptés<span class="bixi-exceeds-badge">⚠ Bixi (${bcount}) > compteur (${counter})</span>`;
            });
            document.getElementById('anomalyDetails').innerHTML = [...anomalyLines, ...exceedsOnlyLines].join('<br>');
            el.style.display = 'flex';
        }

        function selectCounter(instance) {
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
            } else {
                noDataMsg.style.display = 'none';
                document.getElementById('dataWarning').style.display = 'none';
                document.getElementById('anomalyWarning').style.display = 'none';
                updateStats(null);
                updateDayLabel(null);
                updateDirToggle(null);
                updateBixiToggle(null);
            }
            if (typeof markers !== 'undefined') updateMapSelection(instance);
        }

        document.getElementById('btnTimeline').addEventListener('click', function() {
            if (viewMode === 'timeline') return;
            viewMode = 'timeline';
            document.getElementById('btnTimeline').classList.add('active');
            document.getElementById('btnDaily').classList.remove('active');
            const instance = getSelectedCounter();
            if (instance) {
                if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
                createChart(instance);
            }
        });

        document.getElementById('btnDaily').addEventListener('click', function() {
            if (viewMode === 'daily') return;
            viewMode = 'daily';
            document.getElementById('btnDaily').classList.add('active');
            document.getElementById('btnTimeline').classList.remove('active');
            const instance = getSelectedCounter();
            if (instance) {
                if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
                createChart(instance);
            }
        });

        document.getElementById('btnSeparate').addEventListener('click', function() {
            if (displayMode === 'separate') return;
            displayMode = 'separate';
            document.getElementById('btnSeparate').classList.add('active');
            document.getElementById('btnCombined').classList.remove('active');
            const instance = getSelectedCounter();
            if (instance) {
                chartData[instance] = buildFilteredData(instance, currentPeriod);
                if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
                createChart(instance);
                updateStats(instance);
            }
        });

        document.getElementById('btnCombined').addEventListener('click', function() {
            if (displayMode === 'combined') return;
            displayMode = 'combined';
            document.getElementById('btnCombined').classList.add('active');
            document.getElementById('btnSeparate').classList.remove('active');
            const instance = getSelectedCounter();
            if (instance) {
                chartData[instance] = buildFilteredData(instance, currentPeriod);
                if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
                createChart(instance);
                updateStats(instance);
            }
        });

        document.getElementById('counterSelectDesktop').addEventListener('change', function() {
            selectCounter(this.value);
        });

        document.getElementById('arrondissementSelect').addEventListener('change', function() {
            const arr = this.value;
            const wrapper = document.getElementById('counterSelectWrapper');
            const mobileSelect = document.getElementById('counterSelectMobile');
            selectCounter(null);
            mobileSelect.innerHTML = '<option value="">Sélectionnez un compteur</option>';
            if (arr && countersByArrondissement[arr]) {
                countersByArrondissement[arr].forEach(c => {
                    const opt = document.createElement('option');
                    opt.value = c.value;
                    opt.textContent = c.label;
                    mobileSelect.appendChild(opt);
                });
                wrapper.style.visibility = 'visible';
            } else {
                wrapper.style.visibility = 'hidden';
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
            const wrapper = document.getElementById('datepickerWrapper');
            if (days === 0) {
                const instance = getSelectedCounter();
                if (instance && allChartData[instance]) {
                    specificDate = maxDateStr(instance);
                    document.getElementById('specificDatePicker').value = specificDate;
                }
                wrapper.style.display = 'block';
            } else {
                wrapper.style.display = 'none';
            }
            updateViewToggle();
            if (days >= 30 || days === -1) {
                viewMode = 'daily';
                document.getElementById('btnDaily').classList.add('active');
                document.getElementById('btnTimeline').classList.remove('active');
            } else {
                viewMode = 'timeline';
                document.getElementById('btnTimeline').classList.add('active');
                document.getElementById('btnDaily').classList.remove('active');
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
            const defaultArr = arrondissements[Math.floor(Math.random() * arrondissements.length)];
            const counters = countersByArrondissement[defaultArr];
            const defaultCounter = counters[Math.floor(Math.random() * counters.length)].value;

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
                document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
                this.classList.add('active');
                filterDataByPeriod(parseInt(this.getAttribute('data-days')));
            });
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

        // ── Carte Leaflet ──
        let COLOR_DEFAULT  = '#1DB860';
        const COLOR_SELECTED = '#29ABE2';
        const COLOR_GAPPY    = '#F59E0B';

        function markerStyle(selected, gappy) {
            const base = gappy ? COLOR_GAPPY : COLOR_DEFAULT;
            return { radius: 9, fillColor: selected ? COLOR_SELECTED : base, color: '#fff', weight: 2, fillOpacity: 0.92 };
        }

        function updateMapSelection(instance) {
            Object.entries(markers).forEach(([id, m]) => m.setStyle(markerStyle(id === instance, gappyCounters.has(id))));
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
                const m = L.circleMarker([loc.lat, loc.lng], markerStyle(false, gappy))
                    .addTo(map)
                    .bindTooltip((gappy ? '⚠ ' : '') + loc.label, { direction: 'top', offset: [0, -6] });
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

        // ── Thème REV ──
        const REV_COLOR = '#0072BC';
        const GREEN_COLOR = '#1DB860';

        if (localStorage.getItem('theme') === 'rev') {
            document.body.classList.add('rev-mode');
            COLOR_DEFAULT = REV_COLOR;
            document.getElementById('themeToggleBtn').title = 'Revenir au thème Montréal';
        }

        document.getElementById('themeToggleBtn').addEventListener('click', function() {
            if (this.classList.contains('spinning')) return;
            this.classList.add('spinning');
            this.addEventListener('animationend', () => {
                this.classList.remove('spinning');
                const isRev = document.body.classList.toggle('rev-mode');
                COLOR_DEFAULT = isRev ? REV_COLOR : GREEN_COLOR;
                this.title = isRev ? 'Revenir au thème Montréal' : 'Basculer vers le thème REV';
                localStorage.setItem('theme', isRev ? 'rev' : 'green');
                updateMapSelection(getSelectedCounter());
                const selected = getSelectedCounter();
                if (selected && hasDataForPeriod(selected)) {
                    chartData[selected] = buildFilteredData(selected, currentPeriod);
                    if (charts[selected]) { charts[selected].destroy(); charts[selected] = null; }
                    createChart(selected);
                }
            }, { once: true });
        });
    </script>
    <div class="watermark">
        <p>Développé par <a href="https://www.gabfortin.com" target="_blank">Gabriel Fortin</a></p>
        <p style="font-size:0.75rem;color:#aaa;margin-top:6px;">⚠️ Ces données proviennent directement des données ouvertes de la Ville de Montréal et de BIXI. Certaines valeurs peuvent être incomplètes ou erronées.</p>
        <p style="font-size:0.7rem;color:#aaa;margin-top:4px;">Page mise à jour le ''' + datetime.now(timezone.utc).strftime('%Y-%m-%d à %H:%M') + ''' UTC</p>
    </div>
</body>
</html>
''')

html = ''.join(html_parts)

# Écrire le fichier HTML
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("Fichier HTML généré : passages.html")