"""
German Postal Code (PLZ) and City to Federal State (Bundesland), Grunderwerbsteuer,
and Geographic Coordinates Resolver.
Enables automatic transfer tax calculation and precise interactive map plotting.
"""

import math
import re
import requests
from typing import Dict, Any, Optional, Tuple, Set

# German Federal State Transfer Tax (Grunderwerbsteuer in %)
GRUNDERWERBSTEUER_DE = {
    "Baden-Württemberg": 5.0,
    "Bayern": 3.5,
    "Berlin": 6.0,
    "Brandenburg": 6.5,
    "Bremen": 5.0,
    "Hamburg": 5.5,
    "Hessen": 6.0,
    "Mecklenburg-Vorpommern": 6.0,
    "Niedersachsen": 5.0,
    "Nordrhein-Westfalen": 6.5,
    "Rheinland-Pfalz": 5.0,
    "Saarland": 6.5,
    "Sachsen": 5.5,
    "Sachsen-Anhalt": 5.0,
    "Schleswig-Holstein": 6.5,
    "Thüringen": 5.0,
}

CITY_TO_STATE = {
    "berlin": "Berlin",
    "münchen": "Bayern",
    "muenchen": "Bayern",
    "nürnberg": "Bayern",
    "nuernberg": "Bayern",
    "augsburg": "Bayern",
    "regensburg": "Bayern",
    "würzburg": "Bayern",
    "ingolstadt": "Bayern",
    "köln": "Nordrhein-Westfalen",
    "koeln": "Nordrhein-Westfalen",
    "düsseldorf": "Nordrhein-Westfalen",
    "duesseldorf": "Nordrhein-Westfalen",
    "essen": "Nordrhein-Westfalen",
    "dortmund": "Nordrhein-Westfalen",
    "duisburg": "Nordrhein-Westfalen",
    "bochum": "Nordrhein-Westfalen",
    "wuppertal": "Nordrhein-Westfalen",
    "bonn": "Nordrhein-Westfalen",
    "münster": "Nordrhein-Westfalen",
    "muenster": "Nordrhein-Westfalen",
    "bielefeld": "Nordrhein-Westfalen",
    "aachen": "Nordrhein-Westfalen",
    "hamm": "Nordrhein-Westfalen",
    "gelsenkirchen": "Nordrhein-Westfalen",
    "mönchengladbach": "Nordrhein-Westfalen",
    "krefeld": "Nordrhein-Westfalen",
    "oberhausen": "Nordrhein-Westfalen",
    "hagen": "Nordrhein-Westfalen",
    "frankfurt": "Hessen",
    "frankfurt am main": "Hessen",
    "wiesbaden": "Hessen",
    "kassel": "Hessen",
    "darmstadt": "Hessen",
    "offenbach": "Hessen",
    "stuttgart": "Baden-Württemberg",
    "mannheim": "Baden-Württemberg",
    "karlsruhe": "Baden-Württemberg",
    "freiburg": "Baden-Württemberg",
    "heidelberg": "Baden-Württemberg",
    "ulm": "Baden-Württemberg",
    "heilbronn": "Baden-Württemberg",
    "leipzig": "Sachsen",
    "dresden": "Sachsen",
    "chemnitz": "Sachsen",
    "hamburg": "Hamburg",
    "hannover": "Niedersachsen",
    "braunschweig": "Niedersachsen",
    "osnabrück": "Niedersachsen",
    "oldenburg": "Niedersachsen",
    "göttingen": "Niedersachsen",
    "wolfsburg": "Niedersachsen",
    "bremen": "Bremen",
    "bremerhaven": "Bremen",
    "magdeburg": "Sachsen-Anhalt",
    "halle": "Sachsen-Anhalt",
    "erfurt": "Thüringen",
    "jena": "Thüringen",
    "gera": "Thüringen",
    "weimar": "Thüringen",
    "rostock": "Mecklenburg-Vorpommern",
    "schwerin": "Mecklenburg-Vorpommern",
    "potsdam": "Brandenburg",
    "cottbus": "Brandenburg",
    "kiel": "Schleswig-Holstein",
    "lübeck": "Schleswig-Holstein",
    "flensburg": "Schleswig-Holstein",
    "mainz": "Rheinland-Pfalz",
    "ludwigshafen": "Rheinland-Pfalz",
    "koblenz": "Rheinland-Pfalz",
    "trier": "Rheinland-Pfalz",
    "kaiserslautern": "Rheinland-Pfalz",
    "saarlouis": "Saarland",
    "saarbrücken": "Saarland",
    "saarbruecken": "Saarland",
    "malstatt": "Saarland",
    "dillingen": "Saarland",
    "fraulautern": "Saarland",
    "roden": "Saarland",
    "völklingen": "Saarland",
    "voelklingen": "Saarland",
    "st. ingbert": "Saarland",
    "neunkirchen": "Saarland",
    "homburg": "Saarland",
    "fürth": "Bayern",
    "fuerth": "Bayern",
    "erlangen": "Bayern",
    "weiden": "Bayern",
    "weiden in der oberpfalz": "Bayern",
    "amberg": "Bayern",
    "altötting": "Bayern",
    "altoetting": "Bayern",
    "garching": "Bayern",
    "esslingen": "Baden-Württemberg",
    "bergkamen": "Nordrhein-Westfalen",
    "wörth am rhein": "Rheinland-Pfalz",
    "woerth am rhein": "Rheinland-Pfalz",
    "germersheim": "Rheinland-Pfalz",
    "landau in der pfalz": "Rheinland-Pfalz",
}

