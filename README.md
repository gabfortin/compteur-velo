# Compteurs Vélo Montréal

Visualisation interactive des passages de cyclistes à Montréal, à partir des données ouvertes de la Ville. Le site affiche un graphique par compteur avec filtres de période, vue temporelle ou journalière, statistiques dynamiques, et une carte OpenStreetMap. Les données BIXI sont croisées avec les compteurs pour valider leur fiabilité.

> ⚠️ Les données proviennent directement du portail de données ouvertes de la Ville de Montréal et de BIXI. Certaines valeurs peuvent être incomplètes ou erronées.

---

## Structure des fichiers

```
compteur-velo/
├── cyclistes.csv            # Détecteurs sur fût — portail données ouvertes Montréal — ignoré par git
├── compteurs.csv            # Boucles magnétiques — portail données ouvertes Montréal — ignoré par git
├── bixi.csv                 # Données BIXI de l'année en cours (bixi.com/en/open-data) — ignoré par git
├── velo_meta_cache.json     # Cache Nominatim (arrondissement + rue des boucles magnétiques)
├── genMap.py                # Script de génération du site HTML
├── index.html               # Site généré (ne pas modifier manuellement)
├── favico.png               # Icône de l'application
├── test_data.py             # Suite de tests de validation des données
├── update.sh                # Script de mise à jour automatique (télécharge CSV → génère HTML → publie)
├── Dockerfile               # Image Docker pour l'automatisation (cron quotidien)
├── docker-compose.yml       # Orchestration du conteneur d'automatisation
├── entrypoint.sh            # Point d'entrée Docker (injecte les variables dans l'environnement cron)
├── .env                     # Variables d'environnement locales (ignoré par git)
├── .env.example             # Modèle de configuration
├── .gitignore               # Exclut cyclistes.csv, bixi.csv, compteurs.csv et .env
└── CNAME                    # Domaine personnalisé GitHub Pages (compteur.gabfortin.com)
```

---

## Flux de génération

```
cyclistes.csv  ─┐
compteurs.csv  ─┤──  genMap.py  →  index.html  →  GitHub Pages
bixi.csv       ─┘
```

`genMap.py` lit les trois CSV, filtre et transforme les données, puis génère un fichier HTML autonome (CSS + données + JavaScript inline). Il n'y a aucune dépendance serveur : `index.html` s'ouvre directement dans un navigateur.

Pour régénérer le site manuellement après une mise à jour du CSV :

```bash
python3 genMap.py
python3 test_data.py   # Valide les données après génération
```

---

## Sources de données

### `cyclistes.csv` — Détecteurs SUM (comptage permanent)

