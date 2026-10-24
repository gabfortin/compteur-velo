// Compteurs Vélo Montréal — logique de la page.
//
// Ce fichier est statique : il n'est plus généré par genMap.py. Les données
// (data/site.json) sont chargées par assets/boot.js, qui les expose dans
// window.SITE_DATA puis charge ce script.

const allChartData = {};   // horaire, HOURLY_DAYS derniers jours
const allDailyData = {};   // totaux journaliers, fenêtre complète
const hourProfile  = {};   // {période: [24 totaux par heure]} pour périodes > HOURLY_DAYS
const markers = {};
let map;
const chartData = {};
const charts = {};
const countersByArrondissement = {};
let specificDate = null;
let combinedInstance = null;
let currentView = 'main';
// Désactivé jusqu'à ce que la restauration initiale depuis l'URL soit réglée,
// pour que la sélection du compteur par défaut n'écrase pas un lien partagé.
let hashSyncEnabled = false;


// ── Données (data/site.json) ─────────────────────────────────────────────────
// Les étiquettes horaires sont stockées en plages d'heures consécutives
// ([première étiquette, nombre d'heures]) pour alléger le fichier.
function expandHourRuns(runs) {
    const pad = n => String(n).padStart(2, '0');
    const labels = [];
    runs.forEach(([start, count]) => {
        const t0 = Date.UTC(+start.slice(0, 4), +start.slice(5, 7) - 1, +start.slice(8, 10),
                            +start.slice(11, 13), +start.slice(14, 16));
        for (let k = 0; k < count; k++) {
            const d = new Date(t0 + k * 3600000);
            labels.push(`${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
                        `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`);
        }
    });
    return labels;
}

const HOURLY_DAYS = SITE_DATA.HOURLY_DAYS;
Object.entries(SITE_DATA.allChartData).forEach(([id, c]) => {
    allChartData[id] = { labels: expandHourRuns(c.hours), datasets: c.datasets };
});
Object.assign(allDailyData, SITE_DATA.allDailyData);
Object.assign(hourProfile, SITE_DATA.hourProfile);
Object.assign(countersByArrondissement, SITE_DATA.countersByArrondissement);
const counterLocations = SITE_DATA.counterLocations;
const gappyCounters = new Set(SITE_DATA.gappyCounters);
const anomalyDays = SITE_DATA.anomalyDays;
const vfDataQuality = SITE_DATA.vfDataQuality;
// Coupure de financement municipal (printemps 2026) : la Ville a cessé de financer
// le traitement des données Éco-Compteur, et certains compteurs n'ont transmis aucune
// donnée valide pendant plusieurs semaines (reprise début mai 2026). Plages vérifiées
// compteur par compteur à partir des données journalières (trou commençant entre
// janvier et avril 2026, avec reprise en mai 2026) — ne touche pas tout le réseau.
const CITY_FUNDING_GAP_SOURCE = {
    label: '24 Heures, 1 avril 2026',
    url: 'https://www.24heures.ca/2026/04/01/le-comptage-velo-est-suspendu-a-montreal-faute-de-financement',
};
const CITY_FUNDING_GAP = {
    'det-00967-13': ['2026-02-09', '2026-05-27'],
    'det-00970-01': ['2026-02-09', '2026-05-27'],
    'det-00976-02': ['2026-02-09', '2026-05-27'],
    'vf-100034805': ['2026-01-20', '2026-05-22'],
    'vf-100055268': ['2026-03-20', '2026-05-05'],
    'vf-300046833': ['2026-03-20', '2026-05-05'],
};
const weatherData = SITE_DATA.weatherData;
const bixiNearby = SITE_DATA.bixiNearby;
const bixiExceedsDays = SITE_DATA.bixiExceedsDays;
const bixiYearly = SITE_DATA.bixiYearly;
const historicalYearlyTotals = SITE_DATA.historicalYearlyTotals;
const historicalYearlyCounters = SITE_DATA.historicalYearlyCounters;
const counterHistoricalData = SITE_DATA.counterHistoricalData;
const globalStats = SITE_DATA.globalStats;


function themeColor(color) { return color; }

function parseLabel(label) {
    return new Date(label.replace(' ', 'T'));
}

function getMaxDate(labels) {
    let max = null;
    labels.forEach(label => {
        const d = parseLabel(label);
        if (!max || d > max) max = d;
    });
    return max;
}

let displayMode = 'combined';
let viewMode = 'timeline';
let showBixi = false;
let histMode    = null;
let histSubMode = 'annual';

function dateStr(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function maxDateStr(instance) {
    const maxDate = getMaxDate(allChartData[instance].labels);
    return maxDate ? dateStr(maxDate) : null;
}

function buildFilteredData(instance, days) {
    // Au-delà de HOURLY_DAYS, seules les données journalières existent
    // (la vue timeline est alors désactivée).
    if (days > HOURLY_DAYS) return buildDailyData(instance, days);
    const allLabels = allChartData[instance].labels;
    const allDatasets = allChartData[instance].datasets;
    // Ancrer la fenêtre sur la date max propre à ce compteur,
    // pour éviter qu'un écart de fraîcheur entre sources (ex. det- vs vf-)
    // ne décale la fenêtre vers une période sans données.
    const instMax = getMaxDate(allLabels) || globalMaxDate;

    let indices;
    if (days === 0) {
        if (!specificDate) return { labels: [], datasets: [] };
        indices = allLabels.map((l, i) => l.startsWith(specificDate) ? i : -1).filter(i => i >= 0);
    } else {
        if (!instMax) return { labels: [], datasets: [] };
        const cutoffDate = new Date(instMax.getFullYear(), instMax.getMonth(), instMax.getDate() - (days - 1));
        indices = allLabels.map((l, i) => parseLabel(l) >= cutoffDate ? i : -1).filter(i => i >= 0);
    }

    // Pour la vue 7 jours, générer la grille horaire complète et remplir
    // les heures manquantes avec null (les jours sans données apparaissent comme des gaps)
    if (days === 7 && instMax) {
        const cutoff = new Date(instMax.getFullYear(), instMax.getMonth(), instMax.getDate() - 6);
        cutoff.setHours(0, 0, 0, 0);
        const fullHours = [];
        const cur = new Date(cutoff);
        while (cur <= instMax) {
            const lbl = `${cur.getFullYear()}-${String(cur.getMonth()+1).padStart(2,'0')}-${String(cur.getDate()).padStart(2,'0')} ${String(cur.getHours()).padStart(2,'0')}:00`;
            fullHours.push(lbl);
            cur.setHours(cur.getHours() + 1);
        }
        const labelToIdx = {};
        indices.forEach(i => { labelToIdx[allLabels[i]] = i; });
        const hasMissing = fullHours.some(h => !(h in labelToIdx));
        if (hasMissing) {
            const isMulti = allDatasets.length > 1;
            const showCombined = isMulti && displayMode === 'combined';
            if (showCombined) {
                return { labels: fullHours, datasets: [{ label: 'Combiné',
                    data: fullHours.map(lbl => lbl in labelToIdx ? allDatasets.reduce((s, ds) => s + (ds.data[labelToIdx[lbl]] || 0), 0) : 0),
                    borderColor: themeColor('#1DB860'), backgroundColor: themeColor('rgba(29,184,96,0.15)'),
                    fill: true, tension: 0.3, borderWidth: 1, pointRadius: 2, pointHoverRadius: 5 }] };
            }
            return { labels: fullHours, datasets: allDatasets.map(ds => ({
                label: ds.label,
                data: fullHours.map(lbl => lbl in labelToIdx ? ds.data[labelToIdx[lbl]] : 0),
                borderColor: themeColor(ds.color), backgroundColor: themeColor(ds.fill),
                fill: !isMulti, tension: 0.3, borderWidth: 1, pointRadius: 2, pointHoverRadius: 5
            })) };
        }
    }

    const filteredLabels = indices.map(i => allLabels[i]);
    const isMulti = allDatasets.length > 1;
    const showCombined = isMulti && displayMode === 'combined';

    if (showCombined) {
        const combined = indices.map(i =>
            allDatasets.reduce((sum, ds) => sum + (ds.data[i] || 0), 0)
        );
        return {
            labels: filteredLabels,
            datasets: [{ label: 'Combiné', data: combined, borderColor: themeColor('#1DB860'), backgroundColor: themeColor('rgba(29,184,96,0.15)'), fill: true, tension: 0.3, borderWidth: 1, pointRadius: 2, pointHoverRadius: 5 }]
        };
    }

    const isSingle = !isMulti;
    return {
        labels: filteredLabels,
        datasets: allDatasets.map(ds => ({
            label: ds.label,
            data: indices.map(i => ds.data[i]),
            borderColor: themeColor(ds.color),
            backgroundColor: themeColor(ds.fill),
            fill: isSingle,
            tension: 0.3,
            borderWidth: 1,
            pointRadius: 2,
            pointHoverRadius: 5
        }))
    };
}

function buildDailyData(instance, days) {
    const daily = allDailyData[instance];
    const allLabels = daily.labels;
    const allDatasets = allChartData[instance].datasets.map((ds, k) => ({ ...ds, data: daily.data[k] }));
    const instMax = getMaxDate(allChartData[instance].labels) || globalMaxDate;
    if (!instMax) return { labels: [], datasets: [] };
    const cutoffStr = dateStr(new Date(instMax.getFullYear(), instMax.getMonth(), instMax.getDate() - (days - 1)));
    const indices = allLabels.map((l, i) => l >= cutoffStr ? i : -1).filter(i => i >= 0);
    const daySet = {};
    indices.forEach(i => { daySet[allLabels[i].slice(0, 10)] = true; });
    let dayList = Object.keys(daySet).sort();

    // Pour la vue 7 jours, générer tous les jours du calendrier
    // (les jours sans données apparaissent comme des barres à 0, potentiellement en rouge)
    if (days === 7 && instMax) {
        const fullRange = [];
        const end = new Date(instMax.getFullYear(), instMax.getMonth(), instMax.getDate());
        const start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - 6);
        const cur = new Date(start);
        while (cur <= end) {
            fullRange.push(`${cur.getFullYear()}-${String(cur.getMonth()+1).padStart(2,'0')}-${String(cur.getDate()).padStart(2,'0')}`);
            cur.setDate(cur.getDate() + 1);
        }
        dayList = fullRange;
    }

    const anomInst = (typeof anomalyDays !== 'undefined' && anomalyDays[instance]) ? anomalyDays[instance] : {};
    function barBg(d, base)  { return anomInst[d] ? 'rgba(245,158,11,0.65)' : base; }
    function barBd(d, base)  { return anomInst[d] ? 'rgba(245,158,11,0.9)'  : base; }

    const isMulti = allDatasets.length > 1;
    const showCombined = isMulti && displayMode === 'combined';
    if (!isMulti || showCombined) {
        const totals = {};
        dayList.forEach(d => totals[d] = 0);
        indices.forEach(i => {
            const day = allLabels[i].slice(0, 10);
            if (day in totals) allDatasets.forEach(ds => { totals[day] += (ds.data[i] || 0); });
        });
        const defBg = themeColor('rgba(29,184,96,0.75)'), defBd = themeColor('#1DB860');
        return { labels: dayList, datasets: [{ label: showCombined ? 'Combiné' : allDatasets[0].label, data: dayList.map(d => totals[d]),
            backgroundColor: dayList.map(d => barBg(d, defBg)), borderColor: dayList.map(d => barBd(d, defBd)), borderWidth: 1, borderRadius: 4 }] };
    }
    return {
        labels: dayList,
        datasets: allDatasets.map((ds, i) => {
            const totals = {};
            dayList.forEach(d => totals[d] = 0);
            indices.forEach(idx => {
                const day = allLabels[idx].slice(0, 10);
                if (day in totals) totals[day] += (ds.data[idx] || 0);
            });
            const defBg = themeColor(i === 0 ? 'rgba(29,184,96,0.75)' : 'rgba(41,171,226,0.75)');
            const defBd = themeColor(ds.color);
            return { label: ds.label, data: dayList.map(d => totals[d]),
                backgroundColor: dayList.map(d => barBg(d, defBg)), borderColor: dayList.map(d => barBd(d, defBd)), borderWidth: 1, borderRadius: 4 };
        })
    };
}

// Jours avec des données parmi les `days` derniers jours du compteur
function daysWithData(instance, days) {
    const instMax = getMaxDate(allChartData[instance].labels) || globalMaxDate;
    if (!instMax) return [];
    const cutoffStr = dateStr(new Date(instMax.getFullYear(), instMax.getMonth(), instMax.getDate() - (days - 1)));
    return allDailyData[instance].labels.filter(l => l >= cutoffStr);
}

function buildCombinedFilteredData(inst1, inst2, days) {
    const d1 = buildFilteredData(inst1, days);
    const d2 = buildFilteredData(inst2, days);
    const map1 = {};
    d1.labels.forEach((lbl, i) => { map1[lbl] = d1.datasets.reduce((s, ds) => s + (ds.data[i] || 0), 0); });
    const map2 = {};
    d2.labels.forEach((lbl, i) => { map2[lbl] = d2.datasets.reduce((s, ds) => s + (ds.data[i] || 0), 0); });
    const allLabels = [...new Set([...d1.labels, ...d2.labels])].sort();
    const combined = allLabels.map(lbl => (map1[lbl] || 0) + (map2[lbl] || 0));
    return { labels: allLabels, datasets: [{ label: 'Total combiné', data: combined,
        borderColor: themeColor('#1DB860'), backgroundColor: themeColor('rgba(29,184,96,0.15)'),
        fill: true, tension: 0.3, borderWidth: 1.5, pointRadius: 2, pointHoverRadius: 5 }] };
}

function buildCombinedDailyData(inst1, inst2, days) {
    const d1 = buildDailyData(inst1, days);
    const d2 = buildDailyData(inst2, days);
    const map1 = {};
    d1.labels.forEach((lbl, i) => { map1[lbl] = d1.datasets.reduce((s, ds) => s + (ds.data[i] || 0), 0); });
    const map2 = {};
    d2.labels.forEach((lbl, i) => { map2[lbl] = d2.datasets.reduce((s, ds) => s + (ds.data[i] || 0), 0); });
    const allLabels = [...new Set([...d1.labels, ...d2.labels])].sort();
    const combined = allLabels.map(lbl => (map1[lbl] || 0) + (map2[lbl] || 0));
    const defBg = themeColor('rgba(29,184,96,0.75)'), defBd = themeColor('#1DB860');
    return { labels: allLabels, datasets: [{ label: 'Total combiné', data: combined,
        backgroundColor: defBg, borderColor: defBd, borderWidth: 1, borderRadius: 4 }] };
}

const globalMaxDate = (function() {
    let max = null;
    for (let inst in allChartData) {
        const d = getMaxDate(allChartData[inst].labels);
        if (d && (!max || d > max)) max = d;
    }
    return max;
})();

function initializeChartData() {
    for (let instance in allChartData) {
        chartData[instance] = buildFilteredData(instance, 7);
    }
}
initializeChartData();

function updateDayLabel(instance) {
    const el = document.getElementById('dayLabel');
    if (currentPeriod === 0 && specificDate) {
        const d = new Date(specificDate + 'T12:00:00');
        const s = d.toLocaleDateString('fr-CA', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
        el.textContent = s.charAt(0).toUpperCase() + s.slice(1);
        el.style.display = 'block';
        return;
    }
    el.style.display = 'none';
}

function animateCount(el, target) {
    if (!el) return;
    const duration = 700;
    const start = parseInt(el.textContent.replace(/\D/g, '')) || 0;
    const t0 = performance.now();
    function step(now) {
        const t = Math.min((now - t0) / duration, 1);
        const eased = 1 - Math.pow(1 - t, 3);
        el.textContent = Math.round(start + (target - start) * eased).toLocaleString('fr-CA');
        if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
}

function updateStats(instance) {
    const statsRow = document.getElementById('statsRow');
    if (!instance) { statsRow.style.display = 'none'; return; }
    let labels, datasets;
    if (combinedInstance && allChartData[combinedInstance]) {
        const combData = buildCombinedFilteredData(instance, combinedInstance, currentPeriod);
        labels = combData.labels;
        datasets = combData.datasets;
    } else {
        if (!chartData[instance]) { statsRow.style.display = 'none'; return; }
        labels = chartData[instance].labels;
        datasets = chartData[instance].datasets;
    }
    // Combine all directions
    const combined = labels.map((_, i) =>
        datasets.reduce((sum, ds) => sum + (ds.data[i] || 0), 0)
    );
    const total = combined.reduce((a, b) => a + b, 0);
    // Moyenne sur les jours où le compteur a transmis : la vue 7 jours complète les
    // heures manquantes par des zéros pour le graphique, qui ne doivent pas compter.
    let uniqueDays;
    if (currentPeriod > 0) {
        const days = new Set(daysWithData(instance, currentPeriod));
        if (combinedInstance && allChartData[combinedInstance]) daysWithData(combinedInstance, currentPeriod).forEach(d => days.add(d));
        uniqueDays = days.size;
    } else {
        uniqueDays = new Set(labels.map(l => l.slice(0, 10))).size;
    }
    const avg = uniqueDays > 0 ? Math.round(total / uniqueDays) : 0;
    const hourTotals = {};
    if (currentPeriod > HOURLY_DAYS) {
        // Données journalières seulement : profil horaire précalculé
        [instance, combinedInstance].filter(Boolean).forEach(inst => {
            const prof = hourProfile[inst] && hourProfile[inst][currentPeriod];
            if (prof) prof.forEach((v, h) => {
                if (v) { const k = String(h).padStart(2, '0'); hourTotals[k] = (hourTotals[k] || 0) + v; }
            });
        });
    } else {
        labels.forEach((label, i) => {
            const h = label.slice(11, 13);
            if (h) hourTotals[h] = (hourTotals[h] || 0) + combined[i];
        });
    }
    const peaks = Object.entries(hourTotals).sort((a, b) => b[1] - a[1]);
    const peak1 = peaks[0];
    const peak2 = peak1
        ? peaks.find(p => Math.abs(parseInt(p[0]) - parseInt(peak1[0])) >= 3) || null
        : null;
    statsRow.style.display = 'grid';
    animateCount(document.getElementById('statTotal'), total);
    animateCount(document.getElementById('statAvg'), avg);
    const fmtAvg = p => '~' + Math.round(p[1] / uniqueDays);
    if (peak1) {
        const hours = peak1[0] + 'h' + (peak2 ? ' · ' + peak2[0] + 'h' : '');
        const avgs  = fmtAvg(peak1) + (peak2 ? ' · ' + fmtAvg(peak2) : '') + ' passages/heure';
        document.getElementById('statPeak').innerHTML =
            hours + '<br><span style="font-size:0.65em;opacity:0.65">' + avgs + '</span>';
    } else {
        document.getElementById('statPeak').textContent = '—';
    }
}

function createChart(id) {
    if (!charts[id]) {
        const ctx = document.getElementById('chart-' + id);
        if (ctx) {
            const isDaily = viewMode === 'daily';
            let rawData = isDaily ? buildDailyData(id, currentPeriod) : chartData[id];
            if (combinedInstance && allChartData[combinedInstance]) {
                rawData = isDaily
                    ? buildCombinedDailyData(id, combinedInstance, currentPeriod)
                    : buildCombinedFilteredData(id, combinedInstance, currentPeriod);
            }
            const type = isDaily ? 'bar' : 'line';
            const isSingle = rawData.datasets.length === 1;

            const bixiInst = (showBixi && typeof bixiNearby !== 'undefined' && bixiNearby[id]) ? bixiNearby[id] : null;
            let data = rawData;
            let hasBixi = false;
            if (bixiInst) {
                const bixiVals = rawData.labels.map(lbl => bixiInst[lbl.slice(0, 10)] ?? null);
                if (bixiVals.some(v => v !== null)) {
                    hasBixi = true;
                    const bixiDs = isDaily
                        ? { label: 'Bixi', data: bixiVals, type: 'bar',
                            backgroundColor: 'rgba(220,38,38,0.25)', borderColor: 'rgba(220,38,38,0.55)',
                            borderWidth: 1, borderRadius: 3, yAxisID: 'y', order: 0 }
                        : { label: 'Bixi', data: bixiVals, type: 'line', stepped: 'before',
                            pointRadius: 0, borderWidth: 1, borderColor: 'rgba(220,38,38,0.55)',
                            backgroundColor: 'rgba(220,38,38,0.06)', fill: true, tension: 0,
                            yAxisID: 'y', borderDash: [3, 2], order: 10 };
                    data = { labels: rawData.labels, datasets: [...rawData.datasets, bixiDs] };
                }
            }

            if (!isDaily && isSingle && rawData.datasets[0].fill) {
                const gradient = ctx.getContext('2d').createLinearGradient(0, 0, 0, 380);
                gradient.addColorStop(0, themeColor('rgba(29,184,96,0.28)'));
                gradient.addColorStop(1, 'rgba(0,0,0,0)');
                rawData.datasets[0].backgroundColor = gradient;
            }

            // ── Visualisation des données manquantes ──
            // Désactivé en mode combiné (les anomalies n'appartiennent qu'au compteur principal)
            const anomalies = (!combinedInstance && typeof anomalyDays !== 'undefined' && anomalyDays[id]) || {};
            const missingDaySet = new Set(
                Object.entries(anomalies).filter(([, v]) => v.total === 0).map(([d]) => d)
            );
            const missingPlugin = missingDaySet.size ? {
                id: 'missingViz',
                beforeDraw(chart) {
                    if (isDaily) return;
                    const {ctx: c, chartArea: area, scales: {x: xAxis}} = chart;
                    if (!area) return;
                    const labels = chart.data.labels;
                    c.save();
                    c.fillStyle = 'rgba(220,38,38,0.07)';
                    for (let i = 0; i < labels.length - 1; i++) {
                        const d1 = new Date(labels[i].replace(' ', 'T'));
                        const d2 = new Date(labels[i+1].replace(' ', 'T'));
                        if ((d2 - d1) <= 23 * 3600 * 1000) continue;
                        let chk = new Date(d1);
                        chk.setDate(chk.getDate() + 1); chk.setHours(0, 0, 0, 0);
                        let found = false;
                        while (chk < d2) {
                            if (missingDaySet.has(chk.toISOString().slice(0, 10))) { found = true; break; }
                            chk.setDate(chk.getDate() + 1);
                        }
                        if (found) {
                            const x1 = xAxis.getPixelForValue(i), x2 = xAxis.getPixelForValue(i + 1);
                            c.fillRect(x1, area.top, x2 - x1, area.bottom - area.top);
                        }
                    }
                    c.restore();
                }
            } : null;

            // ── Coupure de financement municipal (printemps 2026) ──
            // Compteur concerné et plage connues (voir CITY_FUNDING_GAP) : on grise la
            // période même si elle n'a pas été détectée comme anomalie statistique.
            const fundingGap = !combinedInstance && CITY_FUNDING_GAP[id];
            const fundingGapPlugin = fundingGap ? {
                id: 'fundingGapViz',
                beforeDraw(chart) {
                    if (isDaily) return;
                    const {ctx: c, chartArea: area, scales: {x: xAxis}} = chart;
                    if (!area) return;
                    const labels = chart.data.labels;
                    const gStart = new Date(fundingGap[0] + 'T00:00:00');
                    const gEnd = new Date(fundingGap[1] + 'T23:59:59');
                    let x1 = null, x2 = null;
                    labels.forEach((lbl, i) => {
                        const d = new Date(lbl.replace(' ', 'T'));
                        if (d >= gStart && d <= gEnd) {
                            const px = xAxis.getPixelForValue(i);
                            if (x1 === null) x1 = px;
                            x2 = px;
                        }
                    });
                    if (x1 === null) return;
                    c.save();
                    c.fillStyle = 'rgba(245,158,11,0.14)';
                    c.fillRect(x1, area.top, x2 - x1, area.bottom - area.top);
                    c.strokeStyle = 'rgba(245,158,11,0.6)';
                    c.setLineDash([4, 3]);
                    c.strokeRect(x1, area.top, x2 - x1, area.bottom - area.top);
                    c.setLineDash([]);
                    if (x2 - x1 > 70) {
                        const dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
                        c.fillStyle = dark ? '#fcd34d' : '#92600a';
                        c.font = '11px sans-serif';
                        c.textAlign = 'center';
                        c.fillText('Coupure de financement (Ville)', (x1 + x2) / 2, area.top + 14);
                    }
                    c.restore();
                }
            } : null;

            // Barres fantômes (vue par jour) — dataset réel pour que le hover fonctionne
            if (!combinedInstance && isDaily && missingDaySet.size) {
                const missingVals = data.labels.map(lbl => { const a = anomalies[lbl]; return (a && a.total === 0) ? a.expected : null; });
                if (missingVals.some(v => v !== null)) {
                    data = { labels: data.labels, datasets: [...data.datasets, {
                        label: 'Données absentes', data: missingVals, type: 'bar',
                        backgroundColor: 'rgba(220,38,38,0.12)', borderColor: 'rgba(220,38,38,0.55)',
                        borderWidth: 1.5, borderRadius: 3, yAxisID: 'y', order: 1,
                        grouped: false,
                    }]};
                }
            }

            // Points rouges sur les jours suspects (vue dans le temps)
            if (!isDaily && missingDaySet.size) {
                data.datasets.forEach(ds => {
                    if (!Array.isArray(ds.data)) return;
                    ds.pointBackgroundColor = data.labels.map(lbl => anomalies[lbl.slice(0, 10)] ? 'rgba(220,38,38,0.85)' : (ds.borderColor || '#1DB860'));
                    ds.pointBorderColor    = data.labels.map(lbl => anomalies[lbl.slice(0, 10)] ? 'rgba(220,38,38,1)'    : (ds.borderColor || '#1DB860'));
                    ds.pointRadius         = data.labels.map(lbl => anomalies[lbl.slice(0, 10)] ? 4 : 2);
                    ds.pointHoverRadius    = 6;
                });
            }

            charts[id] = new Chart(ctx, {
                type: type,
                data: data,
                plugins: [missingPlugin, fundingGapPlugin].filter(Boolean),
                options: {
                    responsive: true,
                    animation: { duration: 500, easing: 'easeInOutQuart' },
                    plugins: {
                        legend: { display: data.datasets.length > 1 },
                        tooltip: {
                            backgroundColor: 'rgba(10,40,20,0.88)',
                            titleColor: '#7ee8a2',
                            bodyColor: '#fff',
                            padding: 10,
                            cornerRadius: 8,
                            callbacks: {
                                label: function(item) {
                                    if (item.dataset.label === 'Données absentes')
                                        return ' Attendu : ~' + Math.round(item.raw).toLocaleString('fr-CA') + ' passages';
                                    return ' ' + (item.dataset.label || 'Passages') + ' : ' + item.formattedValue;
                                },
                                title: function(items) {
                                    const label = items[0].label;
                                    if (isDaily) {
                                        const d = new Date(label + 'T12:00:00');
                                        return d.toLocaleDateString('fr-CA', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
                                    }
                                    const d = parseLabel(label);
                                    return d.toLocaleDateString('fr-CA', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' })
                                        + ' · ' + d.toLocaleTimeString('fr-CA', { hour: '2-digit', minute: '2-digit' });
                                },
                                afterBody: function(items) {
                                    const label = items[0].label;
                                    const dateKey = label.slice(0, 10);
                                    const lines = [];
                                    const w = (typeof weatherData !== 'undefined') ? weatherData[dateKey] : null;
                                    if (w) {
                                        lines.push('');
                                        let wline = w.icon + '  ' + (w.tmax !== null ? Math.round(w.tmax) + '°C' : '?');
                                        if (w.tmin !== null) wline += ' / ' + Math.round(w.tmin) + '°C';
                                        lines.push(wline);
                                        if (w.precip > 0.5) lines.push('🌧  ' + w.precip.toFixed(1) + ' mm pluie');
                                        if (w.snow > 0.5) lines.push('❄  ' + w.snow.toFixed(1) + ' cm neige');
                                    }
                                    if (!isDaily) {
                                        const anomaly = anomalies[dateKey];
                                        if (anomaly) {
                                            if (!lines.length) lines.push('');
                                            lines.push(anomaly.total === 0 ? '⚠ Données manquantes' : '⚠ Volume suspect');
                                        }
                                    }
                                    return lines;
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            grid: { color: 'rgba(0,0,0,0.04)' },
                            border: { display: false },
                            title: { display: true, text: 'Date', color: '#999', font: { size: 11 } },
                            ticks: {
                                color: '#777',
                                maxTicksLimit: 10,
                                callback: function(value) {
                                    const label = this.getLabelForValue(value);
                                    if (isDaily) {
                                        const d = new Date(label + 'T12:00:00');
                                        return d.toLocaleDateString('fr-CA', { month: 'short', day: 'numeric' });
                                    }
                                    const d = parseLabel(label);
                                    if (currentPeriod === 0) {
                                        return d.toLocaleTimeString('fr-CA', { hour: '2-digit', minute: '2-digit' });
                                    }
                                    return d.toLocaleDateString('fr-CA', { month: 'short', day: 'numeric' });
                                }
                            }
                        },
                        y: {
                            grid: { color: 'rgba(0,0,0,0.04)' },
                            border: { display: false },
                            title: { display: true, text: 'Passages', color: '#999', font: { size: 11 } },
                            beginAtZero: true,
                            ticks: { color: '#777' }
                        },
                    }
                }
            });
        }
    }
}

function updateViewToggle() {
    const show = currentPeriod >= 7 && currentPeriod <= HOURLY_DAYS;
    const isMobile = window.innerWidth < 768;
    document.getElementById('viewToggle').style.display = show && !isMobile ? 'flex' : 'none';
    document.getElementById('viewToggleMobile').style.display = show && isMobile ? 'block' : 'none';
    if (currentPeriod === 0 && viewMode !== 'timeline') {
        viewMode = 'timeline';
        document.getElementById('btnTimeline').classList.add('active');
        document.getElementById('btnDaily').classList.remove('active');
        document.getElementById('viewSelectMobile').value = 'timeline';
    }
}

function updateDirToggle(instance) {
    const show = !!(instance && allChartData[instance] && allChartData[instance].datasets.length > 1);
    const isMobile = window.innerWidth < 768;
    document.getElementById('dirToggle').style.display = show && !isMobile ? 'flex' : 'none';
    document.getElementById('dirToggleMobile').style.display = show && isMobile ? 'block' : 'none';
}

function updateBixiToggle(instance) {
    document.getElementById('bixiToggle').style.display = 'none';
}

document.getElementById('btnBixi').addEventListener('click', function() {
    showBixi = !showBixi;
    this.classList.toggle('active', showBixi);
    const instance = getSelectedCounter();
    if (instance) {
        if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
        createChart(instance);
    }
});

document.getElementById('btnShareCounter').addEventListener('click', async function() {
    const instance = getSelectedCounter();
    if (!instance) return;
    syncUrlHash(instance);
    const url = location.href;
    const btn = this;
    const original = btn.textContent;
    try {
        await navigator.clipboard.writeText(url);
        btn.textContent = '✓ Lien copié';
    } catch (e) {
        window.prompt('Copiez ce lien :', url);
    }
    setTimeout(() => { btn.textContent = original; }, 1800);
});

function hasDataForPeriod(instance) {
    return instance && chartData[instance] && chartData[instance].labels.length > 0;
}

function updateAnomalyWarning(instance) {
    const el = document.getElementById('anomalyWarning');
    el.style.display = 'none';
    document.getElementById('anomalyDetails').innerHTML = '';
    if (!instance || !anomalyDays[instance] || (currentPeriod !== 0 && currentPeriod !== 7)) return;
    const visibleDates = new Set();
    if (currentPeriod === 0 && specificDate) {
        visibleDates.add(specificDate);
    } else if (chartData[instance]) {
        chartData[instance].labels.forEach(l => visibleDates.add(l.slice(0, 10)));
    }
    const found = Object.entries(anomalyDays[instance]).filter(([d]) => visibleDates.has(d));
    if (!found.length) { el.style.display = 'none'; return; }
    const bixi = (typeof bixiNearby !== 'undefined' && bixiNearby[instance]) ? bixiNearby[instance] : {};
    const exceeds = (typeof bixiExceedsDays !== 'undefined' && bixiExceedsDays[instance]) ? bixiExceedsDays[instance] : {};
    const isMobile = window.innerWidth < 768;
    const anomalyLines = found.map(([d, info]) => {
        const dateObj = new Date(d + 'T12:00:00');
        if (isMobile) {
            const dateStr = dateObj.toLocaleDateString('fr-CA', {day: 'numeric', month: 'short'});
            const label = info.total === 0 ? 'données manquantes' : `${info.total.toLocaleString('fr-CA')} passages`;
            return `${dateStr} — ${label}`;
        }
        const dateStr = dateObj.toLocaleDateString('fr-CA', {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'});
        const label = info.total === 0
            ? `<strong>données manquantes</strong> (attendu ~${info.expected.toLocaleString('fr-CA')} passages)`
            : `<strong>${info.total.toLocaleString('fr-CA')}</strong> passages (attendu ~${info.expected.toLocaleString('fr-CA')}, z = ${info.z_score})`;
        const bixiCount = bixi[d] || 0;
        const exceedsBadge = exceeds[d]
            ? `<span class="bixi-exceeds-badge">⚠ Bixi (${bixiCount}) > compteur (${info.total})</span>`
            : (bixiCount > 0 ? ` — <span style="color:#0085C8">✓ ${bixiCount} trajet${bixiCount > 1 ? 's' : ''} Bixi à proximité</span>` : '');
        return `${dateStr.charAt(0).toUpperCase() + dateStr.slice(1)} : ${label}${exceedsBadge}`;
    });
    const exceedsOnlyDates = Object.keys(exceeds).filter(d => visibleDates.has(d) && !anomalyDays[instance][d]);
    const exceedsOnlyLines = isMobile ? [] : exceedsOnlyDates.map(d => {
        const dateObj = new Date(d + 'T12:00:00');
        const dateStr = dateObj.toLocaleDateString('fr-CA', {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'});
        const {counter, bixi: bcount} = exceeds[d];
        return `${dateStr.charAt(0).toUpperCase() + dateStr.slice(1)} : <strong>${counter.toLocaleString('fr-CA')}</strong> passages comptés<span class="bixi-exceeds-badge">⚠ Bixi (${bcount}) > compteur (${counter})</span>`;
    });
    document.getElementById('anomalyDetails').innerHTML = [...anomalyLines, ...exceedsOnlyLines].join('<br>');
    el.style.display = 'flex';
}

function updateVfQualityWarning(instance) {
    const el = document.getElementById('vfQualityWarning');
    el.style.display = 'none';
    el.className = '';
    if (!instance || typeof vfDataQuality === 'undefined' || !vfDataQuality[instance]) return;
    const q = vfDataQuality[instance];
    if (q.group === 'A') return;
    const fmt = d => d
        ? new Date(d + 'T12:00:00').toLocaleDateString('fr-CA', {day: 'numeric', month: 'long', year: 'numeric'})
        : '—';
    const iconEl = document.getElementById('vfQualityIcon');
    const textEl = document.getElementById('vfQualityText');
    if (q.group === 'B') {
        el.className = 'vf-warn-lag';
        iconEl.textContent = '⚠️';
        textEl.innerHTML = `<strong>Délai de publication</strong> — Les données de ce compteur s'arrêtent au <strong>${fmt(q.last_complete)}</strong>. La Ville de Montréal publie ces données avec environ <strong>${q.lag_days} jours</strong> de délai. Elles seront mises à jour automatiquement.`;
    } else if (q.group === 'C') {
        el.className = 'vf-warn-stopped';
        iconEl.textContent = '⛔';
        textEl.innerHTML = `<strong>Compteur potentiellement inactif</strong> — Aucune donnée reçue depuis le <strong>${fmt(q.last_complete || q.last_any)}</strong>. Ce compteur est peut-être désaffecté ou en réparation.`;
    }
    el.style.display = 'flex';
}

function updateCityGapWarning(instance) {
    const el = document.getElementById('cityGapWarning');
    const range = instance && CITY_FUNDING_GAP[instance];
    if (!range) { el.style.display = 'none'; return; }
    const fmt = d => new Date(d + 'T12:00:00').toLocaleDateString('fr-CA', {day: 'numeric', month: 'long', year: 'numeric'});
    document.getElementById('cityGapWarningText').innerHTML =
        `<strong>Coupure de financement municipal</strong> — Ce compteur n'a transmis aucune donnée valide du <strong>${fmt(range[0])}</strong> au <strong>${fmt(range[1])}</strong> : la Ville de Montréal avait cessé de financer le traitement des données Éco-Compteur durant cette période. ` +
        `<a href="${CITY_FUNDING_GAP_SOURCE.url}" target="_blank" rel="noopener">Source : ${CITY_FUNDING_GAP_SOURCE.label} ↗</a>`;
    el.style.display = 'flex';
}

function selectCounter(instance) {
    // Reset combine state on counter switch
    combinedInstance = null;
    document.getElementById('btnClearCombine').style.display = 'none';
    const nearbyCount = instance ? populateCombineSelect(instance) : 0;
    document.getElementById('combineSection').style.display = (instance && nearbyCount > 0) ? 'block' : 'none';
    updateHistButtons(instance);

    document.querySelectorAll('.table-container').forEach(c => c.classList.remove('visible'));
    const noDataMsg = document.getElementById('noDataMsg');
    if (instance) {
        document.getElementById(instance).classList.add('visible');
        const hasData = hasDataForPeriod(instance);
        noDataMsg.style.display = hasData ? 'none' : 'flex';
        document.getElementById('dataWarning').style.display = gappyCounters.has(instance) ? 'flex' : 'none';
        const canvas = document.getElementById('chart-' + instance);
        if (canvas) canvas.style.display = hasData ? '' : 'none';
        if (hasData) createChart(instance);
        updateStats(instance);
        updateDayLabel(instance);
        updateViewToggle();
        updateDirToggle(instance);
        updateBixiToggle(instance);
        updateAnomalyWarning(instance);
        updateVfQualityWarning(instance);
        updateCityGapWarning(instance);
    } else {
        noDataMsg.style.display = 'none';
        document.getElementById('dataWarning').style.display = 'none';
        document.getElementById('anomalyWarning').style.display = 'none';
        document.getElementById('vfQualityWarning').style.display = 'none';
        document.getElementById('cityGapWarning').style.display = 'none';
        updateStats(null);
        updateDayLabel(null);
        updateDirToggle(null);
        updateBixiToggle(null);
    }
    if (typeof markers !== 'undefined') updateMapSelection(instance);

    // ── Lien partagé ── tant qu'on est sur l'onglet Compteurs, l'ancre de l'URL
    // suit le compteur affiché (voir aussi le bouton #btnShareCounter).
    if (hashSyncEnabled && currentView === 'main') {
        const curHash = (location.hash || '').replace(/^#/, '');
        if (instance) {
            syncUrlHash(instance);
        } else if (counterLocations[curHash]) {
            syncUrlHash('');
        }
    }
}

function applyViewMode(val) {
    if (viewMode === val) return;
    viewMode = val;
    document.getElementById('btnTimeline').classList.toggle('active', val === 'timeline');
    document.getElementById('btnDaily').classList.toggle('active', val === 'daily');
    document.getElementById('viewSelectMobile').value = val;
    const instance = getSelectedCounter();
    if (instance) {
        if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
        createChart(instance);
    }
}

function applyDisplayMode(val) {
    if (displayMode === val) return;
    displayMode = val;
    document.getElementById('btnSeparate').classList.toggle('active', val === 'separate');
    document.getElementById('btnCombined').classList.toggle('active', val === 'combined');
    document.getElementById('dirSelectMobile').value = val;
    const instance = getSelectedCounter();
    if (instance) {
        chartData[instance] = buildFilteredData(instance, currentPeriod);
        if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
        createChart(instance);
        updateStats(instance);
    }
}

document.getElementById('btnTimeline').addEventListener('click', () => applyViewMode('timeline'));
document.getElementById('btnDaily').addEventListener('click', () => applyViewMode('daily'));
document.getElementById('btnSeparate').addEventListener('click', () => applyDisplayMode('separate'));
document.getElementById('btnCombined').addEventListener('click', () => applyDisplayMode('combined'));
document.getElementById('viewSelectMobile').addEventListener('change', function() { applyViewMode(this.value); });
document.getElementById('dirSelectMobile').addEventListener('change', function() { applyDisplayMode(this.value); });

document.getElementById('counterSelectDesktop').addEventListener('change', function() {
    selectCounter(this.value);
});

document.getElementById('arrondissementSelect').addEventListener('change', function() {
    const arr = this.value;
    const wrapper = document.getElementById('counterSelectWrapper');
    const mobileSelect = document.getElementById('counterSelectMobile');
    mobileSelect.innerHTML = '';
    if (arr && countersByArrondissement[arr]) {
        countersByArrondissement[arr].forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.value;
            opt.textContent = c.label;
            mobileSelect.appendChild(opt);
        });
        wrapper.style.visibility = 'visible';
        const first = countersByArrondissement[arr][0].value;
        mobileSelect.value = first;
        selectCounter(first);
    } else {
        wrapper.style.visibility = 'hidden';
        selectCounter(null);
    }
});

document.getElementById('counterSelectMobile').addEventListener('change', function() {
    selectCounter(this.value);
});

let currentPeriod = 7;

function getSelectedCounter() {
    if (window.innerWidth >= 768) {
        return document.getElementById('counterSelectDesktop').value;
    } else {
        return document.getElementById('counterSelectMobile').value;
    }
}

function filterDataByPeriod(days) {
    currentPeriod = days;
    const mPeriod = document.getElementById('periodSelectMobile');
    if (mPeriod) mPeriod.value = String(days);
    document.querySelectorAll('.period-btn').forEach(b => b.classList.toggle('active', parseInt(b.getAttribute('data-days')) === days));
    const wrapper = document.getElementById('datepickerWrapper');
    if (days === 0) {
        const instance = getSelectedCounter();
        if (instance && allChartData[instance]) {
            specificDate = maxDateStr(instance);
            const picker = document.getElementById('specificDatePicker');
            const labels = allChartData[instance].labels;
            if (labels.length) picker.min = labels[0].slice(0, 10);
            picker.max = specificDate;
            picker.value = specificDate;
        }
        wrapper.style.display = 'block';
    } else {
        wrapper.style.display = 'none';
    }
    updateViewToggle();
    if (days >= 30) {
        viewMode = 'daily';
        document.getElementById('btnDaily').classList.add('active');
        document.getElementById('btnTimeline').classList.remove('active');
        document.getElementById('viewSelectMobile').value = 'daily';
    } else {
        viewMode = 'timeline';
        document.getElementById('btnTimeline').classList.add('active');
        document.getElementById('btnDaily').classList.remove('active');
        document.getElementById('viewSelectMobile').value = 'timeline';
    }
    for (let instance in allChartData) {
        chartData[instance] = buildFilteredData(instance, days);
    }
    const selected = getSelectedCounter();
    if (selected) {
        if (charts[selected]) { charts[selected].destroy(); charts[selected] = null; }
        const hasData = hasDataForPeriod(selected);
        const noDataMsg = document.getElementById('noDataMsg');
        noDataMsg.style.display = hasData ? 'none' : 'flex';
        const canvas = document.getElementById('chart-' + selected);
        if (canvas) canvas.style.display = hasData ? '' : 'none';
        if (hasData) createChart(selected);
        updateStats(selected);
        updateDayLabel(selected);
        updateAnomalyWarning(selected);
    }
}

(function() {
    const arrondissements = Object.keys(countersByArrondissement);
    if (!arrondissements.length) return;
    const defaultCounter = 'vf-300046833';
    const defaultArr = counterLocations[defaultCounter]?.arrondissement
        || arrondissements[0];

    // Desktop
    document.getElementById('counterSelectDesktop').value = defaultCounter;

    // Mobile
    const arrSelect = document.getElementById('arrondissementSelect');
    arrSelect.value = defaultArr;
    const mobileSelect = document.getElementById('counterSelectMobile');
    countersByArrondissement[defaultArr].forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.value;
        opt.textContent = c.label;
        mobileSelect.appendChild(opt);
    });
    document.getElementById('counterSelectWrapper').style.visibility = 'visible';
    mobileSelect.value = defaultCounter;

    selectCounter(defaultCounter);
})();

