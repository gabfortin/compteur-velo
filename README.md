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
├── bixi_stats.py            # Agrégats BIXI par année (onglet BIXI) — construit bixi_cache.json
├── bixi_cache.json          # Statistiques BIXI des années passées (2014 → année précédente) — commité
├── hist_cache.json          # Données historiques (2009 → année en cours), enrichies chaque jour — commité
├── historique/              # CSV historiques bruts (2009–2025) — ignoré par git, non nécessaire en prod
│   ├── comptage_velo_2009.csv
│   ├── comptagevelo2010.csv … comptagevelo2018.csv
│   ├── comptage_velo_2019.csv … comptage_velo_2025.csv
│   └── localisation_des_compteurs_velo.csv
├── velo_meta_cache.json     # Cache arrondissement (point-in-polygon) + rue des boucles magnétiques
├── data/
│   └── districts-electoraux-2025.json  # Polygones des districts électoraux (→ arrondissement) — Ville de Montréal
├── genMap.py                # Script de génération des données (data/site.json)
├── site_format.py           # Format de data/site.json (encodage des heures) — partagé avec les tests
├── index.html               # Page statique (HTML) — modifiable directement
├── assets/
│   ├── style.css            # Styles de la page
│   ├── boot.js              # Charge data/site.json, construit les menus et les blocs, puis lance app.js
│   └── app.js               # Logique de la page (graphiques, carte, onglets)
├── data/
│   └── site.json            # Données générées chaque jour par genMap.py (ne pas modifier manuellement)
├── favico.png               # Icône de l'application
├── og-image.png             # Image d’aperçu lors du partage (Open Graph, 1200×630)
├── test_data.py             # Validation des données (data/site.json ↔ CSV)
├── test_sources.py          # Audit : chaque valeur publiée recalculée depuis les sources
├── test_site_format.py      # Tests unitaires de site_format.py
├── test_site.py             # Test de la page dans un navigateur (Playwright)
├── test_site_values.py      # Chaque chiffre affiché par la page recalculé (Playwright)
├── .github/workflows/
│   ├── update.yml           # Automatisation principale — cron GitHub Actions quotidien
│   └── tests.yml            # Tests de la page à chaque modification du code
├── fetch_data.sh            # Téléchargement des 3 CSV — partagé entre update.yml et update.sh
├── update.sh                # Pipeline d'automatisation alternatif (auto-hébergement Docker)
├── Dockerfile               # Image Docker pour l'automatisation alternative (cron quotidien)
├── docker-compose.yml       # Orchestration du conteneur d'automatisation alternative
├── entrypoint.sh            # Point d'entrée Docker (injecte les variables dans l'environnement cron)
├── .env                     # Variables d'environnement locales (ignoré par git)
├── .env.example             # Modèle de configuration
├── .gitignore               # Exclut cyclistes.csv, bixi.csv, compteurs.csv, historique/ et .env
└── CNAME                    # Domaine personnalisé GitHub Pages (compteurvelo.com)
```

---

## Flux de génération

```
cyclistes.csv   ─┐
compteurs.csv   ─┤──  genMap.py  →  data/site.json  ─┐
bixi.csv        ─┤                                    ├──  GitHub Pages
hist_cache.json ─┘    index.html + assets/ (statiques) ─┘
```

`genMap.py` lit les CSV des années courante et précédente (fenêtre glissante de 12 mois) et le cache historique, filtre et transforme les données, puis écrit **uniquement** `data/site.json`. La page elle-même (`index.html`, `assets/style.css`, `assets/app.js`) est statique : on peut modifier le style ou le code et pousser directement, sans relancer la génération.

Au chargement, `assets/boot.js` récupère `data/site.json` (revalidé à chaque visite pour toujours afficher les données du jour), construit les menus de compteurs et les blocs de graphiques, puis charge `assets/app.js`.

Pour régénérer les données manuellement après une mise à jour du CSV :

```bash
python3 genMap.py
python3 test_data.py   # Valide les données après génération
```

Pour voir la page en local, il faut un petit serveur (le navigateur bloque le chargement de `data/site.json` depuis un fichier ouvert directement) :

```bash
python3 -m http.server 8000   # puis ouvrir http://localhost:8000
```

### Tests

```bash
python3 -m unittest test_site_format   # Encodage des heures (aucune donnée requise)
python3 test_data.py                   # data/site.json ↔ cyclistes.csv / compteurs.csv
python3 test_sources.py                # Audit complet contre les sources (voir « Tests et audit des données »)
pip install playwright && python3 -m playwright install chromium
python3 test_site.py                   # La page s'affiche sans erreur avec data/site.json
python3 test_site_values.py            # Chaque chiffre affiché = valeur recalculée depuis data/site.json
```

Le workflow quotidien exécute tous ces tests avant de publier : un seul échec bloque la publication. `tests.yml` exécute les tests unitaires et les deux tests de la page à chaque modification du code.

---

## Données historiques

### Vue d'ensemble

Le site intègre des données historiques allant de **2009 à l'année courante** pour alimenter deux fonctionnalités :

1. **Vue Globale — graphique d'évolution annuelle** : un diagramme à barres montrant le total de passages par année (2009–présent) pour tous les compteurs combinés.
2. **Vue Compteur — boutons d'historique** : pour les compteurs disposant de données antérieures, deux boutons apparaissent sous les contrôles du graphique principal :
   - **📊 Comparer avec une année** : sélection de deux années (A et B), affichage du total annuel de chacune pour comparaison directe.
   - **📅 Historique multi-années** : graphique annuel (total par année) ou mensuel (courbe Jan–Déc pour chaque année disponible).

Les boutons sont masqués pour les compteurs qui n'ont pas de données antérieures à l'année courante (ex. nouveaux détecteurs SUM).

### Sources CSV historiques (dossier `historique/`)

Les fichiers historiques couvrent 2009 à 2025. Ils ne sont **pas commités sur GitHub** (trop volumineux) mais servent à générer `hist_cache.json`. Trois formats coexistent :

| Années | Format | Colonnes clés |
|--------|--------|---------------|
| 2009 | Long — 15 min par capteur | `date`, `heure`, `id_compteur`, `nb_passage`, `longitude`, `latitude` |
| 2010–2012, 2015–2018 | Large — colonnes = noms des compteurs, 1 ligne/jour | `Date` (DD/MM/YYYY), puis une colonne par compteur nommé |
| 2013–2014 | Large — sans colonne heure | `Date` (YYYY-MM-DD), puis une colonne par compteur nommé |
| 2019–2025 | Long — 15 min par capteur | `date`, `heure`, `id_compteur`, `nb_passages`, `longitude`, `latitude` |

Pour les fichiers au format large (2010–2018), `genMap.py` fait correspondre les noms de colonnes aux identifiants numériques via `localisation_des_compteurs_velo.csv` et une liste d'alias codés en dur (ex. `"PierDup"` → `100003040`).

### Cache historique — `hist_cache.json`

Fichier JSON pré-calculé (~60 Ko) commité sur GitHub. Contient :

```json
{
  "yearly":          { "2009": 2291276, "2010": 3495345, ..., "2025": 15391883 },
  "counter_monthly": {
    "vf-100003032": {
      "2009": { "1": 2792, "2": 4152, ..., "12": 9955 },
      "2019": { ... },
      ...
    }
  }
}
```

`yearly` : total de passages pour chaque année.  
`yearly_counters` : nombre de compteurs de chaque année.  
`counter_monthly` : passages mensuels par compteur (`vf-…` capteurs au sol, `det-…` capteurs photo/vidéo).  
`live_years` : années tenues à jour depuis les données récentes (voir ci-dessous).

Les années 2009–2025 viennent des fichiers annuels du portail (capteurs au sol seulement) : `counter_monthly` y couvre les fichiers longs (2009, 2019–2025) et certains compteurs nommés des fichiers larges (2010–2018).

### Mise à jour quotidienne du cache

À chaque génération, `genMap.py` enregistre dans le cache les mois **entièrement compris** dans la fenêtre de 12 mois, puis le workflow commite `hist_cache.json` :

- **Années récentes** (commencées après le début de la fenêtre, à partir de 2026 ; listées dans `live_years`) : tous leurs mois sont réécrits depuis les données récentes — tous les compteurs, capteurs photo/vidéo compris — et leur total annuel est recalculé. Une année reste ainsi dans l'historique après le 1er janvier, et ses mois se figent à mesure qu'ils sortent de la fenêtre.
- **Années du portail** (2025 et avant) : leurs totaux ne changent jamais ; seuls les mois absents sont ajoutés (ex. décembre 2025 des capteurs photo/vidéo, pour que la saison d'hiver 2025–26 reste complète).

`test_sources.py` vérifie chaque jour ces règles et compare le cache au dernier commit : aucun mois ni aucune année ne peut disparaître, et les données du portail ne peuvent pas changer.

Le graphique « Évolution annuelle » sépare les deux types de capteurs : pour les années du portail, le total ne compte que les capteurs au sol.

### Logique de chargement dans `genMap.py`

```
1. hist_cache.json présent ? → charger le cache (rapide, ~0 s)
2. Sinon et historique/ présent ? → parser tous les CSV bruts (~26 s), sauvegarder le cache
3. Sinon → ignorer les données historiques (site fonctionne sans)
```

Puis, dans tous les cas : enregistrer les mois récents dans le cache (voir « Mise à jour quotidienne du cache ») et le réécrire.

> **Ne pas régénérer le cache depuis `historique/`** (`rm hist_cache.json`) : les fichiers du portail ne contiennent que les capteurs au sol, et les années récentes enregistrées depuis les données quotidiennes (capteurs photo/vidéo compris) seraient perdues.

---

## Sources de données

### `cyclistes.csv` — Capteurs photo/vidéo (anciennement « Détecteurs SUM ») (comptage permanent)

Fichier CSV construit à partir du [portail de données ouvertes de la Ville de Montréal](https://donnees.montreal.ca/fr/dataset/cyclistes), où les données sont publiées par année sous les titres **« Velos - Comptage AAAA - Ville »** (`cyclistes_AAAA.csv`). `fetch_data.sh` télécharge les fichiers de l'année courante et de l'année précédente et les fusionne dans `cyclistes.csv`. L'ancien fichier unique `cyclistes.csv` du portail n'est plus mis à jour depuis août 2026 ; il ne sert qu'en repli. Contient les passages horaires des détecteurs installés sur des fûts le long des pistes cyclables.

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
| `periode`        | Horodatage ISO (ex. `2026-04-02 14:00` ; `2025-01-01 14:00:00` dans le fichier 2025) |
| `volume`         | Nombre de passages sur la période                                                     |
| `vitesseMoyenne` | Vitesse moyenne des cyclistes (km/h)                                                  |

Seules les lignes `agg_code = "h"` (données horaires) sont utilisées par `genMap.py`.

### `compteurs.csv` — Boucles magnétiques

Fichier CSV téléchargé depuis le [portail de données ouvertes de la Ville de Montréal](https://donnees.montreal.ca/fr/dataset/cyclistes) (titres **« Velos - Comptage AAAA - Eco-Compteur »**). Contient les passages en intervalles de 15 minutes des boucles magnétiques encastrées dans la chaussée.

Colonnes :

| Colonne        | Description                                           |
|----------------|-------------------------------------------------------|
| `date`         | Date (format `YYYY-MM-DD`)                            |
| `heure`        | Heure de début de l'intervalle (format `HH:MM:SS`)   |
| `id_compteur`  | Identifiant numérique du capteur (ex. `100041114`)    |
| `nb_passages`  | Nombre de passages sur l'intervalle de 15 min         |
| `longitude`    | Longitude GPS                                         |
| `latitude`     | Latitude GPS                                          |

Ces données sont publiées par année selon le schéma `comptage_velo_{année}.csv`. `fetch_data.sh` télécharge les fichiers de l'année courante et de l'année précédente et les fusionne dans `compteurs.csv`. Les lignes sans `nb_passages` (présentes dans le fichier 2025) sont ignorées par `genMap.py`.

**Différences avec `cyclistes.csv` :**
- Granularité 15 min (agrégée en horaire par `genMap.py`) vs horaire natif
- Pas de direction, arrondissement ou nom de rue dans le fichier — le nom de rue vient de `localisation_des_compteurs_velo_update.csv`, et l'arrondissement est résolu par point-in-polygon sur `data/districts-electoraux-2025.json` ; le tout est mis en cache dans `velo_meta_cache.json`
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

Les données sont ensuite filtrées pour ne garder que les `WINDOW_DAYS` (365) derniers jours :

```python
WINDOW_DAYS = 365