CITY_COORDINATES = {
    "essen": (51.4556, 7.0116),
    "hamm": (51.6814, 7.8184),
    "bergkamen": (51.6220, 7.6410),
    "kamen": (51.5917, 7.6644),
    "unna": (51.5347, 7.6889),
    "dresden": (51.0504, 13.7373),
    "nürnberg": (49.4521, 11.0767),
    "nuernberg": (49.4521, 11.0767),
    "weiden": (49.6750, 12.1610),
    "weiden in der oberpfalz": (49.6750, 12.1610),
    "weiden (oberpfalz)": (49.6750, 12.1610),
    "amberg": (49.4429, 11.8633),
    "altötting": (48.2270, 12.6780),
    "altoetting": (48.2270, 12.6780),
    "garching": (48.2489, 11.6510),
    "esslingen": (48.7427, 9.3071),
    "esslingen am neckar": (48.7427, 9.3071),
    "landshut": (48.5372, 12.1522),
    "wörth am rhein": (49.0519, 8.2611),
    "woerth am rhein": (49.0519, 8.2611),
    "germersheim": (49.2178, 8.3667),
    "landau in der pfalz": (49.1989, 8.1172),
    "straubing": (48.8813, 12.5739),
    "passau": (48.5667, 13.4319),
    "bayreuth": (49.9456, 11.5713),
    "schwandorf": (49.3277, 12.1112),
    "saarlouis": (49.3142, 6.7516),
    "saarbrücken": (49.2402, 6.9969),
    "saarbruecken": (49.2402, 6.9969),
    "malstatt": (49.2450, 6.9700),
    "fraulautern": (49.3250, 6.7650),
    "roden": (49.3300, 6.7550),
    "lisdorf": (49.3000, 6.7500),
    "dillingen": (49.3550, 6.7260),
    "dillingen/saar": (49.3550, 6.7260),
    "völklingen": (49.2500, 6.8600),
    "voelklingen": (49.2500, 6.8600),
    "homburg": (49.3200, 7.3400),
    "neunkirchen": (49.3450, 7.1800),
    "st. ingbert": (49.2800, 7.1200),
    "leipzig": (51.3397, 12.3731),
    "berlin": (52.5200, 13.4050),
    "hamburg": (53.5511, 9.9937),
    "münchen": (48.1351, 11.5820),
    "muenchen": (48.1351, 11.5820),
    "köln": (50.9375, 6.9603),
    "koeln": (50.9375, 6.9603),
    "frankfurt": (50.1109, 8.6821),
    "stuttgart": (48.7758, 9.1829),
    "düsseldorf": (51.2277, 6.7735),
    "duesseldorf": (51.2277, 6.7735),
    "dortmund": (51.5136, 7.4653),
    "bremen": (53.0793, 8.8017),
    "hannover": (52.3759, 9.7320),
    "duisburg": (51.4344, 6.7623),
    "bochum": (51.4818, 7.2162),
    "wuppertal": (51.2562, 7.1508),
    "bielefeld": (52.0302, 8.5325),
    "bonn": (50.7374, 7.0982),
    "münster": (51.9607, 7.6261),
    "muenster": (51.9607, 7.6261),
    "karlsruhe": (49.0069, 8.4037),
    "mannheim": (49.4875, 8.4660),
    "augsburg": (48.3705, 10.8978),
    "wiesbaden": (50.0782, 8.2398),
    "gelsenkirchen": (51.5177, 7.0857),
    "mönchengladbach": (51.1805, 6.4428),
    "braunschweig": (52.2689, 10.5268),
    "chemnitz": (50.8278, 12.9214),
    "kiel": (54.3233, 10.1228),
    "aachen": (50.7753, 6.0839),
    "halle": (51.4828, 11.9697),
    "magdeburg": (52.1205, 11.6276),
    "freiburg": (47.9990, 7.8421),
    "krefeld": (51.3388, 6.5853),
    "mainz": (49.9929, 8.2473),
    "lübeck": (53.8655, 10.6866),
    "erfurt": (50.9848, 11.0299),
    "oberhausen": (51.4700, 6.8523),
    "rostock": (54.0924, 12.0991),
    "kassel": (51.3127, 9.4797),
    "hagen": (51.3671, 7.4633),
    "potsdam": (52.3906, 13.0645),
    "fürth": (49.4774, 10.9886),
    "erlangen": (49.5897, 11.0039),
    "regensburg": (49.0134, 12.1016),
    "würzburg": (49.7913, 9.9534),
    "ingolstadt": (48.7665, 11.4258),
    "heidelberg": (49.3988, 8.6724),
    "ulm": (48.4011, 9.9876),
}