document.querySelectorAll('.period-btn').forEach(btn => {
    btn.addEventListener('click', function() {
        filterDataByPeriod(parseInt(this.getAttribute('data-days')));
    });
});

document.getElementById('periodSelectMobile').addEventListener('change', function() {
    filterDataByPeriod(parseInt(this.value));
});

document.getElementById('specificDatePicker').addEventListener('change', function() {
    specificDate = this.value;
    for (let instance in allChartData) {
        chartData[instance] = buildFilteredData(instance, 0);
    }
    const selected = getSelectedCounter();
    if (selected) {
        if (charts[selected]) { charts[selected].destroy(); charts[selected] = null; }
        const hasData = hasDataForPeriod(selected);
        const noDataMsg = document.getElementById('noDataMsg');
        noDataMsg.style.display = hasData ? 'none' : 'flex';
        const canvas = document.getElementById('chart-' + selected);
        if (canvas) canvas.style.display = hasData ? '' : 'none';
        if (hasData) createChart(selected);
        updateStats(selected);
        updateDayLabel(selected);
    }
});

// ── Combiner deux compteurs ──
// Paires exclues du dropdown "Combiner avec" :
// capteurs non-complémentaires (rues différentes, doublons, Eco-Display, etc.)
const EXCLUDED_COMBINE_PAIRS = new Set([
    // ── 0–200 m ──
    'vf-100060991|vf-100060992',  // Avenue Westmount + Avenue Lansdowne — rues différentes
    'vf-100052600|vf-100061409',  // Rachel 3 (Angus) + Rachel/Angus — probable doublon
    'vf-100034805|vf-100041114',  // Gouin/Lajeunesse + Eco-Display Parc Stanley
    'vf-100011747|vf-100047030',  // Saint-Antoine/St-Urbain + Viger/St-Urbain — intersections ≠
    'det-00385-01|det-00421-01',  // Canadiens&Peel + René-Lévesque&Peel — points ≠ sur Peel
    // 'vf-300014995|vf-300014996' — REV St-Denis Duluth/Rachel : paire valide
    'vf-100003032|vf-300034853',  // Berri1 + Ontario&Savoie — rues différentes
    'det-00467-01|vf-100011747',  // Viger&St-Urbain + Saint-Antoine/St-Urbain — intersections ≠
    // ── 200–300 m ──
    'det-00967-13|det-00970-01',  // Boucherville&Notre-Dame + Notre-Dame&Curatteau — points ≠
    'det-00789-01|vf-100055268',  // Bourbonnière&Sherbrooke + Rachel/PieIX — rues différentes
    'det-00268-02|vf-300021685',  // L-H-LaFontaine&Perras Est + A25/Gouin — rues différentes
    'det-00268-03|vf-300021685',  // L-H-LaFontaine&Perras Ouest + A25/Gouin — rues différentes
    'vf-100012218|vf-100052603',  // Boyer/Rosemont + Piste des Carrières — voies différentes
    'det-00385-01|det-00402-02',  // Canadiens&Peel + Peel&Saint-Antoine — points ≠ sur Peel
    'det-00399-01|det-00402-01',  // Notre-Dame&Peel Sud + Peel&Saint-Antoine Nord — intersections ≠
    'det-00399-02|det-00402-01',  // Notre-Dame&Peel Nord + Peel&Saint-Antoine Nord — intersections ≠
    'vf-100012217|vf-300014996',  // Rachel/HôteldeVille + REV St-Denis/Rachel — intersections ≠
    'det-01364-01|det-01725-01',  // Charlevoix&Wellington + Gaétan-Laberge&A-15 — rues différentes
]);
function pairKey(a, b) { return [a, b].sort().join('|'); }