def is_within_window(date_str):
    clean = re.sub(r'[+-]\d{2}$', '', date_str.strip('"').strip())
    row_date = datetime.fromisoformat(clean)
    return row_date >= datetime.now() - timedelta(days=WINDOW_DAYS)
```

> **Note** : le fuseau horaire éventuel (`-05` / `-04`, présent dans l'ancien `cyclistes.csv`) est retiré avec une regex ciblant la fin de la chaîne. Un simple `.replace('-05', '')` corromprait les dates contenant ces chiffres dans le jour (ex. `2025-10-05`).

### Étape 2 — Intégration de `compteurs.csv` (boucles magnétiques)

`load_velo_full()` charge `compteurs.csv`, agrège les intervalles de 15 min en données horaires, et retourne une structure compatible avec `data{}` :

```python
hour_key = f"{row['date']} {row['heure'][:2]}:00:00"
hourly[cid][hour_key] += int(row['nb_passages'])
```

Les métadonnées manquantes sont résolues sans appel réseau : le nom de rue vient de `localisation_des_compteurs_velo_update.csv`, et l'arrondissement est déterminé par point-in-polygon (`resolve_arrondissement()`) sur les districts électoraux de `data/districts-electoraux-2025.json` — chaque district porte directement le nom de son arrondissement (`NOM_ARR`). Les résultats sont mis en cache dans `velo_meta_cache.json` — les générations suivantes ne recalculent rien pour les capteurs déjà connus.

```python
# Extrait de resolve_arrondissement()
for nom_arr, rings in _load_arrondissement_districts():
    if _point_in_polygon(lng, lat, rings):
        return nom_arr