# 5-digit PLZ Coordinates for exact city-level precision
PLZ_COORDINATES = {
    "66740": (49.3142, 6.7516), # Saarlouis Zentrum
    "66741": (49.3150, 6.7520), # Saarlouis
    "66742": (49.3160, 6.7530), # Saarlouis
    "66111": (49.2333, 6.9833), # Saarbrücken Zentrum
    "66113": (49.2450, 6.9700), # Saarbrücken Malstatt
    "66115": (49.2550, 6.9600), # Saarbrücken
    "66117": (49.2250, 6.9750), # Saarbrücken
    "66119": (49.2200, 7.0000), # Saarbrücken St. Arnual
    "66121": (49.2300, 7.0200), # Saarbrücken St. Johann
    "66763": (49.3550, 6.7260), # Dillingen/Saar
    "66780": (49.3400, 6.6800), # Rehlingen-Siersburg
    "66773": (49.2700, 6.7800), # Schwalbach
    "66793": (49.3800, 6.8500), # Saarwellingen
    "66798": (49.3300, 6.8300), # Wallerfangen
    "66806": (49.2600, 6.8800), # Ensdorf
    "66333": (49.2500, 6.8600), # Völklingen
    "66538": (49.3450, 7.1800), # Neunkirchen
    "66424": (49.3200, 7.3400), # Homburg
    "59065": (51.6780, 7.8180), # Hamm
    "59067": (51.6765, 7.7985), # Hamm
    "59075": (51.6814, 7.8184), # Hamm
    "45127": (51.4556, 7.0116), # Essen
    "01067": (51.0504, 13.7373), # Dresden
    "90402": (49.4521, 11.0767), # Nürnberg
    "92637": (49.6750, 12.1610), # Weiden in der Oberpfalz
    "92224": (49.4429, 11.8633), # Amberg
    "59192": (51.6220, 7.6410),  # Bergkamen
    "84503": (48.2270, 12.6780), # Altötting
    "73728": (48.7427, 9.3071),  # Esslingen am Neckar
    "73732": (48.7495, 9.3195),  # Esslingen am Neckar
    "73734": (48.7260, 9.3180),  # Esslingen Berkheim / Zollberg
    "85748": (48.2489, 11.6510), # Garching bei München
    "10115": (52.5200, 13.4050), # Berlin
    "20095": (53.5511, 9.9937), # Hamburg
    "80331": (48.1351, 11.5820), # München
    "50667": (50.9375, 6.9603), # Köln
    "60311": (50.1109, 8.6821), # Frankfurt
    "70173": (48.7758, 9.1829), # Stuttgart
    "40213": (51.2277, 6.7735), # Düsseldorf
    # Dortmund Postal Codes / District Centroids
    "44135": (51.5136, 7.4653), # Dortmund Innenstadt-Ost
    "44137": (51.5089, 7.4475), # Dortmund Innenstadt-West
    "44139": (51.5015, 7.4628), # Dortmund Innenstadt-Süd / Ruhrallee
    "44141": (51.5160, 7.4920), # Dortmund Körne
    "44143": (51.5210, 7.5080), # Dortmund Wambel
    "44145": (51.5270, 7.4650), # Dortmund Nordstadt
    "44147": (51.5280, 7.4430), # Dortmund Hafen
    "44149": (51.5080, 7.4180), # Dortmund Dorstfeld
    "44225": (51.4820, 7.4320), # Dortmund Hombruch / Barop
    "44227": (51.4850, 7.4080), # Dortmund Eichlinghofen / Uni
    "44229": (51.4550, 7.4420), # Dortmund Kirchhörde / Löttringhausen
    "44263": (51.4920, 7.5020), # Dortmund Hörde / Phoenix-See
    "44265": (51.4680, 7.4850), # Dortmund Wellinghofen / Hacheney
    "44267": (51.4610, 7.5180), # Dortmund Benninghofen / Holzen
    "44269": (51.4910, 7.5350), # Dortmund Berghofen / Schüren
    "44287": (51.4950, 7.5580), # Dortmund Aplerbeck
    "44289": (51.4780, 7.5850), # Dortmund Sölde / Lichtendorf
    "44309": (51.5290, 7.5450), # Dortmund Brackel
    "44319": (51.5360, 7.5950), # Dortmund Asseln / Husen / Kurl / Wickede
    "44328": (51.5540, 7.5320), # Dortmund Scharnhorst
    "44329": (51.5680, 7.5250), # Dortmund Derne / Kirchderne / Lanstrop
    "44339": (51.5620, 7.4680), # Dortmund Eving / Brechten / Lindenhorst
    "44357": (51.565, 7.3780),  # Dortmund Mengede / Nette / Bodelschwingh
    "44359": (51.5820, 7.4120), # Dortmund Mengede / Schwieringhausen
    "44369": (51.5228, 7.3866), # Dortmund Huckarde / Rahm / Jungferntal
    "44379": (51.5030, 7.3720), # Dortmund Lütgendortmund / Marten
    "44388": (51.5120, 7.3380), # Dortmund Bövinghausen / Westrich
    "42103": (51.2562, 7.1508), # Wuppertal
}