function haversineM(lat1, lng1, lat2, lng2) {
    const R = 6371000, rad = Math.PI / 180;
    const dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
    const a = Math.sin(dLat/2)**2 + Math.cos(lat1*rad)*Math.cos(lat2*rad)*Math.sin(dLng/2)**2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

function populateCombineSelect(instance) {
    const sel = document.getElementById('combineSelect');
    sel.innerHTML = '<option value="">— Choisir un compteur —</option>';
    const loc = counterLocations[instance];
    if (!loc) return 0;
    const MAX_DIST = 300; // mètres
    const nearby = Object.entries(counterLocations)
        .filter(([id, l]) => id !== instance
            && haversineM(loc.lat, loc.lng, l.lat, l.lng) <= MAX_DIST
            && !EXCLUDED_COMBINE_PAIRS.has(pairKey(instance, id)))
        .sort((a, b) => haversineM(loc.lat, loc.lng, a[1].lat, a[1].lng)
                      - haversineM(loc.lat, loc.lng, b[1].lat, b[1].lng));
    nearby.forEach(([id, l]) => {
        const opt = document.createElement('option');
        opt.value = id;
        const dist = Math.round(haversineM(loc.lat, loc.lng, l.lat, l.lng));
        opt.textContent = `${l.label} (${dist} m)`;
        sel.appendChild(opt);
    });
    return nearby.length;
}

document.getElementById('combineSelect').addEventListener('change', function() {
    const instance = getSelectedCounter();
    if (!instance) return;
    combinedInstance = this.value || null;
    document.getElementById('btnClearCombine').style.display = combinedInstance ? 'inline-block' : 'none';
    if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
    createChart(instance);
    updateStats(instance);
});

document.getElementById('btnClearCombine').addEventListener('click', function() {
    combinedInstance = null;
    document.getElementById('combineSelect').value = '';
    this.style.display = 'none';
    const instance = getSelectedCounter();
    if (instance) {
        if (charts[instance]) { charts[instance].destroy(); charts[instance] = null; }
        createChart(instance);
        updateStats(instance);
    }
});

// ── Carte Leaflet ──
const COLOR_DEFAULT       = '#1DB860';
const COLOR_SELECTED      = '#29ABE2';
const COLOR_GAPPY         = '#F59E0B';
const COLOR_BOUCLE        = '#8B5CF6';
const COLOR_BOUCLE_LAG    = '#F59E0B';   // Groupe B – délai de publication
const COLOR_BOUCLE_STOPPED = '#EF4444';  // Groupe C – compteur inactif

function vfGroup(instance) {
    return (typeof vfDataQuality !== 'undefined' && vfDataQuality[instance])
        ? vfDataQuality[instance].group : 'A';
}

function markerStyle(selected, gappy, type, group) {
    let base;
    if      (group === 'C') base = COLOR_BOUCLE_STOPPED;  // rouge
    else if (group === 'B') base = COLOR_BOUCLE_LAG;      // orange
    else                    base = COLOR_DEFAULT;           // vert
    return { radius: 9, fillColor: selected ? COLOR_SELECTED : base, color: '#fff', weight: 2, fillOpacity: 0.92 };
}

function updateMapSelection(instance) {
    Object.entries(markers).forEach(([id, m]) => {
        const type = counterLocations[id] ? counterLocations[id].type : 'fut';
        m.setStyle(markerStyle(id === instance, gappyCounters.has(id), type, vfGroup(id)));
    });
    if (instance && markers[instance]) markers[instance].bringToFront();
}

setTimeout(() => {
    map = L.map('map').setView([45.53, -73.59], 11);
    const osmLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 19
    });
    const cyclOsmLayer = L.tileLayer('https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png', {
        attribution: '© <a href="https://www.cyclosm.org">CyclOSM</a> © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 20
    });
    osmLayer.addTo(map);
    let cyclOsmActive = false;
    document.getElementById('cyclosm-btn').addEventListener('click', () => {
        cyclOsmActive = !cyclOsmActive;
        if (cyclOsmActive) {
            map.removeLayer(osmLayer);
            cyclOsmLayer.addTo(map);
        } else {
            map.removeLayer(cyclOsmLayer);
            osmLayer.addTo(map);
        }
        document.getElementById('cyclosm-btn').classList.toggle('active', cyclOsmActive);
    });
    Object.entries(counterLocations).forEach(([instance, loc]) => {
        const gappy = gappyCounters.has(instance);
        const grp = vfGroup(instance);
        const typeIcon = loc.type === 'boucle' ? '⬡ ' : '📍 ';
        const tooltipPrefix = grp === 'C' ? '⛔ '
                            : grp === 'B' ? '⚠ '
                            : gappy       ? '⚠ '
                            : typeIcon;
        const m = L.circleMarker([loc.lat, loc.lng], markerStyle(false, gappy, loc.type, grp))
            .addTo(map)
            .bindTooltip(tooltipPrefix + loc.label, { direction: 'top', offset: [0, -6] });
        m.on('click', () => setCounterFromMap(instance));
        markers[instance] = m;
    });
    updateMapSelection(getSelectedCounter());
    map.invalidateSize();
}, 0);

