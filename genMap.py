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

# Filtrer les données pour garder seulement les 3 derniers mois
def is_within_last_3_months(date_str):
    try:
        row_date = datetime.fromisoformat(date_str.replace('-05', '').replace('-04', ''))
        cutoff_date = datetime.now() - timedelta(days=90)
        return row_date >= cutoff_date
    except:
        return True

for instance in data.keys():
    data[instance] = [row for row in data[instance] if is_within_last_3_months(row['periode'])]

# Générer le HTML
html_parts = ['''<html>
<head>
    <meta charset="utf-8">
    <title>Passages Vélo par Compteur</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #333;
            margin: 0;
            padding: 20px;
            min-height: 100vh;
        }
        h1 {
            text-align: center;
            color: #fff;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            margin-bottom: 30px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: rgba(255, 255, 255, 0.9);
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        }
        select {
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 5px;
            background: #fff;
            font-size: 16px;
            margin-bottom: 20px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        .table-container {
            display: none;
        }
        h2 {
            color: #667eea;
            margin-top: 20px;
        }
        canvas {
            max-width: 100%;
            height: 300px;
            margin-bottom: 20px;
        }
        p {
            margin: 10px 0;
            font-weight: bold;
        }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
    <div class="container">
        <h1>Passages Vélo pour Chaque Compteur</h1>
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
        dates = json.dumps([row['periode'] for row in sorted_rows])
        volumes = json.dumps([int(row['volume']) for row in sorted_rows])
        html_parts.append(f'<canvas id="chart-{instance}"></canvas>')
        html_parts.append('</div>')

html_parts.append('''
    </div>
    <script>
        const chartData = {};
        const charts = {};
''')

# Ajouter les données des graphiques
for instance, rows in data.items():
    if rows:  # Vérifier que la liste n'est pas vide
        sorted_rows = sorted(rows, key=lambda x: x['periode'])
        dates = json.dumps([row['periode'] for row in sorted_rows])
        volumes = json.dumps([int(row['volume']) for row in sorted_rows])
        html_parts.append(f"chartData['{instance}'] = {{ labels: {dates}, datasets: [{{ label: 'Passages', data: {volumes}, borderColor: 'rgba(102, 126, 234, 1)', backgroundColor: 'rgba(102, 126, 234, 0.2)', fill: true }}] }};\n")

html_parts.append('''
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
    </script>
</body>
</html>
''')

html = ''.join(html_parts)

# Écrire le fichier HTML
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("Fichier HTML généré : passages.html")