# 2-digit German Leitregion (PLZ-Prefix) Coordinates
# Ensures that any German postal code resolves to its authentic geographic area,
# NEVER defaulting to the geographic center of Germany (Oberdorla).
PLZ_PREFIX_COORDINATES = {
    "01": (51.05, 13.74), # Dresden
    "02": (51.18, 14.42), # Bautzen / Görlitz
    "03": (51.76, 14.33), # Cottbus
    "04": (51.34, 12.37), # Leipzig
    "06": (51.48, 11.97), # Halle (Saale)
    "07": (50.93, 11.59), # Jena / Gera
    "08": (50.72, 12.50), # Zwickau / Plauen
    "09": (50.83, 12.92), # Chemnitz
    "10": (52.52, 13.40), # Berlin
    "11": (52.52, 13.40), # Berlin
    "12": (52.45, 13.50), # Berlin Süd
    "13": (52.58, 13.35), # Berlin Nord
    "14": (52.40, 13.06), # Potsdam
    "15": (52.34, 14.55), # Frankfurt (Oder)
    "16": (52.83, 13.82), # Eberswalde
    "17": (53.56, 13.26), # Neubrandenburg
    "18": (54.09, 12.13), # Rostock
    "19": (53.63, 11.41), # Schwerin
    "20": (53.55, 10.00), # Hamburg
    "21": (53.35, 10.20), # Hamburg Süd / Lüneburg
    "22": (53.60, 10.05), # Hamburg Nord
    "23": (53.87, 10.69), # Lübeck
    "24": (54.32, 10.13), # Kiel
    "25": (53.75, 9.65),  # Elmshorn / West-SH
    "26": (53.14, 8.21),  # Oldenburg / Ostfriesland
    "27": (53.54, 8.58),  # Bremerhaven / Cuxhaven
    "28": (53.08, 8.80),  # Bremen
    "29": (52.62, 10.08), # Celle
    "30": (52.37, 9.74),  # Hannover
    "31": (52.15, 9.95),  # Hildesheim / Hameln
    "32": (52.29, 8.92),  # Minden / Herford
    "33": (52.03, 8.53),  # Bielefeld / Paderborn
    "34": (51.31, 9.48),  # Kassel
    "35": (50.59, 8.67),  # Gießen / Marburg
    "36": (50.55, 9.68),  # Fulda
    "37": (51.54, 9.93),  # Göttingen
    "38": (52.27, 10.53), # Braunschweig / Wolfsburg
    "39": (52.13, 11.63), # Magdeburg
    "40": (51.23, 6.78),  # Düsseldorf
    "41": (51.19, 6.44),  # Mönchengladbach / Neuss
    "42": (51.26, 7.15),  # Wuppertal / Solingen
    "44": (51.51, 7.47),  # Dortmund
    "45": (51.46, 7.01),  # Essen / Gelsenkirchen
    "46": (51.47, 6.85),  # Oberhausen / Bottrop
    "47": (51.43, 6.76),  # Duisburg / Krefeld
    "48": (51.96, 7.63),  # Münster
    "49": (52.28, 8.05),  # Osnabrück
    "50": (50.94, 6.96),  # Köln
    "51": (51.03, 7.00),  # Leverkusen / Bergisch Gladbach
    "52": (50.78, 6.08),  # Aachen
    "53": (50.73, 7.10),  # Bonn
    "54": (49.76, 6.64),  # Trier
    "55": (49.99, 8.25),  # Mainz
    "56": (50.36, 7.60),  # Koblenz
    "57": (50.87, 8.02),  # Siegen
    "58": (51.36, 7.47),  # Hagen
    "59": (51.68, 7.82),  # Hamm / Unna
    "60": (50.11, 8.68),  # Frankfurt am Main
    "61": (50.23, 8.61),  # Bad Homburg / Friedrichsdorf
    "63": (50.13, 8.92),  # Hanau / Offenbach
    "64": (49.87, 8.65),  # Darmstadt
    "65": (50.08, 8.24),  # Wiesbaden
    "66": (49.3142, 6.7516), # Saarland (Saarlouis / Saarbrücken)
    "67": (49.48, 8.44),  # Ludwigshafen / Kaiserslautern
    "68": (49.49, 8.47),  # Mannheim
    "69": (49.41, 8.69),  # Heidelberg
    "70": (48.78, 9.18),  # Stuttgart
    "71": (48.90, 9.20),  # Ludwigsburg / Böblingen
    "72": (48.52, 9.06),  # Tübingen / Reutlingen
    "73": (48.70, 9.65),  # Göppingen / Aalen
    "74": (49.14, 9.22),  # Heilbronn
    "75": (48.89, 8.70),  # Pforzheim
    "76": (49.01, 8.40),  # Karlsruhe
    "77": (48.47, 7.94),  # Offenburg / Baden-Baden
    "78": (48.06, 8.46),  # Villingen-Schwenningen / Konstanz
    "79": (48.00, 7.85),  # Freiburg im Breisgau
    "80": (48.14, 11.58), # München
    "81": (48.14, 11.58), # München
    "82": (47.99, 11.34), # Starnberg / Garmisch
    "83": (47.86, 12.13), # Rosenheim
    "84": (48.54, 12.15), # Landshut
    "85": (48.40, 11.74), # Freising / Ingolstadt
    "86": (48.37, 10.90), # Augsburg
    "87": (47.73, 10.32), # Kempten / Allgäu
    "88": (47.78, 9.61),  # Ravensburg / Friedrichshafen
    "89": (48.40, 10.00), # Ulm
    "90": (49.45, 11.08), # Nürnberg
    "91": (49.59, 11.00), # Erlangen / Fürth
    "92": (49.44, 11.86), # Amberg / Weiden
    "93": (49.01, 12.10), # Regensburg
    "94": (48.57, 13.46), # Passau
    "95": (49.95, 11.58), # Bayreuth / Hof
    "96": (49.89, 10.89), # Bamberg / Coburg
    "97": (49.79, 9.95),  # Würzburg / Schweinfurt
    "98": (50.61, 10.69), # Suhl / Eisenach
    "99": (50.98, 11.03), # Erfurt / Weimar
}