function setCounterFromMap(instance) {
    // Mettre à jour le dropdown desktop
    document.getElementById('counterSelectDesktop').value = instance;

    // Mettre à jour les dropdowns mobile
    const arr = counterLocations[instance].arrondissement;
    const arrSelect = document.getElementById('arrondissementSelect');
    arrSelect.value = arr;
    const mobileSelect = document.getElementById('counterSelectMobile');
    mobileSelect.innerHTML = '<option value="">Sélectionnez un compteur</option>';
    if (countersByArrondissement[arr]) {
        countersByArrondissement[arr].forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.value; opt.textContent = c.label;
            mobileSelect.appendChild(opt);
        });
        document.getElementById('counterSelectWrapper').style.visibility = 'visible';
    }
    mobileSelect.value = instance;

    selectCounter(instance);
    map.panTo([counterLocations[instance].lat, counterLocations[instance].lng]);
}

// ── Toggle vue Compteurs / Méthodologie ──────────────────────────────
// ── Vue globale ──────────────────────────────────────────────────────
function goToCounter(id) {
    if (!id) return;
    showView('main');
    setTimeout(() => setCounterFromMap(id), 0);
}

// Type de capteur : photo/vidéo (det-) ou au sol (vf-), mêmes couleurs que les badges
const isPhotoVideo = inst => inst.startsWith('det-');
const TYPE_COLORS = { boucle: ['rgba(139,92,246,0.72)', '#7C3AED'], fut: ['rgba(29,184,96,0.75)', '#1DB860'] };
const TYPE_LEGEND = { display: true, position: 'bottom', labels: { boxWidth: 12, boxHeight: 12, color: '#555', font: { size: 11 } } };
// Série d'un graphique empilé ; `partial` éclaircit les barres de la période en cours
function typeDataset(label, data, colors, partial) {
    const light = c => c.replace(/[\d.]+\)$/, '0.4)');
    return {
        label, data,
        backgroundColor: partial.map(p => p ? light(colors[0]) : colors[0]),
        borderColor: colors[1], borderWidth: 1, borderRadius: 3,
    };
}

function renderGlobalPage() {
    const s = globalStats;
    if (!s || !s.yesterday) return;

    const fmt = n => (n || 0).toLocaleString('fr-CA');
    const fmtPeak = h => h !== null && h !== undefined ? h + 'h–' + (h + 1) + 'h' : '—';

    const periods = [
        { key: 'yesterday', label: 'Hier' },
        { key: 'week',      label: '7 derniers jours' },
        { key: 'month',     label: 'Ce mois' },
    ];

    document.getElementById('global-cards').innerHTML = periods.map(p => {
        const d = s[p.key];
        const top1 = d.top && d.top[0];
        const top2 = d.top && d.top[1];
        const clickable = top1 && !!top1.id;
        const topRow = (t, rank) => t && t.id
            ? '<span class="global-top-row" onclick="event.stopPropagation();goToCounter(\'' + t.id + '\')">' +
              '<span class="global-top-rank">' + rank + '</span>' +
              '<strong>' + (t.label || '—') + '</strong>' +
              '<span class="global-top-vol">' + fmt(t.vol) + '</span>' +
              '</span>'
            : '';
        return '<div class="global-card' + (clickable ? ' global-card-clickable' : '') + '"' +
            (clickable ? ' onclick="goToCounter(\'' + top1.id + '\')" title="Voir ' + (top1.label || '') + '"' : '') + '>' +
            '<div class="global-card-title">' + p.label + '</div>' +
            '<div class="global-card-date">' + (d.subtitle || '') + '</div>' +
            '<div class="global-card-total">' + fmt(d.total) + '</div>' +
            '<div class="global-card-sub">passages</div>' +
            '<div class="global-card-meta">' +
                '<span>Pointe : <strong>' + fmtPeak(d.peak_hour) + '</strong></span>' +
                topRow(top1, '1') +
                topRow(top2, '2') +
                '<span>' + d.coverage + ' / ' + d.active + ' compteurs avec données</span>' +
            '</div>' +
            (clickable ? '<div class="global-card-cta">Voir le top compteur →</div>' : '') +
        '</div>';
    }).join('');

    // ── Classement arrondissements ──
    const maxVol = s.arr_rank && s.arr_rank.length ? s.arr_rank[0].vol : 1;
    document.getElementById('global-arr').innerHTML =
        '<div class="global-section-title">Top arrondissements <span style="font-size:13px;font-weight:400;color:#9ca3af;">— 7 derniers jours</span></div>' +
        (s.arr_rank || []).map(a =>
            '<div class="arr-row">' +
                '<span class="arr-name">' + a.name + '</span>' +
                '<div class="arr-bar-wrap"><div class="arr-bar" style="width:' + Math.round(a.vol / maxVol * 100) + '%"></div></div>' +
                '<span class="arr-vol">' + fmt(a.vol) + '</span>' +
            '</div>'
        ).join('');

    // ── Statut du réseau ──
    const n = s.net_status || {};
    document.getElementById('global-net').innerHTML =
        '<div class="global-section-title">Statut du réseau</div>' +
        '<div class="net-grid">' +
            '<div><div class="net-val" style="color:#1DB860">' + (n.A || 0) + '</div><div class="net-label">Actifs</div></div>' +
            '<div><div class="net-val" style="color:#F59E0B">' + (n.B || 0) + '</div><div class="net-label">En retard</div></div>' +
            '<div><div class="net-val" style="color:#EF4444">' + (n.C || 0) + '</div><div class="net-label">Inactifs</div></div>' +
        '</div>' +
        '<p style="font-size:11px;color:#9ca3af;margin-top:14px;text-align:center;">' + (n.total || 0) + ' compteurs au total</p>';

    // ── Graphique évolution annuelle ──
    if (typeof historicalYearlyTotals !== 'undefined' && !charts['__global_yearly__']) {
        const years  = Object.keys(historicalYearlyTotals).map(Number).sort();
        // Part des capteurs photo/vidéo dans les années tirées des données récentes ; les
        // totaux des années antérieures (fichiers du portail) ne comptent que les capteurs au sol
        const liveYears = new Set((SITE_DATA.historicalLiveYears || []).map(String));
        const fut = {};
        Object.entries(counterHistoricalData).forEach(([inst, ys]) => {
            if (!isPhotoVideo(inst)) return;
            Object.entries(ys).forEach(([y, months]) => {
                if (!liveYears.has(y)) return;
                fut[y] = (fut[y] || 0) + Object.values(months).reduce((a, b) => a + b, 0);
            });
        });
        const curY   = new Date().getFullYear();
        const ctx    = document.getElementById('global-yearly-chart');
        if (ctx) {
            charts['__global_yearly__'] = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: years,
                    datasets: [
                        typeDataset('Capteurs au sol', years.map(y => historicalYearlyTotals[y] - (fut[y] || 0)),
                                    TYPE_COLORS.boucle, years.map(y => y === curY)),
                        typeDataset('Capteurs photo/vidéo', years.map(y => fut[y] || 0),
                                    TYPE_COLORS.fut, years.map(y => y === curY)),
                    ],
                },
                options: {
                    responsive: true,
                    animation: { duration: 500 },
                    plugins: {
                        legend: TYPE_LEGEND,
                        tooltip: {
                            backgroundColor: 'rgba(10,40,20,0.88)',
                            titleColor: '#7ee8a2',
                            bodyColor: '#fff',
                            padding: 10,
                            cornerRadius: 8,
                            mode: 'index',
                            filter: item => item.raw > 0,
                            callbacks: {
                                title: items => items[0].label + (Number(items[0].label) === curY ? ' (en cours)' : ''),
                                label: item => ' ' + item.dataset.label + ' : ' + Math.round(item.raw).toLocaleString('fr-CA'),
                                footer: items => {
                                    const y = Number(items[0].label);
                                    const n = (typeof historicalYearlyCounters !== 'undefined' && historicalYearlyCounters[y]) || null;
                                    return ['Total : ' + historicalYearlyTotals[y].toLocaleString('fr-CA') + ' passages',
                                            n ? n + ' compteur' + (n > 1 ? 's' : '') : ''].filter(Boolean);
                                },
                            },
                        },
                    },
                    scales: {
                        x: {
                            stacked: true,
                            grid: { color: 'rgba(0,0,0,0.04)' },
                            border: { display: false },
                            ticks: { color: '#777', font: { size: 11 } },
                        },
                        y: {
                            stacked: true,
                            grid: { color: 'rgba(0,0,0,0.04)' },
                            border: { display: false },
                            beginAtZero: true,
                            ticks: {
                                color: '#777',
                                callback: v => v >= 1e6
                                    ? (v / 1e6).toFixed(1) + 'M'
                                    : v >= 1e3 ? (v / 1e3).toFixed(0) + 'k' : v,
                            },
                            title: { display: true, text: 'Passages', color: '#999', font: { size: 11 } },
                        },
                    },
                },
            });
        }
    }
}

// ── Vélo d'hiver ──────────────────────────────────────────────────────
const WINTER_MONTHS_SET = new Set([12, 1, 2, 3]);
const DOW_LABELS_FR = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];
const HIVER_CAT_COLORS = { doux: '#38BDF8', pluie: '#2563EB', froid: '#8B5CF6', neige: '#06B6D4' };
let _hiverGlobalDaily = null;

function isWinterDate(dateStr) {
    return WINTER_MONTHS_SET.has(parseInt(dateStr.slice(5, 7), 10));
}

function dailyTotalsFor(instance) {
    const d = allDailyData[instance];
    const out = {};
    if (!d) return out;
    d.labels.forEach((date, i) => {
        let tot = 0;
        d.data.forEach(series => { const v = series[i]; if (v != null) tot += v; });
        out[date] = tot;
    });
    return out;
}

function hiverGlobalDaily() {
    if (_hiverGlobalDaily) return _hiverGlobalDaily;
    const out = {};
    Object.keys(counterLocations).forEach(inst => {
        const daily = dailyTotalsFor(inst);
        Object.entries(daily).forEach(([date, v]) => { out[date] = (out[date] || 0) + v; });
    });
    _hiverGlobalDaily = out;
    return out;
}

function winterSeasonLabel(dateStr) {
    const y = parseInt(dateStr.slice(0, 4), 10);
    const m = parseInt(dateStr.slice(5, 7), 10);
    return m >= 11
        ? y + '–' + String((y + 1) % 100).padStart(2, '0')
        : (y - 1) + '–' + String(y % 100).padStart(2, '0');
}

// Saison retenue pour les statistiques d'hiver : la plus récente terminée (à défaut,
// celle en cours). La fenêtre de 12 mois peut contenir deux hivers (ex. en décembre) :
// on ne les additionne jamais.
let _hiverSeason;
function hiverSeason() {
    if (_hiverSeason !== undefined) return _hiverSeason;
    const today = dateStr(new Date());
    const current = isWinterDate(today) ? winterSeasonLabel(today) : null;
    const seasons = [...new Set(Object.keys(hiverGlobalDaily()).filter(isWinterDate).map(winterSeasonLabel))].sort();
    const done = seasons.filter(s => s !== current);
    _hiverSeason = done.length ? done[done.length - 1] : (seasons.length ? seasons[seasons.length - 1] : null);
    return _hiverSeason;
}

function computeWinterStats(dailyMap, weekdaysOnly) {
    const season = hiverSeason();
    const dowSum = Array(7).fill(0), dowCount = Array(7).fill(0);
    const dowTempSum = Array(7).fill(0), dowTempCount = Array(7).fill(0);
    const cats = {
        doux:  { sum: 0, count: 0 },
        pluie: { sum: 0, count: 0 },
        froid: { sum: 0, count: 0 },
        neige: { sum: 0, count: 0 },
    };
    let coldestRidden = null, snowiestRidden = null, busiest = null;
    let winterTotal = 0, winterDays = 0, snowDaysTotal = 0, snowDaysRidden = 0;

    let firstDate = null, lastDate = null;
    Object.entries(dailyMap).forEach(([date, v]) => {
        if (!isWinterDate(date) || winterSeasonLabel(date) !== season) return;
        const dow = (new Date(date + 'T00:00:00').getDay() + 6) % 7;
        if (weekdaysOnly && dow >= 5) return;
        if (!firstDate || date < firstDate) firstDate = date;
        if (!lastDate || date > lastDate) lastDate = date;
        winterTotal += v;
        winterDays++;
        dowSum[dow] += v;
        dowCount[dow]++;
        if (busiest === null || v > busiest.v) busiest = { date, v };

        const w = weatherData[date];
        if (!w) return;
        if (w.tmax != null) { dowTempSum[dow] += w.tmax; dowTempCount[dow]++; }
        const snow = w.snow || 0, precip = w.precip || 0, tmax = w.tmax;
        if (snow >= 1) {
            cats.neige.sum += v; cats.neige.count++;
            snowDaysTotal++;
            if (v > 0) snowDaysRidden++;
        } else if (tmax != null && tmax <= -10) {
            cats.froid.sum += v; cats.froid.count++;
        } else if (precip > 5) {
            cats.pluie.sum += v; cats.pluie.count++;
        } else {
            cats.doux.sum += v; cats.doux.count++;
        }
        if (v > 0 && (coldestRidden === null || w.tmin < coldestRidden.tmin)) {
            coldestRidden = { date, tmin: w.tmin, tmax: w.tmax, v };
        }
        if (v > 0 && snow > 0 && (snowiestRidden === null || snow > snowiestRidden.snow)) {
            snowiestRidden = { date, snow, v };
        }
    });

    return {
        avgPassages: dowSum.map((s, i) => dowCount[i] ? Math.round(s / dowCount[i]) : null),
        avgTemp: dowTempSum.map((s, i) => dowTempCount[i] ? +(s / dowTempCount[i]).toFixed(1) : null),
        cats, coldestRidden, snowiestRidden, busiest,
        winterTotal, winterDays, snowDaysTotal, snowDaysRidden, season, firstDate, lastDate,
    };
}

function computeHistoricalWinterTrend() {
    const totals = {}, byType = { boucle: {}, fut: {} }, counters = {};
    if (typeof counterHistoricalData === 'undefined') return { totals, byType, counters };
    const add = (season, inst, vol) => {
        const t = isPhotoVideo(inst) ? 'fut' : 'boucle';
        totals[season] = (totals[season] || 0) + vol;
        byType[t][season] = (byType[t][season] || 0) + vol;
        (counters[season] = counters[season] || new Set()).add(inst);
    };
    const shown = hiverSeason();
    Object.entries(counterHistoricalData).forEach(([inst, years]) => {
        Object.entries(years).forEach(([yrStr, months]) => {
            const yr = parseInt(yrStr, 10);
            Object.entries(months).forEach(([moStr, vol]) => {
                const mo = parseInt(moStr, 10);
                if (![12, 1, 2, 3].includes(mo)) return;
                const season = winterSeasonLabel(yr + '-' + String(mo).padStart(2, '0') + '-01');
                if (season !== shown) add(season, inst, vol);
            });
        });
    });
    // La saison des cartes vient des données quotidiennes (tous les compteurs, décembre
    // compris) : l'historique ne contient pas les capteurs photo/vidéo avant l'année en cours.
    if (shown) {
        Object.keys(counterLocations).forEach(inst => {
            Object.entries(dailyTotalsFor(inst)).forEach(([date, v]) => {
                if (isWinterDate(date) && winterSeasonLabel(date) === shown) add(shown, inst, v);
            });
        });
    }
    return { totals, byType, counters };
}

