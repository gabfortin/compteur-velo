// Démarrage de la page : charge data/site.json (généré chaque jour par
// genMap.py), construit les parties de la page qui dépendent des données
// (listes de compteurs, blocs de graphiques, pied de page), puis charge
// assets/app.js, qui lit les données dans window.SITE_DATA.
(function () {
    function showError(message) {
        const ld = document.getElementById('loading-screen');
        if (!ld) return;
        const sub = ld.querySelector('.loading-sub');
        if (sub) sub.textContent = message;
        const wheel = ld.querySelector('.loading-wheel');
        if (wheel) wheel.style.display = 'none';
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
                `<p><strong>Emplacement:</strong> ${escapeHtml(location)}</p>` +
                `<canvas id="chart-${escapeHtml(id)}"></canvas></div>`;
        }).join('');
        document.getElementById('noDataMsg').insertAdjacentHTML('afterend', html);

        document.getElementById('updated-at').textContent = data.generatedAt;
        document.getElementById('app-version').textContent = data.version;
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
