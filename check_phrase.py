import requests
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
r = requests.get('https://www.kleinanzeigen.de/s-anzeige/frisch-renoviert-und-sofort-frei-hell-mit-erker-im-beliebten-st-johannis/3548241918-196-6825', headers=headers, timeout=6)
print('Status:', r.status_code)
print('Final URL:', r.url)
text = r.text.lower()
for p in ['nicht mehr verfügbar', 'nicht mehr online', 'nicht gefunden', 'gelöscht', 'deaktiviert']:
    if p in text:
        idx = text.find(p)
        print(f"Found '{p}' at: {text[max(0, idx-40):min(len(text), idx+60)]}")