function populateHiverCounterSelect() {
    const sel = document.getElementById('hiverCounterSelect');
    if (!sel || sel.dataset.filled) return;
    sel.dataset.filled = '1';
    Object.keys(countersByArrondissement).sort().forEach(arr => {
        const og = document.createElement('optgroup');
        og.label = arr;
        countersByArrondissement[arr].forEach(c => {
            if (!allDailyData[c.value]) return;
            const opt = document.createElement('option');
            opt.value = c.value;
            opt.textContent = c.label;
            og.appendChild(opt);
        });
        if (og.children.length) sel.appendChild(og);
    });
    sel.addEventListener('change', function() {
        renderHiverDowCharts(this.value);
    });

    const btnFull = document.getElementById('btnHiverFullWeek');
    const btnWeekdays = document.getElementById('btnHiverWeekdays');
    if (btnFull && btnWeekdays && !btnFull.dataset.bound) {
        btnFull.dataset.bound = '1';
        const setMode = weekdaysOnly => {
            hiverWeekdaysOnly = weekdaysOnly;
            btnFull.classList.toggle('active', !weekdaysOnly);
            btnWeekdays.classList.toggle('active', weekdaysOnly);
            renderHiverDowCharts(getSelectedHiverCounter());
        };
        btnFull.addEventListener('click', () => setMode(false));
        btnWeekdays.addEventListener('click', () => setMode(true));
    }
}

function renderHiverFacts() {
    const el = document.getElementById('hiver-cards');
    if (!el) return;
    const stats = computeWinterStats(hiverGlobalDaily());
    const fmt = n => (n || 0).toLocaleString('fr-CA');
    const fmtDate = d => d ? new Date(d + 'T00:00:00').toLocaleDateString('fr-CA', { day: 'numeric', month: 'long', year: 'numeric' }) : '—';
    const pctSnowRidden = stats.snowDaysTotal ? Math.round(stats.snowDaysRidden / stats.snowDaysTotal * 100) : 0;
    // Saison « AAAA–AA » : décembre AAAA à mars AAAA+1 ; on signale une couverture partielle
    const y0 = stats.season ? parseInt(stats.season.slice(0, 4), 10) : null;
    const fmtShort = d => new Date(d + 'T00:00:00').toLocaleDateString('fr-CA', { day: 'numeric', month: 'short' });
    const partial = stats.firstDate && (stats.firstDate > y0 + '-12-01' || stats.lastDate < (y0 + 1) + '-03-31');
    const seasonText = y0 ? 'Déc. ' + y0 + ' à mars ' + (y0 + 1) +
        (partial ? ' (données du ' + fmtShort(stats.firstDate) + ' au ' + fmtShort(stats.lastDate) + ')' : '') +
        ', tous compteurs' : '—';

    const cards = [
        {
            title: stats.season ? "Hiver " + stats.season : "Hiver dernier", date: seasonText,
            total: fmt(stats.winterTotal), sub: 'passages',
            meta: "<span>Sur <strong>" + fmt(stats.winterDays) + "</strong> jours de données</span>",
        },
        {
            title: "Jour le plus froid pédalé", date: stats.coldestRidden ? fmtDate(stats.coldestRidden.date) : '—',
            // Minimum de la journée (souvent la nuit) : étiqueté, avec le maximum pour contexte
            total: stats.coldestRidden ? stats.coldestRidden.tmin.toFixed(1) + "°C<span class='global-card-unit'>min</span>" : '—',
            sub: stats.coldestRidden && stats.coldestRidden.tmax != null
                ? '(max ' + stats.coldestRidden.tmax.toFixed(1) + '°C ce jour-là)' : 'minimum ce jour',
            meta: stats.coldestRidden ? "<span><strong>" + fmt(stats.coldestRidden.v) + "</strong> passages quand même</span>" : '',
        },
        {
            title: "Record d'achalandage hivernal", date: stats.busiest ? fmtDate(stats.busiest.date) : '—',
            total: stats.busiest ? fmt(stats.busiest.v) : '—', sub: 'passages en 1 jour', meta: '',
        },
        {
            title: "Jours de neige, pédalés quand même", date: fmt(stats.snowDaysTotal) + ' jours de neige (≥1 cm)',
            total: pctSnowRidden + '%', sub: 'de ces jours avec des cyclistes',
            meta: "<span><strong>" + fmt(stats.snowDaysRidden) + "</strong> / " + fmt(stats.snowDaysTotal) + " jours</span>",
        },
    ];

    el.innerHTML = cards.map(c =>
        "<div class='global-card'>" +
            "<div class='global-card-title'>" + c.title + "</div>" +
            "<div class='global-card-date'>" + c.date + "</div>" +
            "<div class='global-card-total'>" + c.total + "</div>" +
            "<div class='global-card-sub'>" + c.sub + "</div>" +
            (c.meta ? "<div class='global-card-meta'>" + c.meta + "</div>" : '') +
        "</div>"
    ).join('');
}

function renderHiverTrendChart() {
    if (charts['__hiver_trend__']) return;
    const { totals, byType, counters } = computeHistoricalWinterTrend();
    const seasons = Object.keys(totals).sort();
    const ctx = document.getElementById('hiver-trend-chart');
    if (!ctx || !seasons.length) return;
    const todayStr = dateStr(new Date());
    const curSeason = isWinterDate(todayStr) ? winterSeasonLabel(todayStr) : null;
    const partial = seasons.map(s => s === curSeason);
    charts['__hiver_trend__'] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: seasons,
            datasets: [
                typeDataset('Capteurs au sol', seasons.map(s => byType.boucle[s] || 0), TYPE_COLORS.boucle, partial),
                typeDataset('Capteurs photo/vidéo', seasons.map(s => byType.fut[s] || 0), TYPE_COLORS.fut, partial),
            ],
        },
        options: {
            responsive: true,
            animation: { duration: 500 },
            plugins: {
                legend: TYPE_LEGEND,
                tooltip: {
                    backgroundColor: 'rgba(7,33,51,0.90)',
                    titleColor: '#8FE0FB',
                    bodyColor: '#fff',
                    padding: 10,
                    cornerRadius: 8,
                    mode: 'index',
                    filter: item => item.raw > 0,
                    callbacks: {
                        title: items => items[0].label + (items[0].label === curSeason ? ' (en cours)' : ''),
                        label: item => ' ' + item.dataset.label + ' : ' + Math.round(item.raw).toLocaleString('fr-CA'),
                        footer: items => {
                            const s = items[0].label;
                            const n = counters[s] ? counters[s].size : 0;
                            return ['Total : ' + Math.round(totals[s]).toLocaleString('fr-CA') + ' passages',
                                    n ? n + ' compteur' + (n > 1 ? 's' : '') : ''].filter(Boolean);
                        },
                    },
                },
            },
            scales: {
                x: { stacked: true, grid: { color: 'rgba(0,0,0,0.04)' }, border: { display: false }, ticks: { color: '#777', font: { size: 11 } } },
                y: {
                    stacked: true,
                    grid: { color: 'rgba(0,0,0,0.04)' }, border: { display: false }, beginAtZero: true,
                    ticks: { color: '#777', callback: v => v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(0) + 'k' : v },
                    title: { display: true, text: 'Passages', color: '#999', font: { size: 11 } },
                },
            },
        },
    });
}

function renderHiverWeatherBreakdown(stats) {
    const el = document.getElementById('hiver-weather-breakdown');
    if (!el) return;
    const fmt = n => (n || 0).toLocaleString('fr-CA');
    const defs = [
        { key: 'doux',  icon: '🌤️', label: 'Jours doux (référence)' },
        { key: 'pluie', icon: '🌧️', label: 'Pluie / verglas (>5 mm)' },
        { key: 'froid', icon: '🥶', label: '≤ -10 °C (sans neige)' },
        { key: 'neige', icon: '❄️', label: 'Neige (≥1 cm)' },
    ];
    el.innerHTML = defs.map(d => {
        const c = stats.cats[d.key];
        const avg = c.count ? Math.round(c.sum / c.count) : null;
        return "<div>" +
            "<div class='net-val' style='color:" + HIVER_CAT_COLORS[d.key] + "'>" + (avg !== null ? fmt(avg) : '—') + "</div>" +
            "<div class='net-label'>" + d.icon + ' ' + d.label + "</div>" +
            "<div style='font-size:11px;color:var(--faint);margin-top:2px;'>" + c.count + (c.count === 1 ? ' jour' : ' jours') + "</div>" +
        "</div>";
    }).join('');
}

function renderHiverRecords(stats, instance) {
    const el = document.getElementById('hiver-records');
    if (!el) return;
    const fmt = n => (n || 0).toLocaleString('fr-CA');
    const fmtDate = d => d ? new Date(d + 'T00:00:00').toLocaleDateString('fr-CA', { day: 'numeric', month: 'long', year: 'numeric' }) : '—';
    const label = instance === '__all__' ? 'tous les compteurs' : ((counterLocations[instance] && counterLocations[instance].label) || instance);
    const rows = [];
    if (stats.busiest) {
        rows.push("<p>Journée la plus achalandée de l'hiver pour <strong>" + label + "</strong> : <strong>" + fmtDate(stats.busiest.date) + "</strong>, avec <strong>" + fmt(stats.busiest.v) + "</strong> passages.</p>");
    }
    if (stats.coldestRidden) {
        const maxTxt = stats.coldestRidden.tmax != null ? " (max " + stats.coldestRidden.tmax.toFixed(1) + " °C)" : "";
        rows.push("<p>Malgré un minimum de <strong>" + stats.coldestRidden.tmin.toFixed(1) + " °C</strong>" + maxTxt + " le " + fmtDate(stats.coldestRidden.date) + ", <strong>" + fmt(stats.coldestRidden.v) + "</strong> passages ont tout de même été comptés.</p>");
    }
    if (stats.snowiestRidden) {
        rows.push("<p>Le " + fmtDate(stats.snowiestRidden.date) + ", sous " + stats.snowiestRidden.snow.toFixed(1) + " cm de neige, <strong>" + fmt(stats.snowiestRidden.v) + "</strong> cyclistes ont quand même été comptés.</p>");
    }
    el.innerHTML = "<div class='global-section-title'>Faits marquants — " + label + "</div>" +
        (rows.length ? rows.join('') : "<p style='color:var(--faint);font-size:13px;'>Pas assez de données hivernales pour ce compteur.</p>");
}

function renderHiverCounterWarning(instance, stats) {
    const el = document.getElementById('hiverCounterWarning');
    if (!el) return;
    if (instance === '__all__') { el.innerHTML = ''; return; }

    const fmtDate = d => d ? new Date(d + 'T00:00:00').toLocaleDateString('fr-CA', { day: 'numeric', month: 'long', year: 'numeric' }) : '—';
    const loc = counterLocations[instance];
    const q = (typeof vfDataQuality !== 'undefined') ? vfDataQuality[instance] : null;
    const allDates = Object.keys(dailyTotalsFor(instance)).sort();
    const firstEverDate = allDates.length ? allDates[0] : null;
    const season = stats.season;
    const seasonStart = season ? season.slice(0, 4) + '-12-01' : null;
    const seasonEnd = season ? (parseInt(season.slice(0, 4), 10) + 1) + '-03-31' : null;
    const gap = CITY_FUNDING_GAP[instance];
    const gapOverlapsWinter = gap && seasonStart && seasonEnd && gap[0] <= seasonEnd && gap[1] >= seasonStart;
    const rows = [];

    if (gapOverlapsWinter) {
        rows.push({ cls: 'stopped', icon: '🏛️',
            text: "<strong>Coupure de financement municipal</strong> — ce compteur n'a transmis aucune donnée valide du <strong>" + fmtDate(gap[0]) + "</strong> au <strong>" + fmtDate(gap[1]) + "</strong> (la Ville avait cessé de financer le traitement des données Éco-Compteur) : les statistiques de l'hiver " + season + " sont donc incomplètes pour cette période. <a href=\"" + CITY_FUNDING_GAP_SOURCE.url + "\" target=\"_blank\" rel=\"noopener\">Source : " + CITY_FUNDING_GAP_SOURCE.label + " ↗</a>" });
    }

    if (stats.winterDays === 0) {
        if (firstEverDate && seasonStart && firstEverDate > seasonStart) {
            rows.push({ cls: 'lag', icon: 'ℹ️',
                text: "Ce compteur est en service depuis le <strong>" + fmtDate(firstEverDate) + "</strong>, soit après le début de l'hiver " + season + " : il n'a pas encore de données pour cette saison hivernale." });
        } else if (q && q.group === 'C') {
            rows.push({ cls: 'stopped', icon: '⛔',
                text: "Ce compteur n'a transmis aucune donnée récente (depuis le <strong>" + fmtDate(q.last_complete || q.last_any) + "</strong>) — il est peut-être désaffecté ou en réparation, ce qui explique l'absence de données hivernales." });
        } else {
            rows.push({ cls: 'lag', icon: '⚠️',
                text: "Aucune donnée n'est disponible pour ce compteur durant l'hiver " + (season || '') + " — il a pu être retiré ou mis hors service pendant la saison froide." });
        }
    } else if (q && q.group === 'C') {
        rows.push({ cls: 'stopped', icon: '⛔',
            text: "Ce compteur semble inactif depuis le <strong>" + fmtDate(q.last_complete || q.last_any) + "</strong> — les statistiques ci-dessous peuvent donc être incomplètes pour la fin de la saison." });
    } else if (q && q.group === 'B') {
        rows.push({ cls: 'lag', icon: '⚠️',
            text: "Les données de ce compteur s'arrêtent au <strong>" + fmtDate(q.last_complete) + "</strong> (délai de publication d'environ " + q.lag_days + " jours de la Ville) — les jours les plus récents de l'hiver ne sont pas encore pris en compte." });
    }

    if (loc && loc.type === 'boucle') {
        rows.push({ cls: 'info', icon: '❄️',
            text: "Ce compteur est une <strong>boucle magnétique au sol</strong>, installée sur la piste cyclable : si celle-ci n'était pas bien déneigée, les cyclistes ont pu circuler sur la rue plutôt que sur la piste, et ces passages ne sont alors pas comptabilisés ici." });
    }

    el.innerHTML = rows.map(r =>
        "<div class='hiver-warn-row " + r.cls + "'><span class='warn-icon'>" + r.icon + "</span><span>" + r.text + "</span></div>"
    ).join('');
}

let hiverWeekdaysOnly = false;

function renderHiverDowCharts(instance) {
    const dailyMap = instance === '__all__' ? hiverGlobalDaily() : dailyTotalsFor(instance);
    const stats = computeWinterStats(dailyMap, hiverWeekdaysOnly);
    const labels = hiverWeekdaysOnly ? DOW_LABELS_FR.slice(0, 5) : DOW_LABELS_FR;
    const avgPassages = hiverWeekdaysOnly ? stats.avgPassages.slice(0, 5) : stats.avgPassages;
    const avgTemp = hiverWeekdaysOnly ? stats.avgTemp.slice(0, 5) : stats.avgTemp;

    ['__hiver_dow_passages__', '__hiver_dow_temp__'].forEach(k => {
        if (charts[k]) { charts[k].destroy(); charts[k] = null; }
    });

    const ctx1 = document.getElementById('hiver-dow-chart');
    if (ctx1) {
        charts['__hiver_dow_passages__'] = new Chart(ctx1, {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    label: 'Passages moyens',
                    data: avgPassages,
                    backgroundColor: 'rgba(14,165,216,0.75)',
                    borderColor: '#0EA5D8',
                    borderWidth: 1,
                    borderRadius: 4,
                }],
            },
            options: { ..._histChartOpts('Passages / jour'), plugins: { ..._histChartOpts('Passages / jour').plugins, legend: { display: false } } },
        });
    }
    const ctx2 = document.getElementById('hiver-temp-chart');
    if (ctx2) {
        charts['__hiver_dow_temp__'] = new Chart(ctx2, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: 'Température max. moyenne',
                    data: avgTemp,
                    borderColor: '#22D3EE',
                    backgroundColor: 'rgba(34,211,238,0.18)',
                    fill: true,
                    tension: 0.3,
                    borderWidth: 1.5,
                    pointRadius: 3,
                    pointBackgroundColor: '#22D3EE',
                }],
            },
            options: { ..._histChartOpts('°C'), plugins: { ..._histChartOpts('°C').plugins, legend: { display: false } } },
        });
    }

    renderHiverWeatherBreakdown(stats);
    renderHiverRecords(stats, instance);
    // L'avertissement par compteur porte sur la disponibilité des données, pas sur le
    // sous-ensemble de jours choisi : toujours basé sur la semaine complète.
    renderHiverCounterWarning(instance, hiverWeekdaysOnly ? computeWinterStats(dailyMap) : stats);
}

function getSelectedHiverCounter() {
    const sel = document.getElementById('hiverCounterSelect');
    return sel ? sel.value : '__all__';
}

function renderHiverPage() {
    populateHiverCounterSelect();
    renderHiverFacts();
    renderHiverTrendChart();
    renderHiverDowCharts(getSelectedHiverCounter());
    renderHiverBixiSection();
}

