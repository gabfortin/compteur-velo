"""
Tests unitaires du format de data/site.json (site_format.py).

    python3 -m unittest test_site_format
"""
import unittest

from site_format import decode_hour_runs, encode_hour_runs


class HourRunsTest(unittest.TestCase):
    def roundtrip(self, labels):
        runs = encode_hour_runs(labels)
        self.assertEqual(decode_hour_runs(runs), labels)
        return runs

    def test_vide(self):
        self.assertEqual(self.roundtrip([]), [])

    def test_heures_consecutives_une_seule_plage(self):
        labels = [f'2026-10-20 {h:02d}:00' for h in range(24)] + ['2026-10-21 00:00', '2026-10-21 01:00']
        self.assertEqual(self.roundtrip(labels), [['2026-10-20 00:00', 26]])

    def test_trou_nouvelle_plage(self):
        labels = ['2026-10-20 08:00', '2026-10-20 09:00', '2026-10-20 12:00']
        self.assertEqual(self.roundtrip(labels), [['2026-10-20 08:00', 2], ['2026-10-20 12:00', 1]])

    def test_fin_de_mois_et_annee(self):
        labels = ['2025-12-31 22:00', '2025-12-31 23:00', '2026-01-01 00:00']
        self.assertEqual(self.roundtrip(labels), [['2025-12-31 22:00', 3]])

    def test_annee_bissextile(self):
        labels = ['2028-02-28 23:00', '2028-02-29 00:00', '2028-02-29 23:00', '2028-03-01 00:00']
        self.assertEqual(self.roundtrip(labels), [['2028-02-28 23:00', 2], ['2028-02-29 23:00', 2]])

    def test_passage_heure_ete(self):
        # 8 mars 2026 : 02:00 n'existe pas à Montréal (absent des données)
        labels = ['2026-03-08 00:00', '2026-03-08 01:00', '2026-03-08 03:00', '2026-03-08 04:00']
        self.assertEqual(self.roundtrip(labels), [['2026-03-08 00:00', 2], ['2026-03-08 03:00', 2]])

    def test_retour_heure_normale(self):
        # 1er nov. 2026 : 01:00 se répète, mais les étiquettes sont dédoublonnées
        labels = ['2026-11-01 00:00', '2026-11-01 01:00', '2026-11-01 02:00']
        self.assertEqual(self.roundtrip(labels), [['2026-11-01 00:00', 3]])

    def test_minutes_non_nulles(self):
        labels = ['2026-10-20 08:15', '2026-10-20 09:15', '2026-10-20 09:30']
        self.assertEqual(self.roundtrip(labels), [['2026-10-20 08:15', 2], ['2026-10-20 09:30', 1]])

    def test_etiquette_mal_formee(self):
        with self.assertRaises(ValueError):
            encode_hour_runs(['2026-10-20T08:00'])


if __name__ == '__main__':
    unittest.main()
