"""Embed existing mobile UI, replacing just its HTTP transport for on-board control."""
from pathlib import Path
from json import dumps
base = Path(__file__).resolve().parents[1]
parts = []
for symbol, filename in [('page','index.html'),('style','style.css'),('script','app.js')]:
    content = (base/'web'/filename).read_text(encoding='utf-8-sig')
    if symbol == 'script':
        start = content.index('async function api(')
        end = content.index('function state(',start)
        content = content[:start] + (base/'web'/'board_transport.js').read_text(encoding='utf-8-sig') + '\n' + content[end:]
    raw = content.encode()
    parts.append('static const unsigned char '+symbol+'[] = {\n' +
                 '\n'.join(','.join(str(n) for n in raw[i:i+24])+',' for i in range(0,len(raw),24))+'\n};')
(base/'firmware'/'web_assets.h').write_text('\n'.join(parts)+'\n',encoding='ascii')
