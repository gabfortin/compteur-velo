/* carte.js — composant de carte commun à tous les projets de ~/Developer (modèle : outils/communs/carte/).
   Une API compatible avec Leaflet 1.9 (objet global L : L.map, L.polyline, L.geoJSON, L.marker, L.popup…),
   dessinée par MapLibre GL 5 (maplibre-gl.js, à charger avant ce fichier) sur le fond vectoriel OpenFreeMap
   (style Positron, ou Dark en mode sombre). Remplace Leaflet et les tuiles de tile.openstreetmap.org, qui
   répondaient parfois « 403 Access blocked » (novembre 2026). Guide : ~/Developer/MAP-DESIGN-SYSTEM.md.

   Chargement, dans cet ordre (balises link puis script classiques, sans type="module") :
     feuilles de style vendor/carte/maplibre-gl.css, puis vendor/carte/carte.css ;
     scripts vendor/carte/maplibre-gl.js, puis vendor/carte/carte.js, puis le code de la page.
   Réglage facultatif, avant carte.js : window.CARTE = { fond: 'auto' | 'clair' | 'sombre' } (défaut : auto, selon
   le mode du système ou data-theme sur <html>, suivi en direct), ou l'option { fond } de L.map() ; map.setFond('clair').

   Fonctionnement :
   - chaque volet (« pane » de Leaflet : overlayPane, markerPane…, ou createPane) a une source GeoJSON et cinq
     couches MapLibre (surfaces, traits, pointillés, cercles, symboles), empilées selon le z-index du volet ;
     couleur, épaisseur, opacité, pointillés sont propres à chaque élément ; l'ordre d'ajout est l'ordre de dessin ;
   - marqueurs, fenêtres (popups), infobulles : éléments HTML placés par maplibregl.Marker, avec les classes et la
     structure de Leaflet (carte.css), pour que le CSS des sites s'y applique tel quel ;
   - clics et survol des tracés : queryRenderedFeatures autour du pointeur (tolérance de 8 px, 12 au doigt) ;
   - zoom : compté comme Leaflet (MapLibre, en tuiles de 512 px, compte un niveau de moins) ;
   - un tracé n'a pas d'élément HTML (getElement() rend null) : pour le clavier, prévoir une liste à côté.
   Pas pris en charge : édition de tracés par Leaflet.draw/Editable, autres projections que Web Mercator. */
