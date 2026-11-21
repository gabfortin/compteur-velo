// Démarrage de la page : charge data/site.json (généré chaque jour par
// genMap.py), construit les parties de la page qui dépendent des données
// (listes de compteurs, blocs de graphiques, pied de page), puis charge
// assets/app.js, qui lit les données dans window.SITE_DATA.
(function () {
    function showError(message) {
        const ld = document.getElementById('loading-screen');
        if (!ld) return;
        ld.classList.add('ch-erreur');          // l'animation s'arrête, le message reste
        const titre = ld.querySelector('.loading-title');
        if (titre) titre.textContent = 'Données indisponibles';
        const sub = ld.querySelector('.loading-sub');
        if (sub) { sub.textContent = message; sub.hidden = false; }
    }

    function escapeHtml(s) {
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function renderShell(data) {
        const byArr = data.countersByArrondissement;
        const arrNames = Object.keys(byArr);

        // Liste des compteurs (ordinateur) : un groupe par arrondissement
        const desktop = document.getElementById('counterSelectDesktop');
        arrNames.forEach(name => {
            const group = document.createElement('optgroup');
            group.label = name;
            byArr[name].forEach(c => group.appendChild(new Option(c.label, c.value)));
            desktop.appendChild(group);
        });

        // Liste des arrondissements (mobile)
        const arrSelect = document.getElementById('arrondissementSelect');
        arrNames.forEach(name => arrSelect.appendChild(new Option(name, name)));

        // Un bloc (titre + canevas) par compteur, à la suite de #noDataMsg
        const typeUrl = 'https://donnees.montreal.ca/dataset/cyclistes';
        const html = data.counters.map(([id, location]) => {
            const isBoucle = id.startsWith('vf-');
            const typeLabel = isBoucle ? 'Capteur au sol' : 'Capteur photo/vidéo';
            const typeCls = isBoucle ? 'boucle' : 'fut';
            return `<div id="${escapeHtml(id)}" class="table-container">` +
                `<h2>Compteur ${escapeHtml(id)} <a href="${typeUrl}" target="_blank" rel="noopener" class="counter-type-badge counter-type-${typeCls}">${typeLabel}</a></h2>` +
                `<p><strong>Emplacement :</strong> ${escapeHtml(location)}</p>` +
                `<canvas id="chart-${escapeHtml(id)}"></canvas></div>`;
        }).join('');
        document.getElementById('noDataMsg').insertAdjacentHTML('afterend', html);

        // Fraîcheur des données sous chaque grand titre (DESIGN-PAGES.md, point vert).
        // generatedAt est en UTC (« AAAA-MM-JJ à HH:MM ») : affiché à l'heure de Montréal.
        const m = /^(\d{4})-(\d{2})-(\d{2}) à (\d{2}):(\d{2})$/.exec(data.generatedAt || '');
        const quand = m ? new Date(Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5])) : null;
        const texteDate = quand
            ? quand.toLocaleDateString('fr-CA', { timeZone: 'America/Toronto', day: 'numeric', month: 'long', year: 'numeric' })
              + ' à ' + quand.toLocaleTimeString('fr-CA', { timeZone: 'America/Toronto', hour: 'numeric', minute: '2-digit' })
            : data.generatedAt;
        document.querySelectorAll('[data-fraicheur]').forEach(el => {
            el.textContent = `Données à jour au ${texteDate} · mises à jour automatiquement chaque jour`;
            el.dataset.genereLe = data.generatedAt;
            el.hidden = false;
        });
        document.getElementById('app-version').textContent = data.version;
        afficheurLogo(data.globalStats && data.globalStats.yesterday);
    }

    // Afficheur du logo (#logo-accueil) : total des passages d'hier, tous les compteurs. C'est le chiffre de
    // la carte « Hier » de la Vue globale (globalStats.yesterday, calculé par genMap.py et vérifié par
    // test_sources.py). Une case par chiffre, comme l'afficheur de l'écran d'attente ; les chiffres roulent
    // jusqu'au total quand l'écran d'attente s'en va (pas d'animation si l'appareil demande moins de mouvement).
    function afficheurLogo(hier) {
        const logo = document.getElementById('logo-accueil');
        const g = document.getElementById('logo-compteur');
        if (!logo || !g || !hier || !(hier.total >= 0)) return;
        const total = Math.round(hier.total), n = String(total).length;
        const NS = 'http://www.w3.org/2000/svg';
        const el = (nom, attrs) => { const e = document.createElementNS(NS, nom); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
        const larg = 52, ecart = 8, marge = 14, haut = 94;
        const largeur = n * larg + (n - 1) * ecart + 2 * marge, x0 = 256 - largeur / 2;
        g.replaceChildren(el('rect', { x: x0, y: 34, width: largeur, height: haut, rx: 18, fill: '#0b1610', opacity: '.88' }));
        const chiffres = el('g', { filter: 'url(#lc-chiffres)' });
        const textes = [];
        for (let i = 0; i < n; i++) {
            const x = x0 + marge + i * (larg + ecart);
            chiffres.appendChild(el('rect', { x, y: 46, width: larg, height: 70, rx: 9, fill: '#0f2017' }));
            const t = el('text', { x: x + larg / 2, y: 100, 'text-anchor': 'middle', fill: '#7cf7b4',
                'font-family': 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', 'font-weight': '800', 'font-size': '56' });
            chiffres.appendChild(t); textes.push(t);
        }
        g.appendChild(chiffres);
        const montrer = v => { const s = String(v).padStart(n, '0'); textes.forEach((t, i) => { t.textContent = s[i]; }); };
        const libelle = `Compteurs Vélo Montréal : ${total.toLocaleString('fr-CA')} passages le ${hier.subtitle || 'jour précédent'}, tous les compteurs`;
        document.getElementById('logo-accueil-titre').textContent = libelle;
        logo.dataset.total = total;

        const sansMouvement = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (sansMouvement) { montrer(total); return; }
        montrer(0);
        const rouler = () => {
            const debut = performance.now(), duree = 1400;
            const pas = t => {
                const k = Math.min(1, (t - debut) / duree);
                montrer(Math.round(total * (1 - Math.pow(1 - k, 3))));
                if (k < 1) requestAnimationFrame(pas);
            };
            requestAnimationFrame(pas);
        };
        // Attendre la fin de l'écran d'attente (retiré par app.js), sinon l'animation se jouerait dessous
        const ecran = document.getElementById('loading-screen');
        if (!ecran) { rouler(); return; }
        let parti = false;
        const go = () => { if (!parti) { parti = true; obs.disconnect(); setTimeout(rouler, 150); } };
        const obs = new MutationObserver(() => { if (!document.getElementById('loading-screen') || ecran.classList.contains('ch-sortie')) go(); });
        obs.observe(document.body, { childList: true });
        obs.observe(ecran, { attributes: true, attributeFilter: ['class'] });
        setTimeout(go, 20000);   // même repli que l'écran d'attente
    }

    const domReady = new Promise(resolve => {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', resolve);
        else resolve();
    });

    // no-cache : le navigateur revalide le fichier à chaque visite (réponse 304
    // si rien n'a changé) pour toujours afficher les données du jour.
    const dataReady = fetch('data/site.json', { cache: 'no-cache' }).then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
    });

    Promise.all([dataReady, domReady]).then(([data]) => {
        window.SITE_DATA = data;
        renderShell(data);
        const script = document.createElement('script');
        script.src = 'assets/app.js?v=' + encodeURIComponent(data.version);
        script.onerror = () => showError('Impossible de charger la page. Réessayez plus tard.');
        document.body.appendChild(script);
    }).catch(err => {
        console.error('Chargement des données impossible :', err);
        showError('Impossible de charger les données. Réessayez plus tard.');
    });
})();
