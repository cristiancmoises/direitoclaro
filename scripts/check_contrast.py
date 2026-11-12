"""Small deterministic color-token contrast smoke check. NOT a WCAG audit."""
import json
import re
from pathlib import Path

CSS=Path(__file__).resolve().parents[1]/'web'/'styles.css'


def rgb(h):
    h=h.lstrip('#')
    if len(h)==3: h=''.join(x*2 for x in h)
    return [int(h[i:i+2],16)/255 for i in (0,2,4)]


def luminance(h):
    linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb(h)]
    return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))


def contrast(a,b):
    high,low=sorted((luminance(a),luminance(b)),reverse=True)
    return (high+.05)/(low+.05)


def check():
    css=CSS.read_text(encoding='utf-8')
    # Do not silently check the obsolete v0.1 palette or @media print.
    current=css.split('/* v0.7 — Identidade editorial brasileira:',1)
    if len(current)!=2:
        raise RuntimeError('v0.7 theme palette not found')
    segment=current[1].split('@media(max-width:1100px)',1)[0]
    dark=re.search(r':root\s*\{([^}]*)\}',segment).group(1)
    light=re.search(r'html\[data-theme="light"\]\s*\{([^}]*)\}',segment).group(1)
    checks=[]
    for name,tokens in [('escuro',dark),('claro',light)]:
        values=dict(re.findall(r'--([\w-]+):\s*(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?)',tokens))
        for fg,bg in [('text','bg'),('muted','bg'),('soft','bg'),('green','surface'),('yellow','surface')]:
            ratio=round(contrast(values[fg],values[bg]),2)
            checks.append({'theme':name,'fg':fg,'bg':bg,'ratio':ratio,'pass':ratio>=4.5})
    return checks

if __name__=='__main__':
    cases=check()
    print(json.dumps(cases,ensure_ascii=False,indent=2))
    if not all(case['pass'] for case in cases):raise SystemExit(1)