// ── BIXI l'hiver (introduit en 2023) ────────────────────────────────────
function computeBixiWinterSeasons() {
    const seasons = {};
    if (typeof bixiYearly === 'undefined') return seasons;
    Object.values(bixiYearly).forEach(s => {
        if (!s || !s.daily) return;
        Object.entries(s.daily).forEach(([date, n]) => {
            if (!isWinterDate(date)) return;
            const season = winterSeasonLabel(date);
            seasons[season] = (seasons[season] || 0) + n;
        });
    });
    return seasons;
}

function renderHiverBixiCards(seasons) {
    const el = document.getElementById('hiver-bixi-cards');
    if (!el) return;
    const fmt = n => Math.round(n || 0).toLocaleString('fr-CA');
    const keys = Object.keys(seasons).sort();
    if (!keys.length) {
        el.innerHTML = "<div class='global-card'>Données BIXI hivernales indisponibles.</div>";
        return;
    }
    const last = keys[keys.length - 1];
    // Saisons antérieures au service hivernal (quelques trajets isolés) : exclues
    // de la moyenne, sinon ces mois quasi vides la tirent vers le bas.
    const maxSeason = Math.max(...keys.map(k => seasons[k]));
    const servedKeys = keys.filter(k => seasons[k] >= maxSeason * 0.01);
    const served = new Set(servedKeys);
    const winterMonthsWithData = new Set();
    Object.values(bixiYearly).forEach(s => {
        if (!s || !s.daily) return;
        Object.keys(s.daily).forEach(date => {
            if (isWinterDate(date) && served.has(winterSeasonLabel(date))) winterMonthsWithData.add(date.slice(0, 7));
        });
    });
    const totalWinterTrips = servedKeys.reduce((sum, k) => sum + seasons[k], 0);
    const avgPerMonth = winterMonthsWithData.size ? totalWinterTrips / winterMonthsWithData.size : null;

    const cards = [
        {
            title: 'BIXI — hiver ' + last, date: 'Décembre à mars',
            total: fmt(seasons[last]), sub: 'trajets', meta: '',
        },
        {
            title: 'Moyenne des mois hivernaux', date: 'Déc. à mars, toutes saisons confondues',
            total: avgPerMonth != null ? fmt(avgPerMonth) : '—', sub: 'trajets par mois', meta: '',
        },
    ];
    el.innerHTML = cards.map(c =>
        "<div class='global-card'>" +
            "<div class='global-card-title'>" + c.title + "</div>" +
            "<div class='global-card-date'>" + c.date + "</div>" +
            "<div class='global-card-total'>" + c.total + "</div>" +
            "<div class='global-card-sub'>" + c.sub + "</div>" +
            (c.meta ? "<div class='global-card-meta'>" + c.meta + "</div>" : '') +
        "</div>"
    ).join('');
}

function renderHiverBixiChart(seasons) {
    if (charts['__hiver_bixi__']) return;
    const ctx = document.getElementById('hiver-bixi-chart');
    const keys = Object.keys(seasons).sort();
    if (!ctx || !keys.length) return;
    const fmt = n => Math.round(n || 0).toLocaleString('fr-CA');
    charts['__hiver_bixi__'] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: keys,
            datasets: [{
                label: 'Trajets BIXI (déc–mar)',
                data: keys.map(k => seasons[k]),
                backgroundColor: 'rgba(225,37,27,0.75)',
                borderColor: '#B91C1C',
                borderWidth: 1,
                borderRadius: 4,
            }],
        },
        options: {
            responsive: true,
            animation: { duration: 500 },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(40,10,10,0.90)', titleColor: '#FCA5A5', bodyColor: '#fff',
                    padding: 10, cornerRadius: 8,
                    callbacks: { label: item => ' ' + fmt(item.raw) + ' trajets' },
                },
            },
            scales: {
                x: { grid: { color: 'rgba(0,0,0,0.04)' }, border: { display: false }, ticks: { color: '#777', font: { size: 11 } } },
                y: {
                    grid: { color: 'rgba(0,0,0,0.04)' }, border: { display: false }, beginAtZero: true,
                    ticks: { color: '#777', callback: v => v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(0) + 'k' : v },
                },
            },
        },
    });
}

function renderHiverBixiSection() {
    const seasons = computeBixiWinterSeasons();
    renderHiverBixiCards(seasons);
    renderHiverBixiChart(seasons);
}

// ── Vue Arrondissement ───────────────────────────────────────────────
const ARR_TYPE_LABEL = { fut: "Capteur photo/vidéo", boucle: "Capteur au sol" };
let arrMap = null, arrLayer = null, arrReady = false;

function arrNames() {
    return Object.keys(countersByArrondissement)
        .filter(a => countersByArrondissement[a].some(c => allDailyData[c.value]))
        .sort((a, b) => a.localeCompare(b, "fr"));
}

// Total quotidien d'un compteur (null si aucune direction n'a de donnée)
function arrCounterDaily(inst) {
    const d = allDailyData[inst], out = {};
    d.labels.forEach((date, i) => {
        let tot = null;
        d.data.forEach(s => { const v = s[i]; if (v != null) tot = (tot || 0) + v; });
        if (tot != null) out[date] = tot;
    });
    return out;
}

function arrStats(arr) {
    const ids = countersByArrondissement[arr].map(c => c.value).filter(id => allDailyData[id]);
    const per = {}, span = {};
    ids.forEach(id => {
        per[id] = arrCounterDaily(id);
        const ds = Object.keys(per[id]).sort();
        span[id] = ds.length ? [ds[0], ds[ds.length - 1]] : null;
    });
    const dates = [...new Set(ids.flatMap(id => Object.keys(per[id])))].sort();
    const shift = (d, k) => { const x = new Date(d + "T12:00:00Z"); x.setUTCDate(x.getUTCDate() + k); return x.toISOString().slice(0, 10); };
    // Un compteur est attendu un jour donné s'il a transmis dans les 7 jours avant ou après
    // (les compteurs retirés l'hiver ne rendent donc pas tous les jours d'hiver incomplets)
    const expected = (id, d) => {
        for (let k = 1; k <= 7; k++) if (per[id][shift(d, -k)] != null || per[id][shift(d, k)] != null) return true;
        return false;
    };
    const totals = {}, complete = [];
    dates.forEach(d => {
        let tot = 0, ok = true, n = 0;
        ids.forEach(id => {
            const sp = span[id];
            if (!sp || d < sp[0] || d > sp[1]) return;
            const v = per[id][d];
            if (v != null) tot += v;
            else if (!expected(id, d)) return;
            n++;
            // Jour comparable : chaque compteur attendu a transmis, sans anomalie, et ce
            // n'est ni sa première ni sa dernière journée (souvent incomplètes)
            const anom = typeof anomalyDays !== "undefined" && anomalyDays[id] && anomalyDays[id][d];
            if (v == null || anom || d === sp[0] || d === sp[1]) ok = false;
        });
        totals[d] = tot;
        if (ok && n) complete.push(d);
    });
    const counters = ids.map(id => {
        const vals = Object.values(per[id]);
        return { id, avg: vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0, days: vals.length,
                 stopped: vfGroup(id) === "C" };
    }).sort((a, b) => b.avg - a.avg);
    return { ids, totals, complete, counters };
}

const arrFmt = n => Math.round(n || 0).toLocaleString("fr-CA");
const arrFmtDay = d => {
    const s = new Date(d + "T12:00:00").toLocaleDateString("fr-CA", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
    return s.charAt(0).toUpperCase() + s.slice(1);
};
function arrWeatherHtml(d) {
    const w = typeof weatherData !== "undefined" ? weatherData[d] : null;
    if (!w) return "<span class=\"arr-weather\">—</span>";
    const parts = [(w.icon || "") + " " + Math.round(w.tmax) + "° / " + Math.round(w.tmin) + "°"];
    if (w.snow >= 0.5) parts.push("❄️ " + w.snow.toLocaleString("fr-CA", { maximumFractionDigits: 1 }) + " cm");
    else if (w.precip >= 0.5) parts.push("💧 " + w.precip.toLocaleString("fr-CA", { maximumFractionDigits: 1 }) + " mm");
    return "<span class=\"arr-weather\">" + parts.join(" · ") + "</span>";
}

function arrRenderCards(arr, s, days, avg) {
    const fmtPct = p => (p >= 0 ? "+" : "−") + Math.abs(Math.round(p)) + " %";
    const total = Object.values(s.totals).reduce((a, b) => a + b, 0);
    const nFut = s.ids.filter(id => counterLocations[id] && counterLocations[id].type === "fut").length;
    const nSol = s.ids.length - nFut;
    const isWeekend = d => [0, 6].includes(new Date(d + "T12:00:00").getDay());
    const mean = list => list.length ? list.reduce((a, d) => a + s.totals[d], 0) / list.length : 0;
    const wk = mean(days.filter(d => !isWeekend(d))), we = mean(days.filter(isWeekend));
    const best = days.reduce((b, d) => (!b || s.totals[d] > s.totals[b]) ? d : b, null);
    const top = s.counters[0];
    const cards = [
        { title: "Passages", date: "12 derniers mois, tous compteurs", total: arrFmt(total), sub: "passages",
          meta: "<span><strong>" + s.ids.length + "</strong> compteur" + (s.ids.length > 1 ? "s" : "") +
                " (" + nFut + " photo/vidéo, " + nSol + " au sol)</span>" },
        { title: "Moyenne par jour", date: "Jours où tous les compteurs en service ont transmis", total: arrFmt(avg), sub: "passages / jour",
          meta: days.length ? "<span>Semaine : <strong>" + arrFmt(wk) + "</strong> · Fin de semaine : <strong>" + arrFmt(we) + "</strong></span>" : "" },
        { title: "Journée record", date: best ? arrFmtDay(best) : "—", total: best ? arrFmt(s.totals[best]) : "—", sub: "passages",
          meta: best ? "<span>" + arrWeatherHtml(best) + "</span><span>" + fmtPct((s.totals[best] - avg) / avg * 100) + " vs la moyenne</span>" : "" },
        { title: "Compteur le plus achalandé", date: top ? counterLocations[top.id].label : "—", total: top ? arrFmt(top.avg) : "—", sub: "passages / jour en moyenne",
          meta: top ? "<span class=\"global-top-row\" onclick=\"goToCounter('" + top.id + "')\">Voir le compteur →</span>" : "" },
    ];
    document.getElementById("arr-cards").innerHTML = cards.map(c =>
        "<div class=\"global-card\">" +
            "<div class=\"global-card-title\">" + c.title + "</div>" +
            "<div class=\"global-card-date\">" + c.date + "</div>" +
            "<div class=\"global-card-total\">" + c.total + "</div>" +
            "<div class=\"global-card-sub\">" + c.sub + "</div>" +
            (c.meta ? "<div class=\"global-card-meta\">" + c.meta + "</div>" : "") +
        "</div>"
    ).join("");
}

function arrRenderMap(s) {
    if (typeof L === "undefined") return;
    if (!arrMap) {
        arrMap = L.map("arr-map", { scrollWheelZoom: false }).setView([45.53, -73.59], 11);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "© <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a>", maxZoom: 19,
        }).addTo(arrMap);
    }
    if (arrLayer) arrLayer.remove();
    arrLayer = L.featureGroup().addTo(arrMap);
    s.ids.forEach(id => {
        const loc = counterLocations[id];
        if (!loc) return;
        L.circleMarker([loc.lat, loc.lng], markerStyle(false, gappyCounters.has(id), loc.type, vfGroup(id)))
            .bindTooltip(loc.label, { direction: "top", offset: [0, -6] })
            .on("click", () => goToCounter(id))
            .addTo(arrLayer);
    });
    arrMap.invalidateSize();
    if (arrLayer.getLayers().length) arrMap.fitBounds(arrLayer.getBounds(), { padding: [30, 30], maxZoom: 15 });
}