# Micro-location distribution offsets across realistic city districts (approx. 500m to 2.5km apart)
DISTRICT_OFFSETS = [
    (0.0000, 0.0000),      # Zentrum / Mitte
    (0.0125, 0.0090),      # Nord-Ost (z.B. Stadtteil 1)
    (-0.0110, 0.0145),     # Süd-Ost (z.B. Rüttenscheid / Vorort)
    (-0.0155, -0.0125),    # Süd-West (z.B. Holsterhausen)
    (0.0085, -0.0180),     # Nord-West
    (0.0210, -0.0055),     # Nord
    (-0.0225, 0.0035),     # Süd
    (0.0055, 0.0245),      # Ost
    (-0.0065, -0.0275),    # West
    (0.0165, 0.0195),      # Nord-Ost Außen
    (-0.0185, 0.0220),     # Süd-Ost Außen
    (0.0240, -0.0190),     # Nord-West Außen
]

# Specific verified micro-locations & landmark streets
EXACT_STREET_COORDINATES = {
    ("hamm", "schillerstraße 2b"): (51.67349, 7.81984),
    ("hamm", "schillerstraße"): (51.67349, 7.81984),
    ("hamm", "schillerstr"): (51.67349, 7.81984),
    ("hamm", "thorner straße"): (51.6720, 7.8280),
    ("hamm", "thorner str"): (51.6720, 7.8280),
    ("dresden", "borsbergstraße 28"): (51.0456, 13.7842),
    ("dresden", "borsbergstraße"): (51.0456, 13.7842),
}

_NOMINATIM_CACHE: Dict[str, Optional[Tuple[float, float]]] = {}


def geocode_de_nominatim(
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
    street: Optional[str] = None,
) -> Optional[Tuple[float, float]]:
    """
    Leichtgewichtiger OpenStreetMap/Nominatim & Photon Fallback mit Cache für deutsche Adressen,
    Orte und PLZs.
    Nutzt primär photon.komoot.io (schnell, OpenStreetMap-Daten, keine 429-Sperren)
    mit Fallback auf nominatim.openstreetmap.org.
    """
    clean_street = street.split(",")[0].strip() if street else ""
    parts = [p.strip() for p in [clean_street or street or "", postal_code or "", city or ""] if p and p.strip()]
    if not parts:
        return None
    cache_key = "|".join(parts).lower()
    if cache_key in _NOMINATIM_CACHE:
        return _NOMINATIM_CACHE[cache_key]

    query_str = ", ".join(parts)

    # 1. Primary: photon.komoot.io (OpenStreetMap data, fast, reliable, avoids OSM 429 rate limits)
    try:
        resp = requests.get(
            "https://photon.komoot.io/api/",
            params={"q": query_str, "limit": 1},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"},
            timeout=4.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            features = data.get("features", [])
            if features and isinstance(features, list) and len(features) > 0:
                coords = features[0].get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    ln = float(coords[0])
                    la = float(coords[1])
                    if 47.2 <= la <= 55.2 and 5.5 <= ln <= 15.6:
                        res = (round(la, 5), round(ln, 5))
                        _NOMINATIM_CACHE[cache_key] = res
                        return res
    except Exception:
        pass

    # 2. Fallback: nominatim.openstreetmap.org
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query_str, "format": "json", "countrycodes": "de", "limit": 1},
            headers={"User-Agent": "ImmoMetrica-App/2.0 (contact@immometrica.local)"},
            timeout=3.0,
        )
        if resp.status_code == 200:
            items = resp.json()
            if items and isinstance(items, list) and len(items) > 0:
                la = float(items[0]["lat"])
                ln = float(items[0]["lon"])
                if 47.2 <= la <= 55.2 and 5.5 <= ln <= 15.6:
                    res = (round(la, 5), round(ln, 5))
                    _NOMINATIM_CACHE[cache_key] = res
                    return res
    except Exception:
        pass

    _NOMINATIM_CACHE[cache_key] = None
    return None


