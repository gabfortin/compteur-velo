// Titre dans la barre du haut (DESIGN-PAGES.md) : le texte de la marque
// (.brand-texte) est caché tant que le grand titre de la page est visible, et
// apparaît dès que celui-ci passe sous la barre.
// Fichier identique dans tous les sites (outils/communs/titre-barre.js).
//
// Par défaut : barre = header.site-header, grand titre = .hero h1 (style D).
// Un autre site les précise sur la balise :
//   <script src="…/titre-barre.js" data-barre="#topbar" data-titre="#main-header h1"></script>
//
// Chargé juste après la barre, sans defer : la classe est posée avant le
// premier affichage, donc le titre ne clignote pas au chargement. À ce moment,
// le grand titre n'est pas encore lu : on le cherche au DOMContentLoaded.
(function () {
  const reglages = document.currentScript ? document.currentScript.dataset : {};
  const barre = document.querySelector(reglages.barre || "header.site-header");
  if (!barre || !("IntersectionObserver" in window)) return;

  // Au chargement, on est en haut de page : le grand titre est visible.
  barre.classList.add("titre-en-vue");

  document.addEventListener("DOMContentLoaded", () => {
    const titre = document.querySelector(reglages.titre || ".hero h1");
    if (!titre) {
      barre.classList.remove("titre-en-vue");
      return;
    }

    let observateur;
    const observer = () => {
      observateur?.disconnect();
      // La zone observée commence sous la barre : le titre « sort » quand il passe dessous.
      const bas = Math.max(0, Math.round(barre.getBoundingClientRect().bottom));
      observateur = new IntersectionObserver(([e]) => {
        barre.classList.toggle("titre-en-vue", e.isIntersecting);
      }, { rootMargin: `-${bas}px 0px 0px 0px` });
      observateur.observe(titre);
    };
    observer();
    // La hauteur de la barre change entre mobile et ordinateur.
    window.matchMedia("(max-width: 760px)").addEventListener("change", observer);

    // Les transitions ne s'activent qu'après le premier calcul, pour ne rien animer au chargement.
    requestAnimationFrame(() => requestAnimationFrame(() => barre.classList.add("titre-anime")));
  });
})();