function arrRenderCounters(s) {
    const max = s.counters.length ? s.counters[0].avg : 1;
    document.getElementById("arr-counters").innerHTML = s.counters.map(c => {
        const loc = counterLocations[c.id] || {};
        const type = loc.type || "fut";
        return "<div class=\"arr-row arr-counter-row\" onclick=\"goToCounter('" + c.id + "')\">" +
            "<span class=\"arr-name\" title=\"" + (loc.label || c.id).replace(/"/g, "&quot;") + "\">" +
                "<span class=\"arr-type arr-type-" + type + "\">" + (type === "fut" ? "Photo/vidéo" : "Au sol") + "</span>" + (loc.label || c.id) + "</span>" +
            "<div class=\"arr-bar-wrap\"><div class=\"arr-bar\" style=\"width:" + (c.avg / max * 100).toFixed(1) + "%\"></div></div>" +
            "<span class=\"arr-vol\">" + (c.stopped ? "<span class=\"arr-stopped\">inactif</span>" : arrFmt(c.avg) + " / j") + "</span></div>";
    }).join("");
}

function arrRenderMonthly(s, days) {
    if (charts["__arr_monthly__"]) { charts["__arr_monthly__"].destroy(); delete charts["__arr_monthly__"]; }
    const byMonth = {};
    days.forEach(d => {
        const m = d.slice(0, 7);
        byMonth[m] = byMonth[m] || { sum: 0, n: 0 };
        byMonth[m].sum += s.totals[d]; byMonth[m].n++;
    });
    const months = Object.keys(byMonth).sort();
    const ctx = document.getElementById("arr-monthly-chart");
    if (!ctx || !months.length) return;
    charts["__arr_monthly__"] = new Chart(ctx, {
        type: "bar",
        data: {
            labels: months.map(m => new Date(m + "-15T12:00:00").toLocaleDateString("fr-CA", { month: "short", year: "2-digit" })),
            datasets: [{ data: months.map(m => byMonth[m].sum / byMonth[m].n), backgroundColor: "rgba(29,184,96,0.75)", borderRadius: 4 }],
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
            plugins: { legend: { display: false }, tooltip: { callbacks: {
                label: item => " " + arrFmt(item.raw) + " passages / jour (" + byMonth[months[item.dataIndex]].n + " j)",
            } } },
            scales: {
                x: { grid: { display: false }, border: { display: false }, ticks: { color: "#777", font: { size: 11 } } },
                y: { grid: { color: "rgba(0,0,0,0.04)" }, border: { display: false }, beginAtZero: true, ticks: { color: "#777" } },
            },
        },
    });
}

function arrRenderWeather(s, days, avg) {
    const cats = [
        { icon: "☀️", label: "Jours secs (≥ 10 °C)", test: w => w.precip < 1 && w.snow < 0.5 && w.tmax >= 10 },
        { icon: "🌧️", label: "Pluie (≥ 5 mm)", test: w => w.precip >= 5 && w.snow < 0.5 },
        { icon: "🌨️", label: "Neige (≥ 1 cm)", test: w => w.snow >= 1 },
        { icon: "🥶", label: "Froid (max < −10 °C)", test: w => w.tmax < -10 },
    ];
    document.getElementById("arr-weather-grid").innerHTML = cats.map(c => {
        const list = days.filter(d => typeof weatherData !== "undefined" && weatherData[d] && c.test(weatherData[d]));
        const m = list.length ? list.reduce((a, d) => a + s.totals[d], 0) / list.length : null;
        const diff = m != null && avg ? Math.round((m - avg) / avg * 100) : null;
        return "<div><div class=\"net-val\" style=\"font-size:26px\">" + c.icon + " " + (m != null ? arrFmt(m) : "—") + "</div>" +
            "<div class=\"net-label\">" + c.label + "</div>" +
            "<div class=\"arr-weather\" style=\"margin-top:4px\">" + list.length + " j" +
            (diff != null ? " · " + (diff >= 0 ? "+" : "−") + Math.abs(diff) + " % vs moyenne" : "") + "</div></div>";
    }).join("");
}

function arrDaysTable(s, list, avg) {
    if (!list.length) return "<p class=\"arr-note\">Pas assez de données.</p>";
    return "<table class=\"data-table\"><thead><tr><th>Date</th><th>Passages</th><th>vs moy.</th><th>Météo</th></tr></thead><tbody>" +
        list.map(d => {
            const p = avg ? (s.totals[d] - avg) / avg * 100 : 0;
            return "<tr><td><strong>" + arrFmtDay(d) + "</strong></td><td><strong>" + arrFmt(s.totals[d]) + "</strong></td>" +
                "<td>" + (p >= 0 ? "+" : "−") + Math.abs(Math.round(p)) + " %</td><td>" + arrWeatherHtml(d) + "</td></tr>";
        }).join("") + "</tbody></table>";
}

function renderArrondissement(arr) {
    const s = arrStats(arr);
    // Jours comparables si possible ; sinon tous les jours avec des passages
    const strict = s.complete.length >= 14;
    const days = strict ? s.complete : Object.keys(s.totals).filter(d => s.totals[d] > 0);
    const avg = days.length ? days.reduce((a, d) => a + s.totals[d], 0) / days.length : 0;
    arrRenderCards(arr, s, days, avg);
    arrRenderMap(s);
    arrRenderCounters(s);
    arrRenderMonthly(s, days);
    arrRenderWeather(s, days, avg);
    const sorted = days.slice().sort((a, b) => s.totals[b] - s.totals[a]);
    document.getElementById("arr-top-days").innerHTML = arrDaysTable(s, sorted.slice(0, 10), avg);
    document.getElementById("arr-low-days").innerHTML = arrDaysTable(s, sorted.slice(-10).reverse(), avg);
    document.getElementById("arr-days-note").textContent = strict
        ? "Classement sur " + days.length + " jours où tous les compteurs en service ont transmis des données complètes (jours d’anomalie exclus). Certains compteurs sont retirés l’hiver : les totaux d’hiver portent alors sur moins de compteurs."
        : "Peu de jours où tous les compteurs ont transmis en même temps : le classement porte sur tous les jours avec des données, et un creux peut refléter une panne de compteur.";
}

function renderArrPage() {
    if (!arrReady) {
        arrReady = true;
        const sel = document.getElementById("arrPageSelect");
        const names = arrNames();
        sel.innerHTML = names.map(a => "<option value=\"" + a.replace(/"/g, "&quot;") + "\">" + a + " (" +
            countersByArrondissement[a].filter(c => allDailyData[c.value]).length + ")</option>").join("");
        // Par défaut : l'arrondissement avec le plus de compteurs
        const def = names.reduce((b, a) => (!b || countersByArrondissement[a].length > countersByArrondissement[b].length) ? a : b, null);
        if (def) sel.value = def;
        sel.addEventListener("change", function() { renderArrondissement(this.value); });
        if (sel.value) renderArrondissement(sel.value);
    } else if (arrMap) {
        arrMap.invalidateSize();
    }
}

// ── BIXI ─────────────────────────────────────────────────────────────
const BIXI_MONTHS = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sep", "Oct", "Nov", "Déc"];
const BIXI_DURATION_LABELS = ["< 10 min", "10–20", "20–30", "30–45", "45–60", "60 min +"];
const BIXI_TOOLTIP = { backgroundColor: "rgba(40,10,10,0.90)", titleColor: "#FCA5A5", bodyColor: "#fff", padding: 10, cornerRadius: 8 };
const BIXI_AXIS = { grid: { color: "rgba(0,0,0,0.04)" }, border: { display: false }, ticks: { color: "#777", font: { size: 11 } } };
const bixiFmt = n => Math.round(n || 0).toLocaleString("fr-CA");
const bixiShort = v => v >= 1e6 ? (v / 1e6).toLocaleString("fr-CA", { maximumFractionDigits: 1 }) + " M" : v >= 1e3 ? Math.round(v / 1e3) + " k" : v;
const bixiFmtDate = d => new Date(d + "T00:00:00").toLocaleDateString("fr-CA", { day: "numeric", month: "long", year: "numeric" });

// Périmètre affiché : tout le réseau, ou trajets partis d'une station de la Ville de Montréal
let bixiScope = "all";
let bixiData = {};
function bixiComputeData() {
    bixiData = {};
    if (typeof bixiYearly === "undefined") return;
    Object.entries(bixiYearly).forEach(([y, s]) => {
        const d = bixiScope === "mtl" ? s.mtl : s;
        if (d && d.trips) bixiData[y] = { ...d, partial: s.partial };
    });
}
function bixiYears() { return Object.keys(bixiData).sort(); }
function bixiCompleteYears() { return bixiYears().filter(y => !bixiData[y].partial); }
function bixiCurrentYear() {
    const ys = bixiYears().filter(y => bixiData[y].partial);
    return ys.length ? ys[ys.length - 1] : null;
}
function bixiLastDate(y) {
    const ds = Object.keys(bixiData[y].daily).sort();
    return ds[ds.length - 1];
}
function bixiMonthly(y) {
    const m = new Array(12).fill(0);
    Object.entries(bixiData[y].daily).forEach(([d, n]) => { m[parseInt(d.slice(5, 7), 10) - 1] += n; });
    return m;
}
// Trajets de l'année y jusqu'au jour mmdd inclus (pour comparer à date égale)
function bixiSumUntil(y, mmdd) {
    let s = 0;
    Object.entries(bixiData[y].daily).forEach(([d, n]) => { if (d.slice(5) <= mmdd) s += n; });
    return s;
}
function bixiPct(a, b) { return b ? (a - b) / b * 100 : null; }
function bixiPctHtml(p) {
    if (p == null || !isFinite(p)) return "<span>—</span>";
    const up = p >= 0;
    return "<span class=\"" + (up ? "bixi-up" : "bixi-down") + "\">" + (up ? "▲ +" : "▼ −") +
        Math.abs(p).toLocaleString("fr-CA", { maximumFractionDigits: 1 }) + " %</span>";
}
// Couleur d'une année : l'année en cours en rouge, puis des teintes de plus en plus pâles
function bixiYearColor(y, years) {
    const rank = years.length - 1 - years.indexOf(y);
    const palette = ["#E1251B", "#29ABE2", "#1DB860", "#F59E0B", "#8B5CF6", "#EC4899"];
    return rank < palette.length ? palette[rank] : "rgba(120,120,120,0.45)";
}

function renderBixiCards() {
    const el = document.getElementById("bixi-cards");
    const ys = bixiYears();
    if (!ys.length) {
        el.innerHTML = "<div class=\"global-card\">Données BIXI indisponibles pour le moment.</div>";
        return;
    }
    const complete = bixiCompleteYears();
    const cur = bixiCurrentYear();
    const cards = [];
    if (complete.length) {
        const y = complete[complete.length - 1];
        const prev = bixiData[String(+y - 1)];
        cards.push({
            title: "Année " + y, date: "Du 1er janvier au 31 décembre",
            total: bixiFmt(bixiData[y].trips), sub: "trajets",
            meta: prev ? "<span>" + bixiPctHtml(bixiPct(bixiData[y].trips, prev.trips)) + " vs " + (+y - 1) + "</span>" : "",
        });
    }
    if (cur) {
        const last = bixiLastDate(cur);
        const prevY = String(+cur - 1);
        const ytd = bixiData[cur].trips;
        const prevYtd = bixiData[prevY] ? bixiSumUntil(prevY, last.slice(5)) : 0;
        cards.push({
            title: cur + " à ce jour", date: "Jusqu’au " + bixiFmtDate(last),
            total: bixiFmt(ytd), sub: "trajets",
            meta: prevYtd ? "<span>" + bixiPctHtml(bixiPct(ytd, prevYtd)) + " vs même période en " + prevY + "</span>" : "",
        });
    }
    let best = null;
    ys.forEach(y => Object.entries(bixiData[y].daily).forEach(([d, n]) => { if (!best || n > best.n) best = { d, n }; }));
    if (best) {
        cards.push({ title: "Record en une journée", date: bixiFmtDate(best.d), total: bixiFmt(best.n), sub: "trajets", meta: "" });
    }
    if (complete.length >= 2) {
        const first = complete[0], last = complete[complete.length - 1];
        const a = bixiData[first].trips, b = bixiData[last].trips;
        cards.push({
            title: "Croissance depuis " + first, date: first + " → " + last,
            total: "×" + (b / a).toLocaleString("fr-CA", { maximumFractionDigits: 1 }), sub: "trajets annuels",
            meta: "<span>" + bixiFmt(a) + " → <strong>" + bixiFmt(b) + "</strong></span>",
        });
    }
    el.innerHTML = cards.map(c =>
        "<div class=\"global-card\">" +
            "<div class=\"global-card-title\">" + c.title + "</div>" +
            "<div class=\"global-card-date\">" + c.date + "</div>" +
            "<div class=\"global-card-total\">" + c.total + "</div>" +
            "<div class=\"global-card-sub\">" + c.sub + "</div>" +
            (c.meta ? "<div class=\"global-card-meta\">" + c.meta + "</div>" : "") +
        "</div>"
    ).join("");
}

function renderBixiYearlyChart() {
    const ctx = document.getElementById("bixi-yearly-chart");
    const ys = bixiYears();
    if (!ctx || !ys.length) return;
    const trips = ys.map(y => bixiData[y].trips);
    charts["__bixi_yearly__"] = new Chart(ctx, {
        type: "bar",
        data: {
            labels: ys.map(y => bixiData[y].partial ? y + "*" : y),
            datasets: [{
                label: "Trajets",
                data: trips,
                backgroundColor: ys.map(y => bixiData[y].partial ? "rgba(225,37,27,0.35)" : "rgba(225,37,27,0.80)"),
                borderColor: "#B91C1C",
                borderWidth: 1,
                borderRadius: 4,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: { duration: 500 },
            plugins: {
                legend: { display: false },
                tooltip: { ...BIXI_TOOLTIP, callbacks: {
                    label: item => {
                        const y = ys[item.dataIndex];
                        const lines = [" " + bixiFmt(item.raw) + " trajets"];
                        if (bixiData[y].partial) lines.push(" En cours (jusqu’au " + bixiFmtDate(bixiLastDate(y)) + ")");
                        else if (item.dataIndex > 0 && ys[item.dataIndex - 1] === String(+y - 1)) {
                            const p = bixiPct(item.raw, trips[item.dataIndex - 1]);
                            lines.push(" " + (p >= 0 ? "+" : "−") + Math.abs(p).toLocaleString("fr-CA", { maximumFractionDigits: 1 }) + " % vs " + (+y - 1));
                        }
                        return lines;
                    },
                } },
            },
            scales: {
                x: BIXI_AXIS,
                y: { ...BIXI_AXIS, beginAtZero: true, ticks: { ...BIXI_AXIS.ticks, callback: bixiShort } },
            },
        },
    });
}

function renderBixiCumulChart() {
    const ctx = document.getElementById("bixi-cumul-chart");
    const ys = bixiYears();
    if (!ctx || !ys.length) return;
    // Axe : 365 jours (le 29 février est regroupé avec le 28)
    const labels = [];
    for (let d = new Date(Date.UTC(2023, 0, 1)); d.getUTCFullYear() === 2023; d.setUTCDate(d.getUTCDate() + 1)) {
        labels.push(d.toISOString().slice(5, 10));
    }
    const datasets = ys.map(y => {
        const byDay = {};
        Object.entries(bixiData[y].daily).forEach(([d, n]) => {
            const k = d.slice(5) === "02-29" ? "02-28" : d.slice(5);
            byDay[k] = (byDay[k] || 0) + n;
        });
        const last = bixiData[y].partial ? bixiLastDate(y).slice(5) : "12-31";
        let cum = 0;
        const data = labels.map(k => { cum += byDay[k] || 0; return k <= last ? cum : null; });
        const color = bixiYearColor(y, ys);
        const rank = ys.length - 1 - ys.indexOf(y);
        return {
            label: y, data, borderColor: color, backgroundColor: color,
            borderWidth: rank === 0 ? 3 : 1.5, pointRadius: 0, pointHoverRadius: 3, tension: 0.2,
            hidden: rank >= 6,
        };
    });
    charts["__bixi_cumul__"] = new Chart(ctx, {
        type: "line",
        data: { labels, datasets },
        options: {
            responsive: true, maintainAspectRatio: false, animation: { duration: 500 },
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } },
                tooltip: { ...BIXI_TOOLTIP, callbacks: {
                    title: items => {
                        const [m, d] = items[0].label.split("-");
                        return parseInt(d, 10) + " " + BIXI_MONTHS[parseInt(m, 10) - 1].toLowerCase();
                    },
                    label: item => " " + item.dataset.label + " : " + bixiFmt(item.raw),
                } },
            },
            scales: {
                x: { ...BIXI_AXIS, grid: { display: false }, ticks: { ...BIXI_AXIS.ticks, autoSkip: false, maxRotation: 0,
                    callback: (v, i) => labels[i].endsWith("-01") ? BIXI_MONTHS[parseInt(labels[i].slice(0, 2), 10) - 1] : null } },
                y: { ...BIXI_AXIS, beginAtZero: true, ticks: { ...BIXI_AXIS.ticks, callback: bixiShort } },
            },
        },
    });
}

function renderBixiMonthlyChart() {
    const ctx = document.getElementById("bixi-monthly-chart");
    const ys = bixiYears();
    if (!ctx || !ys.length) return;
    const datasets = ys.map(y => {
        const m = bixiMonthly(y);
        const lastMonth = bixiData[y].partial ? parseInt(bixiLastDate(y).slice(5, 7), 10) : 12;
        const color = bixiYearColor(y, ys);
        const rank = ys.length - 1 - ys.indexOf(y);
        return {
            label: y, data: m.map((v, i) => i < lastMonth ? v : null),
            borderColor: color, backgroundColor: color,
            borderWidth: rank === 0 ? 3 : 1.5, pointRadius: 2, tension: 0.25,
            hidden: rank >= 6,
        };
    });
    charts["__bixi_monthly__"] = new Chart(ctx, {
        type: "line",
        data: { labels: BIXI_MONTHS, datasets },
        options: {
            responsive: true, maintainAspectRatio: false, animation: { duration: 500 },
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } },
                tooltip: { ...BIXI_TOOLTIP, callbacks: { label: item => " " + item.dataset.label + " : " + bixiFmt(item.raw) } },
            },
            scales: {
                x: BIXI_AXIS,
                y: { ...BIXI_AXIS, beginAtZero: true, ticks: { ...BIXI_AXIS.ticks, callback: bixiShort } },
            },
        },
    });
}

function renderBixiAnnualTable() {
    const el = document.getElementById("bixi-annual-table");
    const ys = bixiYears().slice().reverse();
    if (!ys.length) { el.innerHTML = ""; return; }
    const rows = ys.map(y => {
        const s = bixiData[y];
        const prev = bixiData[String(+y - 1)];
        const days = Object.keys(s.daily).length;
        const m = bixiMonthly(y);
        const bestMonth = m.indexOf(Math.max(...m));
        // Année en cours : croissance à date égale
        const growth = !prev ? null : s.partial
            ? bixiPct(s.trips, bixiSumUntil(String(+y - 1), bixiLastDate(y).slice(5)))
            : bixiPct(s.trips, prev.trips);
        return "<tr>" +
            "<td><strong>" + y + (s.partial ? "*" : "") + "</strong></td>" +
            "<td><strong>" + bixiFmt(s.trips) + "</strong></td>" +
            "<td>" + bixiPctHtml(growth) + "</td>" +
            "<td>" + bixiFmt(days ? s.trips / days : 0) + "</td>" +
            "<td>" + (s.avg_duration_min != null ? s.avg_duration_min.toLocaleString("fr-CA") + " min" : "—") + "</td>" +
            "<td>" + (s.member_share != null ? Math.round(s.member_share * 100) + " %" : "—") + "</td>" +
            "<td>" + bixiFmt(s.stations) + "</td>" +
            "<td>" + BIXI_MONTHS[bestMonth] + "</td>" +
        "</tr>";
    });
    el.innerHTML = "<table class=\"data-table\"><thead><tr>" +
        "<th>Année</th><th>Trajets</th><th>Croissance</th><th>Moy./jour</th><th>Durée moy.</th><th>Membres</th><th>Stations</th><th>Mois record</th>" +
        "</tr></thead><tbody>" + rows.join("") + "</tbody></table>";
}

