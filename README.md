# Compteurs Vélo Montréal

Les passages de cyclistes à Montréal, compteur par compteur : graphiques
horaires et journaliers, carte, historique depuis 2009, météo et données BIXI
croisées pour valider la fiabilité des compteurs. Pour les cyclistes, les
élu·es et les journalistes qui veulent suivre l'usage du vélo, y compris
l'hiver.

**En ligne :** [compteurvelo.com](https://compteurvelo.com)

> Les données proviennent directement des portails de données ouvertes de la
> Ville de Montréal et de BIXI, sans correction : certaines valeurs peuvent
> être incomplètes ou erronées. Le site signale les anomalies qu'il détecte.

## Contenu

| Onglet | Ce qu'il montre |
|---|---|
| **Compteurs** | Un graphique par compteur : filtres de période (jour, 7 à 365 jours), vue dans le temps ou par jour, par direction, statistiques, carte ; historique multi-années et comparaison de deux années ; superposition BIXI ; combinaison de deux compteurs voisins |
| **Vue Globale** | Passages agrégés de tous les compteurs actifs : totaux, classement, évolution annuelle depuis 2009 |
| **Arrondissements** | Statistiques, carte et compteurs de chaque arrondissement, effet de la météo, jours les plus et les moins achalandés |
| **Vélo d'hiver** | De décembre à mars : saisons comparées, BIXI l'hiver, explorateur par compteur |
| **BIXI** | La croissance du vélopartage, année après année (réseau et Montréal seulement) |
| **Méthodologie** (fenêtre) | Sources, détection des anomalies, validation croisée avec BIXI |

## Données

### Sources

| Source | Producteur, licence | Utilisée pour |
|---|---|---|
| [Cyclistes](https://donnees.montreal.ca/fr/dataset/cyclistes) (`cyclistes.csv`, capteurs photo/vidéo, et `compteurs.csv`, boucles magnétiques) | Ville de Montréal, CC BY 4.0 | Passages, depuis 2009 (`hist_cache.json` pour les années passées) |
| Districts électoraux 2025 (`data/districts-electoraux-2025.json`) | Ville de Montréal, CC BY 4.0 | Arrondissement de chaque compteur |
| [Données ouvertes de BIXI](https://bixi.com/en/open-data/) | BIXI | Trajets près des compteurs, onglet BIXI (`bixi_cache.json`) |
| [Open-Meteo](https://open-meteo.com) | Open-Meteo, CC BY 4.0 | Météo quotidienne |

Colonnes, filtres, historique, caches et détection des anomalies :
[DONNEES.md](DONNEES.md).

### Mise à jour

**Automatique**, chaque jour à 13 h 15 (UTC), par
[`.github/workflows/update.yml`](.github/workflows/update.yml) :

1. télécharge les trois CSV (`fetch_data.sh`) et complète l'historique BIXI
   (`bixi_stats.py`) ;
2. ajoute 1 à z de la version (`version.txt`) et lance `genMap.py`, qui écrit
   **uniquement** `data/site.json` ;
3. lance tous les tests (voir « Tests ») : **un seul échec bloque la
   publication** ;
4. si les données ont changé : commite
   (`data: mise à jour quotidienne (AAAA-MM-JJ, vX.Y.Z)`) et publie.

Une alternative auto-hébergée (Docker, `update.sh`, même pipeline) est
décrite dans [ARCHITECTURE.md](ARCHITECTURE.md) : ne jamais faire tourner les
deux en même temps.

**À la main**, avec les CSV dans le dossier :

```bash
python3 genMap.py
python3 test_data.py
```

### Mises en garde

- Les compteurs ont des lacunes (pannes, retraits) et des anomalies (jours à
  zéro, pics) : le site les détecte et les signale, sans les corriger ;
  méthode dans [DONNEES.md](DONNEES.md).
- Deux technologies de comptage coexistent (capteurs photo/vidéo et boucles
  magnétiques), avec des historiques différents.
- Initiative indépendante : ni produite ni approuvée par la Ville de Montréal
  ni par BIXI.

## Utilisation locale

```bash
python3 -m http.server 8000   # http://localhost:8000
```

Un serveur est nécessaire : la page charge `data/site.json` par requête réseau.

## Tests

```bash
python3 -m unittest test_site_format   # Encodage des heures (aucune donnée requise)
python3 test_data.py                   # data/site.json ↔ cyclistes.csv / compteurs.csv
python3 test_sources.py                # Audit complet contre les sources
pip install playwright && python3 -m playwright install chromium
python3 test_site.py                   # La page s'affiche sans erreur avec data/site.json
python3 test_site_values.py            # Chaque chiffre affiché = valeur recalculée (≈ 4 700 vérifications)
```

Le robot quotidien les lance tous avant de publier ; `tests.yml` lance les
tests unitaires et les deux tests de la page à chaque modification du code.
Détail de l'audit : [DONNEES.md](DONNEES.md), « Tests et audit des données ».

## Architecture

```
cyclistes.csv   ─┐
compteurs.csv   ─┤──  genMap.py  →  data/site.json  ─┐
bixi.csv        ─┤                                    ├──  GitHub Pages
hist_cache.json ─┘    index.html + assets/ (statiques) ─┘
```

La page est statique : `assets/boot.js` charge `data/site.json`, construit
les menus et les blocs de graphiques, puis charge `assets/app.js`. On peut
modifier le style ou le code et publier sans relancer la génération.

| Fichier | Rôle |
|---|---|
| `genMap.py` | Génération de `data/site.json` (Python, `tqdm` seulement) |
| `site_format.py` | Format de `data/site.json`, partagé avec les tests |
| `bixi_stats.py` | Agrégats BIXI par année (`bixi_cache.json`) |
| `fetch_data.sh` | Téléchargement des trois CSV (robot et Docker) |
| `index.html`, `confidentialite.html` | Pages statiques |
| `assets/boot.js`, `assets/app.js`, `assets/style.css` | Démarrage, logique de la page, styles |
| `assets/apropos.js`, `assets/titre-barre.js` | Composants communs (bouton « i », nom dans la barre) |
| `data/site.json` | Généré chaque jour : ne pas modifier à la main |
| `hist_cache.json`, `bixi_cache.json`, `velo_meta_cache.json` | Caches commités (historique, BIXI, rue et arrondissement) |
| `test_*.py` | Tests et audit |

Style B de **référence** de [DESIGN-PAGES.md](../DESIGN-PAGES.md). Structure
complète, étapes de `genMap.py`, fonctions de `app.js`, automatisation :
[ARCHITECTURE.md](ARCHITECTURE.md).

## Publication

GitHub Pages, branche `main`, domaine `compteurvelo.com` dans `CNAME`.
L'ancien domaine `velo.gabfortin.com` redirige ici grâce au dépôt `velo`.
Version x.y.z et `?v=` écrits par le hook de commit (`version.txt`,
`.version.json`, et `version` de `data/site.json`, qui sert aussi au `?v=`
d'`app.js`). Le robot a besoin de *Settings → Actions → General → Workflow
permissions → Read and write permissions*.

## Confidentialité

Aucun cookie ni compte.

| Quand | Ce qui est envoyé | À qui |
|---|---|---|
| Chaque visite | Page, site d'origine, appareil, pays | GoatCounter (mesure d'audience anonyme) |
| Chaque visite | Chart.js ; Leaflet ; polices | jsDelivr ; unpkg ; Google Fonts |
| Carte | Requêtes de tuiles de la zone affichée | OpenStreetMap (et CyclOSM) |

La météo est téléchargée par le robot, pas par le navigateur. Détails :
[confidentialite.html](confidentialite.html), à mettre à jour (avec sa date)
si un service externe est ajouté.

## Crédits

- [Chart.js](https://www.chartjs.org) 4.4.4 (MIT), chargé depuis jsDelivr
- [Leaflet](https://leafletjs.com) 1.9.4 (BSD-2-Clause), chargé depuis unpkg
- Météo © [Open-Meteo](https://open-meteo.com) (CC BY 4.0)
- Données © Ville de Montréal (CC BY 4.0) et BIXI
- Fond de carte © les contributeurs d'[OpenStreetMap](https://www.openstreetmap.org/copyright) (ODbL), CyclOSM

## Auteur

Développé par [Gabriel Fortin](https://gabfortin.com).

## Documentation détaillée

| Fichier | Contenu |
|---|---|
| [DONNEES.md](DONNEES.md) | Sources en détail, données historiques et caches, lacunes, détection d'anomalies, tests et audit |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Structure des fichiers, génération, logique JavaScript, combinaison de compteurs, robot GitHub Actions et alternative Docker |
