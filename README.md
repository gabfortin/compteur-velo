# Compteurs Vélo Montréal

Visualisation interactive des passages de cyclistes à Montréal, à partir des données ouvertes de la Ville. Le site affiche un graphique horaire par compteur, avec filtres de période et statistiques dynamiques.

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

Fichier CSV téléchargé depuis le portail de données ouvertes de la Ville de Montréal.

**1 423 501 lignes**, colonnes :

| Colonne         | Description                                              |
|-----------------|----------------------------------------------------------|
| `agg_code`      | Niveau d'agrégation : `h` (heure), `d` (jour), `m` (mois), `y` (année), `f` (total) |
| `instance`      | Identifiant unique du compteur (ex. `det-00077-01`)      |
| `longitude`     | Longitude GPS                                            |
| `latitude`      | Latitude GPS                                             |
| `arrondissement`| Arrondissement de Montréal                               |
| `rue_1`         | Rue principale                                           |
| `rue_2`         | Rue secondaire / intersection                            |
| `numeroVoie`    | Numéro de voie du compteur                               |
| `direction`     | Direction comptée (Nord, Sud, Est, Ouest)                |
| `periode`       | Horodatage ISO avec fuseau horaire (ex. `2025-11-04 14:00:00-05`) |
| `volume`        | Nombre de passages sur la période                        |
| `vitesseMoyenne`| Vitesse moyenne des cyclistes (km/h)                     |

Seules les lignes `agg_code = "h"` (données horaires) sont utilisées par le site.

---

## Script de génération — `genMap.py`

### Étape 1 — Lecture et filtrage du CSV

```python
# Garde uniquement les lignes horaires
if row['agg_code'] == 'h':
    data[row['instance']].append(row)
```

```python
# Garde uniquement les 180 derniers jours
def is_within_last_6_months(date_str):
    clean = re.sub(r'[+-]\d{2}$', '', date_str.strip('"').strip())
    row_date = datetime.fromisoformat(clean)
    return row_date >= datetime.now() - timedelta(days=180)
```

> **Note importante** : le fuseau horaire (`-05` / `-04`) est retiré avec une regex qui cible uniquement la fin de la chaîne. Un simple `.replace('-05', '')` corrompt les dates contenant ces chiffres dans le jour (ex. `2025-10-05`).

Résultat : un dictionnaire `data` — `instance → [rows]` — avec 45 compteurs et ~180 jours × 24h de données chacun.

### Étape 2 — Génération du HTML

Le HTML est construit par concaténation de chaînes dans `html_parts`, puis écrit dans `index.html`.

**Structure HTML produite :**

```
<html>
  <head>              ← CSS inline + Chart.js (CDN)
  <body>
    .site-header      ← Logo, titre, sous-titre
    .container
      .period-buttons ← Boutons de filtre (1j / 7j / 30j / 90j / 180j)
      .select-wrapper
        <select>      ← Dropdown groupé par arrondissement (<optgroup>)
      .stats-row      ← 3 cartes de stats (masquées par défaut)
      [N × .table-container]  ← Un div par compteur (masqué par défaut)
        <h2>          ← Nom du compteur
        <p>           ← Emplacement
        <canvas>      ← Graphique Chart.js
    <script>          ← Données + logique JS inline
    .watermark
```

**Génération du dropdown :**

Les options sont groupées par arrondissement (`<optgroup>`) et triées alphabétiquement. Format de chaque option :

```
Rue1 & Rue2 — Direction (det-XXXXX-XX)
```

**Injection des données dans le JS :**

Toutes les données sont injectées dans le HTML comme objet JavaScript global :

```javascript
allChartData['det-00077-01'] = {
    labels: ["2025-11-04 14:00", "2025-11-04 15:00", ...],
    data:   [4, 8, ...]
};
```

Les labels sont tronqués à 16 caractères (`periode[:16]`) pour obtenir le format `YYYY-MM-DD HH:MM`.

---

## Logique JavaScript — `index.html`

Tout le JavaScript est inline dans le HTML généré. Il n'y a aucun fichier `.js` séparé.