function renderBixiYear(y) {
    const s = bixiData[y];
    if (!s) return;
    const prevY = String(+y - 1);
    const prev = bixiData[prevY];
    const m = bixiMonthly(y);
    const pm = prev ? bixiMonthly(prevY) : null;
    const lastDate = s.partial ? bixiLastDate(y) : null;

    // Tableau mensuel
    const rows = [];
    for (let i = 0; i < 12; i++) {
        const mm = String(i + 1).padStart(2, "0");
        const days = Object.entries(s.daily).filter(([d]) => d.slice(5, 7) === mm);
        if (!m[i] && !(pm && pm[i])) continue;
        if (lastDate && i + 1 > parseInt(lastDate.slice(5, 7), 10)) continue;
        const inProgress = lastDate && lastDate.slice(5, 7) === mm;
        // Mois en cours : comparer au même nombre de jours l'an dernier
        let ref = pm ? pm[i] : null;
        if (inProgress && prev) {
            ref = 0;
            Object.entries(prev.daily).forEach(([d, n]) => { if (d.slice(5, 7) === mm && d.slice(5) <= lastDate.slice(5)) ref += n; });
        }
        const best = days.reduce((b, [d, n]) => (!b || n > b[1]) ? [d, n] : b, null);
        rows.push("<tr>" +
            "<td><strong>" + BIXI_MONTHS[i] + (inProgress ? "*" : "") + "</strong></td>" +
            "<td><strong>" + (m[i] ? bixiFmt(m[i]) : "—") + "</strong></td>" +
            "<td>" + (m[i] && ref ? bixiPctHtml(bixiPct(m[i], ref)) : "—") + "</td>" +
            "<td>" + (days.length ? bixiFmt(m[i] / days.length) : "—") + "</td>" +
            "<td>" + (best ? parseInt(best[0].slice(8), 10) + " " + BIXI_MONTHS[i].toLowerCase() + " (" + bixiFmt(best[1]) + ")" : "—") + "</td>" +
        "</tr>");
    }
    document.getElementById("bixi-monthly-table").innerHTML = "<table class=\"data-table\"><thead><tr>" +
        "<th>Mois</th><th>Trajets</th><th>vs " + prevY + "</th><th>Moy./jour</th><th>Jour record</th>" +
        "</tr></thead><tbody>" + rows.join("") + "</tbody></table>" +
        (lastDate ? "<p class=\"bixi-note\">* Mois en cours, comparé aux mêmes jours en " + prevY + ".</p>" : "");

    // Graphiques
    ["__bixi_hour__", "__bixi_dow__", "__bixi_duration__"].forEach(k => {
        if (charts[k]) { charts[k].destroy(); delete charts[k]; }
    });
    const smallOpts = (fmtTip) => ({
        responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
        plugins: { legend: { display: false }, tooltip: { ...BIXI_TOOLTIP, callbacks: { label: fmtTip } } },
        scales: {
            x: { ...BIXI_AXIS, grid: { display: false } },
            y: { ...BIXI_AXIS, beginAtZero: true, ticks: { ...BIXI_AXIS.ticks, callback: bixiShort } },
        },
    });
    const bar = { backgroundColor: "rgba(225,37,27,0.75)", borderRadius: 3 };
    charts["__bixi_hour__"] = new Chart(document.getElementById("bixi-hour-chart"), {
        type: "bar",
        data: { labels: [...Array(24).keys()].map(h => h + "h"), datasets: [{ ...bar, data: s.hourly }] },
        options: smallOpts(item => " " + bixiFmt(item.raw) + " trajets"),
    });
    const dowSum = new Array(7).fill(0), dowN = new Array(7).fill(0);
    Object.entries(s.daily).forEach(([d, n]) => {
        const i = (new Date(d + "T12:00:00").getDay() + 6) % 7;
        dowSum[i] += n; dowN[i] += 1;
    });
    charts["__bixi_dow__"] = new Chart(document.getElementById("bixi-dow-chart"), {
        type: "bar",
        data: { labels: ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"], datasets: [{ ...bar, data: dowSum.map((v, i) => dowN[i] ? v / dowN[i] : 0) }] },
        options: smallOpts(item => " " + bixiFmt(item.raw) + " trajets / jour"),
    });
    const durTotal = (s.duration_hist || []).reduce((a, b) => a + b, 0);
    charts["__bixi_duration__"] = new Chart(document.getElementById("bixi-duration-chart"), {
        type: "bar",
        data: { labels: BIXI_DURATION_LABELS, datasets: [{ ...bar, data: (s.duration_hist || []).map(v => durTotal ? v / durTotal * 100 : 0) }] },
        options: { ...smallOpts(item => " " + item.raw.toLocaleString("fr-CA", { maximumFractionDigits: 1 }) + " % des trajets"),
            scales: { x: { ...BIXI_AXIS, grid: { display: false } }, y: { ...BIXI_AXIS, beginAtZero: true, ticks: { ...BIXI_AXIS.ticks, callback: v => v + " %" } } } },
    });

    // Stations les plus populaires
    const top = s.top_stations || [];
    const max = top.length ? top[0][1] : 1;
    document.getElementById("bixi-top-stations").innerHTML = top.map(([name, n]) =>
        "<div class=\"arr-row\"><span class=\"arr-name\" title=\"" + name.replace(/"/g, "&quot;") + "\">" + name + "</span>" +
        "<div class=\"arr-bar-wrap\"><div class=\"arr-bar\" style=\"width:" + (n / max * 100).toFixed(1) + "%;background:linear-gradient(to right,#FCA5A5,#E1251B)\"></div></div>" +
        "<span class=\"arr-vol\">" + bixiFmt(n) + "</span></div>"
    ).join("") || "<p class=\"bixi-note\">Aucune donnée de station.</p>";
}

let bixiReady = false;
function renderBixiAll() {
    Object.keys(charts).filter(k => k.startsWith("__bixi_")).forEach(k => { charts[k].destroy(); delete charts[k]; });
    bixiComputeData();
    const ys = bixiYears();
    renderBixiCards();
    if (!ys.length) return;
    renderBixiYearlyChart();
    renderBixiCumulChart();
    renderBixiMonthlyChart();
    renderBixiAnnualTable();
    const sel = document.getElementById("bixiYearSelect");
    const keep = sel.value;
    sel.innerHTML = ys.slice().reverse().map(y => "<option value=\"" + y + "\">" + y + (bixiData[y].partial ? " (en cours)" : "") + "</option>").join("");
    if (keep && bixiData[keep]) sel.value = keep;
    renderBixiYear(sel.value);
}

function renderBixiPage() {
    if (bixiReady) return;
    bixiReady = true;
    document.getElementById("bixiYearSelect").addEventListener("change", function() { renderBixiYear(this.value); });
    document.querySelectorAll("#bixi-scope button").forEach(btn => btn.addEventListener("click", function() {
        if (bixiScope === this.dataset.scope) return;
        bixiScope = this.dataset.scope;
        document.querySelectorAll("#bixi-scope button").forEach(b => b.classList.toggle("active", b === this));
        renderBixiAll();
    }));
    renderBixiAll();
}

// ── Fonctions historique compteur ────────────────────────────────────
const _FR_MONTHS = ['Jan','Fév','Mar','Avr','Mai','Juin','Juil','Août','Sep','Oct','Nov','Déc'];
const _PALETTE   = ['#1DB860','#29ABE2','#F59E0B','#EF4444','#8B5CF6','#EC4899','#14B8A6','#F97316'];

function histDataForInstance(instance) {
    if (typeof counterHistoricalData === 'undefined') return null;
    return counterHistoricalData[instance] || null;
}

function destroyHistCharts() {
    ['__hist_compare__', '__hist_timeline__'].forEach(k => {
        if (charts[k]) { charts[k].destroy(); charts[k] = null; }
    });
}

function resetHistPanel() {
    destroyHistCharts();
    document.getElementById('hist-compare-panel').style.display  = 'none';
    document.getElementById('hist-timeline-panel').style.display  = 'none';
    document.getElementById('hist-panel').style.display           = 'none';
    document.getElementById('btnHistCompare').classList.remove('active');
    document.getElementById('btnHistTimeline').classList.remove('active');
    histMode = null;
}

function updateHistButtons(instance) {
    // Désactivé — fonctionnalité réservée à la Vue Globale pour l'instant
    document.getElementById('hist-btns').style.display = 'none';
    resetHistPanel();
}

function buildCompareChart(instance, yearA, yearB) {
    destroyHistCharts();
    const hd  = histDataForInstance(instance);
    if (!hd) return;

    const yearTotal = y => Object.values(hd[y] || {}).reduce((a, b) => a + b, 0);
    const curY = new Date().getFullYear();

    const totA = yearTotal(yearA);
    const totB = yearTotal(yearB);

    const labelA = yearA == curY ? String(yearA) + ' (en cours)' : String(yearA);
    const labelB = yearB == curY ? String(yearB) + ' (en cours)' : String(yearB);
    const colorA = yearA == curY ? 'rgba(29,184,96,0.75)'  : 'rgba(41,171,226,0.75)';
    const colorB = yearB == curY ? 'rgba(29,184,96,0.75)'  : 'rgba(41,171,226,0.45)';
    const bdrA   = yearA == curY ? '#1DB860'               : '#29ABE2';
    const bdrB   = yearB == curY ? '#1DB860'               : 'rgba(41,171,226,0.9)';

    const ctx = document.getElementById('hist-canvas-compare');
    if (!ctx) return;
    charts['__hist_compare__'] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: [labelA, labelB],
            datasets: [{
                label: 'Total annuel',
                data:  [totA, totB],
                backgroundColor: [colorA, colorB],
                borderColor:     [bdrA, bdrB],
                borderWidth: 1,
                borderRadius: 6,
            }],
        },
        options: _histChartOpts('Passages'),
    });
}

function buildTimelineChart(instance, subMode) {
    destroyHistCharts();
    const hd = histDataForInstance(instance);
    if (!hd) return;
    const years = Object.keys(hd).map(Number).sort();
    const ctx   = document.getElementById('hist-canvas-timeline');
    if (!ctx) return;

    if (subMode === 'annual') {
        const totals = years.map(y => Object.values(hd[y] || {}).reduce((a,b) => a+b, 0));
        const curY   = new Date().getFullYear();
        charts['__hist_timeline__'] = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: years,
                datasets: [{
                    label: 'Passages totaux',
                    data:  totals,
                    backgroundColor: years.map(y => y === curY ? 'rgba(29,184,96,0.45)' : 'rgba(29,184,96,0.75)'),
                    borderColor: '#1DB860',
                    borderWidth: 1,
                    borderRadius: 4,
                }],
            },
            options: _histChartOpts('Passages/an'),
        });
    } else {
        // Mensuel : une ligne par année
        const months   = [1,2,3,4,5,6,7,8,9,10,11,12];
        const datasets = years.map((y, i) => ({
            label: String(y),
            data:  months.map(m => (hd[y] || {})[m] || null),
            borderColor:     _PALETTE[i % _PALETTE.length],
            backgroundColor: _PALETTE[i % _PALETTE.length] + '22',
            fill: false, tension: 0.3, borderWidth: 1.5, pointRadius: 2,
        }));
        charts['__hist_timeline__'] = new Chart(ctx, {
            type: 'line',
            data: { labels: _FR_MONTHS, datasets },
            options: { ..._histChartOpts('Passages/mois'), plugins: { ..._histChartOpts('Passages/mois').plugins, legend: { display: true, position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } } },
        });
    }
}

function _histChartOpts(yLabel) {
    return {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 400 },
        plugins: {
            legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
            tooltip: {
                backgroundColor: 'rgba(10,40,20,0.88)',
                titleColor: '#7ee8a2', bodyColor: '#fff',
                padding: 9, cornerRadius: 7,
                callbacks: {
                    label: item => ' ' + (item.dataset.label || '') + ' : ' +
                        (item.raw !== null ? Math.round(item.raw).toLocaleString('fr-CA') : '—'),
                },
            },
        },
        scales: {
            x: { grid: { color: 'rgba(0,0,0,0.04)' }, border: { display: false }, ticks: { color: '#777', font: { size: 11 } } },
            y: { grid: { color: 'rgba(0,0,0,0.04)' }, border: { display: false }, beginAtZero: true,
                ticks: { color: '#777', callback: v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(0)+'k' : v },
                title: { display: true, text: yLabel, color: '#999', font: { size: 11 } } },
        },
    };
}

function _fillYearSelect(sel, years, selected) {
    sel.innerHTML = '';
    years.forEach(y => {
        const opt = document.createElement('option');
        opt.value = y;
        opt.textContent = y;
        sel.appendChild(opt);
    });
    sel.value = selected;
}

function populateHistYearSelect(instance) {
    const hd    = histDataForInstance(instance);
    if (!hd) return;
    const curY  = new Date().getFullYear();
    const years = Object.keys(hd).map(Number).sort().reverse();
    const selA  = document.getElementById('hist-year-a');
    const selB  = document.getElementById('hist-year-b');
    _fillYearSelect(selA, years, curY);
    // default année B = année la plus récente avant curY
    const defaultB = years.find(y => y < curY) || years[years.length - 1];
    _fillYearSelect(selB, years, defaultB);
}

// ── Boutons historique ──
document.getElementById('btnHistCompare').addEventListener('click', function() {
    const instance = getSelectedCounter();
    if (!instance) return;
    const wasActive = histMode === 'compare';
    resetHistPanel();
    if (wasActive) return;
    histMode = 'compare';
    this.classList.add('active');
    document.getElementById('hist-panel-title').textContent = 'Comparaison annuelle';
    populateHistYearSelect(instance);
    document.getElementById('hist-compare-panel').style.display = 'block';
    document.getElementById('hist-panel').style.display = 'block';
    const yA = parseInt(document.getElementById('hist-year-a').value);
    const yB = parseInt(document.getElementById('hist-year-b').value);
    setTimeout(() => buildCompareChart(instance, yA, yB), 0);
});

document.getElementById('btnHistTimeline').addEventListener('click', function() {
    const instance = getSelectedCounter();
    if (!instance) return;
    const wasActive = histMode === 'timeline';
    resetHistPanel();
    if (wasActive) return;
    histMode = 'timeline';
    this.classList.add('active');
    document.getElementById('hist-panel-title').textContent = 'Historique multi-années';
    document.getElementById('hist-timeline-panel').style.display = 'block';
    document.getElementById('hist-panel').style.display = 'block';
    setTimeout(() => buildTimelineChart(instance, histSubMode), 0);
});

function _onCompareYearChange() {
    const instance = getSelectedCounter();
    if (!instance || histMode !== 'compare') return;
    const yA = parseInt(document.getElementById('hist-year-a').value);
    const yB = parseInt(document.getElementById('hist-year-b').value);
    buildCompareChart(instance, yA, yB);
}
document.getElementById('hist-year-a').addEventListener('change', _onCompareYearChange);
document.getElementById('hist-year-b').addEventListener('change', _onCompareYearChange);

document.getElementById('btnHistAnnual').addEventListener('click', function() {
    if (histSubMode === 'annual') return;
    histSubMode = 'annual';
    this.classList.add('active');
    document.getElementById('btnHistMonthly').classList.remove('active');
    setTimeout(() => buildTimelineChart(getSelectedCounter(), 'annual'), 0);
});

document.getElementById('btnHistMonthly').addEventListener('click', function() {
    if (histSubMode === 'monthly') return;
    histSubMode = 'monthly';
    this.classList.add('active');
    document.getElementById('btnHistAnnual').classList.remove('active');
    setTimeout(() => buildTimelineChart(getSelectedCounter(), 'monthly'), 0);
});

const VIEW_NAMES = new Set(['main', 'global', 'hiver', 'bixi', 'arr', 'methodo']);

// Met à jour l'ancre de l'URL sans déclencher le défilement natif du navigateur
// vers un élément du même id (les compteurs ont des éléments portant leur
// identifiant comme id) — contrairement à une affectation directe à location.hash.
function syncUrlHash(hash) {
    const current = (location.hash || '').replace(/^#/, '');
    if (current === hash) return;
    const url = hash ? location.pathname + location.search + '#' + hash : location.pathname + location.search;
    history.pushState(null, '', url);
}

function showView(view) {
    currentView = view;
    const isMain   = view === 'main';
    const isGlobal = view === 'global';
    const isHiver  = view === 'hiver';
    const isMeth   = view === 'methodo';
    const isBixi   = view === 'bixi';
    const isArr    = view === 'arr';
    document.getElementById('main-header').style.display    = isMain   ? '' : 'none';
    document.getElementById('main-container').style.display = isMain   ? '' : 'none';
    document.getElementById('global-page').style.display    = isGlobal ? '' : 'none';
    document.getElementById('hiver-page').style.display     = isHiver  ? '' : 'none';
    document.getElementById('methodo-page').style.display   = isMeth   ? '' : 'none';
    document.getElementById('bixi-page').style.display      = isBixi   ? '' : 'none';
    document.getElementById('arr-page').style.display       = isArr    ? '' : 'none';
    document.getElementById('nav-compteurs').classList.toggle('active', isMain);
    document.getElementById('nav-global').classList.toggle('active', isGlobal);
    document.getElementById('nav-hiver').classList.toggle('active', isHiver);
    document.getElementById('nav-bixi').classList.toggle('active', isBixi);
    document.getElementById('nav-arr').classList.toggle('active', isArr);
    document.getElementById('nav-arr-menu').classList.toggle('active', isArr);
    document.getElementById('nav-bixi-menu').classList.toggle('active', isBixi);
    document.getElementById('nav-global-menu').classList.toggle('active', isGlobal);
    document.getElementById('nav-more').classList.toggle('active', isGlobal || isArr || isBixi);
    document.getElementById('nav-methodo').classList.toggle('active', isMeth);
    document.getElementById('nav-methodo-menu').classList.toggle('active', isMeth);
    if (isGlobal) renderGlobalPage();
    if (isHiver) renderHiverPage();
    if (isBixi) renderBixiPage();
    if (isArr) renderArrPage();
    // La carte Leaflet peut avoir été (re)dimensionnée pendant qu'elle était masquée
    // (ex. arrivée directe sur un autre onglet via l'URL) — recalculer sa taille.
    if (isMain && map) setTimeout(() => map.invalidateSize(), 0);
    window.scrollTo({ top: 0, behavior: 'smooth' });

    // ── URL par onglet ── chaque vue a sa propre ancre (#global, #hiver, …),
    // ce qui permet de la partager ou de revenir en arrière avec le bouton précédent.
    // Sur l'onglet Compteurs, on conserve l'ancre si c'est déjà un identifiant de
    // compteur valide (lien partagé en cours de restauration, ou déjà sélectionné) ;
    // sinon (arrivée depuis un autre onglet) on repart d'une URL propre.
    const curHash = (location.hash || '').replace(/^#/, '');
    const hash = isMain ? (counterLocations[curHash] ? curHash : '') : view;
    syncUrlHash(hash);
}

function showViewFromHash() {
    const raw = (location.hash || '').replace(/^#/, '');
    if (raw && !VIEW_NAMES.has(raw) && counterLocations[raw]) {
        showView('main');
        setTimeout(() => setCounterFromMap(raw), 0);
        return;
    }
    showView(VIEW_NAMES.has(raw) ? raw : 'main');
}
window.addEventListener('popstate', showViewFromHash);
hashSyncEnabled = true;
if ((location.hash || '').replace(/^#/, '')) showViewFromHash();

// Masquer l'écran de chargement après l'initialisation (carte incluse, setTimeout ci-dessus)
setTimeout(() => {
    const ld = document.getElementById("loading-screen");
    if (!ld) return;
    ld.classList.add("done");
    setTimeout(() => ld.remove(), 400);
}, 0);