(function () {
  'use strict';
  const ml = window.maplibregl;
  if (!ml) throw new Error('carte.js : maplibre-gl.js doit être chargé avant');

  const ECART_ZOOM = 1;                       // zoom Leaflet = zoom MapLibre + 1
  const RAYON_TERRE = 6371000;
  const REGLAGE = window.CARTE || {};
  const TACTILE = matchMedia('(pointer: coarse)').matches;
  const STYLES = { clair: 'https://tiles.openfreemap.org/styles/positron', sombre: 'https://tiles.openfreemap.org/styles/dark' };
  // Crédit du fond (obligatoire) ; sur une carte étroite (moins de 600 px), « ⓘ © OpenStreetMap » qui s'ouvre au clic
  const ATTRIBUTION = '<button type="button" class="attr-bouton" aria-label="Crédits de la carte" title="Crédits de la carte" aria-expanded="false">i</button>' +
    '<span class="attr-plein"><a href="https://maplibre.org/" target="_blank" rel="noopener">MapLibre</a> | ' +
    '<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> © ' +
    '<a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">OpenMapTiles</a> </span>© ' +
    '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';
  // Fonds dont OpenFreeMap tient lieu (mêmes données OpenStreetMap ; CARTO light_all/dark_all = Positron/Dark,
  // qui exigent maintenant une clé) : ignorés
  const TUILES_OSM = /(^|\/\/|\.)(tile\.openstreetmap\.(org|fr)|openstreetmap\.fr\/osmfr|tile\.osm\.org|basemaps\.cartocdn\.com)/;

  // ---------- Utilitaires et classes (comme L.Util, L.Class) ----------
  let derniereId = 0;
  const stamp = o => o._leaflet_id || (o._leaflet_id = ++derniereId);
  const extend = (dest, ...srcs) => { for (const s of srcs) if (s) for (const k in s) dest[k] = s[k]; return dest; };
  function setOptions(obj, options) {
    if (!Object.prototype.hasOwnProperty.call(obj, 'options')) obj.options = obj.options ? Object.create(obj.options) : {};
    for (const k in options) obj.options[k] = options[k];
    return obj.options;
  }
  const Util = { extend, setOptions, stamp, bind: (f, o) => f.bind(o), falseFn: () => false,
    template: (s, d) => s.replace(/\{ *([\w_ -]+) *\}/g, (m, k) => d[k] !== undefined ? d[k] : ''),
    isArray: Array.isArray, trim: s => s.trim(), splitWords: s => s.trim().split(/\s+/), emptyImageUrl: 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=' };

  function Class() {}
  Class.extend = function (props) {
    const Parent = this;
    function NewClass(...args) {
      Object.defineProperty(this, '_initHooksCalled', { value: false, writable: true });
      if (this.initialize) this.initialize(...args);
      this.callInitHooks();
    }
    NewClass.__super__ = Parent.prototype;
    const proto = NewClass.prototype = Object.create(Parent.prototype);
    proto.constructor = NewClass;
    for (const k in Parent) if (Object.prototype.hasOwnProperty.call(Parent, k) && k !== 'prototype' && k !== '__super__') NewClass[k] = Parent[k];
    if (props.statics) extend(NewClass, props.statics);
    if (props.includes) for (const m of [].concat(props.includes)) extend(proto, m);
    for (const k in props) if (k !== 'statics' && k !== 'includes' && k !== 'options') proto[k] = props[k];
    if (props.options) proto.options = extend(Object.create(Parent.prototype.options || {}), props.options);
    proto._initHooks = [];
    return NewClass;
  };
  Class.include = function (props) { const o = this.prototype.options; extend(this.prototype, props); if (props.options) this.prototype.options = extend(Object.create(o || {}), props.options); return this; };
  Class.mergeOptions = function (o) { extend(this.prototype.options, o); return this; };
  Class.addInitHook = function (fn, ...a) { const f = typeof fn === 'function' ? fn : function () { this[fn](...a); }; (this.prototype._initHooks = this.prototype._initHooks || []).push(f); return this; };
  Class.prototype.callInitHooks = function () {
    if (this._initHooksCalled) return;
    this._initHooksCalled = true;
    const chaine = [];
    for (let p = Object.getPrototypeOf(this); p && p !== Class.prototype; p = Object.getPrototypeOf(p)) if (Object.prototype.hasOwnProperty.call(p, '_initHooks')) chaine.unshift(p._initHooks);
    for (const hooks of chaine) for (const h of hooks) h.call(this);
  };

  // ---------- Coordonnées ----------
  function LatLng(lat, lng, alt) { this.lat = +lat; this.lng = +lng; if (alt !== undefined) this.alt = +alt; }
  LatLng.prototype = {
    distanceTo(o) {
      o = latLng(o);
      const r = Math.PI / 180, l1 = this.lat * r, l2 = o.lat * r;
      const a = Math.sin((o.lat - this.lat) * r / 2) ** 2 + Math.cos(l1) * Math.cos(l2) * Math.sin((o.lng - this.lng) * r / 2) ** 2;
      return RAYON_TERRE * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    },
    equals(o, marge) { o = latLng(o); const m = marge === undefined ? 1e-9 : marge; return !!o && Math.abs(o.lat - this.lat) <= m && Math.abs(o.lng - this.lng) <= m; },
    clone() { return new LatLng(this.lat, this.lng, this.alt); },
    toString() { return 'LatLng(' + this.lat + ', ' + this.lng + ')'; },
    wrap() { return this.clone(); },
    toBounds(m) { const dlat = m / 2 / 111320, dlng = dlat / Math.cos(this.lat * Math.PI / 180); return new LatLngBounds([this.lat - dlat, this.lng - dlng], [this.lat + dlat, this.lng + dlng]); }
  };
  LatLng.prototype.constructor = LatLng;
  function latLng(a, b, c) {
    if (a instanceof LatLng) return a;
    if (a == null) return a;
    if (Array.isArray(a) && typeof a[0] !== 'object') return a.length >= 2 ? new LatLng(a[0], a[1], a[2]) : null;
    if (typeof a === 'object' && 'lat' in a) return new LatLng(a.lat, 'lng' in a ? a.lng : a.lon, a.alt);
    if (b === undefined) return null;
    return new LatLng(a, b, c);
  }
  function Point(x, y, arrondi) { this.x = arrondi ? Math.round(x) : x; this.y = arrondi ? Math.round(y) : y; }
  Point.prototype = {
    add(o) { o = point(o); return new Point(this.x + o.x, this.y + o.y); },
    subtract(o) { o = point(o); return new Point(this.x - o.x, this.y - o.y); },
    multiplyBy(k) { return new Point(this.x * k, this.y * k); },
    divideBy(k) { return new Point(this.x / k, this.y / k); },
    distanceTo(o) { o = point(o); return Math.hypot(o.x - this.x, o.y - this.y); },
    round() { return new Point(Math.round(this.x), Math.round(this.y)); },
    floor() { return new Point(Math.floor(this.x), Math.floor(this.y)); },
    equals(o) { o = point(o); return o.x === this.x && o.y === this.y; },
    clone() { return new Point(this.x, this.y); }
  };
  Point.prototype.constructor = Point;
  function point(x, y, r) {
    if (x instanceof Point) return x;
    if (Array.isArray(x)) return new Point(x[0], x[1]);
    if (x && typeof x === 'object') return new Point(x.x, x.y);
    if (x == null) return x;
    return new Point(x, y, r);
  }
  const estPoint = a => a instanceof LatLng || (Array.isArray(a) && typeof a[0] === 'number') || (a && typeof a === 'object' && !Array.isArray(a) && 'lat' in a);
  function LatLngBounds(a, b) {
    this._s = this._w = Infinity; this._n = this._e = -Infinity;
    if (!a) return;
    if (b !== undefined && estPoint(a)) { this.extend(a); this.extend(b); } else this.extend(a);
  }
  LatLngBounds.prototype = {
    extend(o) {
      if (!o) return this;
      if (o instanceof LatLngBounds) { if (o.isValid()) { this.extend([o._s, o._w]); this.extend([o._n, o._e]); } return this; }
      if (estPoint(o)) {
        const p = latLng(o);
        this._s = Math.min(this._s, p.lat); this._n = Math.max(this._n, p.lat);
        this._w = Math.min(this._w, p.lng); this._e = Math.max(this._e, p.lng);
        return this;
      }
      if (Array.isArray(o)) for (const x of o) this.extend(x);
      return this;
    },
    isValid() { return this._s <= this._n && this._w <= this._e; },
    getCenter() { return new LatLng((this._s + this._n) / 2, (this._w + this._e) / 2); },
    getSouthWest() { return new LatLng(this._s, this._w); }, getNorthEast() { return new LatLng(this._n, this._e); },
    getNorthWest() { return new LatLng(this._n, this._w); }, getSouthEast() { return new LatLng(this._s, this._e); },
    getSouth() { return this._s; }, getNorth() { return this._n; }, getWest() { return this._w; }, getEast() { return this._e; },
    contains(o) {
      if (o instanceof LatLngBounds || (Array.isArray(o) && Array.isArray(o[0]))) { o = latLngBounds(o); return o._s >= this._s && o._n <= this._n && o._w >= this._w && o._e <= this._e; }
      const p = latLng(o);
      return p.lat >= this._s && p.lat <= this._n && p.lng >= this._w && p.lng <= this._e;
    },
    intersects(o) { o = latLngBounds(o); return o._n >= this._s && o._s <= this._n && o._e >= this._w && o._w <= this._e; },
    pad(r) { const h = (this._n - this._s) * r, w = (this._e - this._w) * r; return new LatLngBounds([this._s - h, this._w - w], [this._n + h, this._e + w]); },
    equals(o) { o = latLngBounds(o); return !!o && o._s === this._s && o._n === this._n && o._w === this._w && o._e === this._e; },
    toBBoxString() { return [this._w, this._s, this._e, this._n].join(','); },
    _lngLat() { return [[this._w, this._s], [this._e, this._n]]; }
  };
  LatLngBounds.prototype.constructor = LatLngBounds;
  const latLngBounds = (a, b) => a instanceof LatLngBounds ? a : new LatLngBounds(a, b);
  function Bounds(a, b) { this.min = point(a); this.max = point(b || a); }
  // Projection Web Mercator (pixels de Leaflet : 256 × 2^zoom)
  const projeter = (ll, z) => { const s = 256 * Math.pow(2, z), p = latLng(ll), y = Math.log(Math.tan(Math.PI / 4 + Math.max(-85.0511, Math.min(85.0511, p.lat)) * Math.PI / 360));
    return new Point((p.lng + 180) / 360 * s, (1 - y / Math.PI) / 2 * s); };
  const deprojeter = (pt, z) => { const s = 256 * Math.pow(2, z), p = point(pt);
    return new LatLng(Math.atan(Math.sinh(Math.PI * (1 - 2 * p.y / s))) * 180 / Math.PI, p.x / s * 360 - 180); };
  const CRS = { EPSG3857: { code: 'EPSG:3857', project: ll => projeter(ll, 0), unproject: p => deprojeter(p, 0), distance: (a, b) => latLng(a).distanceTo(b),
    latLngToPoint: (ll, z) => projeter(ll, z), pointToLatLng: (p, z) => deprojeter(p, z), scale: z => 256 * Math.pow(2, z), zoom: s => Math.log(s / 256) / Math.LN2 } };
  CRS.EPSG900913 = CRS.EPSG3857; CRS.Earth = { R: RAYON_TERRE, distance: CRS.EPSG3857.distance };

  // ---------- Événements ----------
  const Events = {
    on(types, fn, ctx) {
      if (typeof types === 'object') { for (const t in types) this.on(t, types[t], fn); return this; }
      this._ecouteurs = this._ecouteurs || {};
      for (const t of types.split(/\s+/)) if (t) (this._ecouteurs[t] = this._ecouteurs[t] || []).push({ fn, ctx });
      if (this._surOn) this._surOn(types);
      return this;
    },
    off(types, fn, ctx) {
      if (!this._ecouteurs) return this;
      if (!types) { this._ecouteurs = {}; return this; }
      if (typeof types === 'object') { for (const t in types) this.off(t, types[t], fn); return this; }
      for (const t of types.split(/\s+/)) if (this._ecouteurs[t]) this._ecouteurs[t] = fn ? this._ecouteurs[t].filter(e => e.fn !== fn || (ctx && e.ctx !== ctx)) : [];
      return this;
    },
    once(types, fn, ctx) {
      if (typeof types === 'object') { for (const t in types) this.once(t, types[t], fn); return this; }
      const g = (...a) => { this.off(types, g); fn.apply(ctx || this, a); };
      return this.on(types, g);
    },
    fire(type, data, propager) {
      const ev = extend({}, data, { type, target: (data && data.target) || this, sourceTarget: (data && data.sourceTarget) || this });
      for (const e of ((this._ecouteurs && this._ecouteurs[type]) || []).slice()) e.fn.call(e.ctx || this, ev);
      if (propager && this._parents) for (const p of this._parents) p.fire(type, extend({ layer: this, propagatedFrom: this }, ev), true);
      return this;
    },
    listens(type) { return !!(this._ecouteurs && this._ecouteurs[type] && this._ecouteurs[type].length); }
  };
  Events.addEventListener = Events.on; Events.removeEventListener = Events.off; Events.fireEvent = Events.fire; Events.clearAllEventListeners = function () { return this.off(); };
  const Evented = Class.extend(Events);

  // ---------- Couches ----------
  const Layer = Evented.extend({
    options: { pane: 'overlayPane', attribution: null, bubblingMouseEvents: true },
    addTo(carte) { carte.addLayer(this); return this; },
    remove() { if (this._map) this._map.removeLayer(this); return this; },
    removeFrom(c) { if (c) c.removeLayer(this); return this; },
    getPane(nom) { return this._map ? this._map.getPane(nom || this.options.pane) : null; },
    // Couche sur mesure (L.Layer.extend({ onAdd, onRemove })) : onAdd reçoit la carte, ses volets sont des éléments HTML
    _ajouter(carte) { if (this.onAdd) this.onAdd(carte); },
    _retirer(carte) { if (this.onRemove) this.onRemove(carte); },
    getAttribution() { return this.options.attribution; },
    // Fenêtres et infobulles liées (contenu : texte HTML, élément, ou fonction(couche))
    bindPopup(contenu, options) {
      if (contenu instanceof Popup) { this._popup = contenu; contenu._source = this; }
      else { this._popup = new Popup(options, this); this._popup.setContent(contenu); }
      return this;
    },
    unbindPopup() { this._popup = null; return this; },
    getPopup() { return this._popup; },
    setPopupContent(c) { if (this._popup) this._popup.setContent(c); return this; },
    isPopupOpen() { return !!(this._popup && this._popup.isOpen()); },
    openPopup(ll) {
      const p = this._popup || this._popupDuParent();
      const carte = this._map || (this._parentAvecCarte() || {})._map;
      if (!p || !carte) return this;
      p._source = this;
      p.setLatLng(ll ? latLng(ll) : this._ancre());
      carte.openPopup(p);
      return this;
    },
    closePopup() { const p = this._popup; if (p && p._map) p._map.closePopup(p); return this; },
    togglePopup() { return this.isPopupOpen() ? this.closePopup() : this.openPopup(); },
    bindTooltip(contenu, options) {
      if (this._tooltip && this._tooltip._map) this._tooltip.remove();
      this._tooltip = contenu instanceof Tooltip ? contenu : new Tooltip(options, this);
      if (!(contenu instanceof Tooltip)) this._tooltip.setContent(contenu);
      this._tooltip._source = this;
      if (this._map && this._tooltip.options.permanent) this.openTooltip();
      return this;
    },
    unbindTooltip() { if (this._tooltip) this._tooltip.remove(); this._tooltip = null; return this; },
    getTooltip() { return this._tooltip; },
    setTooltipContent(c) { if (this._tooltip) this._tooltip.setContent(c); return this; },
    openTooltip(ll) {
      const t = this._tooltip || this._tooltipDuParent();
      const carte = this._map || (this._parentAvecCarte() || {})._map;
      if (!t || !carte) return this;
      t._source = this;
      t.setLatLng(ll ? latLng(ll) : this._ancreBulle());
      if (!t._map) carte.addLayer(t); else t._majContenu();
      return this;
    },
    closeTooltip() { if (this._tooltip && this._tooltip._map) this._tooltip.remove(); return this; },
    isTooltipOpen() { return !!(this._tooltip && this._tooltip._map); },
    toggleTooltip() { return this.isTooltipOpen() ? this.closeTooltip() : this.openTooltip(); },
    _ancre() { return this._latlng; },
    _ancreBulle() { return this._ancre(); },
    _parentAvecCarte() { return (this._parents ? [...this._parents] : []).find(p => p._map || p._parentAvecCarte()); },
    _popupDuParent() { for (const p of this._parents || []) { const x = p._popup || p._popupDuParent(); if (x) return x; } return null; },
    _tooltipDuParent() { for (const p of this._parents || []) { const x = p._tooltip || p._tooltipDuParent(); if (x) return x; } return null; },
    // Après ajout : fire('add'), infobulle permanente
    _apresAjout() {
      const t = this._tooltip || this._tooltipDuParent();
      if (t && t.options.permanent) this.openTooltip();
      this.fire('add');
    }
  });

  // Tracés (polyline, polygon, circleMarker, circle) : entités GeoJSON du volet
  const Path = Layer.extend({
    options: { stroke: true, color: '#3388ff', weight: 3, opacity: 1, lineCap: 'round', lineJoin: 'round', dashArray: null,
      fill: false, fillColor: null, fillOpacity: 0.2, interactive: true, bubblingMouseEvents: true },
    _ajouter(carte) { carte._ajouterVecteur(this); this._apresAjout(); },
    _retirer(carte) { carte._retirerVecteur(this); if (this._tooltip && this._tooltip._map) this._tooltip.remove(); },
    setStyle(style) { setOptions(this, style); if (this._map) this._map._planifier(this._volet()); return this; },
    redraw() { if (this._map) this._map._planifier(this._volet()); return this; },
    bringToFront() { this._ordre = ++derniereId; return this.redraw(); },
    bringToBack() { this._ordre = -(++derniereId); return this.redraw(); },
    getElement() { return null; },
    _volet() { return this.options.pane || 'overlayPane'; },
    _proprietes(carte) {
      const o = this.options, w = o.stroke === false ? 0 : o.weight;
      const tirets = o.dashArray ? String(o.dashArray).split(/[\s,]+/).filter(Boolean).map(Number) : null;
      return { i: stamp(this), s: this._ordre !== undefined ? this._ordre : stamp(this), c: o.color, w, o: o.stroke === false ? 0 : o.opacity,
        cap: o.lineCap || 'round', join: o.lineJoin || 'round',
        dk: tirets && tirets.length && w ? carte._cleTirets(tirets.map(v => v / Math.max(w, 0.1))) : '',
        fc: o.fillColor || o.color, fo: o.fill ? o.fillOpacity : 0 };
    }
  });
  const enLatLngs = ll => Array.isArray(ll) && ll.length && !estPoint(ll[0]) ? ll.map(enLatLngs) : (ll || []).map(p => latLng(p));
  const Polyline = Path.extend({
    initialize(latlngs, options) { setOptions(this, options); this._latlngs = enLatLngs(latlngs || []); },
    getLatLngs() { return this._latlngs; },
    setLatLngs(ll) { this._latlngs = enLatLngs(ll); return this.redraw(); },
    addLatLng(ll, anneau) { (anneau || (estPoint(this._latlngs[0]) || !this._latlngs.length ? this._latlngs : this._latlngs[0])).push(latLng(ll)); return this.redraw(); },
    isEmpty() { return !this._latlngs.length; },
    getBounds() { return new LatLngBounds(this._latlngs); },
    getCenter() { return this.getBounds().getCenter(); },
    _ancre() { const t = this._parties()[0] || []; return t[Math.floor(t.length / 2)] || this.getCenter(); },
    _parties() { const l = this._latlngs; return !l.length ? [] : estPoint(l[0]) ? [l] : l.flatMap(x => estPoint(x[0]) ? [x] : x); },
    _entites(carte) {
      const pr = this._proprietes(carte);
      return this._parties().filter(t => t.length > 1).map(t => ({ type: 'Feature', properties: pr,
        geometry: { type: 'LineString', coordinates: t.map(p => [p.lng, p.lat]) } }));
    },
    toGeoJSON() { const p = this._parties().map(t => t.map(q => [q.lng, q.lat]));
      return { type: 'Feature', properties: (this.feature && this.feature.properties) || {}, geometry: p.length === 1 ? { type: 'LineString', coordinates: p[0] } : { type: 'MultiLineString', coordinates: p } }; }
  });
  const Polygon = Polyline.extend({
    options: { fill: true },
    // Anneaux : [anneau] ou [[anneau, trou…], …] (multipolygone)
    _polygones() {
      const l = this._latlngs;
      if (!l.length) return [];
      if (estPoint(l[0])) return [[l]];
      if (estPoint(l[0][0])) return [l];
      return l;
    },
    _parties() { return this._polygones().flatMap(p => p); },
    _entites(carte) {
      const pr = this._proprietes(carte);
      return this._polygones().filter(p => p[0] && p[0].length > 2).map(p => ({ type: 'Feature', properties: pr,
        geometry: { type: 'Polygon', coordinates: p.map(a => { const c = a.map(q => [q.lng, q.lat]); c.push(c[0]); return c; }) } }));
    },
    getCenter() { return this.getBounds().getCenter(); },
    _ancre() { return this.getCenter(); },
    toGeoJSON() { const p = this._polygones().map(x => x.map(a => { const c = a.map(q => [q.lng, q.lat]); c.push(c[0]); return c; }));
      return { type: 'Feature', properties: (this.feature && this.feature.properties) || {}, geometry: p.length === 1 ? { type: 'Polygon', coordinates: p[0] } : { type: 'MultiPolygon', coordinates: p } }; }
  });
  const Rectangle = Polygon.extend({ initialize(b, o) { b = latLngBounds(b); Polygon.prototype.initialize.call(this, [b.getSouthWest(), b.getNorthWest(), b.getNorthEast(), b.getSouthEast()], o); } });
  const CircleMarker = Path.extend({
    options: { radius: 10, fill: true },
    initialize(ll, options) { setOptions(this, options); this._latlng = latLng(ll); this._radius = this.options.radius; },
    getLatLng() { return this._latlng; },
    setLatLng(ll) { this._latlng = latLng(ll); this.fire('move', { latlng: this._latlng }); return this.redraw(); },
    setRadius(r) { this.options.radius = this._radius = r; return this.redraw(); },
    getRadius() { return this._radius; },
    getBounds() { return new LatLngBounds(this._latlng, this._latlng); },
    setStyle(s) { Path.prototype.setStyle.call(this, s); if (s && s.radius !== undefined) this._radius = s.radius; return this; },
    _entites(carte) {
      const pr = this._proprietes(carte);
      return [{ type: 'Feature', geometry: { type: 'Point', coordinates: [this._latlng.lng, this._latlng.lat] },
        properties: extend({}, pr, { r: this._radius, sc: this.options.color, sw: pr.w, so: pr.o }) }];
    },
    toGeoJSON() { return { type: 'Feature', properties: (this.feature && this.feature.properties) || {}, geometry: { type: 'Point', coordinates: [this._latlng.lng, this._latlng.lat] } }; }
  });
  // Cercle en mètres : polygone de 64 côtés
  const Circle = Polygon.extend({
    initialize(ll, options, o2) {
      if (typeof options === 'number') options = extend({}, o2, { radius: options });
      setOptions(this, options); this._latlng = latLng(ll); this._mRadius = this.options.radius || 10; this._majCercle();
    },
    _majCercle() {
      const c = this._latlng, r = this._mRadius, pts = [];
      for (let k = 0; k < 64; k++) {
        const a = k / 64 * 2 * Math.PI;
        pts.push(new LatLng(c.lat + r * Math.cos(a) / 111320, c.lng + r * Math.sin(a) / (111320 * Math.cos(c.lat * Math.PI / 180))));
      }
      this._latlngs = pts;
    },
    getLatLng() { return this._latlng; },
    setLatLng(ll) { this._latlng = latLng(ll); this._majCercle(); return this.redraw(); },
    setRadius(r) { this._mRadius = r; this._majCercle(); return this.redraw(); },
    getRadius() { return this._mRadius; },
    _ancre() { return this._latlng; },
    toGeoJSON() { return { type: 'Feature', properties: (this.feature && this.feature.properties) || {}, geometry: { type: 'Point', coordinates: [this._latlng.lng, this._latlng.lat] } }; }
  });

  // Icônes (L.Icon, L.DivIcon, L.Icon.Default) : createIcon() rend l'élément, placé comme dans Leaflet
  const Icon = Class.extend({
    options: { popupAnchor: [0, 0], tooltipAnchor: [0, 0], className: '' },
    initialize(options) { setOptions(this, options); },
    createIcon(ancien) {
      const src = this.options.iconUrl;
      if (!src) throw new Error('iconUrl manquant');
      const img = ancien && ancien.tagName === 'IMG' ? ancien : document.createElement('img');
      img.src = src; img.alt = this.options.alt || '';
      this._setIconStyles(img, 'icon');
      return img;
    },
    createShadow() { return null; },
    _setIconStyles(el, nom) {
      const o = this.options, t = o[nom + 'Size'] || o.iconSize;
      const taille = typeof t === 'number' ? new Point(t, t) : point(t);
      const ancre = point(nom === 'shadow' && o.shadowAnchor || o.iconAnchor || (taille && taille.divideBy(2)));
      el.className = 'leaflet-marker-' + nom + ' ' + (o.className || '');
      if (ancre) { el.style.marginLeft = (-ancre.x) + 'px'; el.style.marginTop = (-ancre.y) + 'px'; }
      if (taille) { el.style.width = taille.x + 'px'; el.style.height = taille.y + 'px'; }
    }
  });
  const DivIcon = Icon.extend({
    options: { iconSize: [12, 12], html: false, bgPos: null, className: 'leaflet-div-icon' },
    createIcon(ancien) {
      const div = ancien && ancien.tagName === 'DIV' ? ancien : document.createElement('div'), o = this.options;
      if (o.html instanceof Element) { div.innerHTML = ''; div.appendChild(o.html); } else div.innerHTML = o.html !== false ? o.html : '';
      this._setIconStyles(div, 'icon');
      return div;
    }
  });
  // Icône par défaut : épingle bleue (SVG), comme celle de Leaflet
  const EPINGLE = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="25" height="41" viewBox="0 0 25 41"><path d="M12.5 0C5.6 0 0 5.6 0 12.5 0 21.9 12.5 41 12.5 41S25 21.9 25 12.5C25 5.6 19.4 0 12.5 0z" fill="#2a81cb" stroke="#1d5f99"/><circle cx="12.5" cy="12.5" r="5" fill="#fff"/></svg>');
  Icon.Default = Icon.extend({ options: { iconUrl: EPINGLE, iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], tooltipAnchor: [16, -28] } });
  Icon.Default.imagePath = '';

  const Marker = Layer.extend({
    options: { pane: 'markerPane', interactive: true, keyboard: true, title: '', alt: 'Marqueur', zIndexOffset: 0, opacity: 1,
      riseOnHover: false, riseOffset: 250, draggable: false, bubblingMouseEvents: false, autoPanOnFocus: true },
    initialize(ll, options) { setOptions(this, options); this._latlng = latLng(ll); },
    getLatLng() { return this._latlng; },
    setLatLng(ll) { const old = this._latlng; this._latlng = latLng(ll); if (this._gl) this._gl.setLngLat([this._latlng.lng, this._latlng.lat]); if (this._tooltip && this._tooltip._map) this._tooltip.setLatLng(this._latlng); return this.fire('move', { oldLatLng: old, latlng: this._latlng }); },
    getElement() { return this._el || null; },
    getIcon() { return this.options.icon || new Icon.Default(); },
    setIcon(ic) { this.options.icon = ic; if (this._map) { const c = this._map; this._retirer(c); this._ajouter(c, true); } return this; },
    setOpacity(o) { this.options.opacity = o; if (this._el) this._el.style.opacity = o; return this; },
    setZIndexOffset(z) { this.options.zIndexOffset = z; this._majZ(); return this; },
    getBounds() { return new LatLngBounds(this._latlng, this._latlng); },
    toGeoJSON() { return { type: 'Feature', properties: (this.feature && this.feature.properties) || {}, geometry: { type: 'Point', coordinates: [this._latlng.lng, this._latlng.lat] } }; },
    _majZ(plus) {
      const z = (this._map ? this._map._z(this.options.pane) : 600) + (this.options.zIndexOffset || 0) + (plus || 0);
      if (this._el) this._el.style.zIndex = z;
      if (this._conteneur) this._conteneur.style.zIndex = z;
    },
    _ajouter(carte, silencieux) {
      const o = this.options, el = this.getIcon().createIcon();
      el.classList.add('leaflet-marker-icon', o.interactive ? 'leaflet-interactive' : 'non-interactif');
      if (o.title) el.title = o.title;
      if (el.tagName === 'IMG') el.alt = o.alt || '';
      if (o.keyboard && o.interactive) { el.tabIndex = 0; el.setAttribute('role', 'button'); }
      if (o.opacity !== 1) el.style.opacity = o.opacity;
      this._el = el;
      this._majZ();
      const conteneur = document.createElement('div');   // MapLibre déplace ce conteneur ; l'icône garde ses marges
      conteneur.className = 'carte-marqueur';
      conteneur.appendChild(el);
      const ombre = this.getIcon().createShadow && this.getIcon().createShadow();
      if (ombre) { ombre.classList.add('leaflet-marker-shadow'); conteneur.insertBefore(ombre, el); }
      this._conteneur = conteneur;
      this._majZ();
      if (o.interactive) {
        el.addEventListener('click', ev => { ev.stopPropagation(); carte._clicMarqueur(this, ev, 'click'); });
        el.addEventListener('dblclick', ev => { ev.stopPropagation(); this.fire('dblclick', this._ev(carte, ev), true); });
        el.addEventListener('contextmenu', ev => { ev.stopPropagation(); this.fire('contextmenu', this._ev(carte, ev), true); });
        el.addEventListener('mouseenter', ev => { if (o.riseOnHover) this._majZ(o.riseOffset);
          this.fire('mouseover', this._ev(carte, ev), true); carte._montrerBulle(this, this._latlng); });
        el.addEventListener('mouseleave', ev => { if (o.riseOnHover) this._majZ();
          this.fire('mouseout', this._ev(carte, ev), true); carte._cacherBulle(this); });
        if (o.keyboard) el.addEventListener('keypress', ev => { if (ev.key === 'Enter') { ev.preventDefault(); carte._clicMarqueur(this, ev, 'click'); } });
      }
      this._gl = new ml.Marker({ element: conteneur, anchor: 'top-left', draggable: !!o.draggable }).setLngLat([this._latlng.lng, this._latlng.lat]).addTo(carte._gl);
      const glisser = (t) => () => { const p = this._gl.getLngLat(); const old = this._latlng; this._latlng = new LatLng(p.lat, p.lng);
        this.fire(t, { latlng: this._latlng, oldLatLng: old }); if (t === 'drag') this.fire('move', { latlng: this._latlng, oldLatLng: old }); };
      this._gl.on('dragstart', glisser('dragstart')); this._gl.on('drag', glisser('drag')); this._gl.on('dragend', glisser('dragend'));
      this.dragging = { enable: () => { o.draggable = true; this._gl && this._gl.setDraggable(true); }, disable: () => { o.draggable = false; this._gl && this._gl.setDraggable(false); }, enabled: () => !!o.draggable };
      if (!silencieux) this._apresAjout();
    },
    _ev(carte, domEv) { return { latlng: this._latlng, originalEvent: domEv, containerPoint: carte.latLngToContainerPoint(this._latlng) }; },
    _retirer(carte) { if (this._gl) this._gl.remove(); this._gl = null; this._el = null; carte._cacherBulle(this); if (this._tooltip && this._tooltip._map) this._tooltip.remove(); },
    _ancrePopup() { const ic = this.getIcon(); return point((ic.options && ic.options.popupAnchor) || [0, 0]); },
    _ancreBulle() { return this._latlng; }
  });

  // Image posée sur la carte : adresse (image MapLibre) ou canevas
  const ImageOverlay = Layer.extend({
    options: { opacity: 1, interactive: false },
    initialize(source, bounds, options) { setOptions(this, options); this._source = source; this._bornes = latLngBounds(bounds); },
    _volet() { return this.options.pane || 'overlayPane'; },
    _ajouter(carte) { carte._ajouterImage(this); this.fire('add'); },
    _retirer(carte) { carte._retirerImage(this); },
    setOpacity(o) { this.options.opacity = o; if (this._map && this._couche) this._map._gl.setPaintProperty(this._couche, 'raster-opacity', o); return this; },
    setUrl(u) { const c = this._map; this._source = u; if (c) { c._retirerImage(this); c._ajouterImage(this); } return this; },
    setBounds(b) { this._bornes = latLngBounds(b); return this.setUrl(this._source); },
    getBounds() { return this._bornes; },
    getElement() { return null; },
    _coins() { const b = this._bornes; return [[b.getWest(), b.getNorth()], [b.getEast(), b.getNorth()], [b.getEast(), b.getSouth()], [b.getWest(), b.getSouth()]]; }
  });

  // Tuiles matricielles (autres que celles d'OpenStreetMap, remplacées par le fond OpenFreeMap)
  const TileLayer = Layer.extend({
    options: { opacity: 1, subdomains: 'abc', maxZoom: 18, minZoom: 0, attribution: null, tileSize: 256, pane: 'tilePane' },
    initialize(url, options) { setOptions(this, options); this._url = url; this._osm = TUILES_OSM.test(url); },
    _ajouter(carte) { if (!this._osm) carte._ajouterTuiles(this); this.fire('add'); },
    _retirer(carte) { if (!this._osm) carte._retirerTuiles(this); },
    setOpacity(o) { this.options.opacity = o; if (this._couche && this._map) this._map._gl.setPaintProperty(this._couche, 'raster-opacity', o); return this; },
    setUrl(u) { const c = this._map; if (c) this._retirer(c); this._url = u; this._osm = TUILES_OSM.test(u); if (c) this._ajouter(c); return this; },
    redraw() { return this; },
    bringToFront() { return this; }, bringToBack() { return this; },
    _tuiles() {
      const s = this.options.subdomains, sd = typeof s === 'string' ? s.split('') : s;
      const u = this._url.replace('{r}', '').replace(/\{([xyz])\}/g, '{$1}');
      return u.includes('{s}') ? sd.map(d => u.replace('{s}', d)) : [u];
    }
  });

  // Groupes
  const LayerGroup = Layer.extend({
    initialize(couches, options) { setOptions(this, options); this._couches = new Map(); if (couches) for (const c of couches) this.addLayer(c); },
    addLayer(c) {
      const id = stamp(c);
      if (this._couches.has(id)) return this;
      this._couches.set(id, c);
      (c._parents = c._parents || new Set()).add(this);
      if (this._map) this._map.addLayer(c);
      return this;
    },
    removeLayer(c) {
      const id = typeof c === 'number' ? c : stamp(c), x = this._couches.get(id);
      if (!x) return this;
      this._couches.delete(id);
      if (x._parents) x._parents.delete(this);
      if (this._map && x._map) this._map.removeLayer(x);
      return this;
    },
    hasLayer(c) { return !!c && this._couches.has(typeof c === 'number' ? c : stamp(c)); },
    clearLayers() { for (const c of [...this._couches.values()]) this.removeLayer(c); return this; },
    invoke(m, ...a) { for (const c of this._couches.values()) if (c[m]) c[m](...a); return this; },
    eachLayer(f, ctx) { for (const c of [...this._couches.values()]) f.call(ctx, c); return this; },
    getLayer(id) { return this._couches.get(id); },
    getLayers() { return [...this._couches.values()]; },
    getLayerId(c) { return stamp(c); },
    setZIndex() { return this; },
    _ajouter(carte) { for (const c of this._couches.values()) carte.addLayer(c); this.fire('add'); },
    _retirer(carte) { for (const c of this._couches.values()) if (c._map) carte.removeLayer(c); },
    toGeoJSON() { return { type: 'FeatureCollection', features: this.getLayers().map(c => c.toGeoJSON && c.toGeoJSON()).filter(Boolean) }; }
  });
  const FeatureGroup = LayerGroup.extend({
    setStyle(s) { return this.invoke('setStyle', s); },
    bringToFront() { return this.invoke('bringToFront'); },
    bringToBack() { return this.invoke('bringToBack'); },
    getBounds() { const b = new LatLngBounds(); for (const c of this._couches.values()) if (c.getBounds) b.extend(c.getBounds()); else if (c.getLatLng) b.extend(c.getLatLng()); return b; }
  });
  // GeoJSON : une couche par entité (layer.feature), options style / pointToLayer / onEachFeature / filter
  const GeoJSON = FeatureGroup.extend({
    initialize(data, options) { setOptions(this, options); this._couches = new Map(); if (data) this.addData(data); },
    addData(d) {
      if (!d) return this;
      const liste = Array.isArray(d) ? d : d.type === 'FeatureCollection' ? d.features : d.type === 'GeometryCollection' ? d.geometries : [d];
      for (const f0 of liste) {
        if (!f0) continue;
        if (Array.isArray(f0) || f0.type === 'FeatureCollection') { this.addData(f0); continue; }
        const f = f0.type === 'Feature' ? f0 : { type: 'Feature', geometry: f0, properties: {} };
        if (this.options.filter && !this.options.filter(f)) continue;
        const c = GeoJSON.geometryToLayer(f, this.options);
        if (!c) continue;
        c.feature = f;
        c.defaultOptions = c.options;
        this.resetStyle(c);
        if (this.options.onEachFeature) this.options.onEachFeature(f, c);
        this.addLayer(c);
      }
      return this;
    },
    resetStyle(c) {
      if (c === undefined) return this.eachLayer(this.resetStyle, this);
      if (c.feature && c.setStyle) { c.options = extend(Object.create(c.defaultOptions || c.options), {}); this._appliquerStyle(c, this.options.style); }
      return this;
    },
    setStyle(s) { return this.eachLayer(c => this._appliquerStyle(c, s)); },
    _appliquerStyle(c, s) { if (!c.setStyle) return; if (typeof s === 'function') s = s(c.feature); if (s) c.setStyle(s); else c.redraw && c.redraw(); }
  });
  GeoJSON.coordsToLatLng = c => new LatLng(c[1], c[0], c[2]);
  GeoJSON.coordsToLatLngs = (cs, prof, f) => cs.map(c => prof ? GeoJSON.coordsToLatLngs(c, prof - 1, f) : (f || GeoJSON.coordsToLatLng)(c));
  GeoJSON.latLngToCoords = ll => [ll.lng, ll.lat];
  GeoJSON.geometryToLayer = function (f, options) {
    const g = f.type === 'Feature' ? f.geometry : f, o = options || {}, c2l = o.coordsToLatLng || GeoJSON.coordsToLatLng;
    if (!g) return null;
    const style = extend({}, o);
    switch (g.type) {
      case 'Point': { const ll = c2l(g.coordinates); return o.pointToLayer ? o.pointToLayer(f, ll) : new Marker(ll); }
      case 'MultiPoint': return new FeatureGroup(g.coordinates.map(c => { const ll = c2l(c); return o.pointToLayer ? o.pointToLayer(f, ll) : new Marker(ll); }));
      case 'LineString': case 'MultiLineString': return new Polyline(GeoJSON.coordsToLatLngs(g.coordinates, g.type === 'LineString' ? 0 : 1, c2l), style);
      case 'Polygon': case 'MultiPolygon': return new Polygon(GeoJSON.coordsToLatLngs(g.coordinates, g.type === 'Polygon' ? 1 : 2, c2l), style);
      case 'GeometryCollection': return new FeatureGroup(g.geometries.map(x => GeoJSON.geometryToLayer({ type: 'Feature', geometry: x, properties: f.properties }, o)).filter(Boolean));
      default: return null;
    }
  };

  // Flèches le long d'un tracé (comme L.polylineDecorator + L.Symbol.arrowHead) : symboles MapLibre
  const Decorator = Layer.extend({
    initialize(chemins, options) { setOptions(this, options); this._chemins = [].concat(chemins); this._motifs = this.options.patterns || []; },
    setPatterns(m) { this._motifs = m; if (this._map) this._map._planifier(this._volet()); return this; },
    setPaths(p) { this._chemins = [].concat(p); if (this._map) this._map._planifier(this._volet()); return this; },
    _volet() { return this.options.pane || 'overlayPane'; },
    _ajouter(carte) { carte._ajouterVecteur(this); this.fire('add'); },
    _retirer(carte) { carte._retirerVecteur(this); },
    _entites(carte) {
      const out = [];
      for (const ch of this._chemins) {
        const parties = ch instanceof Polyline ? ch._parties() : [enLatLngs(ch)];
        for (const t of parties) {
          const P = t.map(p => projeter(p, 20)), seg = [];
          let total = 0;
          for (let i = 1; i < P.length; i++) { seg.push(P[i].distanceTo(P[i - 1])); total += seg[i - 1]; }
          if (!total) continue;
          for (const m of this._motifs) {
            const lire = (v, d) => v === undefined ? d : typeof v === 'string' && v.endsWith('%') ? parseFloat(v) / 100 * total : +v * Math.pow(2, 20 - carte.getZoom());
            const debut = lire(m.offset, 0), pas = lire(m.repeat, 0), fin = total - lire(m.endOffset, 0);
            const sym = m.symbol || {}, so = sym.options || {}, po = so.pathOptions || {};
            const img = carte._imageFleche(po.color || '#3388ff', so.pixelSize || 10, po.weight || 2, !!so.polygon, po.opacity === undefined ? 1 : po.opacity);
            for (let d = debut, n = 0; d <= fin + 1e-6 && n < 500; d += pas || Infinity, n++) {
              let r = d, i = 0;
              while (i < seg.length - 1 && r > seg[i]) { r -= seg[i]; i++; }
              const a = P[i], b = P[i + 1], k = seg[i] ? Math.min(1, r / seg[i]) : 0;
              const ll = deprojeter(new Point(a.x + (b.x - a.x) * k, a.y + (b.y - a.y) * k), 20);
              out.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [ll.lng, ll.lat] },
                properties: { i: stamp(this), s: stamp(this), img, rot: Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI } });
              if (!pas) break;
            }
          }
        }
      }
      return out;
    }
  });
  const Symbol_ = { arrowHead: o => ({ type: 'arrowHead', options: extend({ pixelSize: 10, polygon: true, headAngle: 60 }, o) }),
    dash: o => ({ type: 'dash', options: o || {} }), marker: o => ({ type: 'marker', options: o || {} }) };

  // ---------- Fenêtre (popup) et infobulle (tooltip), structure HTML de Leaflet ----------
  const Popup = Layer.extend({
    options: { pane: 'popupPane', offset: [0, 7], maxWidth: 300, minWidth: 50, maxHeight: null, autoPan: true, autoPanPadding: [5, 5],
      keepInView: false, closeButton: true, autoClose: true, closeOnClick: undefined, closeOnEscapeKey: true, className: '' },
    initialize(options, source) {
      if (options instanceof LatLng || Array.isArray(options)) { this._latlng = latLng(options); options = source; source = undefined; }
      setOptions(this, options); this._source = source;
    },
    setLatLng(ll) { this._latlng = latLng(ll); if (this._gl) { this._gl.setLngLat([this._latlng.lng, this._latlng.lat]); this._majPos(); } return this; },
    getLatLng() { return this._latlng; },
    setContent(c) { this._contenu = c; if (this._el) this._majContenu(); return this; },
    getContent() { return this._contenu; },
    getElement() { return this._el || null; },
    isOpen() { return !!this._map; },
    openOn(carte) { carte.openPopup(this); return this; },
    close() { if (this._map) this._map.closePopup(this); return this; },
    toggle(l) { if (this.isOpen()) this.close(); else if (l) l.openPopup(); return this; },
    bringToFront() { return this; }, bringToBack() { return this; },
    update() { if (this._el) { this._majContenu(); this._majPos(); } return this; },
    _decalage() {
      let o = point(this.options.offset);
      if (this._source instanceof Marker) o = o.add(this._source._ancrePopup());
      return o;
    },
    _majPos() { if (this._gl) { const o = this._decalage(); this._gl.setOffset([o.x, o.y - 20]); } },
    _creer() {
      const el = document.createElement('div');
      el.className = 'leaflet-popup leaflet-zoom-animated ' + (this.options.className || '');
      if (this.options.closeButton) {
        const b = document.createElement('a');
        b.className = 'leaflet-popup-close-button'; b.href = '#close'; b.setAttribute('role', 'button'); b.setAttribute('aria-label', 'Fermer');
        b.innerHTML = '<span aria-hidden="true">&#215;</span>';
        b.addEventListener('click', ev => { ev.preventDefault(); if (this._map) this._map.closePopup(this); });
        el.appendChild(b);
      }
      const env = document.createElement('div'); env.className = 'leaflet-popup-content-wrapper';
      this._noeud = document.createElement('div'); this._noeud.className = 'leaflet-popup-content';
      env.appendChild(this._noeud); el.appendChild(env);
      el.insertAdjacentHTML('beforeend', '<div class="leaflet-popup-tip-container"><div class="leaflet-popup-tip"></div></div>');
      ['mousedown', 'touchstart', 'pointerdown', 'wheel', 'dblclick', 'contextmenu', 'click'].forEach(t => el.addEventListener(t, e => e.stopPropagation()));
      this._el = el;
    },
    _majContenu() {
      const c = this._noeud;
      let x = this._contenu;
      if (typeof x === 'function') x = x(this._source || this);
      if (typeof x === 'string' || typeof x === 'number') c.innerHTML = x; else { c.innerHTML = ''; if (x) c.appendChild(x); }
      c.style.width = ''; c.style.whiteSpace = 'nowrap';
      let w = Math.min(c.offsetWidth, this.options.maxWidth); w = Math.max(w, this.options.minWidth);
      c.style.width = (w + 1) + 'px'; c.style.whiteSpace = '';
      if (this.options.maxHeight && c.offsetHeight > this.options.maxHeight) { c.style.height = this.options.maxHeight + 'px'; c.classList.add('leaflet-popup-scrolled'); }
      this.fire('contentupdate');
    },
    _ajouter(carte) {
      if (!this._el) this._creer();
      this._el.style.zIndex = carte._z(this.options.pane);
      const o = this._decalage();
      this._gl = new ml.Marker({ element: this._el, anchor: 'bottom', offset: [o.x, o.y - 20] }).setLngLat([this._latlng.lng, this._latlng.lat]).addTo(carte._gl);
      this._majContenu();
      this.fire('add');
      carte.fire('popupopen', { popup: this });
      if (this._source) this._source.fire('popupopen', { popup: this }, true);
      if (this.options.autoPan) requestAnimationFrame(() => this._recentrer());
    },
    _retirer(carte) {
      if (this._gl) this._gl.remove();
      this._gl = null;
      if (carte._fenetre === this) carte._fenetre = null;
      carte.fire('popupclose', { popup: this });
      if (this._source) this._source.fire('popupclose', { popup: this }, true);
    },
    _recentrer() {
      const carte = this._map;
      if (!carte || !this._el) return;
      const o = this.options, tl = point(o.autoPanPaddingTopLeft || o.autoPanPadding), br = point(o.autoPanPaddingBottomRight || o.autoPanPadding);
      const r = this._el.getBoundingClientRect(), c = carte._el.getBoundingClientRect();
      let dx = 0, dy = 0;
      if (r.right + br.x > c.right) dx = r.right + br.x - c.right;
      if (r.left - dx - tl.x < c.left) dx = r.left - tl.x - c.left;
      if (r.bottom + br.y > c.bottom) dy = r.bottom + br.y - c.bottom;
      if (r.top - dy - tl.y < c.top) dy = r.top - tl.y - c.top;
      if (dx || dy) { carte.fire('autopanstart'); carte._gl.panBy([dx, dy], { duration: 250 }); }
    }
  });
  const Tooltip = Layer.extend({
    options: { pane: 'tooltipPane', offset: [0, 0], direction: 'auto', permanent: false, sticky: false, opacity: 0.9, className: '', interactive: false },
    initialize(options, source) {
      if (options instanceof LatLng || Array.isArray(options)) { this._latlng = latLng(options); options = source; source = undefined; }
      setOptions(this, options); this._source = source;
    },
    setLatLng(ll) { this._latlng = latLng(ll); if (this._gl) this._gl.setLngLat([this._latlng.lng, this._latlng.lat]); return this; },
    getLatLng() { return this._latlng; },
    setContent(c) { this._contenu = c; if (this._el) this._majContenu(); return this; },
    getContent() { return this._contenu; },
    getElement() { return this._el || null; },
    isOpen() { return !!this._map; },
    openOn(carte) { carte.addLayer(this); return this; },
    close() { return this.remove(); },
    setOpacity(o) { this.options.opacity = o; if (this._el) this._el.style.opacity = o; return this; },
    update() { if (this._el) this._majContenu(); return this; },
    bringToFront() { return this; }, bringToBack() { return this; },
    _majContenu() {
      let x = this._contenu;
      if (typeof x === 'function') x = x(this._source || this);
      if (typeof x === 'string' || typeof x === 'number') this._el.innerHTML = x; else { this._el.innerHTML = ''; if (x) this._el.appendChild(x); }
    },
    _ajouter(carte) {
      const o = this.options;
      let dir = o.direction;
      if (dir === 'auto') dir = this._source instanceof Marker ? 'right' : 'top';
      const el = this._el = document.createElement('div');
      el.className = 'leaflet-tooltip leaflet-tooltip-' + dir + (o.className ? ' ' + o.className : '') + (o.interactive ? ' leaflet-interactive' : '');
      el.style.opacity = o.opacity;
      el.style.zIndex = carte._z(o.pane);
      this._majContenu();
      let off = point(o.offset);
      if (this._source instanceof Marker) { const a = point((this._source.getIcon().options || {}).tooltipAnchor || [0, 0]); off = off.add(dir === 'left' ? [-a.x, a.y] : a); }
      const ancre = { top: 'bottom', bottom: 'top', left: 'right', right: 'left', center: 'center' }[dir] || 'bottom';
      const ll = this._latlng || (this._source && this._source._ancreBulle());
      this._gl = new ml.Marker({ element: el, anchor: ancre, offset: [off.x, off.y] }).setLngLat([ll.lng, ll.lat]).addTo(carte._gl);
      if (!o.interactive) el.style.pointerEvents = 'none';
      this.fire('add');
      carte.fire('tooltipopen', { tooltip: this });
      if (this._source) this._source.fire('tooltipopen', { tooltip: this });
    },
    _retirer(carte) {
      if (this._gl) this._gl.remove(); this._gl = null; this._el = null;
      carte.fire('tooltipclose', { tooltip: this });
      if (this._source) this._source.fire('tooltipclose', { tooltip: this });
    }
  });

  // ---------- Contrôles (structure et classes de Leaflet) ----------
  const Control = Class.extend({
    options: { position: 'topright' },
    initialize(options) { setOptions(this, options); },
    getPosition() { return this.options.position; },
    setPosition(p) { const c = this._map; if (c) this.remove(); this.options.position = p; if (c) this.addTo(c); return this; },
    getContainer() { return this._container; },
    addTo(carte) {
      this.remove();
      this._map = carte;
      const el = this._container = this.onAdd(carte);
      el.classList.add('leaflet-control');
      const coin = carte._coin(this.options.position);
      if (this.options.position.indexOf('bottom') !== -1) coin.insertBefore(el, coin.firstChild); else coin.appendChild(el);
      return this;
    },
    remove() { if (!this._map) return this; if (this._container) this._container.remove(); if (this.onRemove) this.onRemove(this._map); this._map = null; return this; },
    onAdd() { return document.createElement('div'); }
  });
  const ControlZoom = Control.extend({
    options: { position: 'topleft', zoomInText: '<span aria-hidden="true">+</span>', zoomInTitle: 'Zoom avant',
      zoomOutText: '<span aria-hidden="true">&#x2212;</span>', zoomOutTitle: 'Zoom arrière' },
    onAdd(carte) {
      const el = document.createElement('div'), o = this.options;
      el.className = 'leaflet-control-zoom leaflet-bar';
      const bouton = (cls, txt, titre, f) => {
        const a = document.createElement('a');
        a.className = cls; a.href = '#'; a.title = titre; a.setAttribute('role', 'button'); a.setAttribute('aria-label', titre);
        a.innerHTML = txt;
        a.addEventListener('click', ev => { ev.preventDefault(); f(); });
        el.appendChild(a);
      };
      bouton('leaflet-control-zoom-in', o.zoomInText, o.zoomInTitle, () => carte.zoomIn());
      bouton('leaflet-control-zoom-out', o.zoomOutText, o.zoomOutTitle, () => carte.zoomOut());
      return el;
    }
  });
  const ControlAttribution = Control.extend({
    options: { position: 'bottomright', prefix: '' },
    onAdd(carte) {
      const el = document.createElement('div'); el.className = 'leaflet-control-attribution'; this._el = el; this._ajouts = this._ajouts || []; this._maj();
      el.addEventListener('click', e => { const b = e.target.closest('.attr-bouton'); if (!b) return; const o = el.classList.toggle('attr-ouvert'); b.setAttribute('aria-expanded', String(o)); });
      const etroite = () => carte._el.classList.toggle('carte-etroite', carte._el.clientWidth < 600);
      if (window.ResizeObserver) new ResizeObserver(etroite).observe(carte._el); etroite();
      return el;
    },
    setPrefix() { return this; },   // le préfixe « Leaflet » n'existe plus ; le crédit du fond est toujours affiché
    addAttribution(t) { (this._ajouts = this._ajouts || []).push(t); this._maj(); return this; },
    removeAttribution(t) { this._ajouts = (this._ajouts || []).filter(x => x !== t); this._maj(); return this; },
    _maj() { if (this._el) this._el.innerHTML = ATTRIBUTION + (this._ajouts.length ? '<span class="attr-plein"> | ' + this._ajouts.join(', ') + '</span>' : ''); }
  });
  const ControlLayers = Control.extend({
    options: { position: 'topright', collapsed: true, autoZIndex: true, hideSingleBase: false, sortLayers: false },
    initialize(bases, calques, options) { setOptions(this, options); this._entrees = []; for (const k in bases || {}) this._entrees.push({ nom: k, couche: bases[k], base: true });
      for (const k in calques || {}) this._entrees.push({ nom: k, couche: calques[k], base: false }); },
    addBaseLayer(c, n) { this._entrees.push({ nom: n, couche: c, base: true }); this._maj(); return this; },
    addOverlay(c, n) { this._entrees.push({ nom: n, couche: c, base: false }); this._maj(); return this; },
    removeLayer(c) { this._entrees = this._entrees.filter(e => e.couche !== c); this._maj(); return this; },
    expand() { if (this._container) this._container.classList.add('leaflet-control-layers-expanded'); return this; },
    collapse() { if (this._container) this._container.classList.remove('leaflet-control-layers-expanded'); return this; },
    onAdd(carte) {
      const el = document.createElement('div');
      el.className = 'leaflet-control-layers' + (this.options.collapsed ? '' : ' leaflet-control-layers-expanded');
      el.setAttribute('aria-haspopup', 'true');
      if (this.options.collapsed) {
        const a = document.createElement('a'); a.className = 'leaflet-control-layers-toggle'; a.href = '#'; a.title = 'Calques'; a.setAttribute('role', 'button');
        a.addEventListener('click', e => { e.preventDefault(); el.classList.toggle('leaflet-control-layers-expanded'); });
        el.appendChild(a);
        el.addEventListener('mouseenter', () => this.expand()); el.addEventListener('mouseleave', () => this.collapse());
      }
      const f = this._liste = document.createElement('section'); f.className = 'leaflet-control-layers-list';
      el.appendChild(f);
      ['click', 'dblclick', 'mousedown', 'wheel', 'touchstart'].forEach(t => el.addEventListener(t, e => e.stopPropagation()));
      this._carte = carte;
      setTimeout(() => this._maj());
      return el;
    },
    _maj() {
      if (!this._liste) return;
      const carte = this._carte, l = this._liste;
      l.innerHTML = '';
      const bases = this._entrees.filter(e => e.base), calques = this._entrees.filter(e => !e.base);
      const bloc = cls => { const d = document.createElement('div'); d.className = cls; l.appendChild(d); return d; };
      const db = bloc('leaflet-control-layers-base');
      if (bases.length && calques.length) bloc('leaflet-control-layers-separator');
      const dc = bloc('leaflet-control-layers-overlays');
      const nomRadio = 'leaflet-base-layers_' + stamp(this);
      if (!(this.options.hideSingleBase && bases.length < 2)) for (const e of bases) {
        const lab = document.createElement('label'), i = document.createElement('input');
        i.type = 'radio'; i.name = nomRadio; i.className = 'leaflet-control-layers-selector'; i.checked = carte.hasLayer(e.couche);
        i.addEventListener('change', () => { for (const x of bases) if (x !== e && carte.hasLayer(x.couche)) carte.removeLayer(x.couche); carte.addLayer(e.couche); carte.fire('baselayerchange', { layer: e.couche, name: e.nom }); });
        const s = document.createElement('span'); s.innerHTML = ' ' + e.nom; lab.append(i, s); db.appendChild(lab);
      }
      for (const e of calques) {
        const lab = document.createElement('label'), i = document.createElement('input');
        i.type = 'checkbox'; i.className = 'leaflet-control-layers-selector'; i.checked = carte.hasLayer(e.couche);
        i.addEventListener('change', () => { if (i.checked) { carte.addLayer(e.couche); carte.fire('overlayadd', { layer: e.couche, name: e.nom }); } else { carte.removeLayer(e.couche); carte.fire('overlayremove', { layer: e.couche, name: e.nom }); } });
        const s = document.createElement('span'); s.innerHTML = ' ' + e.nom; lab.append(i, s); dc.appendChild(lab);
      }
    }
  });
  const ControlScale = Control.extend({ options: { position: 'bottomleft', maxWidth: 100 },
    onAdd(carte) { const el = document.createElement('div'); el.className = 'leaflet-control-scale'; const l = document.createElement('div'); l.className = 'leaflet-control-scale-line'; el.appendChild(l);
      const maj = () => { const y = carte.getSize().y / 2, m = carte.containerPointToLatLng([0, y]).distanceTo(carte.containerPointToLatLng([this.options.maxWidth, y]));
        const p = Math.pow(10, Math.floor(Math.log10(m))), d = m / p, r = (d >= 5 ? 5 : d >= 3 ? 3 : d >= 2 ? 2 : 1) * p;
        l.style.width = Math.round(this.options.maxWidth * r / m) + 'px'; l.textContent = r >= 1000 ? r / 1000 + ' km' : r + ' m'; };
      carte.on('moveend zoomend', maj); setTimeout(maj); return el; } });
  const control = o => new Control(o);
  control.zoom = o => new ControlZoom(o); control.attribution = o => new ControlAttribution(o);
  control.layers = (b, c, o) => new ControlLayers(b, c, o); control.scale = o => new ControlScale(o);
  Control.Zoom = ControlZoom; Control.Attribution = ControlAttribution; Control.Layers = ControlLayers; Control.Scale = ControlScale;

  // ---------- Carte ----------
  const EVENEMENTS_GL = { zoomend: 'zoomend', zoomstart: 'zoomstart', zoom: 'zoom', moveend: 'moveend', movestart: 'movestart', move: 'move',
    resize: 'resize', dragstart: 'dragstart', drag: 'drag', dragend: 'dragend' };
  const COINS = { topleft: 'leaflet-top leaflet-left', topright: 'leaflet-top leaflet-right', bottomleft: 'leaflet-bottom leaflet-left', bottomright: 'leaflet-bottom leaflet-right' };
  const VOLETS_LEAFLET = [['mapPane', 400], ['tilePane', 200], ['overlayPane', 400], ['shadowPane', 500], ['markerPane', 600], ['tooltipPane', 650], ['popupPane', 700]];
  const GESTES = { scrollWheelZoom: 'scrollZoom', dragging: 'dragPan', doubleClickZoom: 'doubleClickZoom', touchZoom: 'touchZoomRotate', boxZoom: 'boxZoom', keyboard: 'keyboard' };

  const Map_ = Evented.extend({
    options: { zoomControl: true, attributionControl: true, zoomSnap: 1, zoomDelta: 1, minZoom: undefined, maxZoom: undefined,
      scrollWheelZoom: true, dragging: true, doubleClickZoom: true, touchZoom: true, boxZoom: true, keyboard: true, fond: undefined, crs: CRS.EPSG3857 },
    initialize(conteneur, options) {
      setOptions(this, options);
      const o = this.options;
      const el = this._el = typeof conteneur === 'string' ? document.getElementById(conteneur) : conteneur;
      if (!el) throw new Error('Conteneur de carte introuvable');
      if (el._leaflet_id) throw new Error('Conteneur de carte déjà utilisé');
      stamp(el);
      el._carte = this;   // pour les tests et la console : document.getElementById('map')._carte
      el.classList.add('leaflet-container', 'leaflet-touch');
      if (!el.hasAttribute('tabindex')) el.tabIndex = 0;
      const fondVoulu = o.fond || REGLAGE.fond || 'auto', sombre = matchMedia('(prefers-color-scheme: dark)');
      // Mode « auto » : data-theme="dark" | "light" sur <html> (bouton du site) l'emporte sur le réglage du système
      const fondAuto = () => { const t = document.documentElement.dataset.theme; return t === 'dark' ? 'sombre' : t === 'light' ? 'clair' : sombre.matches ? 'sombre' : 'clair'; };
      const fond = fondVoulu === 'auto' ? fondAuto() : fondVoulu;
      this._fond = fond;
      el.classList.add('carte-fond-' + fond);
      const centre = o.center ? latLng(o.center) : new LatLng(45.52, -73.58);
      this._gl = new ml.Map({
        container: el, style: STYLES[fond] || STYLES.clair, center: [centre.lng, centre.lat], zoom: (o.zoom !== undefined ? o.zoom : 12) - ECART_ZOOM,
        minZoom: o.minZoom !== undefined ? Math.max(0, o.minZoom - ECART_ZOOM) : 0, maxZoom: o.maxZoom !== undefined ? o.maxZoom - ECART_ZOOM : 19,
        maxBounds: o.maxBounds ? latLngBounds(o.maxBounds)._lngLat() : undefined, attributionControl: false, dragRotate: false, pitchWithRotate: false,
        touchPitch: false, maxPitch: 0, renderWorldCopies: false, fadeDuration: 150, clickTolerance: 4
      });
      this._gl.touchZoomRotate.disableRotation();
      this._gl.keyboard.disableRotation();
      for (const [k, g] of Object.entries(GESTES)) {
        const h = this._gl[g];
        this[k] = { enable: () => { h.enable(); if (k === 'touchZoom') h.disableRotation(); }, disable: () => h.disable(), enabled: () => h.isEnabled() };
        if (o[k] === false) h.disable();
      }
      this._volets = new Map();
      this._conteneurVolets = document.createElement('div');
      this._conteneurVolets.className = 'leaflet-pane leaflet-map-pane carte-volets';
      el.appendChild(this._conteneurVolets);
      for (const [n, z] of VOLETS_LEAFLET) this.createPane(n).style.zIndex = z;
      this._objets = new Map();
      this._tirets = new Map(); this._tiretsAJour = true;
      this._images = new Map();
      this._pret = false;
      this._tolerance = TACTILE ? 12 : 8;
      this._fenetre = null;
      this._loaded = false;
      this._attente = [];
      this._gl.on('load', () => { this._devenirPret(); this.fire('load'); this._loaded = true; });
      if (fondVoulu === 'auto') {
        // Suit le mode du système et le bouton du site, même après le chargement
        const suivre = () => this.setFond(fondAuto());
        sombre.addEventListener('change', suivre);
        new MutationObserver(suivre).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
      }
      this._gl.on('styleimagemissing', e => this._imageManquante(e.id));
      this._gl.on('click', e => this._clic(e, 'click'));
      this._gl.on('dblclick', e => this._clic(e, 'dblclick'));
      this._gl.on('contextmenu', e => this._clic(e, 'contextmenu'));
      this._gl.on('mousemove', e => this._survol(e));
      this._gl.on('mouseout', e => { this._sortie(); this.fire('mouseout', { originalEvent: e.originalEvent }); });
      this._gl.on('mousedown', e => this.fire('mousedown', this._evenement(e)));
      this._gl.on('mouseup', e => this.fire('mouseup', this._evenement(e)));
      el.addEventListener('keydown', e => { this.fire('keydown', { originalEvent: e }); if (e.key === 'Escape' && this._fenetre && this._fenetre.options.closeOnEscapeKey) this.closePopup(); });
      el.addEventListener('keypress', e => this.fire('keypress', { originalEvent: e }));
      el.addEventListener('keyup', e => this.fire('keyup', { originalEvent: e }));
      el.addEventListener('focus', e => this.fire('focus', { originalEvent: e }));
      el.addEventListener('blur', e => this.fire('blur', { originalEvent: e }));
      this._coins = {};
      const cc = document.createElement('div'); cc.className = 'leaflet-control-container';
      for (const [k, cls] of Object.entries(COINS)) { const d = document.createElement('div'); d.className = cls; cc.appendChild(d); this._coins[k] = d; }
      el.appendChild(cc);
      if (o.zoomControl) { this.zoomControl = new ControlZoom(); this.zoomControl.addTo(this); }
      this.attributionControl = new ControlAttribution();
      if (o.attributionControl !== false) this.attributionControl.addTo(this);
      // L'attribution est obligatoire (OpenFreeMap, OpenMapTiles, OpenStreetMap) : ajoutée même si attributionControl est faux,
      // sauf si la page en ajoute une elle-même (control.attribution)
      else setTimeout(() => { if (!this._el.querySelector('.leaflet-control-attribution')) this.attributionControl.addTo(this); });
      if (o.center && o.zoom !== undefined) this._vue = true;
    },
    _surOn(types) {
      for (const t of types.split(/\s+/)) {
        if (EVENEMENTS_GL[t] && !(this._ponts || (this._ponts = new Set())).has(t)) { this._ponts.add(t); this._gl.on(EVENEMENTS_GL[t], e => this.fire(t, { originalEvent: e.originalEvent })); }
      }
    },
    // --- volets ---
    createPane(nom, conteneur) {
      let v = this._volets.get(nom);
      if (v) return v.el;
      const el = document.createElement('div');
      el.className = 'leaflet-pane leaflet-' + nom.replace(/Pane$/, '') + '-pane';
      (conteneur || this._conteneurVolets).appendChild(el);
      v = { nom, el, objets: new Map(), images: new Set(), couches: false, sale: false };
      this._volets.set(nom, v);
      return el;
    },
    getPane(nom) { if (typeof nom !== 'string') return nom; return (this._volets.get(nom) || {}).el; },
    getPanes() { const o = {}; for (const [k, v] of this._volets) o[k] = v.el; return o; },
    getContainer() { return this._el; },
    getCanvasContainer() { return this._gl.getCanvasContainer(); },
    _z(nom) { const v = this._volets.get(nom); if (!v) return 400; const z = parseInt(v.el.style.zIndex, 10); return isNaN(z) ? 400 : z; },
    _volet(nom) { if (!this._volets.has(nom)) this.createPane(nom); return this._volets.get(nom); },
    // --- couches ---
    addLayer(c) {
      if (c instanceof Popup) return this.openPopup(c);
      if (c._map === this) return this;
      c._map = this;
      c._ajouter(this);
      this.fire('layeradd', { layer: c });
      return this;
    },
    removeLayer(c) {
      if (!c || c._map !== this) return this;
      if (c instanceof Popup && this._fenetre === c) this._fenetre = null;
      c._retirer(this);
      c._map = null;
      c.fire('remove');
      this.fire('layerremove', { layer: c });
      return this;
    },
    hasLayer(c) { return !!c && c._map === this; },
    eachLayer(f, ctx) { for (const o of this._objets.values()) f.call(ctx, o); return this; },
    _ajouterVecteur(o) { const v = this._volet(o._volet()); v.objets.set(stamp(o), o); this._objets.set(stamp(o), o); this._planifier(v.nom); },
    _retirerVecteur(o) { const v = this._volet(o._volet()); v.objets.delete(stamp(o)); this._objets.delete(stamp(o)); this._planifier(v.nom); if (this._survolCourant === o) { this._survolCourant = null; this._cacherBulle(); } },
    _ajouterImage(im) { const v = this._volet(im._volet()); v.images.add(im); this._planifier(v.nom); },
    _retirerImage(im) {
      const v = this._volet(im._volet()); v.images.delete(im);
      if (im._couche && this._gl.getLayer(im._couche)) { this._gl.removeLayer(im._couche); this._gl.removeSource(im._couche); }
      im._couche = null; this._rangs = null;
    },
    _ajouterTuiles(t) {
      const poser = () => {
        if (t._map !== this || t._couche) return;
        const id = 'tuiles-' + stamp(t);
        this._gl.addSource(id, { type: 'raster', tiles: t._tuiles(), tileSize: t.options.tileSize || 256, maxzoom: Math.min(t.options.maxNativeZoom || t.options.maxZoom || 18, 22) });
        // Au-dessus de tout le fond vectoriel (noms de rues compris : une carte matricielle a les siens), sous les tracés de la page
        const premiere = (this._gl.getStyle().layers.find(l => /^(v-|img-)/.test(l.id)) || {}).id;
        this._gl.addLayer({ id, type: 'raster', source: id, paint: { 'raster-opacity': t.options.opacity } }, premiere);
        t._couche = id; this._rangs = null;
        if (t.options.attribution) this.attributionControl.addAttribution(t.options.attribution);
      };
      if (this._pret) poser(); else this._attente.push(poser);
    },
    _retirerTuiles(t) {
      if (t._couche && this._gl.getLayer(t._couche)) { this._gl.removeLayer(t._couche); this._gl.removeSource(t._couche); }
      if (t.options.attribution) this.attributionControl.removeAttribution(t.options.attribution);
      t._couche = null;
    },
    _planifier(nom) {
      if (nom) this._volet(nom).sale = true;
      if (this._prevu) return;
      this._prevu = true;
      queueMicrotask(() => { this._prevu = false; this._appliquer(); });
    },
    _cleTirets(motif) {
      const m = motif.map(v => +v.toFixed(3)), k = m.join(',');
      if (!this._tirets.has(k)) { this._tirets.set(k, m); this._tiretsAJour = false; }
      return k;
    },
    _exprTirets() {
      if (!this._tirets.size) return ['literal', [1, 0]];
      return ['match', ['get', 'dk'], ...[...this._tirets].flatMap(([k, m]) => [k, ['literal', m]]), ['literal', [1, 0]]];
    },
    _couchesDe(v) { const n = 'v-' + v.nom; return [n + '-surfaces', n + '-traits', n + '-tirets', n + '-cercles', n + '-symboles']; },
    _avant(z) {
      const plusHauts = [...this._volets.values()].filter(v => v.couches && this._z(v.nom) > z).sort((a, b) => this._z(a.nom) - this._z(b.nom));
      for (const v of plusHauts) { const im = [...v.images].find(i => i._couche); return im ? im._couche : this._couchesDe(v)[0]; }
      return undefined;
    },
    _creerCouches(v) {
      const gl = this._gl, src = 'v-' + v.nom, avant = this._avant(this._z(v.nom));
      gl.addSource(src, { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, tolerance: 0.2 });
      const [surfaces, traits, tirets, cercles, symboles] = this._couchesDe(v);
      const estLigne = ['any', ['==', ['geometry-type'], 'LineString'], ['==', ['geometry-type'], 'Polygon']];
      gl.addLayer({ id: surfaces, type: 'fill', source: src, filter: ['all', ['==', ['geometry-type'], 'Polygon'], ['>', ['get', 'fo'], 0]],
        layout: { 'fill-sort-key': ['get', 's'] }, paint: { 'fill-color': ['get', 'fc'], 'fill-opacity': ['get', 'fo'] } }, avant);
      const ligne = (id, filtre, pointilles) => gl.addLayer({ id, type: 'line', source: src, filter: filtre,
        layout: { 'line-cap': ['get', 'cap'], 'line-join': ['get', 'join'], 'line-sort-key': ['get', 's'] },
        paint: extend({ 'line-color': ['get', 'c'], 'line-width': ['get', 'w'], 'line-opacity': ['get', 'o'] }, pointilles ? { 'line-dasharray': this._exprTirets() } : {}) }, avant);
      ligne(traits, ['all', estLigne, ['==', ['get', 'dk'], ''], ['>', ['get', 'w'], 0]], false);
      ligne(tirets, ['all', estLigne, ['!=', ['get', 'dk'], '']], true);
      gl.addLayer({ id: cercles, type: 'circle', source: src, filter: ['all', ['==', ['geometry-type'], 'Point'], ['has', 'r']],
        layout: { 'circle-sort-key': ['get', 's'] },
        paint: { 'circle-radius': ['get', 'r'], 'circle-color': ['get', 'fc'], 'circle-opacity': ['get', 'fo'], 'circle-stroke-color': ['get', 'sc'],
          'circle-stroke-width': ['get', 'sw'], 'circle-stroke-opacity': ['get', 'so'], 'circle-pitch-alignment': 'map' } }, avant);
      gl.addLayer({ id: symboles, type: 'symbol', source: src, filter: ['all', ['==', ['geometry-type'], 'Point'], ['has', 'img']],
        layout: { 'icon-image': ['get', 'img'], 'icon-rotate': ['get', 'rot'], 'icon-rotation-alignment': 'map', 'icon-allow-overlap': true, 'icon-ignore-placement': true } }, avant);
      v.couches = true;
      this._rangs = null;
    },
    _devenirPret() {
      this._pret = true; this._rangs = null;
      for (const f of this._attente.splice(0)) f();
      this._appliquer();
    },
    // Change le fond ('clair' | 'sombre') en gardant les tracés, images et tuiles de la page (sources et couches v-, img-, tuiles-)
    setFond(fond) {
      if (!STYLES[fond] || fond === this._fond) return this;
      const el = this._gl.getContainer();
      el.classList.remove('carte-fond-' + this._fond); el.classList.add('carte-fond-' + fond);
      this._fond = fond;
      const aNous = id => /^(v-|img-|tuiles-)/.test(id);
      this._pret = false;
      this._gl.setStyle(STYLES[fond], {
        diff: false,
        transformStyle: (avant, apres) => !avant ? apres : {
          ...apres,
          sources: { ...apres.sources, ...Object.fromEntries(Object.entries(avant.sources).filter(([id]) => aNous(id))) },
          layers: [...apres.layers, ...avant.layers.filter(c => aNous(c.id))]
        }
      });
      this._gl.once('style.load', () => this._devenirPret());   // les icônes (flèches) reviennent par styleimagemissing
      return this;
    },
    getFond() { return this._fond; },
    _appliquer() {
      if (!this._pret) return;
      const gl = this._gl;
      for (const v of [...this._volets.values()].sort((a, b) => this._z(a.nom) - this._z(b.nom))) {
        if (!v.sale) continue;
        v.sale = false;
        if (!v.couches) { if (!v.objets.size && !v.images.size) continue; this._creerCouches(v); }
        const entites = [];
        for (const o of v.objets.values()) entites.push(...o._entites(this));
        if (!this._tiretsAJour) {
          this._tiretsAJour = true;
          for (const w of this._volets.values()) if (w.couches) gl.setPaintProperty(this._couchesDe(w)[2], 'line-dasharray', this._exprTirets());
        }
        gl.getSource('v-' + v.nom).setData({ type: 'FeatureCollection', features: entites });
        for (const im of v.images) if (!im._couche) this._poserImage(im, v);
      }
    },
    _poserImage(im, v) {
      const id = 'img-' + stamp(im), avant = this._couchesDe(v)[0];
      if (im._source instanceof HTMLCanvasElement) this._gl.addSource(id, { type: 'canvas', canvas: im._source, animate: false, coordinates: im._coins() });
      else this._gl.addSource(id, { type: 'image', url: im._source, coordinates: im._coins() });
      this._gl.addLayer({ id, type: 'raster', source: id, paint: { 'raster-opacity': im.options.opacity, 'raster-fade-duration': 0, 'raster-resampling': 'nearest' } }, avant);
      im._couche = id; this._rangs = null;
    },
    // Image de flèche (L.Symbol.arrowHead) dessinée au besoin
    _imageFleche(couleur, taille, epaisseur, plein, opacite) {
      const id = 'fleche|' + [couleur, taille, epaisseur, plein ? 1 : 0, opacite].join('|');
      if (!this._images.has(id)) this._images.set(id, { couleur, taille, epaisseur, plein, opacite });
      return id;
    },
    _imageManquante(id) {
      const f = this._images.get(id);
      if (this._gl.hasImage(id)) return;
      // Image absente du style du fond (ex. « wood-pattern » du style sombre) : pixel transparent, sans avertissement
      if (!f) { this._gl.addImage(id, { width: 1, height: 1, data: new Uint8Array(4) }); return; }
      const dpr = 2, s = Math.ceil((f.taille * 2 + f.epaisseur * 2) * dpr), c = document.createElement('canvas');
      c.width = c.height = s;
      const x = c.getContext('2d');
      x.translate(s / 2, s / 2); x.scale(dpr, dpr);
      x.beginPath(); x.moveTo(-f.taille * 0.6, -f.taille * 0.7); x.lineTo(f.taille * 0.6, 0); x.lineTo(-f.taille * 0.6, f.taille * 0.7);
      x.globalAlpha = f.opacite; x.lineJoin = 'round'; x.lineCap = 'round';
      if (f.plein) { x.closePath(); x.fillStyle = f.couleur; x.fill(); } else { x.strokeStyle = f.couleur; x.lineWidth = f.epaisseur; x.stroke(); }
      this._gl.addImage(id, x.getImageData(0, 0, s, s), { pixelRatio: dpr });
    },
    // --- clics et survol ---
    _rangCouches() { return this._rangs || (this._rangs = new Map(this._gl.getStyle().layers.map((l, i) => [l.id, i]))); },
    _objetSous(pt) {
      if (!this._pret) return null;
      const couches = [...this._volets.values()].filter(v => v.couches).flatMap(v => this._couchesDe(v));
      if (!couches.length) return null;
      const t = this._tolerance;
      const f = this._gl.queryRenderedFeatures([[pt.x - t, pt.y - t], [pt.x + t, pt.y + t]], { layers: couches });
      const rang = this._rangCouches();
      let meilleur = null, cle = null;
      for (const x of f) {
        const o = this._objets.get(x.properties.i);
        if (!o || o.options.interactive === false || o instanceof Decorator) continue;
        const k = [rang.get(x.layer.id) || 0, x.properties.s];
        if (!cle || k[0] > cle[0] || (k[0] === cle[0] && k[1] > cle[1])) { cle = k; meilleur = o; }
      }
      return meilleur;
    },
    _evenement(e, cible) {
      const ll = e.lngLat ? new LatLng(e.lngLat.lat, e.lngLat.lng) : cible && cible._latlng;
      const cp = e.point ? new Point(e.point.x, e.point.y) : null;
      return { latlng: ll, containerPoint: cp, layerPoint: cp, originalEvent: e.originalEvent || e, layer: cible, target: cible || this, sourceTarget: cible || this };
    },
    _fermerAuClic() { const p = this._fenetre; if (p && (p.options.closeOnClick !== undefined ? p.options.closeOnClick : this.options.closePopupOnClick !== false)) this.closePopup(); },
    _clic(e, type) {
      const t = e.originalEvent && e.originalEvent.target;
      if (t && t.closest && t.closest('.maplibregl-marker, .leaflet-control-container')) return;
      const o = this._objetSous(e.point);
      if (type === 'click' && !(o && o._popup && this._fenetre === o._popup)) this._fermerAuClic();
      const ev = this._evenement(e, o);
      let arret = false;
      if (o) {
        if (type === 'contextmenu' && o.listens('contextmenu')) e.preventDefault();
        o.fire(type, ev, true);
        arret = ev._arret || ev.originalEvent._arret || o.options.bubblingMouseEvents === false;
        if (type === 'click' && (o._popup || o._popupDuParent())) o.openPopup(ev.latlng);
      }
      if (type === 'dblclick' && o) e.preventDefault();
      if (!arret) { ev.target = this; ev.sourceTarget = this; ev.layer = undefined; this.fire(type, ev); }
    },
    _clicMarqueur(m, domEv, type) {
      if (type === 'click' && !(m._popup && this._fenetre === m._popup)) this._fermerAuClic();
      const ev = m._ev(this, domEv);
      m.fire(type, ev, true);
      if (type === 'click' && (m._popup || m._popupDuParent())) m.openPopup();
    },
    _survol(e) {
      const t = e.originalEvent && e.originalEvent.target;
      if (t && t.closest && t.closest('.maplibregl-marker')) return;
      this._dernierSurvol = e;
      if (this._survolPrevu) return;
      this._survolPrevu = true;
      requestAnimationFrame(() => {
        this._survolPrevu = false;
        const ev0 = this._dernierSurvol, o = this._objetSous(ev0.point), ev = this._evenement(ev0, o);
        const ancien = this._survolCourant;
        if (ancien !== o) {
          if (ancien && ancien._map) ancien.fire('mouseout', extend({}, ev, { target: ancien, layer: ancien }), true);
          if (o) o.fire('mouseover', ev, true);
          this._survolCourant = o;
        } else if (o) o.fire('mousemove', ev, true);
        const outil = this._el.classList.contains('outil-actif') || this._el.classList.contains('leaflet-crosshair');
        this._gl.getCanvas().style.cursor = outil ? 'crosshair' : o ? 'pointer' : '';
        const b = o && (o._tooltip || o._tooltipDuParent());
        if (b && !b.options.permanent) this._montrerBulle(o, ev.latlng); else if (!b || !b.options.permanent) this._cacherBulle();
        this.fire('mousemove', extend({}, ev, { target: this, layer: undefined }));
      });
    },
    _sortie() { const a = this._survolCourant; if (a && a._map) a.fire('mouseout', { target: a }, true); this._survolCourant = null; this._cacherBulle(); this._gl.getCanvas().style.cursor = ''; },
    _montrerBulle(o, ll) {
      const b = o._tooltip || o._tooltipDuParent();
      if (!b || b.options.permanent) return;
      const sticky = b.options.sticky && !(o instanceof Marker);
      const pos = sticky ? ll : (o instanceof Marker || o instanceof CircleMarker ? o._latlng : (this._bulleObjet === o && this._bulle ? null : ll));
      b._source = o;
      if (this._bulleObjet !== o || !b._map) {
        this._cacherBulle();
        b.setLatLng(pos || ll);
        this.addLayer(b);
        this._bulle = b; this._bulleObjet = o;
      } else if (pos) b.setLatLng(pos);
    },
    _cacherBulle(o) {
      if (o && this._bulleObjet !== o) return;
      if (this._bulle && this._bulle._map && !this._bulle.options.permanent) this.removeLayer(this._bulle);
      this._bulle = null; this._bulleObjet = null;
    },
    // --- fenêtres ---
    openPopup(p, ll, options) {
      if (!(p instanceof Popup)) { const c = p; p = new Popup(options); p.setContent(c); p.setLatLng(ll); }
      else if (ll) p.setLatLng(ll);
      if (this._fenetre && this._fenetre !== p && this._fenetre.options.autoClose !== false) this.closePopup(this._fenetre);
      this._fenetre = p;
      if (!p._map) { p._map = this; p._ajouter(this); }
      else { p._majContenu(); p._majPos(); if (p.options.autoPan) p._recentrer(); }
      return this;
    },
    closePopup(p) {
      p = p || this._fenetre;
      if (!p || !p._map) return this;
      this.removeLayer(p);
      return this;
    },
    openTooltip(t, ll) { if (!(t instanceof Tooltip)) { const c = t; t = new Tooltip(); t.setContent(c); } if (ll) t.setLatLng(ll); this.addLayer(t); return this; },
    closeTooltip(t) { if (t) this.removeLayer(t); return this; },
    // --- vue (zoom compté comme Leaflet) ---
    getZoom() { return this._gl.getZoom() + ECART_ZOOM; },
    getMinZoom() { return this._gl.getMinZoom() + ECART_ZOOM; },
    getMaxZoom() { return this._gl.getMaxZoom() + ECART_ZOOM; },
    setMinZoom(z) { this._gl.setMinZoom(Math.max(0, z - ECART_ZOOM)); return this; },
    setMaxZoom(z) { this._gl.setMaxZoom(z - ECART_ZOOM); return this; },
    setMaxBounds(b) { this._gl.setMaxBounds(b ? latLngBounds(b)._lngLat() : null); return this; },
    getCenter() { const c = this._gl.getCenter(); return new LatLng(c.lat, c.lng); },
    getBounds() { const b = this._gl.getBounds(); return new LatLngBounds([b.getSouth(), b.getWest()], [b.getNorth(), b.getEast()]); },
    getSize() { return new Point(this._el.clientWidth, this._el.clientHeight); },
    getPixelBounds() { const c = this.project(this.getCenter()), s = this.getSize().divideBy(2); return new Bounds(c.subtract(s), c.add(s)); },
    _arrondir(z) { const p = this.options.zoomSnap; return p ? Math.round(z / p) * p : z; },
    setView(ll, z, options) {
      const p = latLng(ll) || this.getCenter(), cible = { center: [p.lng, p.lat], zoom: (z === undefined || z === null ? this.getZoom() : z) - ECART_ZOOM };
      this._vue = true;
      if ((options && options.animate === false) || !this._loaded) this._gl.jumpTo(cible); else this._gl.easeTo(extend(cible, { duration: (options && options.duration ? options.duration * 1000 : 300) }));
      return this;
    },
    setZoom(z, o) { return this.setView(this.getCenter(), z, o); },
    zoomIn(d, o) { return this.setZoom(this.getZoom() + (d || this.options.zoomDelta), o); },
    zoomOut(d, o) { return this.setZoom(this.getZoom() - (d || this.options.zoomDelta), o); },
    setZoomAround(ll, z) { const p = latLng(ll) || this.containerPointToLatLng(ll); this._gl.easeTo({ zoom: z - ECART_ZOOM, around: [p.lng, p.lat], duration: 250 }); return this; },
    panTo(ll, o) { const p = latLng(ll); this._gl.panTo([p.lng, p.lat], o && o.animate === false ? { duration: 0 } : { duration: 300 }); return this; },
    panBy(off, o) { const p = point(off); this._gl.panBy([p.x, p.y], o && o.animate === false ? { duration: 0 } : { duration: 250 }); return this; },
    panInside(ll) { if (!this.getBounds().contains(ll)) this.panTo(ll); return this; },
    flyTo(ll, z, o) { const p = latLng(ll); this._vue = true; this._gl.flyTo({ center: [p.lng, p.lat], zoom: (z === undefined ? this.getZoom() : z) - ECART_ZOOM, duration: o && o.duration ? o.duration * 1000 : 1200 }); return this; },
    flyToBounds(b, o) { return this.fitBounds(b, extend({}, o, { _vol: true })); },
    // Options de Leaflet : padding, paddingTopLeft, paddingBottomRight ([x, y]), maxZoom, animate
    getBoundsZoom(b, inside, pad) {
      const bb = latLngBounds(b), s = this.getSize().subtract(point(pad || [0, 0]));
      const a = projeter(bb.getNorthWest(), 0), c = projeter(bb.getSouthEast(), 0);
      const z = Math.log2(Math.min(s.x / Math.max(c.x - a.x, 1e-9), s.y / Math.max(c.y - a.y, 1e-9)));
      const pas = this.options.zoomSnap;
      return Math.min(this.getMaxZoom(), pas ? Math.floor(z / pas + 1e-6) * pas : z);
    },
    fitBounds(b, options) {
      const o = options || {}, bb = latLngBounds(b);
      if (!bb.isValid()) return this;
      const base = point(o.padding || [0, 0]), tl = point(o.paddingTopLeft || [0, 0]).add(base), br = point(o.paddingBottomRight || [0, 0]).add(base);
      const s = this.getSize();
      const kx = tl.x + br.x > s.x - 20 ? (s.x - 20) / (tl.x + br.x) : 1, ky = tl.y + br.y > s.y - 20 ? (s.y - 20) / (tl.y + br.y) : 1;
      const pad = { left: tl.x * kx, right: br.x * kx, top: tl.y * ky, bottom: br.y * ky };
      let z = this.getBoundsZoom(bb, false, [pad.left + pad.right, pad.top + pad.bottom]);
      if (o.maxZoom !== undefined) z = Math.min(z, o.maxZoom);
      z = Math.max(z, this.getMinZoom());
      // Centre : milieu des bornes, décalé au milieu de la zone utile (sans le rembourrage), au zoom retenu
      const a = projeter(bb.getNorthWest(), z), d = projeter(bb.getSouthEast(), z);
      const p = new Point((a.x + d.x) / 2 - (pad.left - pad.right) / 2, (a.y + d.y) / 2 - (pad.top - pad.bottom) / 2);
      const c = deprojeter(p, z);
      this._vue = true;
      const cible = { center: [c.lng, c.lat], zoom: z - ECART_ZOOM };
      if (o._vol) this._gl.flyTo(extend(cible, { duration: o.duration ? o.duration * 1000 : 1200 }));
      else if (o.animate === false || !this._loaded) this._gl.jumpTo(cible); else this._gl.easeTo(extend(cible, { duration: 350 }));
      return this;
    },
    fitWorld(o) { return this.fitBounds([[-60, -170], [75, 170]], o); },
    stop() { this._gl.stop(); return this; },
    whenReady(f, ctx) { if (this._loaded) f.call(ctx || this, { target: this }); else this.once('load', f, ctx); return this; },
    invalidateSize() { this._gl.resize(); return this; },
    remove() { this._gl.remove(); this._el.classList.remove('leaflet-container'); delete this._el._leaflet_id; this.fire('unload'); return this; },
    latLngToContainerPoint(ll) { const p = latLng(ll), q = this._gl.project([p.lng, p.lat]); return new Point(q.x, q.y); },
    containerPointToLatLng(pt) { const p = point(pt), q = this._gl.unproject([p.x, p.y]); return new LatLng(q.lat, q.lng); },
    latLngToLayerPoint(ll) { return this.latLngToContainerPoint(ll); },
    layerPointToLatLng(pt) { return this.containerPointToLatLng(pt); },
    containerPointToLayerPoint(pt) { return point(pt); },
    layerPointToContainerPoint(pt) { return point(pt); },
    mouseEventToContainerPoint(e) { const r = this._el.getBoundingClientRect(); return new Point(e.clientX - r.left, e.clientY - r.top); },
    mouseEventToLatLng(e) { return this.containerPointToLatLng(this.mouseEventToContainerPoint(e)); },
    mouseEventToLayerPoint(e) { return this.mouseEventToContainerPoint(e); },
    project(ll, z) { return projeter(ll, z === undefined ? this.getZoom() : z); },
    unproject(p, z) { return deprojeter(p, z === undefined ? this.getZoom() : z); },
    distance(a, b) { return latLng(a).distanceTo(b); },
    wrapLatLng(ll) { return latLng(ll); },
    _coin(position) { return this._coins[position] || this._coins.topright; },
    addControl(c) { c.addTo(this); return this; },
    removeControl(c) { c.remove(); return this; },
    getRenderer() { return {}; }
  });

  // ---------- DomUtil, DomEvent, Browser ----------
  const DomUtil = {
    get: id => typeof id === 'string' ? document.getElementById(id) : id,
    create(tag, cls, parent) { const el = document.createElement(tag); if (cls) el.className = cls; if (parent) parent.appendChild(el); return el; },
    remove(el) { if (el && el.parentNode) el.parentNode.removeChild(el); },
    empty(el) { while (el.firstChild) el.removeChild(el.firstChild); },
    addClass(el, c) { el.classList.add(...c.split(/\s+/).filter(Boolean)); },
    removeClass(el, c) { el.classList.remove(...c.split(/\s+/).filter(Boolean)); },
    hasClass(el, c) { return el.classList.contains(c); },
    toggleClass(el, c) { el.classList.toggle(c); },
    setClass(el, c) { el.className = c; },
    getClass(el) { return el.className; },
    setOpacity(el, v) { el.style.opacity = v; },
    setPosition(el, p) { el._leaflet_pos = point(p); el.style.transform = 'translate(' + el._leaflet_pos.x + 'px,' + el._leaflet_pos.y + 'px)'; },
    getPosition(el) { return el._leaflet_pos || new Point(0, 0); },
    setTransform(el, off, s) { const p = point(off || [0, 0]); el.style.transform = 'translate(' + p.x + 'px,' + p.y + 'px)' + (s ? ' scale(' + s + ')' : ''); },
    toFront(el) { el.parentNode.appendChild(el); }, toBack(el) { el.parentNode.insertBefore(el, el.parentNode.firstChild); },
    disableTextSelection() {}, enableTextSelection() {}, disableImageDrag() {}, enableImageDrag() {}
  };
  const DomEvent = {
    on(el, types, fn, ctx) { if (typeof types === 'object') { for (const t in types) DomEvent.on(el, t, types[t], fn); return this; }
      for (const t of types.split(/\s+/)) el.addEventListener(t, ctx ? fn.bind(ctx) : fn); return this; },
    off(el, types, fn) { if (typeof types === 'object') { for (const t in types) DomEvent.off(el, t, types[t]); return this; } for (const t of types.split(/\s+/)) el.removeEventListener(t, fn); return this; },
    stopPropagation(e) { if (!e) return this; e._arret = true; if (e.originalEvent) e.originalEvent._arret = true; if (e.stopPropagation) e.stopPropagation(); return this; },
    preventDefault(e) { if (e && e.preventDefault) e.preventDefault(); else if (e && e.originalEvent && e.originalEvent.preventDefault) e.originalEvent.preventDefault(); return this; },
    stop(e) { DomEvent.preventDefault(e); DomEvent.stopPropagation(e); return this; },
    disableClickPropagation(el) { ['click', 'dblclick', 'mousedown', 'touchstart', 'pointerdown', 'contextmenu'].forEach(t => el.addEventListener(t, e => e.stopPropagation())); return this; },
    disableScrollPropagation(el) { el.addEventListener('wheel', e => e.stopPropagation()); return this; },
    getMousePosition(e, c) { if (!c) return new Point(e.clientX, e.clientY); const r = c.getBoundingClientRect(); return new Point(e.clientX - r.left, e.clientY - r.top); }
  };
  DomEvent.addListener = DomEvent.on; DomEvent.removeListener = DomEvent.off;
  const Browser = { mobile: TACTILE, touch: 'ontouchstart' in window, retina: window.devicePixelRatio > 1, pointer: true, canvas: true, svg: true,
    android: /android/i.test(navigator.userAgent), safari: /^((?!chrome|android).)*safari/i.test(navigator.userAgent), chrome: /chrome/i.test(navigator.userAgent),
    ie: false, edge: false, webkit: /webkit/i.test(navigator.userAgent), gecko: /gecko\//i.test(navigator.userAgent), any3d: true, mac: /mac/i.test(navigator.platform) };
  const LineUtil = { isFlat: ll => !Array.isArray(ll[0]) || (typeof ll[0][0] !== 'object' && typeof ll[0][0] !== 'undefined') };

  // ---------- Espace de noms L ----------
  const fabrique = C => (...a) => new C(...a);
  const L = {
    version: '1.9.4-carte (MapLibre ' + (ml.getVersion ? ml.getVersion() : '') + ')',
    Class, Evented, Mixin: { Events }, Util, extend, setOptions, stamp, bind: Util.bind,
    LatLng, LatLngBounds, Point, Bounds, CRS, Projection: { SphericalMercator: { project: ll => projeter(ll, 0), unproject: p => deprojeter(p, 0) } },
    latLng, latLngBounds, point, bounds: (a, b) => new Bounds(a, b),
    Layer, Path, Polyline, Polygon, Rectangle, CircleMarker, Circle, Marker, Icon, DivIcon, ImageOverlay, TileLayer, LayerGroup, FeatureGroup, GeoJSON,
    Popup, Tooltip, Control, Map: Map_, Renderer: Class.extend({}), Canvas: Class.extend({}), SVG: Class.extend({}),
    map: fabrique(Map_), polyline: fabrique(Polyline), polygon: fabrique(Polygon), rectangle: fabrique(Rectangle), circleMarker: fabrique(CircleMarker),
    circle: fabrique(Circle), marker: fabrique(Marker), icon: fabrique(Icon), divIcon: fabrique(DivIcon), imageOverlay: fabrique(ImageOverlay),
    tileLayer: fabrique(TileLayer), layerGroup: fabrique(LayerGroup), featureGroup: fabrique(FeatureGroup),
    geoJSON: fabrique(GeoJSON), geoJson: fabrique(GeoJSON), popup: fabrique(Popup), tooltip: fabrique(Tooltip), control,
    canvas: o => extend({}, o), svg: o => extend({}, o),     // « rendus » de Leaflet : sans effet, MapLibre dessine tout
    polylineDecorator: fabrique(Decorator), PolylineDecorator: Decorator, Symbol: Symbol_,
    DomUtil, DomEvent, Browser, LineUtil, noConflict() { return L; }
  };
  TileLayer.WMS = TileLayer; L.tileLayer.wms = L.tileLayer;
  window.L = L;
})();
