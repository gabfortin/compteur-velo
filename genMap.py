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
    <style>
        * {
            box-sizing: border-box;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1DB860 0%, #0f6e3a 100%);
            color: #333;
            margin: 0;
            padding: 12px;
            min-height: 100vh;
        }
        h1 {
            text-align: center;
            color: #fff;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            margin: 15px 0 20px 0;
            font-size: 24px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 8px;
            padding: 16px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        }
        select {
            width: 100%;
            padding: 14px;
            border: none;
            border-radius: 6px;
            background: #fff;
            font-size: 16px;
            margin-bottom: 18px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            -webkit-appearance: none;
            -moz-appearance: none;
            appearance: none;
            cursor: pointer;
        }
        .table-container {
            display: none;
        }
        h2 {
            color: #1DB860;
            margin: 16px 0 8px 0;
            font-size: 18px;
        }
        canvas {
            max-width: 100%;
            height: 280px;
            margin-bottom: 16px;
        }
        p {
            margin: 8px 0;
            font-weight: normal;
            font-size: 14px;
        }
        p strong {
            color: #1DB860;
        }
        .watermark {
            text-align: center;
            padding: 16px 10px;
            color: rgba(29, 184, 96, 0.4);
            font-size: 12px;
            margin-top: 30px;
            border-top: 1px solid rgba(0, 0, 0, 0.1);
        }
        .watermark a {
            color: rgba(29, 184, 96, 0.6);
            text-decoration: none;
            transition: color 0.3s ease;
        }
        .watermark a:hover {
            color: rgba(29, 184, 96, 1);
            text-decoration: underline;
        }
        .period-buttons {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-bottom: 18px;
        }
        .period-btn {
            padding: 11px 12px;
            border: 2px solid rgba(29, 184, 96, 0.5);
            background: #fff;
            color: #1DB860;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            transition: all 0.3s ease;
            text-align: center;
        }
        .period-btn:active {
            transform: scale(0.98);
        }
        .period-btn:hover {
            border-color: #1DB860;
            background: rgba(29, 184, 96, 0.1);
        }
        .period-btn.active {
            background: #1DB860;
            color: #fff;
            border-color: #1DB860;
        }
        
        @media (min-width: 768px) {
            body {
                padding: 20px;
            }
            h1 {
                font-size: 28px;
                margin-bottom: 30px;
            }
            .container {
                padding: 24px;
            }
            select {
                padding: 12px;
                font-size: 16px;
                margin-bottom: 20px;
            }
            h2 {
                font-size: 20px;
                margin: 20px 0 10px 0;
            }
            canvas {
                height: 300px;
                margin-bottom: 20px;
            }
            p {
                font-size: 15px;
                margin: 10px 0;
                font-weight: bold;
            }
            .watermark {
                padding: 20px 10px;
                font-size: 14px;
                margin-top: 40px;
            }
            .period-buttons {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                justify-content: center;
                margin-bottom: 20px;
                grid-template-columns: unset;
            }
            .period-btn {
                padding: 10px 20px;
                font-size: 14px;
                flex: 0 1 auto;
            }
            .period-btn:active {
                transform: none;
            }
        }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
    <div class="container">
        <h1>Compteurs Vélo Montréal</h1>
        <div class="period-buttons">
            <button class="period-btn active" data-days="7">7 derniers jours</button>
            <button class="period-btn" data-days="30">1 dernier mois</button>
            <button class="period-btn" data-days="90">3 derniers mois</button>
            <button class="period-btn" data-days="180">6 derniers mois</button>
        </div>
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
        dates = json.dumps([row['periode'][:10] for row in sorted_rows])
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
        dates = json.dumps([row['periode'][:10] for row in sorted_rows])
        volumes = json.dumps([int(row['volume']) for row in sorted_rows])
        html_parts.append(f"allChartData['{instance}'] = {{ labels: {dates}, data: {volumes} }};\n")

html_parts.append('''
        // Initialiser chartData avec les données filtrées pour 7 jours par défaut
        function initializeChartData() {
            const now = new Date();
            const cutoffDate = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
            
            for (let instance in allChartData) {
                const allLabels = allChartData[instance].labels;
                const allVolumes = allChartData[instance].data;
                
                const filteredLabels = [];
                const filteredData = [];
                
                allLabels.forEach((label, index) => {
                    const dataDate = new Date(label + 'T00:00:00');
                    if (dataDate >= cutoffDate) {
                        filteredLabels.push(label);
                        filteredData.push(allVolumes[index]);
                    }
                });
                
                chartData[instance] = {
                    labels: filteredLabels,
                    datasets: [{
                        label: 'Passages',
                        data: filteredData,
                        borderColor: 'rgba(29, 184, 96, 1)',
                        backgroundColor: 'rgba(29, 184, 96, 0.2)',
                        fill: true
                    }]
                };
            }
        }
        
        // Initialiser au chargement
        initializeChartData();
        
        function createChart(id) {
            if (!charts[id]) {
                const ctx = document.getElementById('chart-' + id);
                if (ctx) {
                    charts[id] = new Chart(ctx, {
                        type: 'line',
                        data: chartData[id],
                        options: {
                            responsive: true,
                            scales: {
                                x: {
                                    title: {
                                        display: true,
                                        text: 'Date'
                                    }
                                },
                                y: {
                                    title: {
                                        display: true,
                                        text: 'Nombre de Passages'
                                    },
                                    beginAtZero: true
                                }
                            }
                        }
                    });
                }
            }
        }

        document.getElementById('counterSelect').addEventListener('change', function() {
            var selected = this.value;
            var containers = document.querySelectorAll('.table-container');
            containers.forEach(function(c) {
                c.style.display = 'none';
            });
            if (selected) {
                document.getElementById(selected).style.display = 'block';
                createChart(selected);
            }
        });

        let currentPeriod = 7;
        
        function filterDataByPeriod(days) {
            currentPeriod = days;
            const now = new Date();
            const cutoffDate = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
            
            // Mettre à jour les données filtrées pour chaque instance
            for (let instance in allChartData) {
                const allLabels = allChartData[instance].labels;
                const allVolumes = allChartData[instance].data;
                
                const filteredLabels = [];
                const filteredData = [];
                
                allLabels.forEach((label, index) => {
                    const dataDate = new Date(label + 'T00:00:00');
                    if (dataDate >= cutoffDate) {
                        filteredLabels.push(label);
                        filteredData.push(allVolumes[index]);
                    }
                });
                
                // Mettre à jour chartData avec les données filtrées
                chartData[instance] = {
                    labels: filteredLabels,
                    datasets: [{
                        label: 'Passages',
                        data: filteredData,
                        borderColor: 'rgba(29, 184, 96, 1)',
                        backgroundColor: 'rgba(29, 184, 96, 0.2)',
                        fill: true
                    }]
                };
            }
            
            // Redessiner le graphique actuel si un est affiché
            const selected = document.getElementById('counterSelect').value;
            if (selected && charts[selected]) {
                charts[selected].destroy();
                charts[selected] = null;
                createChart(selected);
            }
        }
        
        // Ajouter les event listeners aux boutons de période
        document.querySelectorAll('.period-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
                this.classList.add('active');
                const days = parseInt(this.getAttribute('data-days'));
                filterDataByPeriod(days);
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