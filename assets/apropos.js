// Bouton « i » + modale « À propos » (DESIGN-PAGES.md). Fichier identique dans
// tous les sites hors cartes. Attend #gfInfoBtn, #gfInfoOverlay et #gfInfoClose.
// - [data-copie-de="id"] dans la modale : reçoit le texte de l'élément #id
//   (ex. la date de mise à jour du pied de page) à chaque ouverture.
// - [data-ferme-apropos] : lien interne (ex. #methodologie) qui ferme la modale.
(function () {
  function init() {
    var btn = document.getElementById('gfInfoBtn');
    var overlay = document.getElementById('gfInfoOverlay');
    var closeBtn = document.getElementById('gfInfoClose');
    if (!btn || !overlay || !closeBtn) return;

    function ouvrir() {
      overlay.querySelectorAll('[data-copie-de]').forEach(function (cible) {
        var source = document.getElementById(cible.getAttribute('data-copie-de'));
        var texte = source ? source.textContent.trim() : '';
        if (texte && texte !== '—' && texte !== '…') cible.textContent = texte;
      });
      overlay.classList.add('open');
      document.documentElement.style.overflow = 'hidden';
      closeBtn.focus();
    }
    function fermer(rendreFocus) {
      if (!overlay.classList.contains('open')) return;
      overlay.classList.remove('open');
      document.documentElement.style.overflow = '';
      if (rendreFocus !== false) btn.focus();
    }

    btn.addEventListener('click', ouvrir);
    closeBtn.addEventListener('click', function () { fermer(); });
    overlay.addEventListener('click', function (e) { if (e.target === overlay) fermer(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') fermer(); });
    overlay.querySelectorAll('[data-ferme-apropos]').forEach(function (lien) {
      lien.addEventListener('click', function () { fermer(false); });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