return 'Montréal'  # hors des 19 arrondissements (ex. Westmount, Mont-Royal…)
```

> **Note historique** : avant mai 2026, l'arrondissement était obtenu par reverse geocoding Nominatim (OpenStreetMap). Ce code a été retiré au profit du fichier de localisation pour les noms de rue, mais la résolution d'arrondissement n'avait pas été remplacée — tous les nouveaux capteurs retombaient alors sur `'Montréal'` par défaut. Le point-in-polygon ci-dessus corrige ce bug.

Les instances sont préfixées `vf-` et fusionnées dans `data{}` :

```python
result[f"vf-{cid}"] = {'N/A': rows}   # Une seule direction, pas de direction connue
data.update(vf_data)
```

Après la fusion, tout le pipeline (détection de lacunes, détection d'anomalies, génération HTML, carte, dropdowns) s'applique uniformément aux deux types de compteurs.

### Étape 3 — Données météo

`fetch_weather_data()` interroge [Open-Meteo](https://open-meteo.com/) (gratuit, sans clé API) en deux passes pour couvrir les 12 derniers mois sans discontinuité :

| Source | URL | Couverture |
|--------|-----|------------|
| API archive (ERA5) | `archive-api.open-meteo.com/v1/archive` | J-367 à J-0 (lag ~5 jours) |
| API forecast | `api.open-meteo.com/v1/forecast` | `past_days=10` (comble le lag de l'archive) |

Les deux résultats sont fusionnés, les données forecast prenant priorité pour les dates récentes.

Champs récupérés par jour : `temperature_2m_max`, `temperature_2m_min`, `precipitation_sum`, `snowfall_sum`, `weather_code` (WMO). Un emoji est calculé depuis le code WMO par `weather_icon()`.

Le résultat est embarqué dans le HTML sous la forme `const weatherData = {...}`, puis utilisé :
1. pour exclure les jours sévères de la détection d'anomalies (`is_bad_weather()`)
2. pour enrichir le tooltip des graphiques journaliers côté JS

### Onglet Arrondissements

Vue calculée entièrement côté navigateur à partir de `allDailyData`, `counterLocations`, `anomalyDays` et `weatherData` : statistiques de l'arrondissement choisi, carte et liste de ses compteurs, passages moyens par jour par mois, effet de la météo, et jours les plus et les moins achalandés avec leur météo. Pour que les totaux soient comparables, les classements ne retiennent que les jours où chaque compteur « en service » (ayant transmis dans les 7 jours avant ou après) a transmis des données, sans anomalie, en excluant la première et la dernière journée de chaque compteur (souvent partielles).

### Onglet BIXI — croissance de l'utilisation

`bixi_stats.py` télécharge les ZIP annuels publiés sur [bixi.com/en/open-data](https://bixi.com/en/open-data/) (formats 2014–2021 et 2022+) et en tire, pour chaque année : trajets par jour, répartition horaire, durée moyenne et répartition des durées, part des membres (jusqu'en 2021), nombre de stations actives et stations de départ les plus populaires. Le résultat est stocké dans `bixi_cache.json` (commité) ; seules les années absentes du cache sont téléchargées, donc l'exécution quotidienne (étape « Historique BIXI » du workflow) ne fait normalement rien, sauf en début d'année.

Chaque année est résumée pour tout le réseau et pour « Montréal seulement » (clé `mtl`) : les trajets partis d'une station située dans l'un des 19 arrondissements de la Ville. Les stations sont situées par point-dans-polygone avec les [limites administratives](https://donnees.montreal.ca/dataset/limites-administratives-agglomeration) des données ouvertes ; si ce fichier est inaccessible, la colonne `STARTSTATIONARRONDISSEMENT` (2022+) sert de repli. Une année du cache sans clé `mtl` est recalculée.

`genMap.py` ajoute l'année en cours (calculée depuis `bixi.csv`, marquée `partial`) et injecte le tout dans `const bixiYearly`. L'onglet propose un filtre « Tout le réseau / Montréal seulement » et affiche les trajets par année, le cumul depuis le 1er janvier, les trajets par mois, un tableau annuel et un explorateur par année (tableau mensuel, heures, jours de semaine, durées, stations).

### Étape 4 — Traitement BIXI

Si `bixi.csv` est présent, `genMap.py` croise chaque trajet BIXI avec les compteurs à proximité via la distance haversine (rayon de 150 m) :

```python
def _haversine_m(lat1, lon1, lat2, lon2):
    # Retourne la distance en mètres entre deux coordonnées GPS
    ...

