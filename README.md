# Compteurs Vélo Montréal

Visualisation interactive des passages de cyclistes à Montréal, à partir des données ouvertes de la Ville. Le site affiche un graphique par compteur avec filtres de période, vue temporelle ou journalière, statistiques dynamiques, et une carte OpenStreetMap. Les données BIXI sont croisées avec les compteurs pour valider leur fiabilité.

> ⚠️ Les données proviennent directement du portail de données ouvertes de la Ville de Montréal et de BIXI. Certaines valeurs peuvent être incomplètes ou erronées.

---

## Structure des fichiers

```
compteur-velo/
├── cyclistes.csv        # Source principale (portail données ouvertes Montréal) — ignoré par git
├── bixi.csv             # Données BIXI de l'année en cours (bixi.com/en/open-data) — ignoré par git
├── genMap.py            # Script de génération du site HTML
├── index.html           # Site généré (ne pas modifier manuellement)
├── favico.png           # Icône de l'application
├── test_data.py         # Suite de tests de validation des données
├── update.sh            # Script de mise à jour automatique (télécharge CSV → génère HTML → publie)
├── Dockerfile           # Image Docker pour l'automatisation (cron quotidien)
├── docker-compose.yml   # Orchestration du conteneur d'automatisation
├── entrypoint.sh        # Point d'entrée Docker (injecte les variables dans l'environnement cron)
├── .env                 # Variables d'environnement locales (ignoré par git)
├── .env.example         # Modèle de configuration
├── .gitignore           # Exclut cyclistes.csv, bixi.csv et .env
└── CNAME                # Domaine personnalisé GitHub Pages (compteur.gabfortin.com)
```

---

## Flux de génération

```
cyclistes.csv  ─┐
                ├──  genMap.py  →  index.html  →  GitHub Pages
bixi.csv       ─┘
```

`genMap.py` lit les deux CSV, filtre et transforme les données, puis génère un fichier HTML autonome (CSS + données + JavaScript inline). Il n'y a aucune dépendance serveur : `index.html` s'ouvre directement dans un navigateur.

Pour régénérer le site manuellement après une mise à jour du CSV :

```bash
python3 genMap.py
python3 test_data.py   # Valide les données après génération
```

---

## Source de données — `cyclistes.csv`

