import requests, json, io, base64, PIL.Image
from app.database import get_system_setting, get_connection
from app.image_analyst import BROWSER_HEADERS

key = get_system_setting('gemini_api_key')
conn = get_connection()
c = conn.cursor()
c.execute('SELECT * FROM properties WHERE id = 457')
prop = dict(c.fetchone())
urls = json.loads(prop.get('gallery_images') or '[]')
conn.close()

b64_list = []
for u in urls[:8]:
    r = requests.get(u, headers=BROWSER_HEADERS, timeout=5.0)
    img = PIL.Image.open(io.BytesIO(r.content)).convert('RGB')
    img.thumbnail((384, 384), PIL.Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=65)
    b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    b64_list.append(('image/jpeg', b64))

# Let's import prompt from app.image_analyst
# By getting source or using inspect
import inspect, app.image_analyst
src = inspect.getsource(app.image_analyst.call_gemini_vision)
# Let's extract prompt lines
active_images = b64_list[:8]
full_description = prop.get('description', '')[:2000]

# Let's read the exact prompt from app.image_analyst
prompt = f"""Du bist ein DEKRA-zertifizierter Bausachverständiger und Immobilien-Due-Diligence-Auditor in Deutschland.
Analysiere die beigefügten Fotos der Immobilie '{prop.get('title')}' in {prop.get('city')} (Baujahr {prop.get('year_built', 'k.A.')}, {prop.get('living_space', 50)} m² Wohnfläche).

Objektbeschreibung des Verkäufers:
\"\"\"{full_description[:2000]}\"\"\"

WICHTIGE PRÜFKRITERIEN:
1. Prüfe zuerst, ob es sich um echte Innenraumfotos oder um Bauzeichnungen / Grundrisse / Pläne handelt.
2. Wenn nur Bauzeichnungen/Pläne vorliegen: Gib in 'notice' an, dass keine Innenraumfotos vorliegen, und leite den Zustand/CaPEx aus der Objektbeschreibung ab (z.B. wenn Verkäufer 'Renovieren!!!!' oder Sanierungsstau angibt).
3. Untersuche den visuellen IST-Zustand folgender 6 Bauteile auf den Fotos:
   - Badezimmer & Sanitär (Fliesenalter, Zustand Dusche/Wanne/WC, Fugen, Armaturen)
   - Küche & EBK (Vorhandensein EBK, Zustand Fronten/Arbeitsplatte, Gerätealter)
   - Böden (Material z.B. Parkett/Laminat/Fliesen, Trittspuren, Kratzer)
   - Fenster & Dämmung (Rahmen z.B. Kunststoff/Holz, Isolierverglasung, Rollläden)
   - Elektrik & Heizung (Heizkörpertyp z.B. modern Flachheizkörper vs. alt Gussrippen, Schalterprogramm)
   - Wände & Decken (Malerbedarf, Risse, Feuchtigkeits-/Schimmelhinweise, Tapeten)
4. Kalkuliere realistische deutsche Handwerkerkosten (CaPEx) nach BKI-Standard für notwendige Modernisierungen vor Neuvermietung.
5. QUELLENANGABE & BILDBELEGE FÜR JEDES CaPEx-GEWERK (SEHR WICHTIG!):
   - Prüfe kritisch: Welche Modernisierungskosten sind TATSÄCHLICH auf den Fotos sichtbar, und welche wären reine Baujahrs-/Sicherheitsmutmaßung?
   - Wenn Fenster auf den Fotos augenscheinlich intakte weiße/moderne Kunststoff-Isolierfenster sind -> setze `categories.windows.cost = 0.0` und füge KEINE Fenstermodernisierung zu `visual_capex_items` hinzu!
   - Gib bei JEDEM Eintrag in `visual_capex_items` zwingend an:
     * `source_type`: `"FOTO_BELEG"` oder `"BAUJAHR_MUTMASSUNG"`
     * `photo_indices`: Array der 1-basierten Fotonummern
     * `evidence_source`: Begründung

Klassifiziere und beschreibe JEDES der beigefügten Fotos 1 bis {len(active_images)} einzeln der Reihe nach.
Gib bei `description` pro Foto eine gut lesbare, informative 2-3-Sätze-Erklärung (sichtbare Ausstattung, Boden, Fenster, Heizkörper, Zustand und eventuelle Mängel).

Antworte AUSSCHLIESSLICH mit gültigem JSON nach diesem Schema:
{{
  "has_interior_photos": true,
  "notice": null,
  "overall_visual_score": 7.0,
  "condition_rating": "GEPFLEGT",
  "condition_label": "🟡 Zustand",
  "photo_classifications": [
    {{
      "index": 1,
      "tag": "🏢 Fassade / Außenansicht",
      "description": "...",
      "quality_score": 8.0,
      "is_blueprint": false,
      "findings": []
    }}
  ],
  "categories": {{
    "bathroom": {{"score": 7.5, "status": "Solide", "findings": "...", "cost": 0.0}},
    "kitchen": {{"score": 8.0, "status": "Gepflegt", "findings": "...", "cost": 0.0}},
    "flooring": {{"score": 8.5, "status": "Neuwertig", "findings": "...", "cost": 0.0}},
    "windows": {{"score": 9.0, "status": "Moderne Kunststofffenster", "findings": "...", "cost": 0.0}},
    "heating_electrics": {{"score": 8.0, "status": "Fernwärme", "findings": "...", "cost": 0.0}},
    "walls_substance": {{"score": 8.0, "status": "Guter Zustand", "findings": "...", "cost": 0.0}}
  }},
  "detected_findings": [],
  "visual_capex_items": [],
  "total_visual_capex": 0.0,
  "investor_summary": "..."
}}
"""

user_content = [{'type': 'text', 'text': prompt}]
for idx, (mime_type, b64_data) in enumerate(active_images, start=1):
    user_content.append({'type': 'text', 'text': f'--- Foto #{idx} ---'})
    user_content.append({'type': 'image_url', 'image_url': {'url': f'data:{mime_type};base64,{b64_data}'}})

payload = {
    'model': 'claude-haiku-5-5',
    'messages': [{'role': 'user', 'content': user_content}],
    'max_tokens': 4000
}

resp = requests.post(
    'https://api.oneprovider.dev/v1/chat/completions',
    json=payload,
    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
    timeout=90.0
)
res = resp.json()
print("Status:", resp.status_code)
print("Choices length:", len(res.get("choices", [])))
if res.get("choices"):
    c = res["choices"][0]
    print("finish_reason:", c.get("finish_reason"))
    msg = c.get("message", {}).get("content", "")
    print("msg length:", len(msg))
    print("Usage:", res.get("usage"))