BIXI_RADIUS_M = 150
```

Pour chaque trajet, si la station de **départ ou d'arrivée** se trouve dans le rayon d'un compteur, ce compteur est incrémenté pour la date du trajet. Un même trajet ne compte qu'une seule fois par compteur, même si les deux stations sont dans son rayon.

Résultat : deux structures publiées dans `data/site.json` :

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

### Étape 5 — Écriture de `data/site.json`

Toutes les données utilisées par la page sont réunies dans le dictionnaire `site_data`, puis écrites dans `data/site.json` (JSON compact, UTF-8).

**Structure de la page statique (`index.html`) :**

```
<html>
  <head>                 ← assets/style.css + Chart.js 4.4.4 + Leaflet 1.9.4 (CDN) + GoatCounter
  <body>
    <nav #topbar>        ← Barre de navigation : liens Pistes / Compteurs + lien profil gabfortin.com
    .site-header         ← Logo, titre, sous-titre (liens vers données ouvertes Ville + BIXI)
    .container
      .period-buttons    ← Filtres de période (Jour spécifique / 7j / 30j / 90j / 180j / 365j)
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
          [N × .table-container]  ← Un div par compteur (masqué par défaut), ajouté par boot.js
                                     Le titre <h2> contient un badge cliquable indiquant le type de capteur
        #map             ← Carte Leaflet avec légende de types
    <script boot.js>     ← Charge data/site.json puis assets/app.js
    .watermark           ← Crédit auteur + avertissement qualité des données + timestamp de génération