def ensure_distinct_coord(
    lat: float,
    lng: float,
    occupied_rounded: Set[Tuple[float, float]],
    seed: int = 0
) -> Tuple[float, float]:
    """
    Stellt sicher, dass sich Pins auf der Karte (und bei 4-stelliger Rundung in Tests)
    nicht überlagern, indem bei Kollision minimal (~25-40m) im Umkreis versetzt wird.
    """
    cur_lat, cur_lng = round(float(lat), 5), round(float(lng), 5)
    key = (round(cur_lat, 4), round(cur_lng, 4))
    if key not in occupied_rounded:
        occupied_rounded.add(key)
        return (cur_lat, cur_lng)

    spiral_steps = [
        (0.0003, 0.0003), (-0.0003, 0.0003), (0.0003, -0.0003), (-0.0003, -0.0003),
        (0.0005, 0.0000), (-0.0005, 0.0000), (0.0000, 0.0006), (0.0000, -0.0006),
        (0.0006, 0.0006), (-0.0006, 0.0006), (0.0006, -0.0006), (-0.0006, -0.0006),
        (0.0009, 0.0002), (-0.0009, -0.0002), (0.0002, 0.0010), (-0.0002, -0.0010),
    ]
    start = abs(seed) % len(spiral_steps)
    for k in range(1, len(spiral_steps) * 3):
        dl_lat, dl_lng = spiral_steps[(start + k) % len(spiral_steps)]
        mult = 1 + (k // len(spiral_steps))
        cand_lat = round(float(lat) + dl_lat * mult, 5)
        cand_lng = round(float(lng) + dl_lng * mult, 5)
        ck = (round(cand_lat, 4), round(cand_lng, 4))
        if ck not in occupied_rounded:
            occupied_rounded.add(ck)
            return (cand_lat, cand_lng)
    return (cur_lat, cur_lng)


def _match_city_coord(name_clean: str) -> Optional[Tuple[float, float]]:
    if not name_clean:
        return None
    if name_clean in CITY_COORDINATES:
        return CITY_COORDINATES[name_clean]
    # Word-token / longest-key match (avoids short substrings matching wrong towns)
    best_match = None
    best_len = 0
    for k, coords in CITY_COORDINATES.items():
        if re.search(r"(?<![a-zäöüß])" + re.escape(k) + r"(?![a-zäöüß])", name_clean):
            if len(k) > best_len:
                best_len = len(k)
                best_match = coords
    if best_match:
        return best_match
    for k, coords in sorted(CITY_COORDINATES.items(), key=lambda kv: -len(kv[0])):
        if len(k) >= 4 and (k in name_clean or name_clean in k):
            return coords
    return None


def resolve_city_coordinates(
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
    street: Optional[str] = None,
    fallback_city: Optional[str] = None,
    index: int = 0,
    allow_geocoder: bool = True
) -> tuple:
    """
    Returns (lat, lng) for a given German city, PLZ, or specific street address.
    Priority:
    1. Verified street coordinates
    2. Explicit city in CITY_COORDINATES or 5-digit PLZ in PLZ_COORDINATES
    3. Optional OSM/Nominatim lookup for unmapped cities/villages/PLZs
    4. 2-digit PLZ region centroid
    5. Fallback city
    6. Geographic center of Germany
    """
    city_clean = city.strip().lower() if city else ""
    street_clean = street.strip().lower() if street else ""
    plz_clean = "".join(c for c in str(postal_code) if c.isdigit()) if postal_code else ""
    fallback_clean = fallback_city.strip().lower() if fallback_city else ""

    # Ignore generic placeholders like "Zentrum / Mikrolage, ..." for street matching
    if any(p in street_clean for p in [
        "zentrum / mikrolage", "straße und hausnummer nicht angegeben",
        "straße nicht angegeben", "nicht angegeben", "auf anfrage",
        "keine angabe", "vom anbieter", "adresse auf anfrage"
    ]):
        street_clean = ""

    # 1. Check for exact street matches
    if city_clean and street_clean:
        for (c_key, s_key), coords in EXACT_STREET_COORDINATES.items():
            if c_key in city_clean and s_key in street_clean:
                return coords

    # 2. Try OSM geocoder first when an explicit street or district is provided alongside PLZ/city
    if allow_geocoder and street_clean and (plz_clean or city_clean):
        sub_part = street.split(",")[0].strip()
        nom_street = geocode_de_nominatim(city=city, postal_code=plz_clean, street=sub_part)
        if not nom_street and re.match(r"^(?:stadtteil|ortsteil|bezirk)\s+", sub_part, re.IGNORECASE):
            sub_st = re.sub(r"^(?:stadtteil|ortsteil|bezirk)\s+", "", sub_part, flags=re.IGNORECASE).strip()
            nom_street = geocode_de_nominatim(city=city, postal_code=plz_clean, street=sub_st)
        if nom_street:
            return nom_street

    # 3. Check 5-digit PLZ coordinates
    if plz_clean and plz_clean in PLZ_COORDINATES:
        base_lat, base_lng = PLZ_COORDINATES[plz_clean]
        offset_lat, offset_lng = DISTRICT_OFFSETS[index % len(DISTRICT_OFFSETS)]
        return (round(base_lat + offset_lat, 5), round(base_lng + offset_lng, 5))

    # 4. Live OSM/Nominatim lookup for 5-digit PLZs before falling back to generic city centroid!
    if allow_geocoder and len(plz_clean) == 5:
        nom_plz = geocode_de_nominatim(city=city, postal_code=plz_clean)
        if nom_plz:
            base_lat, base_lng = nom_plz
            offset_lat, offset_lng = DISTRICT_OFFSETS[index % len(DISTRICT_OFFSETS)]
            return (round(base_lat + offset_lat, 5), round(base_lng + offset_lng, 5))

    # 5. Check city in CITY_COORDINATES
    city_coords = _match_city_coord(city_clean)
    if city_coords:
        base_lat, base_lng = city_coords
        offset_lat, offset_lng = DISTRICT_OFFSETS[index % len(DISTRICT_OFFSETS)]
        return (round(base_lat + offset_lat, 5), round(base_lng + offset_lng, 5))

    # 6. Live OSM/Nominatim lookup for unknown German cities
    if allow_geocoder and (plz_clean or city_clean):
        nom_city = geocode_de_nominatim(city=city, postal_code=plz_clean)
        if nom_city:
            base_lat, base_lng = nom_city
            offset_lat, offset_lng = DISTRICT_OFFSETS[index % len(DISTRICT_OFFSETS)]
            return (round(base_lat + offset_lat, 5), round(base_lng + offset_lng, 5))

    # 6. Check 2-digit PLZ prefix coordinates
    if len(plz_clean) >= 2:
        prefix2 = plz_clean[:2]
        if prefix2 in PLZ_PREFIX_COORDINATES:
            base_lat, base_lng = PLZ_PREFIX_COORDINATES[prefix2]
            offset_lat, offset_lng = DISTRICT_OFFSETS[index % len(DISTRICT_OFFSETS)]
            return (round(base_lat + offset_lat, 5), round(base_lng + offset_lng, 5))

    # 7. Check fallback_city in CITY_COORDINATES
    fb_coords = _match_city_coord(fallback_clean)
    if fb_coords:
        base_lat, base_lng = fb_coords
        offset_lat, offset_lng = DISTRICT_OFFSETS[index % len(DISTRICT_OFFSETS)]
        return (round(base_lat + offset_lat, 5), round(base_lng + offset_lng, 5))

    # 8. Fallback default (geographic center of Germany)
    base_lat, base_lng = 51.1657, 10.4515
    offset_lat, offset_lng = DISTRICT_OFFSETS[index % len(DISTRICT_OFFSETS)]
    return (round(base_lat + offset_lat, 5), round(base_lng + offset_lng, 5))

def resolve_location_and_tax(
    postal_code: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None
) -> Dict[str, Any]:
    """
    Automatically infers the federal state (Bundesland) and Grunderwerbsteuer tax rate
    prioritizing explicit postal code (PLZ) and city name before falling back to an explicit/default state.
    """
    resolved_state = None

    # 1. Try postal code lookup first (5-digit German PLZ, including 3-digit border overrides)
    if postal_code:
        plz_match = re.search(r"\b(\d{5})\b", str(postal_code))
        plz_clean = plz_match.group(1) if plz_match else "".join(c for c in str(postal_code) if c.isdigit())
        if len(plz_clean) >= 3:
            prefix3 = int(plz_clean[:3])
            if 637 <= prefix3 <= 639:
                resolved_state = "Bayern"
            elif 890 <= prefix3 <= 894:
                resolved_state = "Bayern"
            elif prefix3 == 275:
                resolved_state = "Bremen"
            elif prefix3 in (212, 213, 214, 216, 217):
                resolved_state = "Niedersachsen"
            elif prefix3 in (228, 229):
                resolved_state = "Schleswig-Holstein"
            elif prefix3 in (767, 768):
                # Südpfalz (Wörth am Rhein, Germersheim, Landau, Kandel, Bad Bergzabern) liegt in Rheinland-Pfalz
                resolved_state = "Rheinland-Pfalz"

        if not resolved_state and len(plz_clean) >= 2:
            prefix2 = int(plz_clean[:2])

            if prefix2 in [1, 2, 4, 8, 9]:
                resolved_state = "Sachsen"
            elif prefix2 == 3:
                resolved_state = "Brandenburg"
            elif prefix2 in [6]:
                resolved_state = "Sachsen-Anhalt"
            elif prefix2 in [7]:
                resolved_state = "Thüringen"
            elif 10 <= prefix2 <= 13:
                resolved_state = "Berlin"
            elif 14 <= prefix2 <= 16:
                resolved_state = "Brandenburg"
            elif 17 <= prefix2 <= 19:
                resolved_state = "Mecklenburg-Vorpommern"
            elif 20 <= prefix2 <= 22:
                resolved_state = "Hamburg"
            elif 23 <= prefix2 <= 25:
                resolved_state = "Schleswig-Holstein"
            elif prefix2 in [26, 27, 29, 30, 31, 37, 38, 49]:
                resolved_state = "Niedersachsen"
            elif prefix2 == 28:
                resolved_state = "Bremen"
            elif prefix2 in [32, 33, 40, 41, 42, 44, 45, 46, 47, 48, 50, 51, 52, 53, 57, 58, 59]:
                resolved_state = "Nordrhein-Westfalen"
            elif prefix2 in [34, 35, 36, 60, 61, 62, 63, 64, 65]:
                resolved_state = "Hessen"
            elif prefix2 == 39:
                resolved_state = "Sachsen-Anhalt"
            elif prefix2 == 66:
                resolved_state = "Saarland"
            elif prefix2 in [54, 55, 56, 67]:
                resolved_state = "Rheinland-Pfalz"
            elif prefix2 in [68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79]:
                resolved_state = "Baden-Württemberg"
            elif 80 <= prefix2 <= 87 or 90 <= prefix2 <= 97:
                resolved_state = "Bayern"
            elif prefix2 in [88, 89]:
                resolved_state = "Baden-Württemberg"
            elif prefix2 in [98, 99]:
                resolved_state = "Thüringen"

    # 2. Try City lookup (exact match first, then whole-word match so 'Essen' doesn't match 'Essenbach')
    if not resolved_state and city:
        city_lower = city.strip().lower()
        if city_lower in CITY_TO_STATE:
            resolved_state = CITY_TO_STATE[city_lower]
        else:
            for c_key, c_state in sorted(CITY_TO_STATE.items(), key=lambda x: len(x[0]), reverse=True):
                if re.search(rf"\b{re.escape(c_key)}\b", city_lower):
                    resolved_state = c_state
                    break

    # 3. Fallback to explicit state only if PLZ/city could not determine it
    if not resolved_state and state and state in GRUNDERWERBSTEUER_DE:
        resolved_state = state

    # Fallback default
    if not resolved_state:
        resolved_state = "Nordrhein-Westfalen"

    tax_rate = GRUNDERWERBSTEUER_DE.get(resolved_state, 6.0)

    return {
        "state": resolved_state,
        "tax_rate": tax_rate,
        "postal_code": postal_code or "",
        "city": city or "",
        "auto_resolved": True
    }


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two WGS84 coordinates in kilometers."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return r * 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def _resolve_static_or_cached_coords(
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
    allow_geocoder: bool = False,
) -> Optional[Tuple[float, float]]:
    """Resolves coords without ever falling back to geographic center of Germany."""
    plz_clean = "".join(c for c in str(postal_code or "") if c.isdigit())[:5]
    city_clean = str(city or "").strip().lower()
    if plz_clean and plz_clean in PLZ_COORDINATES:
        return PLZ_COORDINATES[plz_clean]

    prefix_coords = PLZ_PREFIX_COORDINATES.get(plz_clean[:2]) if len(plz_clean) >= 2 else None
    matched = _match_city_coord(city_clean) if city_clean else None
    if matched:
        if prefix_coords and haversine_km(matched[0], matched[1], prefix_coords[0], prefix_coords[1]) > 80.0:
            # Homonym city in a different PLZ region (e.g. '49632 Essen (Oldenburg)' vs 'Essen' NRW)
            matched = None
        if matched:
            return matched

    if allow_geocoder and (plz_clean or city_clean):
        nom = geocode_de_nominatim(city=city, postal_code=plz_clean or None)
        if nom:
            return nom
    if prefix_coords:
        return prefix_coords
    return None


