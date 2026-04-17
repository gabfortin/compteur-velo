# Compteurs Vélo Montréal

Visualisation interactive des passages de cyclistes à Montréal, à partir des données ouvertes de la Ville. Le site affiche un graphique horaire par compteur, avec filtres de période, statistiques dynamiques, et une carte OpenStreetMap.

---

## Structure des fichiers

```
compteur-velo/
├── cyclistes.csv     # Source de données (portail données ouvertes Montréal)
├── genMap.py         # Script de génération du site HTML
├── index.html        # Site généré (ne pas modifier manuellement)
├── favico.png        # Icône de l'application
├── test_data.py      # Suite de tests de validation des données
└── CNAME             # Domaine personnalisé GitHub Pages
```

---

## Flux de génération

```
cyclistes.csv  →  genMap.py  →  index.html
```

`genMap.py` lit le CSV, filtre et transforme les données, puis génère un fichier HTML autonome (CSS + données + JavaScript inline). Il n'y a aucune dépendance serveur : `index.html` s'ouvre directement dans un navigateur.

Pour régénérer le site après une mise à jour du CSV :

```bash
python3 genMap.py
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

Seules les lignes `agg_code = "h"` (données horaires) sont utilisées.

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

### Étape 2 — Génération du HTML

Le HTML est construit par concaténation dans `html_parts`, puis écrit dans `index.html`.

**Structure HTML produite :**

```
<html>
  <head>                 ← CSS inline + Chart.js 4.4.4 + Leaflet 1.9.4 (CDN) + Google Analytics
  <body>
    .site-header         ← Logo, titre, sous-titre (lien vers données ouvertes), lien gabfortin.com
    .container
      .period-buttons    ← Filtres de période (1j / 7j / 30j / 90j / 180j)
      .select-wrapper.desktop-only
        <select>         ← Dropdown unique groupé par arrondissement (desktop)
      .mobile-only
        <select>         ← Dropdown arrondissement (mobile)
        <select>         ← Dropdown compteur (mobile, peuplé dynamiquement)
      .stats-row         ← 3 cartes : passages totaux, moyenne/jour, heure de pointe
      .dir-toggle        ← Toggle "Par direction / Combiné" (bi-directionnels uniquement)
      #chart-map-layout  ← Grid 2 colonnes sur desktop, empilé sur mobile
        #chart-area      ← Div contenant les table-containers
          [N × .table-container]  ← Un div par compteur (masqué par défaut)
        #map             ← Carte Leaflet
    <script>             ← Données + logique JS inline
    .watermark
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
```

---

## Logique JavaScript — `index.html`

Tout le JavaScript est inline dans le HTML généré.

### Variables globales

| Variable                    | Contenu                                                              |
|-----------------------------|----------------------------------------------------------------------|
| `allChartData`              | Données brutes des 6 derniers mois, par instance                     |
| `chartData`                 | Données filtrées pour la période active, par instance                |
| `charts`                    | Instances Chart.js créées (cache)                                    |
| `markers`                   | Markers Leaflet, par instance                                        |
| `countersByArrondissement`  | Liste de compteurs par arrondissement (pour dropdown mobile)         |
| `counterLocations`          | Coordonnées GPS et métadonnées par instance                          |
| `currentPeriod`             | Nombre de jours de la période active (défaut : `7`)                  |
| `displayMode`               | `'separate'` ou `'combined'` (toggle bi-directionnel)                |
| `map`                       | Instance Leaflet (initialisée dans un `setTimeout`)                  |

### Fonctions principales

#### `buildFilteredData(instance, days)`
Filtre les données pour la période active **à partir de la date la plus récente disponible** (pas d'aujourd'hui). Garantit l'affichage même si le CSV n'est pas à jour.

Selon `displayMode` :
- `'separate'` : retourne un dataset par direction (vert / bleu ciel)
- `'combined'` : somme toutes les directions en un seul dataset vert

#### `selectCounter(instance)`
Affiche le graphique du compteur sélectionné, met à jour les stats, le toggle directionnel, et la carte.

#### `updateMapSelection(instance)`
Met le marker du compteur sélectionné en bleu ciel (`#29ABE2`) et les autres en vert (`#1DB860`).

#### `setCounterFromMap(instance)`
Appelé lors d'un clic sur un marker. Met à jour les dropdowns desktop et mobile, appelle `selectCounter`, et centre la carte sur le compteur.

#### `updateStats(instance)`
Calcule les 3 statistiques en combinant toutes les directions :
- **Passages totaux** : somme des volumes filtrés
- **Moyenne par jour** : total ÷ jours uniques
- **Heure de pointe** : heure cumulant le plus de passages

Les valeurs sont animées avec `animateCount()` (easing `easeOutCubic`, 700 ms).

#### `filterDataByPeriod(days)`
Change la période active : reconstruit `chartData` pour toutes les instances (en tenant compte de `displayMode`), détruit et recrée le graphique actif.

### Sélection responsive des compteurs

| Contexte | Interface |
|----------|-----------|
| Desktop (≥ 768px) | Un seul dropdown avec `<optgroup>` par arrondissement |
| Mobile (< 768px)  | Deux dropdowns en cascade : arrondissement → compteur |

Au chargement, un arrondissement et un compteur sont choisis **aléatoirement**.

### Graphiques bi-directionnels

Pour les 18 compteurs avec deux directions :
- Par défaut : **2 lignes** sur le graphique (vert `#1DB860` + bleu ciel `#29ABE2`), légende affichée
- Mode combiné : **1 ligne** verte avec fill dégradé, somme des deux directions
- Un toggle "Par direction / Combiné" apparaît uniquement pour ces compteurs

### Carte Leaflet

Initialisée dans un `setTimeout(..., 0)` pour laisser le layout CSS Grid se calculer avant que Leaflet mesure la hauteur du conteneur. Sur desktop, la carte occupe 320px de large et s'aligne en hauteur avec le graphique via `display: grid`.

### Comportement de l'axe X

| Période active | Axe X           | Tooltip                            |
|----------------|-----------------|------------------------------------|
| Dernier jour   | `14:00`, `15:00`… | `mer. 4 nov. 2025 · 14:00`       |
| Autres         | `4 nov.`, `5 nov.`… | `mer. 4 nov. 2025 · 14:00`     |

---

## Tests — `test_data.py`

Suite de validation qui compare directement le CSV et `index.html`.

```bash
python3 test_data.py
```

Les tests doivent être relancés après chaque regénération du HTML.

---

## Déploiement

Le site est hébergé sur **GitHub Pages**. Le fichier `CNAME` définit le domaine personnalisé (`compteur.gabfortin.com`). Seuls `index.html` et `favico.png` sont servis — tout est statique, sans backend.

Après une mise à jour du CSV :

```bash
python3 genMap.py        # Regénère index.html
python3 test_data.py     # Valide les données
# Puis commit + push → déploiement automatique
```