```

**Contenu de `data/site.json` :**

Pour alléger les données, le détail horaire n'est conservé que pour les `HOURLY_DAYS` (90) derniers jours de chaque compteur, comptés depuis sa dernière donnée. Au-delà, seuls les totaux journaliers (`allDailyData`) sont publiés, et le profil par heure (`hourProfile`) est précalculé pour les périodes de 6 et 12 mois afin que l'heure de pointe reste exacte.

Les étiquettes horaires sont stockées en plages d'heures consécutives (`hours: [[première étiquette, nombre d'heures], ...]`, voir `site_format.py`) puis redéployées en `labels` par `assets/app.js` : le fichier passe ainsi d'environ 7 Mo à 2 Mo.

Les exemples ci-dessous montrent chaque clé telle que `assets/app.js` l'expose (variables globales de même nom) :

```javascript
const HOURLY_DAYS = 90;

// Données horaires par instance et direction (HOURLY_DAYS derniers jours)
// Dans site.json : { hours: [["2025-11-04 14:00", 2160]], datasets: [...] }
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

// Totaux journaliers sur la fenêtre complète (12 mois) — une série par direction,
// dans le même ordre que allChartData[...].datasets
allDailyData['det-00709-01'] = { labels: ["2025-10-15", ...], data: [[310, ...], [295, ...]] };

// Passages par heure de la journée (0–23), toutes directions, pour les périodes > HOURLY_DAYS
hourProfile['det-00709-01'] = { "180": [12, 4, ...], "365": [20, 7, ...] };

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

// Météo quotidienne de Montréal (Open-Meteo, 12 derniers mois)
const weatherData = { "2026-05-21": { tmax: 15.1, tmin: 7.2, precip: 0, snow: 0, icon: "🌤️" } };
```

---

## Logique JavaScript — `assets/app.js`

La logique de la page est dans `assets/app.js` (statique). Il est chargé par `assets/boot.js` une fois `data/site.json` récupéré, et lit les données dans `window.SITE_DATA`.

### Variables globales

| Variable                    | Contenu                                                                              |
|-----------------------------|--------------------------------------------------------------------------------------|
| `allChartData`              | Données horaires des `HOURLY_DAYS` (90) derniers jours, par instance                 |
| `allDailyData`              | Totaux journaliers des 12 derniers mois, par instance et direction                   |
| `hourProfile`               | Profil horaire précalculé (heure de pointe) pour les périodes de 180 et 365 jours    |
| `chartData`                 | Données filtrées pour la période active, par instance                                |
| `charts`                    | Instances Chart.js créées (cache)                                                    |
| `markers`                   | Markers Leaflet, par instance                                                        |
| `countersByArrondissement`  | Liste de compteurs par arrondissement (pour dropdown mobile)                         |
| `counterLocations`          | Coordonnées GPS, métadonnées et type (`'fut'` / `'boucle'`) par instance             |
| `globalMaxDate`             | Date la plus récente parmi tous les compteurs — référence commune pour filtrer       |
| `currentPeriod`             | Nombre de jours de la période active (`0` = jour spécifique, défaut : `7`)          |
| `displayMode`               | `'combined'` (défaut) ou `'separate'` (toggle bi-directionnel)                      |
| `viewMode`                  | `'timeline'` (courbe horaire) ou `'daily'` (barres journalières)                    |
| `showBixi`                  | `false` — overlay BIXI désactivé par défaut                                         |
| `specificDate`              | Date sélectionnée en mode "Jour spécifique" (format `YYYY-MM-DD`)                   |
| `gappyCounters`             | `Set` des instances avec lacunes significatives — depuis `data/site.json`            |
| `anomalyDays`               | Objet `{instance: {date: {total, expected, z_score}}}` — depuis `data/site.json`    |
| `bixiNearby`                | Trajets BIXI par compteur par jour — depuis `data/site.json`                        |
| `bixiExceedsDays`           | Jours où BIXI > compteur — depuis `data/site.json`                                  |
| `weatherData`               | Météo quotidienne `{date: {tmax, tmin, precip, snow, icon}}` — depuis `data/site.json` |
| `map`                       | Instance Leaflet (initialisée dans un `setTimeout`)                                  |

### Fonctions principales

#### `buildFilteredData(instance, days)`
Filtre les données horaires pour la période active en se basant sur `globalMaxDate` (pas la date du système). Garantit qu'un compteur avec un CSV en retard affiche quand même ses données récentes.

- `days = 0` : filtre sur `specificDate` uniquement (le sélecteur de date est limité à la plage horaire disponible)
- `0 < days ≤ HOURLY_DAYS` : retourne les N derniers jours à partir de `globalMaxDate`
- `days > HOURLY_DAYS` : délègue à `buildDailyData` (données journalières seulement ; la vue timeline est masquée)

Selon `displayMode` :
- `'combined'` : somme de toutes les directions en un seul dataset vert (défaut)
- `'separate'` : un dataset par direction (vert / bleu ciel)

#### `buildDailyData(instance, days)`
Lit les totaux journaliers de `allDailyData` pour le diagramme à barres. Même logique de filtrage et même respect de `displayMode` que `buildFilteredData`, mais produit une barre par jour au lieu d'un point par heure.

#### Écran de chargement
Un overlay `#loading-screen` (HTML/CSS pur, placé en tête du `<body>`) s'affiche pendant le téléchargement de `data/site.json`. Il est retiré à la fin de `assets/app.js`, après l'initialisation de la carte, avec un repli (erreur JS ou 20 s) pour ne jamais bloquer la page. Si les données ne peuvent pas être chargées, `boot.js` y affiche un message d'erreur.

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

Exécutée en Python dans `genMap.py` après le filtrage des 12 derniers mois. Un compteur est flaggé si **l'une ou l'autre** condition est vraie dans sa plage active (première à dernière donnée) :

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

Exécutée après le filtrage des 12 derniers mois. Pour chaque instance, les totaux journaliers sont calculés en agrégeant toutes les directions et toutes les heures. Seuls les jours avec **≥ 18 heures de données** sont inclus.

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

## Tests et audit des données

Principe : chaque valeur est **recalculée indépendamment** (code séparé de `genMap.py` et de `assets/app.js`) puis comparée à ce qui est publié. Quand la source fournit ses propres agrégats, ils servent de référence externe.

### `test_data.py` — données horaires des capteurs photo/vidéo

Compare `data/site.json` à `cyclistes.csv` : labels horaires (`HOURLY_DAYS` derniers jours), volumes, totaux journaliers (12 mois), nombre de directions, présence de chaque compteur dans les menus.

### `test_sources.py` — audit contre les fichiers sources

```bash
python3 test_sources.py                              # après fetch_data.sh et genMap.py (~1 min)
python3 test_sources.py --profond                    # + historique et BIXI des années passées, retéléchargés
BIXI_ANNEES=2024,2025 python3 test_sources.py --profond   # limite les ZIP BIXI téléchargés
```

1. **`cyclistes.csv`** : lignes lisibles, aucune heure publiée deux fois pour une même voie (plusieurs voies d'un même compteur sont additionnées), somme des heures = **total journalier publié par la Ville** pour chaque voie et chaque jour, quarts d'heure = heures, totaux mensuels et annuels de la Ville cohérents, aucun compteur déplacé de plus de 25 m.
2. **`compteurs.csv`** : grille de 15 min, aucun quart d'heure en double, totaux journaliers et horaires de chaque capteur au sol recalculés depuis les quarts d'heure (règle des journées d'au moins 12 h).
3. **Capteurs photo/vidéo** : totaux journaliers et mensuels publiés = **totaux de la Ville**.
4. Profils horaires (6 et 12 mois), localisations, bornes de la fenêtre.
5. Statut de chaque compteur (actif / en retard / inactif), compteurs lacunaires, cohérence de chaque anomalie signalée.
6. Vue globale : totaux d'hier, des 7 derniers jours et du mois, compteurs avec données, top 2, heure de pointe, classement des arrondissements, statut du réseau.
7. Historique : données publiées = `hist_cache.json` ; mois des années récentes = données recalculées ; aucun mois ni aucune année perdus, et totaux du portail inchangés depuis le dernier commit.
8. Météo : valeurs plausibles, complètes, identiques à Open-Meteo (revérifiées en ligne).
9. BIXI : cohérence de chaque année, année en cours **recomptée trajet par trajet** depuis `bixi.csv` (jour de Montréal), trajets à moins de 150 m de chaque compteur.
10. `--profond` : totaux historiques retéléchargés depuis le portail de la Ville, années BIXI recomptées depuis les ZIP officiels.

Les **avertissements** (non bloquants) signalent des limites de méthode — par exemple une année dont le réseau de compteurs diffère de l'année précédente.

### `test_site_values.py` — chiffres affichés par la page

Ouvre la page dans Chromium (Playwright) et compare chaque chiffre affiché à sa valeur recalculée en Python depuis `data/site.json`, selon la définition affichée à l'utilisateur : statistiques de chaque compteur sur 7, 30, 90, 180 et 365 jours ; cartes, classement et graphique annuel de la Vue globale ; cartes, liste des compteurs, météo, jours record et moyennes mensuelles de **chaque arrondissement** ; cartes, saisons, BIXI l'hiver et explorateur de **chaque compteur** de l'onglet Vélo d'hiver ; cartes, tableau annuel et détail mensuel de **chaque année** BIXI (réseau et Montréal). Environ 4 700 vérifications.

---

## Automatisation — GitHub Actions (recommandé)

La méthode principale d'automatisation est un **workflow GitHub Actions** planifié, qui ne nécessite aucun serveur à maintenir. Tout tourne sur l'infrastructure de GitHub, gratuitement pour un dépôt public.

### Fichier — `.github/workflows/update.yml`

```
1. Déclenchement : cron quotidien (13:15 UTC ≈ 08:15 heure de Montréal) ou manuel (workflow_dispatch)
2. actions/checkout      → clone le dépôt dans le runner
3. actions/setup-python  → installe Python 3.11
4. pip install tqdm
5. ./fetch_data.sh        → télécharge cyclistes.csv, bixi.csv, compteurs.csv
6. python3 genMap.py      → lit hist_cache.json + fichiers téléchargés → génère index.html
7. Incrémente version.txt (patch)
8. python3 test_data.py, python3 test_sources.py
9. python3 test_site.py, python3 test_site_values.py
10. git add + commit + push (uniquement si data/site.json a changé)
```

`fetch_data.sh` est un script partagé entre ce workflow et `update.sh` (voir section Docker ci-dessous) — toute la logique de scraping/téléchargement (API CKAN, scraping BIXI, extraction du ZIP) vit dans ce seul fichier pour éviter la duplication.

> **Note sur le fuseau horaire** : la syntaxe `cron` de GitHub Actions est toujours en UTC et ne suit pas les changements d'heure été/hiver. Le déclencheur `15 13 * * *` correspond à 08h15 en heure normale de l'Est (hiver) et glissera à 09h15 en heure avancée de l'Est (été). Ce décalage d'une heure deux fois par an est sans conséquence pour une mise à jour quotidienne.
>
> **Note sur le cache CSV** : contrairement au conteneur Docker (qui persiste les CSV entre les exécutions et ne retélécharge que si le hash change), chaque exécution GitHub Actions démarre sur un runner neuf sans fichiers précédents. `fetch_data.sh` télécharge donc les données à chaque run, et `genMap.py` régénère systématiquement `index.html` — la protection contre les commits inutiles se fait uniquement à l'étape finale (`git diff --staged --quiet`), qui ne publie que si le contenu généré a réellement changé.

### Permissions requises

Le workflow déclare `permissions: contents: write` — aucun secret ni token personnel n'est nécessaire. `actions/checkout` configure automatiquement l'authentification Git avec le `GITHUB_TOKEN` intégré, qui a le droit de push sur le même dépôt grâce à cette permission.

> Si le dépôt a une règle de protection de branche sur `main` qui bloque les push directs (même depuis Actions), il faut soit assouplir la règle pour le bot `github-actions[bot]`, soit passer par un Personal Access Token stocké en secret.

### Déclencher manuellement

Dans l'onglet **Actions** du dépôt GitHub → sélectionner le workflow **« Mise à jour quotidienne »** → **Run workflow**. Équivalent à `workflow_dispatch`, utile pour tester ou forcer une mise à jour hors cron.

### Limites connues des workflows planifiés GitHub Actions

- Un cron peut être retardé de quelques minutes en période de forte charge sur l'infrastructure GitHub — sans conséquence pour un job quotidien.
- GitHub désactive automatiquement les workflows planifiés après **60 jours d'inactivité du dépôt** (aucun push/commit). Comme ce dépôt reçoit des commits automatiques chaque jour où les données changent, ce cas ne devrait pas se présenter ; sinon, il suffit de relancer manuellement une fois pour réactiver le cron.

---

## Automatisation alternative — Docker (auto-hébergement)

Une alternative à GitHub Actions, utile pour auto-héberger l'automatisation sur son propre serveur plutôt que de dépendre de l'infrastructure GitHub. Le conteneur Docker exécute `update.sh` tous les jours à **08h15 heure de Montréal** via cron. Il clone ou met à jour le dépôt GitHub, télécharge les CSV depuis les portails de données ouvertes, régénère `index.html`, valide les données, puis publie si des changements sont détectés.

> ⚠️ Ne pas exécuter les deux méthodes d'automatisation simultanément sur le même dépôt — cela produirait des commits dupliqués (l'un constatant que index.html n'a pas changé après que l'autre l'a déjà publié, ce qui est inoffensif, mais redondant).

### Fichiers impliqués

| Fichier            | Rôle                                                                 |
|--------------------|----------------------------------------------------------------------|
| `Dockerfile`       | Image `python:3.11-slim` + git + cron + tqdm. Cron configuré à 08h15 |
| `docker-compose.yml` | Monte `./logs` dans `/var/log`, charge `.env`, redémarre toujours  |
| `entrypoint.sh`    | Exporte les variables d'env vers `/etc/environment` pour que cron y ait accès, puis lance `cron -f` |
| `update.sh`        | Pipeline complet : clone/pull → `fetch_data.sh` → `genMap.py` → `test_data.py` → commit + push |
| `fetch_data.sh`    | Téléchargement des 3 CSV (partagé avec le workflow GitHub Actions, voir section précédente) |

### Pipeline `update.sh`

```
1. clone / git pull
2. ./fetch_data.sh  (cyclistes.csv → CSV_CHANGED, bixi.csv → BIXI_CHANGED, compteurs.csv → COMPTEURS_CHANGED)
3. Si aucun changement → exit 0
4. python3 genMap.py    → lit hist_cache.json + fichiers live → génère index.html
5. python3 test_data.py
6. git add index.html velo_meta_cache.json
7. git commit + git push si index.html a changé
```

> **Note** : `hist_cache.json` est enrichi à chaque génération et commité avec `data/site.json`. Le dossier `historique/` n'est pas présent sur le serveur de production — `genMap.py` détecte l'absence du dossier et utilise uniquement le cache.

Les URLs de `cyclistes.csv` et `compteurs.csv` sont résolues via l'API CKAN du portail (`package_show` sur l'UUID du dataset [cyclistes](https://donnees.montreal.ca/fr/dataset/cyclistes)). Le script cherche `cyclistes_{année}.csv` (repli : `cyclistes.csv`) et `comptage_velo_{année}.csv`, ainsi que les fichiers de l'année précédente, puis fusionne chaque paire (fenêtre de 12 mois).

`velo_meta_cache.json` est commité dans le dépôt pour que le cache (rue + arrondissement) persiste entre les runs Docker. Si de nouveaux capteurs apparaissent dans `compteurs.csv` (nouveaux IDs non présents dans le cache), `genMap.py` les résout lors de la génération et le cache mis à jour est commité dans le même commit que `index.html`.

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

Le site est hébergé sur **GitHub Pages** (branche `main`). Le fichier `CNAME` définit le domaine personnalisé (`compteurvelo.com`). Seuls `index.html` et `favico.png` sont servis — tout est statique, sans backend.

L'ancien domaine `velo.gabfortin.com` redirige (301) vers `https://compteurvelo.com` : GitHub Pages n'accepte qu'un seul domaine personnalisé par dépôt, la redirection est donc configurée côté DNS / registraire de `gabfortin.com`, pas dans ce dépôt.

Chaque `push` sur `main` déclenche automatiquement le déploiement. En production, c'est le workflow GitHub Actions (`update.yml`) qui s'en charge. En développement local :

```bash
python3 genMap.py        # Régénère index.html
python3 test_data.py     # Valide les données
git add index.html velo_meta_cache.json
git commit -m "Mise à jour manuelle"
git push
```