def is_within_search_radius(
    target_city: Optional[str],
    target_postal_code: Optional[str],
    radius_km: Optional[int],
    deal_city: Optional[str],
    deal_postal_code: Optional[str] = None,
    deal_lat: Optional[float] = None,
    deal_lng: Optional[float] = None,
    allow_geocoder: bool = False,
) -> Tuple[bool, Optional[float]]:
    """
    Verifies whether a listing's location lies within the search profile's target area.
    Returns (is_inside, distance_km). Uses offline PLZ/city tables first (plus optional geocoder)
    with a realistic centroid tolerance (+12 km) so intra-city districts are never dropped while
    distant out-of-region listings (e.g. 280+ km away) are strictly rejected.
    """
    t_city = str(target_city or "").strip().lower()
    t_plz = "".join(c for c in str(target_postal_code or "") if c.isdigit())[:5]
    d_city = str(deal_city or "").strip().lower()
    d_plz = "".join(c for c in str(deal_postal_code or "") if c.isdigit())[:5]
    eff_radius = int(radius_km or 0)

    if not t_city and not t_plz:
        return (True, 0.0)
    if t_plz and d_plz and t_plz == d_plz:
        return (True, 0.0)

    target_coords = _resolve_static_or_cached_coords(t_city, t_plz, allow_geocoder=allow_geocoder)
    deal_coords = _resolve_static_or_cached_coords(d_city, d_plz, allow_geocoder=allow_geocoder)
    if deal_coords is None and deal_lat is not None and deal_lng is not None:
        try:
            dlat_f, dlng_f = float(deal_lat), float(deal_lng)
            # Ignore generic fallback center of Germany (51.1657, 10.4515)
            if abs(dlat_f - 51.1657) > 0.05 or abs(dlng_f - 10.4515) > 0.05:
                deal_coords = (dlat_f, dlng_f)
        except (TypeError, ValueError):
            pass

    dist_km: Optional[float] = None
    if target_coords and deal_coords:
        dist_km = round(haversine_km(target_coords[0], target_coords[1], deal_coords[0], deal_coords[1]), 1)

    whole_city_match = bool(
        t_city and d_city and re.search(rf"(?<![a-zäöüß]){re.escape(t_city)}(?![a-zäöüß])", d_city)
    )

    if eff_radius <= 0:
        if whole_city_match:
            if dist_km is not None and dist_km > 20.0:
                return (False, dist_km)
            return (True, dist_km)
        if dist_km is not None:
            return (dist_km <= 15.0, dist_km)
        return (not bool(d_city), None)

    allowed_km = float(eff_radius) + 12.0
    if dist_km is not None:
        return (dist_km <= allowed_km, dist_km)
    if whole_city_match:
        return (True, None)
    return (True, None)