### Données

| Variable      | Contenu                                                            |
|---------------|--------------------------------------------------------------------|
| `allChartData`| Toutes les données brutes des 6 derniers mois, par instance        |
| `chartData`   | Données filtrées pour la période active, par instance              |
| `charts`      | Instances Chart.js créées, par instance (cache)                    |
| `currentPeriod` | Nombre de jours de la période active (défaut : `7`)              |

### Fonctions principales

#### `parseLabel(label)`
Convertit un label `"YYYY-MM-DD HH:MM"` en objet `Date` JS.
```javascript
// L'espace doit être remplacé par T pour que new Date() parse correctement
return new Date(label.replace(' ', 'T'));
```

#### `getMaxDate(labels)`
Retourne la date la plus récente d'un tableau de labels. Utilisée comme ancre pour le filtre de période.

#### `buildFilteredData(instance, days)`
Filtre les données d'une instance pour ne garder que les `days` derniers jours **à partir de la date la plus récente disponible** (et non à partir d'aujourd'hui). Cela garantit que le bouton "Dernier jour" affiche toujours des données, même si le CSV n'est pas à jour.

```javascript
const cutoffDate = new Date(
    maxDate.getFullYear(),
    maxDate.getMonth(),
    maxDate.getDate() - (days - 1)   // minuit du premier jour inclus
);
```

#### `createChart(id)`
Crée un graphique Chart.js pour un compteur. Le graphique est mis en cache dans `charts[id]` et n'est créé qu'une seule fois par instance. À la création, un dégradé vertical est appliqué comme couleur de fond.

#### `filterDataByPeriod(days)`
Change la période active : reconstruit `chartData` pour toutes les instances, détruit et recrée le graphique actif.

#### `updateStats(instance)`
Calcule et affiche les 3 statistiques pour la période active :
- **Passages totaux** : somme des volumes filtrés
- **Moyenne par jour** : total ÷ nombre de jours uniques
- **Heure de pointe** : heure (0–23h) cumulant le plus de passages

Les valeurs numériques sont animées avec `animateCount()` (easing `easeOutCubic`, 700 ms).

#### Sélection aléatoire au chargement
Une IIFE choisit un compteur au hasard parmi les 45 et l'affiche au chargement de la page.

### Comportement de l'axe X

| Période active | Axe X affiche     | Tooltip affiche                    |
|----------------|-------------------|------------------------------------|
| Dernier jour   | `14:00`, `15:00`… | `mer. 4 nov. 2025 · 14:00`         |
| Autres         | `4 nov.`, `5 nov.`…| `mer. 4 nov. 2025 · 14:00`        |

---

## Tests — `test_data.py`

Suite de validation qui compare directement le CSV et `index.html`.

```bash
python3 test_data.py
```

### Tests exécutés

| Catégorie           | Test                                                      |
|---------------------|-----------------------------------------------------------|
| Couverture          | Toutes les instances CSV sont présentes dans le HTML      |
| Couverture          | Aucune instance dans le HTML absente du CSV               |
| Données par instance| Nombre de points identique (CSV vs HTML)                  |
| Données par instance| Labels identiques et dans le même ordre                   |
| Données par instance| Volumes identiques point par point                        |
| Données par instance| Format des labels (`YYYY-MM-DD HH:MM`)                    |
| Données par instance| Aucune donnée hors de la fenêtre 6 mois                   |
| Données par instance| Somme des volumes cohérente                               |
| Dropdown            | Label de chaque option contient `rue_1`, `rue_2`, direction |
| Optgroups           | Tous les arrondissements sont présents comme `<optgroup>` |

Les tests doivent être relancés après chaque regénération du HTML.

---

## Déploiement

Le site est hébergé sur **GitHub Pages**. Le fichier `CNAME` définit le domaine personnalisé. Seul `index.html` (et `favico.png`) sont servis — tout est statique, sans backend.

Après une mise à jour du CSV :

```bash
python3 genMap.py        # Regénère index.html
python3 test_data.py     # Valide les données
# Puis commit + push → déploiement automatique
```
