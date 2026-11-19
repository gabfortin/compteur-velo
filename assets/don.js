// Bouton « Me supporter » (don) dans la barre du haut (DESIGN-PAGES.md, « Bouton
// Me supporter »). Fichier identique dans tous les sites publics de Gabriel,
// sauf les projets faits en collaboration (carte-au, avantapres, locataire).
//
// Le lien est défini ICI seulement : pour le changer, modifier LIEN_DON dans
// outils/communs/don.js, puis lancer outils/recopier_communs.py --appliquer.
//
// Chargé juste après le bouton « i », sans defer : le bouton est inséré avant
// le premier affichage, donc la barre ne bouge pas au chargement.
//   <button class="gf-info-btn" id="gfInfoBtn" …>…</button>
//   <script src="js/don.js"></script>
// Le bouton est placé juste avant le « i ». Un autre point d'ancrage se
// précise sur la balise : <script src="…/don.js" data-avant="#autre"></script>
(function () {
  var LIEN_DON = "https://gabfortin.com/don/";

  var reglages = document.currentScript ? document.currentScript.dataset : {};
  var ancre = document.querySelector(reglages.avant || "#gfInfoBtn");
  if (!ancre || document.querySelector(".gf-don-btn")) return;

  if (!document.getElementById("gf-don-style")) {
    var style = document.createElement("style");
    style.id = "gf-don-style";
    // Même dégradé que le bouton « i » : --gf-info-* (pages) ou --brand-* (cartes).
    style.textContent =
      ".gf-don-btn{display:inline-flex;align-items:center;gap:6px;flex-shrink:0;" +
      "height:32px;padding:0 13px 0 11px;border-radius:999px;" +
      "background:linear-gradient(140deg,var(--gf-info-1,var(--brand-light,#1DB860)),var(--gf-info-2,var(--brand-dark,#129a4c)));" +
      "color:#fff !important;text-decoration:none !important;" +
      "font:600 13px/1 Inter,system-ui,-apple-system,'Segoe UI',sans-serif;white-space:nowrap;" +
      "box-shadow:0 4px 14px -4px var(--gf-info-glow,var(--brand-glow,rgba(18,154,76,.6))),inset 0 1px 0 rgba(255,255,255,.45);" +
      "transition:transform .2s cubic-bezier(.22,.68,0,1),box-shadow .2s cubic-bezier(.22,.68,0,1)}" +
      ".gf-don-btn svg{width:15px;height:15px;flex-shrink:0}" +
      ".gf-don-btn:hover{transform:translateY(-1px) scale(1.05)}" +
      ".gf-don-btn:active{transform:scale(.96)}" +
      ".gf-don-btn:focus-visible{outline:2px solid var(--gf-info-2,var(--brand-dark,#129a4c));outline-offset:3px}" +
      ".gf-don-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}" +
      "@media (max-width:760px){.gf-don-btn{width:28px;height:28px;padding:0;justify-content:center}" +
      ".gf-don-btn svg{width:14px;height:14px}" +
      ".gf-don-texte{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}}" +
      "@media (prefers-reduced-motion:reduce){.gf-don-btn{transition:none}.gf-don-btn:hover{transform:none}}" +
      "@media print{.gf-don-btn{display:none !important}}";
    document.head.appendChild(style);
  }

  var lien = document.createElement("a");
  lien.className = "gf-don-btn";
  lien.href = LIEN_DON;
  lien.target = "_blank";
  lien.rel = "noopener";
  lien.title = "Me supporter (faire un don)";
  lien.innerHTML =
    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false">' +
    '<path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35Z"/></svg>' +
    '<span class="gf-don-texte">Me supporter</span>' +
    '<span class="gf-don-sr"> (s’ouvre dans un nouvel onglet)</span>';
  ancre.parentNode.insertBefore(lien, ancre);
})();