Fichier CSV téléchargé depuis le [portail de données ouvertes de la Ville de Montréal](https://donnees.montreal.ca/dataset/velos-comptage), sous le titre **« Vélos - comptage permanent »**. Depuis 2026, ces données sont publiées sur la même page que les Éco-Compteurs. Contient les passages horaires des détecteurs installés sur des fûts le long des pistes cyclables.

Colonnes :

| Colonne          | Description                                                                           |
|------------------|---------------------------------------------------------------------------------------|
| `agg_code`       | Niveau d'agrégation : `h` (heure), `d` (jour), `m` (mois), `y` (année), `f` (total) |
| `instance`       | Identifiant unique du compteur (ex. `det-00077-01`)                                   |
| `longitude`      | Longitude GPS                                                                         |
| `latitude`       | Latitude GPS                                                                          |
| `arrondissement` | Arrondissement de Montréal                                                            |
| `rue_1`          | Rue principale                                                                        |
| `rue_2`          | Rue secondaire / intersection                                                         |
| `numeroVoie`     | Numéro de voie du compteur                                                            |
| `direction`      | Direction comptée (Nord, Sud, Est, Ouest)                                             |
| `periode`        | Horodatage ISO avec fuseau horaire (ex. `2025-11-04 14:00:00-05`)                    |
| `volume`         | Nombre de passages sur la période                                                     |
| `vitesseMoyenne` | Vitesse moyenne des cyclistes (km/h)                                                  |

Seules les lignes `agg_code = "h"` (données horaires) sont utilisées par `genMap.py`.

### `compteurs.csv` — Boucles magnétiques

Fichier CSV téléchargé depuis le [portail de données ouvertes de la Ville de Montréal](https://donnees.montreal.ca/dataset/velos-comptage). Contient les passages en intervalles de 15 minutes des boucles magnétiques encastrées dans la chaussée.

Colonnes :

| Colonne        | Description                                           |
|----------------|-------------------------------------------------------|
| `date`         | Date (format `YYYY-MM-DD`)                            |
| `heure`        | Heure de début de l'intervalle (format `HH:MM:SS`)   |
| `id_compteur`  | Identifiant numérique du capteur (ex. `100041114`)    |
| `nb_passages`  | Nombre de passages sur l'intervalle de 15 min         |
| `longitude`    | Longitude GPS                                         |
| `latitude`     | Latitude GPS                                          |

Ces données sont publiées pour l'année courante. L'URL de téléchargement suit le schéma `comptage_velo_{année}.csv` (hors fichier `permanent`) — `update.sh` scrape automatiquement l'URL correcte pour l'année en cours, en excluant le fichier de comptage permanent.

**Différences avec `cyclistes.csv` :**
- Granularité 15 min (agrégée en horaire par `genMap.py`) vs horaire natif
- Pas de direction, arrondissement ou nom de rue dans le fichier — ces métadonnées sont obtenues par reverse geocoding Nominatim (OpenStreetMap) et mises en cache dans `velo_meta_cache.json`
- Identifiants numériques (`100041114`) préfixés `vf-` dans `genMap.py` (ex. `vf-100041114`) pour éviter tout conflit avec les `det-` de `cyclistes.csv`
- 54 capteurs couvrant des emplacements majoritairement différents des 46 détecteurs sur fût

### `bixi.csv`

Fichier CSV téléchargé depuis [bixi.com/en/open-data](https://bixi.com/en/open-data/). Contient tous les trajets BIXI de l'année en cours.

Colonnes utilisées :

| Colonne                    | Description                                  |
|----------------------------|----------------------------------------------|
| `STARTSTATIONLATITUDE`     | Latitude de la station de départ             |
| `STARTSTATIONLONGITUDE`    | Longitude de la station de départ            |
| `ENDSTATIONLATITUDE`       | Latitude de la station d'arrivée             |
| `ENDSTATIONLONGITUDE`      | Longitude de la station d'arrivée            |
| `STARTTIMEMS`              | Horodatage de départ en millisecondes (Unix) |

---

## Script de génération — `genMap.py`

### Étape 1 — Lecture et filtrage de `cyclistes.csv`

Les données sont groupées par instance **et par direction** :

```python
data[row['instance']][row['direction']].append(row)
```

Certains compteurs ont deux directions (ex. Est + Ouest). Les regrouper séparément évite les timestamps dupliqués et les volumes gonflés.

Les données sont ensuite filtrées pour ne garder que les 180 derniers jours :

```python
def is_within_last_6_months(date_str):
    clean = re.sub(r'[+-]\d{2}$', '', date_str.strip('"').strip())
    row_date = datetime.fromisoformat(clean)
    return row_date >= datetime.now() - timedelta(days=180)
```

> **Note** : le fuseau horaire (`-05` / `-04`) est retiré avec une regex ciblant la fin de la chaîne. Un simple `.replace('-05', '')` corromprait les dates contenant ces chiffres dans le jour (ex. `2025-10-05`).

### Étape 2 — Intégration de `compteurs.csv` (boucles magnétiques)

`load_velo_full()` charge `compteurs.csv`, agrège les intervalles de 15 min en données horaires, et retourne une structure compatible avec `data{}` :

```python
hour_key = f"{row['date']} {row['heure'][:2]}:00:00"
hourly[cid][hour_key] += int(row['nb_passages'])
```

Les métadonnées manquantes (arrondissement, rue) sont obtenues par reverse geocoding via `fetch_nominatim_meta()` (API Nominatim / OpenStreetMap, gratuit, sans clé), avec un délai de 1,1 s entre les requêtes pour respecter la limite de débit. Les résultats sont mis en cache dans `velo_meta_cache.json` — les générations suivantes n'effectuent aucune requête pour les capteurs déjà connus.

```python
# Extrait de fetch_nominatim_meta()
arr  = addr.get('city_district') or addr.get('quarter') or addr.get('suburb') or 'Montréal'
rue1 = addr.get('road') or addr.get('pedestrian') or addr.get('path') or 'Écocompteur'
```

Les instances sont préfixées `vf-` et fusionnées dans `data{}` :

```python
result[f"vf-{cid}"] = {'N/A': rows}   # Une seule direction, pas de direction connue
data.update(vf_data)
```

Après la fusion, tout le pipeline (détection de lacunes, détection d'anomalies, génération HTML, carte, dropdowns) s'applique uniformément aux deux types de compteurs.

### Étape 3 — Données météo

`fetch_weather_data()` interroge [Open-Meteo](https://open-meteo.com/) (gratuit, sans clé API) en deux passes pour couvrir les 6 derniers mois sans discontinuité :

| Source | URL | Couverture |
|--------|-----|------------|
| API archive (ERA5) | `archive-api.open-meteo.com/v1/archive` | J-182 à J-0 (lag ~5 jours) |
| API forecast | `api.open-meteo.com/v1/forecast` | `past_days=10` (comble le lag de l'archive) |

Les deux résultats sont fusionnés, les données forecast prenant priorité pour les dates récentes.

Champs récupérés par jour : `temperature_2m_max`, `temperature_2m_min`, `precipitation_sum`, `snowfall_sum`, `weather_code` (WMO). Un emoji est calculé depuis le code WMO par `weather_icon()`.

Le résultat est embarqué dans le HTML sous la forme `const weatherData = {...}`, puis utilisé :
1. pour exclure les jours sévères de la détection d'anomalies (`is_bad_weather()`)
2. pour enrichir le tooltip des graphiques journaliers côté JS

### Étape 4 — Traitement BIXI

Si `bixi.csv` est présent, `genMap.py` croise chaque trajet BIXI avec les compteurs à proximité via la distance haversine (rayon de 150 m) :

```python
def _haversine_m(lat1, lon1, lat2, lon2):
    # Retourne la distance en mètres entre deux coordonnées GPS
    ...

BIXI_RADIUS_M = 150
```

Pour chaque trajet, si la station de **départ ou d'arrivée** se trouve dans le rayon d'un compteur, ce compteur est incrémenté pour la date du trajet. Un même trajet ne compte qu'une seule fois par compteur, même si les deux stations sont dans son rayon.

Résultat : deux structures injectées dans le JS :

```javascript
// Nombre de trajets BIXI par compteur par jour
const bixiNearby = {
    "det-00709-01": { "2026-01-23": 59, "2026-01-27": 48, ... }
};

// Jours où les trajets BIXI dépassent le volume du compteur
const bixiExceedsDays = {
    "det-00709-01": {
        "2026-01-23": { "counter": 0, "bixi": 59 },
        ...
    }
};
```

### Étape 5 — Génération du HTML

Le HTML est construit par concaténation dans `html_parts`, puis écrit dans `index.html`.

**Structure HTML produite :**

```
<html>
  <head>                 ← CSS inline + Chart.js 4.4.4 + Leaflet 1.9.4 (CDN) + Google Analytics
  <body>
    <nav #topbar>        ← Barre de navigation : liens Pistes / Compteurs + lien profil gabfortin.com
    .site-header         ← Logo, titre, sous-titre (liens vers données ouvertes Ville + BIXI)
    .container
      .period-buttons    ← Filtres de période (Jour spécifique / 7j / 30j / 90j / 180j / Tout)
      .select-wrapper.desktop-only
        <select>         ← Dropdown unique groupé par arrondissement (desktop)
                            Détecteurs sur fût en premier, boucles magnétiques en second dans chaque groupe
      .mobile-only
        <select>         ← Dropdown arrondissement (mobile)
        <select>         ← Dropdown compteur (mobile, peuplé dynamiquement)
      .stats-row         ← 3 cartes : passages totaux, moyenne/jour, heure de pointe
      #viewToggle        ← Toggle "Dans le temps / Par jour" (masqué pour Jour spécifique uniquement)
      #dirToggle         ← Toggle "Par direction / Combiné" (bi-directionnels uniquement)
      #bixiToggle        ← Toggle "Bixi" on/off (visible uniquement pour les compteurs avec données BIXI)
      #chart-map-layout  ← Grid 2 colonnes sur desktop, empilé sur mobile
        #chart-area
          #noDataMsg     ← Message "Aucune donnée disponible" (affiché si période vide)
          [N × .table-container]  ← Un div par compteur (masqué par défaut)
                                     Le titre <h2> contient un badge cliquable indiquant le type de capteur
        #map             ← Carte Leaflet avec légende de types
    <script>             ← Données + logique JS inline
    .watermark           ← Crédit auteur + avertissement qualité des données + timestamp de génération
```

**Données injectées dans le JS :**

```javascript
// Données horaires par instance et direction
allChartData['det-00709-01'] = {
    labels: ["2025-11-04 14:00", ...],
    datasets: [
        { label: 'Ouest', color: '#1DB860', fill: 'rgba(...)', data: [4, 8, ...] },
        { label: 'Est',   color: '#29ABE2', fill: 'rgba(...)', data: [7, 12, ...] }
    ]
};
// Pour les boucles magnétiques, une seule direction 'N/A' → label 'Passages'
allChartData['vf-100012217'] = {
    labels: ["2026-01-01 10:00", ...],
    datasets: [{ label: 'Passages', color: '#1DB860', data: [...] }]
};

// Compteurs par arrondissement (pour le dropdown mobile)
// Préfixe '[Boucle] ' pour les boucles magnétiques
countersByArrondissement["Le Plateau-Mont-Royal"] = [
    { value: "det-00709-01", label: "Papineau & Rachel (det-00709-01)" },
    { value: "vf-100012217", label: "[Boucle] Rue Rachel Est (vf-100012217)" }
];

// Coordonnées GPS et type de capteur pour la carte
counterLocations['det-00709-01'] = { lat: 45.53, lng: -73.57, label: '...', arrondissement: '...', type: 'fut' };
counterLocations['vf-100012217'] = { lat: 45.52, lng: -73.58, label: '...', arrondissement: '...', type: 'boucle' };

// Compteurs avec lacunes significatives
const gappyCounters = new Set(["det-00077-01", "det-01452-01", "det-13259-02"]);

// Jours anormaux détectés statistiquement
const anomalyDays = { "det-00709-01": { "2026-01-23": { total: 0, expected: 858, z_score: -99 } } };

// Trajets BIXI par compteur par jour (rayon 150 m)
const bixiNearby = { "det-00709-01": { "2026-01-23": 59, ... } };

// Jours où BIXI > compteur
const bixiExceedsDays = { "det-00709-01": { "2026-01-23": { counter: 0, bixi: 59 } } };

// Météo quotidienne de Montréal (Open-Meteo, 6 derniers mois)
const weatherData = { "2026-05-21": { tmax: 15.1, tmin: 7.2, precip: 0, snow: 0, icon: "🌤️" } };
```

---

## Logique JavaScript — `index.html`

Tout le JavaScript est inline dans le HTML généré.

### Variables globales

| Variable                    | Contenu                                                                              |
|-----------------------------|--------------------------------------------------------------------------------------|
| `allChartData`              | Données brutes des 6 derniers mois, par instance                                     |
| `chartData`                 | Données filtrées pour la période active, par instance                                |
| `charts`                    | Instances Chart.js créées (cache)                                                    |
| `markers`                   | Markers Leaflet, par instance                                                        |
| `countersByArrondissement`  | Liste de compteurs par arrondissement (pour dropdown mobile)                         |
| `counterLocations`          | Coordonnées GPS, métadonnées et type (`'fut'` / `'boucle'`) par instance             |
| `globalMaxDate`             | Date la plus récente parmi tous les compteurs — référence commune pour filtrer       |
| `currentPeriod`             | Nombre de jours de la période active (`-1` = tout, défaut : `7`)                    |
| `displayMode`               | `'combined'` (défaut) ou `'separate'` (toggle bi-directionnel)                      |
| `viewMode`                  | `'timeline'` (courbe horaire) ou `'daily'` (barres journalières)                    |
| `showBixi`                  | `false` — overlay BIXI désactivé par défaut                                         |
| `specificDate`              | Date sélectionnée en mode "Jour spécifique" (format `YYYY-MM-DD`)                   |
| `gappyCounters`             | `Set` des instances avec lacunes significatives — injecté par `genMap.py`            |
| `anomalyDays`               | Objet `{instance: {date: {total, expected, z_score}}}` — injecté par `genMap.py`    |
| `bixiNearby`                | Trajets BIXI par compteur par jour — injecté par `genMap.py`                        |
| `bixiExceedsDays`           | Jours où BIXI > compteur — injecté par `genMap.py`                                  |
| `weatherData`               | Météo quotidienne `{date: {tmax, tmin, precip, snow, icon}}` — injecté par `genMap.py` |
| `map`                       | Instance Leaflet (initialisée dans un `setTimeout`)                                  |

### Fonctions principales

#### `buildFilteredData(instance, days)`
Filtre les données horaires pour la période active en se basant sur `globalMaxDate` (pas la date du système). Garantit qu'un compteur avec un CSV en retard affiche quand même ses données récentes.

- `days = 0` : filtre sur `specificDate` uniquement
- `days = -1` : retourne toutes les données sans filtre
- `days > 0` : retourne les N derniers jours à partir de `globalMaxDate`

Selon `displayMode` :
- `'combined'` : somme de toutes les directions en un seul dataset vert (défaut)
- `'separate'` : un dataset par direction (vert / bleu ciel)

#### `buildDailyData(instance, days)`
Agrège les données horaires en totaux journaliers pour le diagramme à barres. Même logique de filtrage et même respect de `displayMode` que `buildFilteredData`, mais produit une barre par jour au lieu d'un point par heure.

#### `createChart(id)`
Crée le graphique Chart.js pour le compteur donné. Si `showBixi` est actif et que `bixiNearby[id]` contient des données pour la période affichée, un dataset BIXI est ajouté :

- **Vue timeline** : step-line rouge pâle pointillée (`stepped: 'before'`), sur l'axe Y principal
- **Vue daily** : barres rouges pâles, sur l'axe Y principal

Dans les deux cas, les valeurs BIXI sont sur la même échelle que les passages du compteur, permettant une comparaison directe.

#### `updateAnomalyWarning(instance)`
Affiche ou masque `#anomalyWarning` selon que des anomalies ou des dépassements BIXI tombent dans la période visible. Pour chaque jour signalé :

- Si `bixiExceedsDays[instance][date]` existe → badge rouge **"⚠ Bixi (N) > compteur (M)"**
- Sinon si `bixiNearby[instance][date] > 0` → mention bleue "✓ N trajets Bixi à proximité"
- Les jours où BIXI dépasse le compteur mais non détectés par l'algorithme statistique sont aussi listés

#### `updateBixiToggle(instance)`
Affiche `#bixiToggle` uniquement pour les compteurs ayant des données BIXI dans `bixiNearby`.

#### `updateDirToggle(instance)`
Affiche `#dirToggle` uniquement pour les compteurs bi-directionnels (détecteurs sur fût uniquement — les boucles magnétiques n'ont pas de direction).

#### `updateViewToggle()`
Affiche ou masque `#viewToggle`. Le toggle est **masqué uniquement pour "Jour spécifique"** (`currentPeriod === 0`).

#### `updateStats(instance)`
Calcule les 3 statistiques en combinant toutes les directions :
- **Passages totaux** : somme des volumes filtrés
- **Moyenne par jour** : total ÷ jours uniques
- **Heure de pointe** : heure cumulant le plus de passages

Les valeurs sont animées avec `animateCount()` (easing `easeOutCubic`, 700 ms).

#### `updateMapSelection(instance)`
Met le marker du compteur sélectionné en **bleu ciel** (`#29ABE2`), les détecteurs sur fût en **vert** (`#1DB860`), et les boucles magnétiques en **violet** (`#8B5CF6`). La couleur de base dépend de `counterLocations[id].type`.

#### `setCounterFromMap(instance)`
Appelé lors d'un clic sur un marker Leaflet. Met à jour les dropdowns desktop et mobile, appelle `selectCounter`, et centre la carte sur le compteur.

#### `buildCombinedFilteredData(inst1, inst2, days)` / `buildCombinedDailyData(inst1, inst2, days)`
Construisent un dataset synthétique en fusionnant les données de deux compteurs. Pour chaque horodatage (ou journée), les volumes des deux sources sont sommés. Les labels résultants sont l'union triée des deux séries — les trous dans l'une des sources contribuent à `0` dans la somme.

#### `haversineM(lat1, lng1, lat2, lng2)`
Calcule la distance entre deux coordonnées GPS en mètres (formule de Haversine). Utilisée par `populateCombineSelect` pour filtrer les compteurs voisins.

#### `populateCombineSelect(instance)`
Peuple le dropdown « Combiner avec… » avec les compteurs situés à ≤ 300 m du compteur sélectionné, en excluant les faux positifs géographiques. Retourne le nombre de voisins valides — si 0, le panneau est masqué. Triés par distance croissante, avec la distance affichée en mètres.

### Sélection responsive des compteurs

| Contexte | Interface |
|----------|-----------|
| Desktop (≥ 768px) | Un seul dropdown avec `<optgroup>` par arrondissement. Dans chaque groupe : détecteurs sur fût en premier (tri alphabétique), puis boucles magnétiques préfixées `[Boucle]` |
| Mobile (< 768px)  | Deux dropdowns en cascade : arrondissement → compteur (même ordre et préfixes) |

Au chargement, le compteur `det-00709-01` (Papineau / Rachel) est sélectionné par défaut.

### Graphiques bi-directionnels

Pour les détecteurs sur fût avec deux directions :
- Par défaut : **mode combiné** — 1 ligne verte avec fill dégradé, somme des deux directions
- Mode séparé : 2 lignes (vert `#1DB860` + bleu ciel `#29ABE2`), légende affichée
- Le toggle "Par direction / Combiné" est visible uniquement pour ces compteurs

Les boucles magnétiques n'ont qu'une seule série ("Passages") — le toggle de direction est masqué pour elles.

### Overlay BIXI

Pour les compteurs ayant au moins une station BIXI dans un rayon de 150 m :
- Un bouton **"Bixi"** rouge apparaît dans la barre de contrôle
- En vue timeline : step-line rouge pâle pointillée superposée à la courbe des passages
- En vue daily : barres rouges pâles sur le même axe Y que les barres du compteur

### Code couleur des graphiques

| Couleur | Signification |
|---------|--------------|
| Vert `#1DB860` | Passages du compteur (normal) |
| Bleu ciel `#29ABE2` | 2e direction (mode séparé) |
| Orange `rgba(245,158,11,...)` | Jour d'anomalie détectée (barres daily) |
| Rouge pâle `rgba(220,38,38,...)` | Données BIXI (overlay) ou données manquantes |

### Carte Leaflet

Initialisée dans un `setTimeout(..., 0)` pour laisser le layout CSS Grid se calculer avant que Leaflet mesure la hauteur du conteneur.

**Code couleur des markers :**

| Couleur | Signification |
|---------|--------------|
| Vert `#1DB860` | Détecteur sur fût (non sélectionné) |
| Violet `#8B5CF6` | Boucle magnétique (non sélectionnée) |
| Bleu ciel `#29ABE2` | Compteur sélectionné (quel que soit le type) |

Une légende est affichée en bas à gauche de la carte. Le tooltip au survol préfixe `📍` pour les fûts et `⬡` pour les boucles. Les badges de type dans le panneau de détail (`<h2>`) sont des liens cliquables vers la source de données correspondante.

### Comportement de l'axe X

| Mode         | Période active   | Axe X                   | Tooltip                              |
|--------------|------------------|-------------------------|--------------------------------------|
| Dans le temps | Jour spécifique | `14:00`, `15:00`…       | `mer. 4 nov. 2025 · 14:00`          |
| Dans le temps | Autres          | `4 nov.`, `5 nov.`…     | `mer. 4 nov. 2025 · 14:00`          |
| Par jour      | Toutes          | `4 nov.`, `5 nov.`…     | `mer. 4 nov. 2025` + météo du jour  |

En mode **Par jour**, le tooltip affiche en plus la météo issue de `weatherData` : icône, température max/min, et précipitations si > 0,5 mm ou neige si > 0,5 cm (ex. `🌧️  12°C / 5°C · 18,3 mm pluie`).

---

## Qualité des données — compteurs avec lacunes

Certains compteurs présentent des interruptions prolongées dans leurs données (compteur hors service, maintenance, etc.). Ces compteurs sont identifiés automatiquement à la génération et signalés visuellement dans l'interface.

### Détection — `has_significant_gaps()`

Exécutée en Python dans `genMap.py` après le filtrage des 6 derniers mois. Un compteur est flaggé si **l'une ou l'autre** condition est vraie dans sa plage active (première à dernière donnée) :

| Critère | Seuil |
|---------|-------|
| Trou consécutif | > 14 jours sans aucune donnée |
| Ratio de jours manquants | > 20 % des jours de la plage active |

> **Pourquoi 14 jours ?** L'analyse du CSV révèle que la quasi-totalité des compteurs ont exactement 7 jours consécutifs manquants — une lacune systémique dans la source de données, non spécifique à un compteur. Le seuil de 14 jours permet d'ignorer ce bruit et de ne signaler que les interruptions réellement significatives.

### Signalisation visuelle

| Élément | Comportement |
|---------|-------------|
| Bannière `#dataWarning` | S'affiche sous le titre du compteur avec un message d'avertissement |

---

## Qualité des données — détection d'anomalies

En plus des lacunes, `genMap.py` détecte les jours dont le volume de passages est anormalement bas par rapport à l'historique du même jour de semaine. Ces jours correspondent probablement à un dysfonctionnement du capteur.

### Détection — `detect_anomalies()`

Exécutée après le filtrage des 6 derniers mois. Pour chaque instance, les totaux journaliers sont calculés en agrégeant toutes les directions et toutes les heures. Seuls les jours avec **≥ 18 heures de données** sont inclus.

Un jour est signalé comme anomalie si l'**une ou l'autre** des deux méthodes suivantes est déclenchée :

**Méthode 1 — Z-score par jour de semaine** (robuste aux tendances long terme) :

| Critère | Formule | Seuil |
|---------|---------|-------|
| Score Z négatif | `z = (taux − μ) / σ` | `z < −2,5` |
| Taux trop faible | `taux / μ` | `< 50 %` de la moyenne |

**Méthode 2 — Jours adjacents** (robuste à la saisonnalité) :

| Critère | Seuil |
|---------|-------|
| Taux vs moyenne ±6 jours | `< 20 %` de la moyenne des jours complets adjacents |
| Cohérence des références | CV (σ/μ) des jours adjacents `< 0,6` |
| Nombre de références | ≥ 4 jours complets dans la fenêtre |

**Jours entièrement absents** : tout jour sans aucune donnée entre la première mesure du compteur et **hier** (`datetime.now() - timedelta(days=1)`) est automatiquement signalé si le volume attendu dépasse le seuil dynamique du compteur. La borne supérieure est étendue à hier (et non limitée à la dernière date dans les données) pour détecter les compteurs qui n'ont pas transmis de données récentes.

**Seuil dynamique** (`min_expected_effective`) : plutôt qu'un seuil fixe, le seuil est calculé à partir de l'historique propre au compteur :

```python
min_expected_effective = max(10, median_journalière × 0.25)
```

où `median_journalière` est la médiane des totaux de passages sur les journées complètes (≥ 18 h). Un plancher absolu de 10 garantit que les compteurs extrêmement peu fréquentés ne génèrent pas de faux positifs. Ce seuil adaptatif permet de détecter des anomalies sur des compteurs à faible trafic (~10–20 passages/jour) qui étaient systématiquement ignorés avec un seuil fixe à 50.

**Suppression météo** : les jours de météo sévère ne sont pas signalés, quelle que soit la méthode. Les critères sont issus de `weather_data` (voir section Météo) :

| Condition | Seuil |
|-----------|-------|
| Précipitations | > 15 mm/jour |
| Neige | > 5 cm/jour |
| Température maximale | < −15 °C |

### Validation croisée BIXI

En complément de la détection statistique, les anomalies sont croisées avec les données BIXI pour les compteurs couverts :

- Si des trajets BIXI sont enregistrés à proximité un jour où le compteur affiche 0 ou presque → le compteur était probablement en panne
- Les jours où **BIXI > compteur** sont signalés même s'ils ne déclenchent pas l'algorithme statistique

### Signalisation visuelle

| Élément | Comportement |
|---------|-------------|
| Bannière `#anomalyWarning` | S'affiche pour les vues **Jour spécifique** et **7 derniers jours**, avec dates suspectes et volumes observés vs attendus |
| Badge rouge `⚠ Bixi (N) > compteur (M)` | Affiché sur chaque jour où BIXI dépasse le compteur |
| Mention bleue BIXI | Affiché quand des trajets BIXI confirment une anomalie sans la dépasser |
| Barres oranges (vue Par jour) | Les barres correspondant à des jours anomaux apparaissent en orange |
| Gaps (vue Dans le temps) | Les jours sans données apparaissent comme des interruptions dans la courbe |

---

## Combinaison de compteurs

Lorsqu'un compteur sélectionné possède un voisin complémentaire à moins de **300 m**, un sélecteur « ⊕ Combiner avec… » apparaît dans l'interface. Il permet de superposer les flux de deux capteurs en un seul graphique (somme des passages).

### Logique de filtrage

1. **Distance de Haversine ≤ 300 m** entre les deux compteurs (coordonnées GPS de `counterLocations`)
2. **Liste d'exclusion manuelle** (`EXCLUDED_COMBINE_PAIRS`) — paires géographiquement proches mais non-complémentaires (rues différentes, doublons, panneaux d'affichage Eco-Display, intersections distinctes sur la même avenue, etc.)

### Paires valides (15)

| Distance | Compteur A | Compteur B |
|----------|-----------|-----------|
| 0 m | Girouard & Terrebonne — Ouest `det-00118-01` | Girouard & Terrebonne `det-00118-02` |
| 0 m | L-H-La-Fontaine & Perras — Est `det-00268-02` | L-H-La-Fontaine & Perras — Ouest `det-00268-03` |
| 0 m | Notre-Dame & Peel — Sud `det-00399-01` | Notre-Dame & Peel — Nord `det-00399-02` |
| 0 m | Côte-Ste-Catherine & Mont-Royal — Ouest `det-00523-01` | Côte-Ste-Catherine & Mont-Royal — Est `det-00523-02` |
| 1 m | REV St-Denis/Castelnau SB `vf-300020816` | REV St-Denis/Castelnau NB `vf-300020817` |
| 5 m | Berri & de la Gauchetière `det-02047-01` | Berri/de la Gauchetière `vf-300046832` |
| 11 m | Rue Verdun côté Nord `vf-300041662` | Rue Verdun côté Sud `vf-300041663` |
| 21 m | Côte-de-Liesse & Lucerne — Sud `det-13259-02` | Côte-de-Liesse & Lucerne — Nord `det-13259-03` |
| 31 m | REV St-Denis/Carrières dir sud `vf-300014985` | REV St-Denis/Carrières dir nord `vf-300014986` |
| 37 m | Henri-Bourassa & Saint-Vital — Ouest `det-08888-01` | Henri-Bourassa & Saint-Vital — Est `det-08888-02` |
| 42 m | Saint-Urbain & Viger `det-00467-01` | Viger / Saint-Urbain `vf-100047030` |
| 43 m | Ottawa & Peel — Nord `det-00401-01` | Ottawa & Peel — Sud `det-00401-02` |
| 51 m | Peel & Saint-Antoine — Nord `det-00402-01` | Peel & Saint-Antoine — Sud `det-00402-02` |
| 169 m | REV St-Denis/Duluth dir nord `vf-300014995` | REV St-Denis/Rachel dir sud `vf-300014996` |
| 269 m | Henri-Bourassa / Tanguay `vf-300061935` | Henri-Bourassa / Loblaws `vf-300064654` |

### Comportement en mode combiné

- Le graphique affiche **une seule courbe verte** (somme des deux flux)
- Les statistiques (total, moyenne/jour, heure de pointe) sont recalculées sur la somme
- La visualisation des anomalies est **désactivée** (les anomalies sont définies par capteur individuel)
- Changer de compteur principal **réinitialise** la combinaison

---

## Tests — `test_data.py`

Suite de validation qui compare directement les CSV et `index.html`. À relancer après chaque régénération du HTML.

```bash
python3 test_data.py
```

Le script charge `cyclistes.csv` et `compteurs.csv` (si présent) pour établir la liste des instances attendues, puis valide :

- Toutes les instances de `cyclistes.csv` sont présentes dans le HTML
- Aucune instance inconnue dans le HTML (les `vf-` de `compteurs.csv` sont acceptées)
- Pour chaque instance `det-` : labels, volumes et nombre de directions correspondent exactement au CSV
- Tous les arrondissements de `cyclistes.csv` ont un `<optgroup>` correspondant (des groupes supplémentaires pour les boucles magnétiques sont acceptés)

---

## Automatisation — Docker

Le conteneur Docker exécute `update.sh` tous les jours à **08h15 heure de Montréal** via cron. Il clone ou met à jour le dépôt GitHub, télécharge les CSV depuis les portails de données ouvertes, régénère `index.html`, valide les données, puis publie si des changements sont détectés.

### Fichiers impliqués

| Fichier            | Rôle                                                                 |
|--------------------|----------------------------------------------------------------------|
| `Dockerfile`       | Image `python:3.11-slim` + git + cron + tqdm. Cron configuré à 08h15 |
| `docker-compose.yml` | Monte `./logs` dans `/var/log`, charge `.env`, redémarre toujours  |
| `entrypoint.sh`    | Exporte les variables d'env vers `/etc/environment` pour que cron y ait accès, puis lance `cron -f` |
| `update.sh`        | Pipeline complet : clone/pull → télécharge les 3 CSV → `genMap.py` → `test_data.py` → commit + push |

### Pipeline `update.sh`

```
1. clone / git pull
2. Scraper + télécharger cyclistes.csv       → CSV_CHANGED       (« Vélos - comptage permanent », même page que les éco-compteurs)
3. Scraper + télécharger bixi.csv (ZIP)      → BIXI_CHANGED
4. Scraper + télécharger compteurs.csv       → COMPTEURS_CHANGED (fichier « comptage_velo_{année}.csv », éco-compteurs)
5. Si aucun changement → exit 0
6. python3 genMap.py
7. python3 test_data.py
8. git add index.html velo_meta_cache.json
9. git commit + git push si index.html a changé
```

`cyclistes.csv` et `compteurs.csv` sont tous les deux scrappés sur `https://donnees.montreal.ca/dataset/velos-comptage`. Le script distingue les deux fichiers : `cyclistes.csv` cherche un lien contenant `permanent` et l'année ; `compteurs.csv` cherche `comptage_velo_{année}` en excluant les liens contenant `permanent`.

`velo_meta_cache.json` est commité dans le dépôt pour que le cache Nominatim persiste entre les runs Docker. Si de nouveaux capteurs apparaissent dans `compteurs.csv` (nouveaux IDs non présents dans le cache), `genMap.py` les géocode lors de la génération et le cache mis à jour est commité dans le même commit que `index.html`.

### Variables d'environnement — `.env`

Copier `.env.example` vers `.env` et remplir les valeurs :

```bash
cp .env.example .env
```

| Variable       | Description                                                     |
|----------------|-----------------------------------------------------------------|
| `GITHUB_TOKEN` | Token GitHub avec permission "Contents: Read & Write"           |
| `GITHUB_REPO`  | Dépôt cible, format `utilisateur/nom-du-depot`                  |
| `GIT_EMAIL`    | Email pour les commits automatiques                             |
| `GIT_NAME`     | Nom pour les commits automatiques                               |

### Démarrer le conteneur

```bash
docker compose build          # À relancer après chaque modification de update.sh ou genMap.py
docker compose up -d          # Démarre en arrière-plan
docker compose logs -f        # Suit les logs en temps réel
```

Les logs d'exécution sont persistés dans `./logs/update.log`.

### Forcer une mise à jour immédiate

```bash
docker compose exec velo-updater /app/update.sh
```

---

## Déploiement — GitHub Pages

Le site est hébergé sur **GitHub Pages** (branche `main`). Le fichier `CNAME` définit le domaine personnalisé (`compteur.gabfortin.com`). Seuls `index.html` et `favico.png` sont servis — tout est statique, sans backend.

Chaque `push` sur `main` déclenche automatiquement le déploiement. En production, c'est `update.sh` qui s'en charge. En développement local :

```bash
python3 genMap.py        # Régénère index.html
python3 test_data.py     # Valide les données
git add index.html velo_meta_cache.json
git commit -m "Mise à jour manuelle"
git push
```
