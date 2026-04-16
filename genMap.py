import csv
from collections import defaultdict
import os
from tqdm import tqdm
import json
from datetime import datetime, timedelta

# Compter le nombre total de lignes pour la barre de progression
total_lines = sum(1 for line in open('cyclistes.csv', encoding='utf-8')) - 1  # Soustraire la ligne d'en-tête

# Lire le fichier CSV et grouper par instance (compteur)
data = defaultdict(list)
with open('cyclistes.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in tqdm(reader, total=total_lines, desc="Traitement des données"):
        if row['agg_code'] == 'h':
            data[row['instance']].append(row)

# Filtrer les données pour garder les 6 derniers mois
def is_within_last_6_months(date_str):
    try:
        row_date = datetime.fromisoformat(date_str.replace('-05', '').replace('-04', ''))
        cutoff_date = datetime.now() - timedelta(days=180)
        return row_date >= cutoff_date
    except:
        return True

for instance in data.keys():
    data[instance] = [row for row in data[instance] if is_within_last_6_months(row['periode'])]

# Générer le HTML
html_parts = ['''<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Passages Vélo par Compteur</title>
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
        @media (min-width: 768px) {
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
    <div class="site-header">
        <img src="favico.png" alt="Logo" style="width:72px;height:72px;border-radius:18px;margin-bottom:12px;box-shadow:0 4px 16px rgba(0,0,0,0.2);">
        <h1>Compteurs Vélo Montréal</h1>
        <p class="subtitle">Données de passage de cyclistes à Montréal, tirées du portail de données ouvertes de la Ville.</p>
    </div>
    <div class="container">
        <div class="period-buttons">
            <button class="period-btn" data-days="1">Dernier jour</button>
            <button class="period-btn active" data-days="7">7 derniers jours</button>
            <button class="period-btn" data-days="30">1 dernier mois</button>
            <button class="period-btn" data-days="90">3 derniers mois</button>
            <button class="period-btn" data-days="180">6 derniers mois</button>
        </div>
        <div class="select-wrapper">
        <select id="counterSelect">
            <option value="">Sélectionnez un compteur</option>
''']

# Ajouter les options pour le dropdown
for instance in data.keys():
    if data[instance]:  # Vérifier que la liste n'est pas vide
        first_row = data[instance][0]
        location = f"{first_row['arrondissement']} - {first_row['rue_1']} {first_row['rue_2']} - Direction {first_row['direction']}"
        html_parts.append(f'<option value="{instance}">{instance} - {location}</option>')

html_parts.append('''
        </select>
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
''')

for instance, rows in tqdm(data.items(), desc="Génération HTML"):
    if rows:  # Vérifier que la liste n'est pas vide
        # Informations du compteur
        first_row = rows[0]
        location = f"{first_row['arrondissement']} - {first_row['rue_1']} {first_row['rue_2']} - Direction {first_row['direction']}"
        html_parts.append(f'<div id="{instance}" class="table-container">')
        html_parts.append(f'<h2>Compteur {instance}</h2>')
        html_parts.append(f'<p><strong>Emplacement:</strong> {location}</p>')
        
        # Préparer les données pour le graphique
        sorted_rows = sorted(rows, key=lambda x: x['periode'])
        dates = json.dumps([row['periode'][:16] for row in sorted_rows])
        volumes = json.dumps([int(row['volume']) for row in sorted_rows])
        html_parts.append(f'<canvas id="chart-{instance}"></canvas>')
        html_parts.append('</div>')

html_parts.append('''
    </div>
    <script>
        const allChartData = {};
        const chartData = {};
        const charts = {};
''')

# Ajouter les données des graphiques
for instance, rows in data.items():
    if rows:  # Vérifier que la liste n'est pas vide
        sorted_rows = sorted(rows, key=lambda x: x['periode'])
        dates = json.dumps([row['periode'][:16] for row in sorted_rows])
        volumes = json.dumps([int(row['volume']) for row in sorted_rows])
        html_parts.append(f"allChartData['{instance}'] = {{ labels: {dates}, data: {volumes} }};\n")

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

        function buildFilteredData(instance, days) {
            const allLabels = allChartData[instance].labels;
            const allVolumes = allChartData[instance].data;
            const maxDate = getMaxDate(allLabels);
            if (!maxDate) return { labels: [], datasets: [{ label: 'Passages', data: [], borderColor: '#1DB860', backgroundColor: 'rgba(29,184,96,0.15)', fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2, pointHoverRadius: 5 }] };

            const cutoffDate = new Date(maxDate.getFullYear(), maxDate.getMonth(), maxDate.getDate() - (days - 1));

            const filteredLabels = [];
            const filteredData = [];
            allLabels.forEach((label, index) => {
                const dataDate = parseLabel(label);
                if (dataDate >= cutoffDate) {
                    filteredLabels.push(label);
                    filteredData.push(allVolumes[index]);
                }
            });

            return {
                labels: filteredLabels,
                datasets: [{ label: 'Passages', data: filteredData, borderColor: '#1DB860', backgroundColor: 'rgba(29,184,96,0.15)', fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2, pointHoverRadius: 5 }]
            };
        }

        function initializeChartData() {
            for (let instance in allChartData) {
                chartData[instance] = buildFilteredData(instance, 7);
            }
        }
        initializeChartData();

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
            const volumes = chartData[instance].datasets[0].data;
            const labels  = chartData[instance].labels;
            const total   = volumes.reduce((a, b) => a + b, 0);
            const uniqueDays = new Set(labels.map(l => l.slice(0, 10))).size;
            const avg = uniqueDays > 0 ? Math.round(total / uniqueDays) : 0;
            const hourTotals = {};
            labels.forEach((label, i) => {
                const h = label.slice(11, 13);
                if (h) hourTotals[h] = (hourTotals[h] || 0) + volumes[i];
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
                    const gradient = ctx.getContext('2d').createLinearGradient(0, 0, 0, 380);
                    gradient.addColorStop(0, 'rgba(29,184,96,0.28)');
                    gradient.addColorStop(1, 'rgba(29,184,96,0)');
                    chartData[id].datasets[0].backgroundColor = gradient;
                    charts[id] = new Chart(ctx, {
                        type: 'line',
                        data: chartData[id],
                        options: {
                            responsive: true,
                            animation: { duration: 500, easing: 'easeInOutQuart' },
                            plugins: {
                                legend: { display: false },
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

        document.getElementById('counterSelect').addEventListener('change', function() {
            const selected = this.value;
            document.querySelectorAll('.table-container').forEach(c => c.classList.remove('visible'));
            if (selected) {
                document.getElementById(selected).classList.add('visible');
                createChart(selected);
                updateStats(selected);
            } else {
                updateStats(null);
            }
        });

        let currentPeriod = 7;

        function filterDataByPeriod(days) {
            currentPeriod = days;
            for (let instance in allChartData) {
                chartData[instance] = buildFilteredData(instance, days);
            }
            const selected = document.getElementById('counterSelect').value;
            if (selected && charts[selected]) {
                charts[selected].destroy();
                charts[selected] = null;
                createChart(selected);
                updateStats(selected);
            }
        }

        (function() {
            const instances = Object.keys(allChartData);
            if (instances.length > 0) {
                const randomInstance = instances[Math.floor(Math.random() * instances.length)];
                const select = document.getElementById('counterSelect');
                select.value = randomInstance;
                document.getElementById(randomInstance).classList.add('visible');
                createChart(randomInstance);
                updateStats(randomInstance);
            }
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