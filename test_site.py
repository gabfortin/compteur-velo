"""
Test de fumée de la page dans un vrai navigateur (Playwright + Chromium) :
la page statique charge data/site.json, s'affiche sans erreur JavaScript,
et chaque onglet s'ouvre.

    pip install playwright && python3 -m playwright install chromium
    python3 test_site.py

Variables d'environnement facultatives :
  CHROMIUM_PATH  exécutable Chromium à utiliser au lieu de celui de Playwright
  CDN_DIR        dossier node_modules contenant leaflet et chart.js, servis à
                 la place des CDN (pour tester sans accès réseau)
"""
import functools
import http.server
import json
import os
import sys
import threading

from playwright.sync_api import sync_playwright

from site_format import SITE_DATA_FILE, decode_hour_runs

PASS = "\033[92m PASS\033[0m"
FAIL = "\033[91m FAIL\033[0m"
failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"{PASS}  {name}")
    else:
        print(f"{FAIL}  {name}")
        print(f"       → {detail}")
        failures.append(name)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_server():
    root = os.path.dirname(os.path.abspath(__file__))
    handler = functools.partial(QuietHandler, directory=root)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def route_offline(page, cdn_dir):
    """Sert Leaflet et Chart.js depuis CDN_DIR ; bloque les autres hôtes externes."""
    files = {
        'leaflet@1.9.4/dist/leaflet.js': 'leaflet/dist/leaflet.js',
        'leaflet@1.9.4/dist/leaflet.css': 'leaflet/dist/leaflet.css',
        'chart.js@4.4.4/dist/chart.umd.min.js': 'chart.js/dist/chart.umd.js',
    }

    def handle(route):
        url = route.request.url
        if url.startswith('http://127.0.0.1'):
            return route.continue_()
        for suffix, path in files.items():
            if url.endswith(suffix):
                return route.fulfill(path=os.path.join(cdn_dir, path))
        return route.abort()
    page.route('**/*', handle)


def open_page(browser, base, viewport, cdn_dir, errors):
    page = browser.new_page(viewport=viewport)
    page.on('pageerror', lambda e: errors.append(str(e)))
    if cdn_dir:
        route_offline(page, cdn_dir)
    page.goto(base)
    page.wait_for_function("!document.getElementById('loading-screen')", timeout=30000)
    return page


def main():
    with open(SITE_DATA_FILE, encoding='utf-8') as f:
        site = json.load(f)
    expected_labels = {inst: decode_hour_runs(c['hours']) for inst, c in site['allChartData'].items()}
    n_options = sum(len(v) for v in site['countersByArrondissement'].values())
    first_counter = next(iter(site['allChartData']))

    server, base = start_server()
    cdn_dir = os.environ.get('CDN_DIR')
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or None)

        print("\n=== Ordinateur ===")
        errors = []
        page = open_page(browser, base, {'width': 1366, 'height': 900}, cdn_dir, errors)
        check("Menu des compteurs rempli",
              page.locator('#counterSelectDesktop option[value]:not([value=""])').count() == n_options,
              f"{page.locator('#counterSelectDesktop option').count()} options, {n_options} attendues")
        check("Un bloc de graphique par compteur",
              page.locator('.table-container').count() == len(site['counters']),
              f"{page.locator('.table-container').count()} blocs, {len(site['counters'])} attendus")
        check("Version et date de mise à jour affichées",
              page.text_content('#app-version') == site['version']
              and page.text_content('#updated-at') == site['generatedAt'])

        mismatches = page.evaluate("""expected => Object.keys(expected).filter(id =>
            !allChartData[id] || JSON.stringify(allChartData[id].labels) !== JSON.stringify(expected[id]))""",
                                   expected_labels)
        check("Étiquettes horaires décodées par app.js identiques à site_format.py",
              not mismatches, f"Compteurs en écart : {mismatches[:5]}")

        page.select_option('#counterSelectDesktop', first_counter)
        page.wait_for_timeout(1000)
        check("Sélection d'un compteur : graphique affiché",
              page.locator(f'#chart-{first_counter}').is_visible())
        check("Sélection d'un compteur : statistiques calculées",
              page.text_content('#statTotal').strip() not in ('', '—'),
              f"statTotal = {page.text_content('#statTotal')!r}")
        page.click('.period-btn[data-days="365"]')
        page.wait_for_timeout(500)

        for button, element in [('#nav-global', '#global-page'), ('#nav-arr', '#arr-page'),
                                ('#nav-hiver', '#hiver-page'), ('#nav-bixi', '#bixi-page'),
                                ('#nav-methodo', '#methodo-page'), ('#nav-compteurs', '#main-container')]:
            n_errors = len(errors)
            page.click(button)
            page.wait_for_timeout(500)
            check(f"Onglet {button} affiché sans erreur",
                  page.locator(element).is_visible() and len(errors) == n_errors, errors[n_errors:][:2])
        check("Aucune erreur JavaScript (ordinateur)", not errors, errors[:3])
        page.close()

        print("\n=== Mobile ===")
        errors = []
        page = open_page(browser, base, {'width': 390, 'height': 844}, cdn_dir, errors)
        arr = next(iter(site['countersByArrondissement']))
        page.select_option('#arrondissementSelect', arr)
        page.wait_for_timeout(300)
        counter = site['countersByArrondissement'][arr][0]['value']
        page.select_option('#counterSelectMobile', counter)
        page.wait_for_timeout(1000)
        check("Mobile : arrondissement puis compteur → graphique affiché",
              page.locator(f'#chart-{counter}').is_visible())
        check("Aucune erreur JavaScript (mobile)", not errors, errors[:3])
        page.close()

        print("\n=== Données indisponibles ===")
        page = browser.new_page()
        if cdn_dir:
            route_offline(page, cdn_dir)
        page.route('**/data/site.json', lambda route: route.fulfill(status=404, body='introuvable'))
        page.goto(base)
        page.wait_for_function("document.querySelector('.loading-sub').textContent.includes('Impossible')",
                               timeout=10000)
        check("Message d'erreur si data/site.json est introuvable", True)
        page.close()

        browser.close()
    server.shutdown()

    print("\n" + "=" * 50)
    if failures:
        print(f"\033[91m{len(failures)} test(s) échoué(s) :\033[0m")
        for f in failures:
            print(f"  • {f}")
        sys.exit(1)
    print("\033[92mTous les tests de la page sont passés.\033[0m")


if __name__ == '__main__':
    main()
