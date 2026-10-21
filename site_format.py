"""Format de data/site.json, partagé par genMap.py et les tests.

La page (index.html, assets/) est statique : genMap.py ne produit que ce
fichier de données, chargé par assets/boot.js puis lu par assets/app.js.
"""
from datetime import datetime, timedelta
import os

SITE_DATA_FILE = os.path.join('data', 'site.json')

_LABEL_FMT = '%Y-%m-%d %H:%M'


def encode_hour_runs(labels):
    """Compacte des étiquettes horaires triées ('AAAA-MM-JJ HH:MM') en plages
    d'heures consécutives [[première étiquette, nombre d'heures], ...].

    Calcul en heure « murale » (sans fuseau), comme expandHourRuns() dans
    assets/app.js : un saut d'heure (changement d'heure, donnée manquante)
    commence simplement une nouvelle plage.
    """
    runs, prev = [], None
    for label in labels:
        dt = datetime.strptime(label, _LABEL_FMT)
        if runs and dt - prev == timedelta(hours=1):
            runs[-1][1] += 1
        else:
            runs.append([label, 1])
        prev = dt
    return runs


def decode_hour_runs(runs):
    """Inverse de encode_hour_runs."""
    return [(datetime.strptime(start, _LABEL_FMT) + timedelta(hours=k)).strftime(_LABEL_FMT)
            for start, count in runs for k in range(count)]
