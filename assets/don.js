// Bouton « Me soutenir » (don) dans la barre du haut (DESIGN-PAGES.md, « Bouton
// Me soutenir »). Fichier identique dans tous les sites publics de Gabriel,
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
    // Comme le bouton « Faire un don » de gabfortin.com (.nav-don) : pilule à
    // contour de 1,5 px, texte de la couleur du texte de la barre, cœur et
    // contour à la couleur du site, fond très pâle au survol ; sans dégradé,
    // ombre ni grossissement. Couleur : --gf-info-trait (pages) ou --brand-ink
    // (cartes), qui suivent le mode sombre ; police : celle du site.
    var TRAIT = "var(--gf-info-trait,var(--brand-ink,var(--gf-info-2,#1a9455)))";
    style.textContent =
      ".gf-don-btn{display:inline-flex;align-items:center;gap:6px;flex-shrink:0;" +
      "height:32px;padding:0 13px 0 11px;border-radius:999px;box-sizing:border-box;" +
      "border:1.5px solid " + TRAIT + ";background:transparent;" +
      "color:inherit !important;text-decoration:none !important;" +
      "font-family:inherit;font-size:13px;font-weight:600;line-height:1;white-space:nowrap;" +
      "transition:background-color .2s ease}" +
      ".gf-don-btn svg{width:15px;height:15px;flex-shrink:0;color:" + TRAIT + "}" +
      ".gf-don-btn:hover{background:color-mix(in srgb," + TRAIT + " 10%,transparent)}" +
      ".gf-don-btn:focus-visible{outline:2px solid " + TRAIT + ";outline-offset:3px}" +
      ".gf-don-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}" +
      "@media (max-width:760px){.gf-don-btn{width:28px;height:28px;padding:0;justify-content:center}" +
      ".gf-don-btn svg{width:14px;height:14px}" +
      ".gf-don-texte{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}}" +
      "@media (prefers-reduced-motion:reduce){.gf-don-btn{transition:none}}" +
      "@media print{.gf-don-btn{display:none !important}}";
    document.head.appendChild(style);
  }

  var lien = document.createElement("a");
  lien.className = "gf-don-btn";
  lien.href = LIEN_DON;
  lien.target = "_blank";
  lien.rel = "noopener";
  lien.title = "Me soutenir (faire un don)";
  lien.innerHTML =
    '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-linejoin="round" aria-hidden="true" focusable="false">' +
    '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>' +
    '<span class="gf-don-texte">Me soutenir</span>' +
    '<span class="gf-don-sr"> (s’ouvre dans un nouvel onglet)</span>';
  // Clic compté dans GoatCounter comme un événement « clic-don/<hôte> » (le
  // panneau admin en fait le total par site ; annoncé dans la page de
  // confidentialité). Rien n'est envoyé si GoatCounter n'est pas chargé
  // (bloqueur de contenu, serveur local) : le lien s'ouvre quand même.
  lien.addEventListener("click", function () {
    var gc = window.goatcounter;
    if (gc && typeof gc.count === "function") {
      gc.count({ path: "clic-don/" + location.host, title: "Me soutenir", event: true });
    }
  });
  ancre.parentNode.insertBefore(lien, ancre);
})();
