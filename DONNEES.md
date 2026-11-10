# Données — Compteurs Vélo Montréal

Sources, historique, qualité des données et audit. Vue d'ensemble : [README.md](README.md).

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
