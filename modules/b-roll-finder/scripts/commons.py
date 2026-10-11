#!/usr/bin/env python3
"""Wikimedia Commons: search free files and fetch them with their licence, never without it.
Usage: commons.py search "Palais Brongniart" [--video] [--limit 12] [--json out.json]
       commons.py get "File:Palais Brongniart.jpg" DEST_DIR [--width 3840]
search prints one line per file the licence lets us use in a monetised video (public domain, CC0, CC BY, CC BY-SA),
best licence first; anything else (non-commercial, unknown, fair use) is left out and counted.
get downloads the file (a 3840-px rendering for big stills and SVGs) next to a .json sidecar: licence, author,
page, restrictions and the credit line, which contact_sheet.py and place_broll.py read."""
import argparse, html, json, os, re, sys, urllib.parse, urllib.request

API = 'https://commons.wikimedia.org/w/api.php'
# Wikimedia asks every client to say who it is
UA = 'b-roll-finder/1.0 (Roughcut module; https://github.com/Bouliw/fcpxml-roughcut)'
# licence family -> (rank, label on the contact sheet); lower rank = freer
TIERS = {'pd': (0, 'Public domain'), 'cc0': (0, 'CC0'), 'cc-by': (1, 'CC BY (credit required)'),
         'cc-by-sa': (2, 'CC BY-SA (credit + share-alike)')}

def call(params):
    q = urllib.parse.urlencode(dict(params, format='json', formatversion=2))
    req = urllib.request.Request(f'{API}?{q}', headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def text(value):
    """Plain text of an extmetadata HTML value."""
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', value or ''))).strip()

def family(licence):
    """'cc-by-sa-4.0' -> 'cc-by-sa', 'pd' -> 'pd'; None for anything we do not use (NC, ND, GFDL only, fair use)."""
    l = (licence or '').lower()
    if l.startswith('pd') or l in ('public domain', 'publicdomain'):
        return 'pd'
    if l.startswith('cc0') or l.startswith('cc-zero'):
        return 'cc0'
    if l.startswith('cc-by-sa'):
        return 'cc-by-sa'
    if l.startswith('cc-by') and not re.match(r'cc-by-(nc|nd)', l):
        return 'cc-by'
    return None

def describe(page):
    """What we need to judge and credit a file, or None when its licence is not usable."""
    info = (page.get('imageinfo') or [{}])[0]
    meta = {k: (v or {}).get('value', '') for k, v in (info.get('extmetadata') or {}).items()}
    fam = family(meta.get('License') or meta.get('LicenseShortName'))
    if not fam or meta.get('NonFree', '').lower() == 'true':
        return None
    title = page['title']
    author = text(meta.get('Artist')) or text(meta.get('Credit')) or 'unknown author'
    short = meta.get('LicenseShortName') or fam.upper()
    name = re.sub(r'\.[^.]+$', '', title.split(':', 1)[1])
    credit = (f'"{name}" by {author}, {short}, via Wikimedia Commons' if fam in ('cc-by', 'cc-by-sa')
              else f'"{name}", {short}, via Wikimedia Commons')
    return {'title': title, 'page': info.get('descriptionurl'), 'url': info.get('url'), 'thumb': info.get('thumburl'),
            'mime': info.get('mime'), 'width': info.get('width'), 'height': info.get('height'),
            'duration': info.get('duration'), 'licence': short, 'licence_url': meta.get('LicenseUrl', ''),
            'tier': fam, 'rank': TIERS[fam][0], 'tier_label': TIERS[fam][1], 'author': author,
            'restrictions': [r for r in (meta.get('Restrictions') or '').split('|') if r],
            'description': text(meta.get('ImageDescription'))[:200], 'date': text(meta.get('DateTimeOriginal')),
            'credit': credit, 'source': 'Wikimedia Commons'}

def search(query, video=False, limit=12, width=1920):
    kind = 'video' if video else 'bitmap|drawing'
    d = call({'action': 'query', 'generator': 'search', 'gsrnamespace': 6, 'gsrlimit': min(50, limit * 3),
              'gsrsearch': f'{query} filetype:{kind}', 'prop': 'imageinfo',
              'iiprop': 'url|size|mime|extmetadata', 'iiurlwidth': width})
    pages = sorted((d.get('query') or {}).get('pages', []), key=lambda p: p.get('index', 0))
    found = [x for x in map(describe, pages) if x]
    found.sort(key=lambda x: (x['rank'], -(x['width'] or 0)))
    return found[:limit], len(pages) - len(found)

def get(title, dest, width=3840):
    if not title.startswith('File:'):
        title = 'File:' + title
    d = call({'action': 'query', 'titles': title, 'prop': 'imageinfo', 'iiprop': 'url|size|mime|extmetadata',
              'iiurlwidth': width})
    page = d['query']['pages'][0]
    if page.get('missing'):
        raise SystemExit(f'{title}: not on Commons')
    x = describe(page)
    if not x:
        raise SystemExit(f'{title}: its licence does not allow it here (not PD/CC0/CC BY/CC BY-SA)')
    still = (x['mime'] or '').startswith('image/')
    big = still and (x['mime'] == 'image/svg+xml' or (x['width'] or 0) > width or x['mime'] == 'image/tiff')
    url = x['thumb'] if big and x['thumb'] else x['url']
    os.makedirs(dest, exist_ok=True)
    base = re.sub(r'[^\w.-]+', '_', title.split(':', 1)[1])
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or '.bin'
    path = os.path.join(dest, os.path.splitext(base)[0] + ext)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(path + '.part', 'wb') as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(path + '.part', path)
    x['file'] = path
    with open(path + '.json', 'w', encoding='utf-8') as f:
        json.dump(x, f, ensure_ascii=False, indent=1)
    return x

def main():
    p = argparse.ArgumentParser(description='Free files from Wikimedia Commons, with their licence.')
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('search')
    s.add_argument('query')
    s.add_argument('--video', action='store_true', help='videos instead of stills')
    s.add_argument('--limit', type=int, default=12)
    s.add_argument('--json', help='also write the results here')
    g = sub.add_parser('get')
    g.add_argument('title')
    g.add_argument('dest')
    g.add_argument('--width', type=int, default=3840, help='rendering width for big stills and SVGs (default 3840)')
    a = p.parse_args()
    if a.cmd == 'get':
        x = get(a.title, a.dest, a.width)
        print(f"{x['file']}\n{x['licence']} · {x['credit']}" + (f"\n⚠ restrictions: {', '.join(x['restrictions'])}" if x['restrictions'] else ''))
        return
    found, dropped = search(a.query, a.video, a.limit)
    for x in found:
        size = f"{x['width']}x{x['height']}" + (f" {x['duration']:.0f}s" if x.get('duration') else '')
        flag = ' ⚠ ' + ','.join(x['restrictions']) if x['restrictions'] else ''
        low = ' (low res)' if (x['width'] or 0) < 1600 else ''
        print(f"{x['licence']:<14} {size:<14}{low} {x['title']}{flag}")
    print(f'{len(found)} usable, {dropped} left out (licence not usable)', file=sys.stderr)
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(found, f, ensure_ascii=False, indent=1)

if __name__ == '__main__':
    main()
