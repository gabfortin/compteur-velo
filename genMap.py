import csv
import re
from collections import defaultdict
import os
from tqdm import tqdm
import json
from datetime import datetime, timedelta

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
    <title>Compteurs Vélo Montréal</title>
    <link rel="icon" type="image/png" href="favico.png">
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(160deg, #22c76a 0%, #0f6e3a 100%);
            color: #333;
            margin: 0;
            padding: 12px;
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
            margin-bottom: 18px;
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
        @media (min-width: 768px) {
            .desktop-only { display: block; }
            .mobile-only { display: none; }
            body { padding: 20px; }
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
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
    <div style="text-align:right;padding:6px 12px;font-size:12px;">
        <a href="https://www.gabfortin.com" style="color:rgba(255,255,255,0.7);text-decoration:none;" target="_blank">gabfortin.com</a>
    </div>
    <div class="site-header">
        <img src="favico.png" alt="Logo" style="width:72px;height:72px;border-radius:18px;margin-bottom:12px;box-shadow:0 4px 16px rgba(0,0,0,0.2);">
        <h1>Compteurs Vélo Montréal</h1>
        <p class="subtitle">Données de passage de cyclistes à Montréal, tirées du portail de <a href="https://donnees.montreal.ca/dataset/cyclistes" target="_blank" style="color:rgba(255,255,255,0.9);text-decoration:underline;">données ouvertes de la Ville</a>.</p>
    </div>
    <div class="container">
        <div class="period-buttons">
            <button class="period-btn" data-days="1">Dernier jour</button>
            <button class="period-btn active" data-days="7">7 derniers jours</button>
            <button class="period-btn" data-days="30">1 dernier mois</button>
            <button class="period-btn" data-days="90">3 derniers mois</button>
            <button class="period-btn" data-days="180">6 derniers mois</button>
        </div>
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
        <div class="dir-toggle" id="dirToggle" style="display:none">
            <button class="dir-btn active" id="btnSeparate">Par direction</button>
            <button class="dir-btn" id="btnCombined">Combiné</button>
        </div>
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
    <script>
        const allChartData = {};
        const chartData = {};
        const charts = {};
        const countersByArrondissement = {};
''')

DIRECTION_COLORS = ['#1DB860', '#E8832A']
DIRECTION_FILLS  = ['rgba(29,184,96,0.15)', 'rgba(232,131,42,0.15)']

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

html_parts.append('''
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

        let displayMode = 'separate';

        function buildFilteredData(instance, days) {
            const allLabels = allChartData[instance].labels;
            const allDatasets = allChartData[instance].datasets;
            const maxDate = getMaxDate(allLabels);
            if (!maxDate) return { labels: [], datasets: [] };

            const cutoffDate = new Date(maxDate.getFullYear(), maxDate.getMonth(), maxDate.getDate() - (days - 1));
            const indices = allLabels.map((l, i) => parseLabel(l) >= cutoffDate ? i : -1).filter(i => i >= 0);
            const filteredLabels = indices.map(i => allLabels[i]);

            const isMulti = allDatasets.length > 1;
            const showCombined = isMulti && displayMode === 'combined';

            if (showCombined) {
                const combined = indices.map(i =>
                    allDatasets.reduce((sum, ds) => sum + (ds.data[i] || 0), 0)
                );
                return {
                    labels: filteredLabels,
                    datasets: [{ label: 'Combiné', data: combined, borderColor: '#1DB860', backgroundColor: 'rgba(29,184,96,0.15)', fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2, pointHoverRadius: 5 }]
                };
            }

            const isSingle = !isMulti;
            return {
                labels: filteredLabels,
                datasets: allDatasets.map(ds => ({
                    label: ds.label,
                    data: indices.map(i => ds.data[i]),
                    borderColor: ds.color,
                    backgroundColor: ds.fill,
                    fill: isSingle,
                    tension: 0.3,
                    borderWidth: 2,
                    pointRadius: 2,
                    pointHoverRadius: 5
                }))
            };
        }

        function initializeChartData() {
            for (let instance in allChartData) {
                chartData[instance] = buildFilteredData(instance, 7);
            }
        }
        initializeChartData();

        function updateDayLabel(instance) {
            const el = document.getElementById('dayLabel');
            if (currentPeriod === 1 && instance && allChartData[instance]) {
                const maxDate = getMaxDate(allChartData[instance].labels);
                if (maxDate) {
                    const s = maxDate.toLocaleDateString('fr-CA', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
                    el.textContent = s.charAt(0).toUpperCase() + s.slice(1);
                    el.style.display = 'block';
                    return;
                }
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
                    const isSingle = chartData[id].datasets.length === 1;
                    if (isSingle && chartData[id].datasets[0].fill) {
                        const gradient = ctx.getContext('2d').createLinearGradient(0, 0, 0, 380);
                        gradient.addColorStop(0, 'rgba(29,184,96,0.28)');
                        gradient.addColorStop(1, 'rgba(29,184,96,0)');
                        chartData[id].datasets[0].backgroundColor = gradient;
                    }
                    charts[id] = new Chart(ctx, {
                        type: 'line',
                        data: chartData[id],
                        options: {
                            responsive: true,
                            animation: { duration: 500, easing: 'easeInOutQuart' },
                            plugins: {
                                legend: { display: !isSingle },
                                tooltip: {
                                    backgroundColor: 'rgba(10,40,20,0.88)',
                                    titleColor: '#7ee8a2',
                                    bodyColor: '#fff',
                                    padding: 10,
                                    cornerRadius: 8,
                                    callbacks: {
                                        title: function(items) {
                                            const d = parseLabel(items[0].label);
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
                                            const d = parseLabel(label);
                                            if (currentPeriod === 1) {
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
                                }
                            }
                        }
                    });
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

        function selectCounter(instance) {
            document.querySelectorAll('.table-container').forEach(c => c.classList.remove('visible'));
            if (instance) {
                document.getElementById(instance).classList.add('visible');
                createChart(instance);
                updateStats(instance);
                updateDayLabel(instance);
                updateDirToggle(instance);
            } else {
                updateStats(null);
                updateDayLabel(null);
                updateDirToggle(null);
            }
        }

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
            for (let instance in allChartData) {
                chartData[instance] = buildFilteredData(instance, days);
            }
            const selected = getSelectedCounter();
            if (selected && charts[selected]) {
                charts[selected].destroy();
                charts[selected] = null;
                createChart(selected);
                updateStats(selected);
                updateDayLabel(selected);
            }
        }

        (function() {
            const arrondissements = Object.keys(countersByArrondissement).sort();
            if (!arrondissements.length) return;
            const defaultArr = arrondissements[0];
            const defaultCounter = countersByArrondissement[defaultArr][0].value;

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
    </script>
    <div class="watermark">
        <p>Développé par <a href="https://www.gabfortin.com" target="_blank">Gabriel Fortin</a></p>
    </div>
</body>
</html>
''')

html = ''.join(html_parts)

# Écrire le fichier HTML
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("Fichier HTML généré : passages.html")