Fichier CSV téléchargé depuis le [portail de données ouvertes de la Ville de Montréal](https://donnees.montreal.ca/dataset/cyclistes).

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

---

## Source de données — `bixi.csv`

Fichier CSV téléchargé depuis [bixi.com/en/open-data](https://bixi.com/en/open-data/). Contient tous les trajets BIXI de l'année en cours.

Colonnes utilisées :

| Colonne                    | Description                                 |
|----------------------------|---------------------------------------------|
| `STARTSTATIONLATITUDE`     | Latitude de la station de départ            |
| `STARTSTATIONLONGITUDE`    | Longitude de la station de départ           |
| `ENDSTATIONLATITUDE`       | Latitude de la station d'arrivée            |
| `ENDSTATIONLONGITUDE`      | Longitude de la station d'arrivée           |
| `STARTTIMEMS`              | Horodatage de départ en millisecondes (Unix) |

Les données sont publiées annuellement (non en temps réel). Le fichier de l'année courante peut être remplacé dans le workflow lorsqu'une nouvelle version est publiée.

---

## Script de génération — `genMap.py`

### Étape 1 — Lecture et filtrage du CSV

Les données sont groupées par instance **et par direction** :

```python
data[row['instance']][row['direction']].append(row)
```

18 des 45 compteurs ont deux directions (ex. Est + Ouest). Les regrouper séparément évite les timestamps dupliqués et les volumes gonflés.

Les données sont ensuite filtrées pour ne garder que les 180 derniers jours :

```python
def is_within_last_6_months(date_str):
    clean = re.sub(r'[+-]\d{2}$', '', date_str.strip('"').strip())
    row_date = datetime.fromisoformat(clean)
    return row_date >= datetime.now() - timedelta(days=180)
```

> **Note** : le fuseau horaire (`-05` / `-04`) est retiré avec une regex ciblant la fin de la chaîne. Un simple `.replace('-05', '')` corromprait les dates contenant ces chiffres dans le jour (ex. `2025-10-05`).

Après le filtrage, `has_significant_gaps` analyse chaque compteur pour détecter les lacunes importantes (voir section [Qualité des données](#qualité-des-données--compteurs-avec-lacunes)).

### Étape 2 — Traitement BIXI

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

### Étape 3 — Génération du HTML

Le HTML est construit par concaténation dans `html_parts`, puis écrit dans `index.html`.

**Structure HTML produite :**

```
<html>
  <head>                 ← CSS inline + Chart.js 4.4.4 + Leaflet 1.9.4 (CDN) + Google Analytics
  <body>
    .site-header         ← Logo, titre, sous-titre (liens vers données ouvertes Ville + BIXI), lien gabfortin.com
    .container
      .period-buttons    ← Filtres de période (Jour spécifique / 7j / 30j / 90j / 180j / Tout)
      .select-wrapper.desktop-only
        <select>         ← Dropdown unique groupé par arrondissement (desktop)
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
        #map             ← Carte Leaflet
    <script>             ← Données + logique JS inline
    .watermark           ← Crédit auteur + avertissement qualité des données
```

**Données injectées dans le JS :**

```javascript
// Données horaires par instance et direction
allChartData['det-00077-01'] = {
    labels: ["2025-11-04 14:00", ...],
    datasets: [
        { label: 'Ouest', color: '#1DB860', fill: 'rgba(...)', data: [4, 8, ...] },
        { label: 'Est',   color: '#29ABE2', fill: 'rgba(...)', data: [7, 12, ...] }
    ]
};

// Compteurs par arrondissement (pour le dropdown mobile)
countersByArrondissement["Le Plateau-Mont-Royal"] = [
    { value: "det-00709-01", label: "Papineau & Rachel (det-00709-01)" }
];

// Coordonnées GPS pour la carte
counterLocations['det-00709-01'] = { lat: 45.53, lng: -73.57, label: '...', arrondissement: '...' };

// Compteurs avec lacunes significatives
const gappyCounters = new Set(["det-00077-01", "det-01452-01", "det-13259-02"]);

// Jours anormaux détectés statistiquement
const anomalyDays = { "det-00709-01": { "2026-01-23": { total: 0, expected: 858, z_score: -99 } } };

// Trajets BIXI par compteur par jour (rayon 150 m)
const bixiNearby = { "det-00709-01": { "2026-01-23": 59, ... } };

// Jours où BIXI > compteur
const bixiExceedsDays = { "det-00709-01": { "2026-01-23": { counter: 0, bixi: 59 } } };
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
| `counterLocations`          | Coordonnées GPS et métadonnées par instance                                          |
| `globalMaxDate`             | Date la plus récente parmi tous les compteurs — référence commune pour filtrer       |
| `currentPeriod`             | Nombre de jours de la période active (`-1` = tout, défaut : `7`)                    |
| `displayMode`               | `'combined'` (défaut) ou `'separate'` (toggle bi-directionnel)                      |
| `viewMode`                  | `'timeline'` (courbe horaire) ou `'daily'` (barres journalières)                    |
| `showBixi`                  | `true` (défaut) — affiche ou masque l'overlay BIXI sur les graphiques               |
| `specificDate`              | Date sélectionnée en mode "Jour spécifique" (format `YYYY-MM-DD`)                   |
| `gappyCounters`             | `Set` des instances avec lacunes significatives — injecté par `genMap.py`            |
| `anomalyDays`               | Objet `{instance: {date: {total, expected, z_score}}}` — injecté par `genMap.py`    |
| `bixiNearby`                | Trajets BIXI par compteur par jour — injecté par `genMap.py`                        |
| `bixiExceedsDays`           | Jours où BIXI > compteur — injecté par `genMap.py`                                  |
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
Affiche `#dirToggle` uniquement pour les compteurs bi-directionnels (18 sur 45).

#### `updateViewToggle()`
Affiche ou masque `#viewToggle`. Le toggle est **masqué uniquement pour "Jour spécifique"** (`currentPeriod === 0`).

#### `updateStats(instance)`
Calcule les 3 statistiques en combinant toutes les directions :
- **Passages totaux** : somme des volumes filtrés
- **Moyenne par jour** : total ÷ jours uniques
- **Heure de pointe** : heure cumulant le plus de passages

Les valeurs sont animées avec `animateCount()` (easing `easeOutCubic`, 700 ms).

#### `updateMapSelection(instance)`
Met le marker du compteur sélectionné en bleu ciel (`#29ABE2`), les compteurs normaux en vert (`#1DB860`), et les compteurs avec lacunes en amber (`#F59E0B`).

#### `setCounterFromMap(instance)`
Appelé lors d'un clic sur un marker Leaflet. Met à jour les dropdowns desktop et mobile, appelle `selectCounter`, et centre la carte sur le compteur.

### Sélection responsive des compteurs

| Contexte | Interface |
|----------|-----------|
| Desktop (≥ 768px) | Un seul dropdown avec `<optgroup>` par arrondissement |
| Mobile (< 768px)  | Deux dropdowns en cascade : arrondissement → compteur |

Au chargement, un arrondissement et un compteur sont choisis **aléatoirement**.

### Graphiques bi-directionnels

Pour les 18 compteurs avec deux directions :
- Par défaut : **mode combiné** — 1 ligne verte avec fill dégradé, somme des deux directions
- Mode séparé : 2 lignes (vert `#1DB860` + bleu ciel `#29ABE2`), légende affichée
- Le toggle "Par direction / Combiné" est visible uniquement pour ces compteurs

### Overlay BIXI

Pour les 14 compteurs ayant au moins une station BIXI dans un rayon de 150 m :
- Un bouton **"Bixi"** rouge apparaît dans la barre de contrôle (actif par défaut)
- En vue timeline : step-line rouge pâle pointillée superposée à la courbe des passages
- En vue daily : barres rouges pâles sur le même axe Y que les barres du compteur
- Le bouton masque/affiche l'overlay sans recharger la page

### Code couleur des graphiques

| Couleur | Signification |
|---------|--------------|
| Vert `#1DB860` | Passages du compteur (normal) |
| Bleu ciel `#29ABE2` | 2e direction (mode séparé) |
| Orange `rgba(245,158,11,...)` | Jour d'anomalie détectée (barres daily) |
| Rouge pâle `rgba(220,38,38,...)` | Données BIXI (overlay) |

### Message "aucune donnée"

Quand un compteur n'a aucune donnée dans la fenêtre temporelle sélectionnée :
- Le canvas est masqué
- `#noDataMsg` s'affiche à sa place
- La référence de temps est `globalMaxDate`, commune à tous les compteurs

### Carte Leaflet

Initialisée dans un `setTimeout(..., 0)` pour laisser le layout CSS Grid se calculer avant que Leaflet mesure la hauteur du conteneur. Sur desktop, la carte occupe 320 px de large et s'aligne en hauteur avec le graphique via `display: grid`.

### Comportement de l'axe X

| Mode         | Période active   | Axe X                   | Tooltip                              |
|--------------|------------------|-------------------------|--------------------------------------|
| Dans le temps | Jour spécifique | `14:00`, `15:00`…       | `mer. 4 nov. 2025 · 14:00`          |
| Dans le temps | Autres          | `4 nov.`, `5 nov.`…     | `mer. 4 nov. 2025 · 14:00`          |
| Par jour      | Toutes          | `4 nov.`, `5 nov.`…     | `mer. 4 nov. 2025`                  |

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
| Marker carte | Amber (`#F59E0B`) au lieu du vert, avec `⚠` dans le tooltip au survol |
| Marker sélectionné | Bleu ciel (`#29ABE2`) comme les autres (comportement inchangé) |
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

**Jours entièrement absents** : tout jour sans aucune donnée dans la plage active du compteur est automatiquement signalé si le volume attendu dépasse 50 passages/jour.

### Validation croisée BIXI

En complément de la détection statistique, les anomalies sont croisées avec les données BIXI pour les 14 compteurs couverts :

- Si des trajets BIXI sont enregistrés à proximité un jour où le compteur affiche 0 ou presque → le compteur était probablement en panne
- Les jours où **BIXI > compteur** (204 cas identifiés sur 3 mois) sont signalés même s'ils ne déclenchent pas l'algorithme statistique

```javascript
const bixiExceedsDays = {
    "det-00709-01": {
        "2026-01-23": { "counter": 0, "bixi": 59 }
    }
};
```

### Signalisation visuelle

| Élément | Comportement |
|---------|-------------|
| Bannière `#anomalyWarning` | S'affiche pour les vues **Jour spécifique** et **7 derniers jours**, avec dates suspectes et volumes observés vs attendus |
| Badge rouge `⚠ Bixi (N) > compteur (M)` | Affiché sur chaque jour où BIXI dépasse le compteur |
| Mention bleue BIXI | Affiché quand des trajets BIXI confirment une anomalie sans la dépasser |
| Barres oranges (vue Par jour) | Les barres correspondant à des jours anomaux apparaissent en orange (`rgba(245,158,11,...)`) |
| Gaps (vue Dans le temps) | Les jours sans données apparaissent comme des interruptions dans la courbe |

---

## Tests — `test_data.py`

Suite de validation qui compare directement le CSV et `index.html`. À relancer après chaque régénération du HTML.

```bash
python3 test_data.py
```

---

## Automatisation — Docker

Le conteneur Docker exécute `update.sh` tous les jours à **08h15 heure de Montréal** via cron. Il clone ou met à jour le dépôt GitHub, télécharge le CSV depuis le portail de données ouvertes, régénère `index.html`, valide les données, puis publie si des changements sont détectés.

### Fichiers impliqués

| Fichier            | Rôle                                                                 |
|--------------------|----------------------------------------------------------------------|
| `Dockerfile`       | Image `python:3.11-slim` + git + cron + tqdm. Cron configuré à 08h15 |
| `docker-compose.yml` | Monte `./logs` dans `/var/log`, charge `.env`, redémarre toujours  |
| `entrypoint.sh`    | Exporte les variables d'env vers `/etc/environment` pour que cron y ait accès, puis lance `cron -f` |
| `update.sh`        | Pipeline complet : clone/pull → scrape URL CSV → télécharge CSV → `genMap.py` → `test_data.py` → commit + push |

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
git add index.html
git commit -m "Mise à jour manuelle"
git push
```
