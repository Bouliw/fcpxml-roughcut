#!/usr/bin/env python3
"""The contact sheet the user picks from: one row per beat (time, the words, what it is about), its candidates side
by side, each with a picture, a lettered key (1A, 1B...) and a licence badge worked out here, not by the agent:
  green  Free (public domain, CC0, CC BY, CC BY-SA), Own graphic, Your channel
  blue   Official page capture
  red    CLAIM RISK: any YouTube excerpt from a channel that is not the user's own
  amber  Licence to check (anything else), and restrictions (a recognisable person, a trademark)
Usage: contact_sheet.py candidates.json [-o contact-sheet.png] [--own-channel "Name"]... [--per-page 5]
candidates.json: {"title": "...", "own_channels": ["..."], "beats": [{"id": "b01", "at": 12.4, "word": "Westminster",
  "quote": "we went to Westminster", "why": "Parliament, not the district", "route": "Entity", "candidates": [
  {"key": "A", "file": "raw/westminster.jpg", "source": "Wikimedia Commons", "licence": "CC BY-SA 4.0", "credit": "...",
   "url": "...", "in": 0, "seconds": 3, "note": "facade"},
  {"key": "B", "file": "raw/yt.mp4", "source": "YouTube", "channel": "BBC News", "in": 12.0, "url": "..."}]}]}
A file fetched by commons.py brings its licence, credit and restrictions from its .json sidecar."""
import argparse, json, os, re, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFont

W, MARGIN, LEFT, TILE_W, TILE_H, GAP = 1920, 40, 470, 440, 248, 26
GREEN, BLUE, RED, AMBER, GREY = (22, 128, 61), (29, 78, 216), (200, 30, 30), (180, 110, 0), (90, 90, 90)
FONTS = {False: ('/System/Library/Fonts/Supplemental/Arial.ttf', '/Library/Fonts/Arial.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
         True: ('/System/Library/Fonts/Supplemental/Arial Bold.ttf', '/Library/Fonts/Arial Bold.ttf',
                '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')}

def font(size, bold=False):
    for f in FONTS[bold]:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)

def lic_family(text):
    l = (text or '').lower().replace(' ', '-')
    if l.startswith(('pd', 'public-domain')) or l.startswith('cc0') or l == 'cc-zero':
        return 'pd'
    if l.startswith('cc-by-sa'):
        return 'cc-by-sa'
    if l.startswith('cc-by') and not re.match(r'cc-by-(nc|nd)', l):
        return 'cc-by'
    return None

def badge(c, own):
    """(label, colour) of a candidate: the copyright risk the user takes by picking it."""
    src, lic = (c.get('source') or '').lower(), c.get('licence') or ''
    url = (c.get('url') or '').lower()
    if 'youtube' in src or 'youtu.be' in url or 'youtube.com' in url:
        if (c.get('channel') or '').strip().lower() in {o.strip().lower() for o in own if o}:
            return 'Your channel', GREEN
        return 'CLAIM RISK', RED
    fam = lic_family(lic)
    if fam == 'pd':
        return 'Free · ' + ('CC0' if 'cc0' in lic.lower() else 'public domain'), GREEN
    if fam == 'cc-by':
        return 'Free · credit required', GREEN
    if fam == 'cc-by-sa':
        return 'Free · credit (BY-SA)', GREEN
    if 'capture' in src or 'capture' in lic.lower():
        return 'Official page capture', BLUE
    if lic.strip().lower() == 'own' or 'hyperframes' in src:
        return 'Own graphic', GREEN
    return 'Licence to check', AMBER

def with_sidecar(c, base):
    path = c['file'] if os.path.isabs(c['file']) else os.path.join(base, c['file'])
    side = path + '.json'
    if os.path.exists(side):
        with open(side, encoding='utf-8') as f:
            s = json.load(f)
        c = {**{k: s.get(k) for k in ('source', 'licence', 'credit', 'restrictions') if s.get(k)},
             'url': s.get('page'), **c}
    return dict(c, path=path)

def thumb(c, tmp):
    """(picture, info text) of a candidate."""
    r = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', c['path']],
                       capture_output=True, text=True)
    if r.returncode:
        return None, 'unreadable file'
    d = json.loads(r.stdout)
    v = next((s for s in d['streams'] if s.get('codec_type') == 'video'), {})
    dur = float(d['format'].get('duration') or 0)
    video = dur >= 0.5 and v.get('codec_name') not in ('png', 'mjpeg', 'webp', 'bmp', 'tiff')
    at = float(c.get('in', 0)) + float(c.get('seconds', 3)) * 0.75 if video else 0  # a graphic has settled by then
    out = os.path.join(tmp, f'{abs(hash(c["path"] + str(at)))}.png')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *(['-ss', f'{min(at, max(dur - 0.1, 0)):.2f}'] if video else []),
                    '-i', c['path'], '-frames:v', '1', out], capture_output=True)
    if not os.path.exists(out):
        return None, 'no picture'
    w, h = v.get('width', 0), v.get('height', 0)
    info = f'{w}×{h}' + (' · low res' if min(w, h) and min(w, h) < 1080 and max(w, h) < 1920 else '')
    if video:
        start = float(c.get('in', 0))
        info += f' · video {dur:.0f} s' + (f', excerpt {start:.0f}–{start + float(c.get("seconds", 3)):.0f} s' if start else '')
    else:
        info += ' · still'
    return Image.open(out).convert('RGB'), info

def wrap(draw, text, f, width, lines=3):
    words, out, cur = (text or '').split(), [], ''
    for w in words:
        t = (cur + ' ' + w).strip()
        if draw.textlength(t, font=f) <= width:
            cur = t
        else:
            out.append(cur)
            cur = w
    out.append(cur)
    out = [l for l in out if l]
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1].rstrip(' .,;') + '…'
    return out

def mmss(t):
    m, s = divmod(float(t), 60)
    return f'{int(m)}:{s:04.1f}'

def fit(im, w, h):
    sc = min(w / im.width, h / im.height)
    im = im.resize((max(1, round(im.width * sc)), max(1, round(im.height * sc))), Image.LANCZOS)
    box = Image.new('RGB', (w, h), (20, 20, 20))
    box.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    return box

def draw_beat(page, y, n, beat, cands, tmp, own):
    d = ImageDraw.Draw(page)
    d.text((MARGIN, y), f'{n}  ·  {mmss(beat.get("at", 0))}', font=font(40, True), fill=(15, 15, 15))
    yy = y + 58
    if beat.get('word'):
        for line in wrap(d, f'word: "{beat["word"]}"', font(26, True), LEFT - MARGIN - 10, 2):
            d.text((MARGIN, yy), line, font=font(26, True), fill=(60, 60, 60))
            yy += 36
        yy += 2
    for line in wrap(d, f'"{beat.get("quote", "")}"', font(26), LEFT - MARGIN - 10, 4):
        d.text((MARGIN, yy), line, font=font(26), fill=(40, 40, 40))
        yy += 34
    if beat.get('why'):
        yy += 6
        for line in wrap(d, '→ ' + beat['why'], font(24), LEFT - MARGIN - 10, 3):
            d.text((MARGIN, yy), line, font=font(24), fill=GREY)
            yy += 31
    if beat.get('route'):
        d.text((MARGIN, yy + 8), beat['route'], font=font(22, True), fill=BLUE)
    per_row = (W - LEFT - MARGIN + GAP) // (TILE_W + GAP)
    tile_full = TILE_H + 44 + 3 * 28 + 24
    for i, c in enumerate(cands):
        x = LEFT + (i % per_row) * (TILE_W + GAP)
        ty = y + (i // per_row) * tile_full
        label, colour = badge(c, own)
        d.rectangle((x, ty, x + TILE_W, ty + 40), fill=colour)
        d.text((x + 12, ty + 6), f'{n}{c.get("key", chr(65 + i))}', font=font(26, True), fill='white')
        d.text((x + 70, ty + 8), label, font=font(23, True), fill='white')
        im, info = thumb(c, tmp)
        page.paste(fit(im, TILE_W, TILE_H) if im else Image.new('RGB', (TILE_W, TILE_H), (60, 60, 60)), (x, ty + 40))
        src = c.get('source') or ''
        if c.get('channel'):
            src += f' — {c["channel"]}'
        if c.get('licence') and lic_family(c['licence']):
            src += f' · {c["licence"]}'
        lines = [(src, (30, 30, 30), True), (info, GREY, False)]
        note = c.get('note') or ''
        if c.get('restrictions'):
            note = ('⚠ ' + ', '.join(c['restrictions']) + (' · ' + note if note else ''))
        if note:
            lines.append((note, AMBER if c.get('restrictions') else GREY, False))
        ly = ty + 40 + TILE_H + 8
        for text, col, bold in lines[:3]:
            text = wrap(d, text, font(22, bold), TILE_W, 1)[0] if text else ''
            d.text((x, ly), text, font=font(22, bold), fill=col)
            ly += 28
    rows = max(1, -(-len(cands) // per_row))
    return max(rows * tile_full, yy - y + 50)

def main():
    p = argparse.ArgumentParser(description='Contact sheet of the b-roll candidates, with licence badges.')
    p.add_argument('candidates')
    p.add_argument('-o', dest='out', help='contact-sheet.png (pages: contact-sheet-1.png, contact-sheet-2.png...)')
    p.add_argument('--own-channel', action='append', default=[], help="the user's own YouTube channel(s)")
    p.add_argument('--per-page', type=int, default=5)
    a = p.parse_args()
    with open(a.candidates, encoding='utf-8') as f:
        data = json.load(f)
    base = os.path.dirname(os.path.abspath(a.candidates))
    own = a.own_channel + data.get('own_channels', [])
    out = a.out or os.path.join(base, 'contact-sheet.png')
    beats = data['beats']
    pages = [beats[i:i + a.per_page] for i in range(0, len(beats), a.per_page)] or [[]]
    tmp = tempfile.mkdtemp()
    written, risky = [], 0
    for pn, chunk in enumerate(pages, 1):
        page = Image.new('RGB', (W, 6000), 'white')
        d = ImageDraw.Draw(page)
        title = data.get('title', 'B-roll contact sheet') + (f'  ({pn}/{len(pages)})' if len(pages) > 1 else '')
        d.text((MARGIN, 30), title, font=font(44, True), fill=(10, 10, 10))
        lx = MARGIN
        for label, colour in (('Free / own', GREEN), ('Official capture', BLUE),
                              ('CLAIM RISK', RED), ('To check', AMBER)):
            d.rectangle((lx, 98, lx + 22, 120), fill=colour)
            d.text((lx + 30, 94), label, font=font(24), fill=(40, 40, 40))
            lx += 60 + d.textlength(label, font=font(24))
        d.text((lx + 20, 94), 'Answer with the keys: "1A, 2C, 3 none"', font=font(24, True), fill=(40, 40, 40))
        y = 160
        for i, beat in enumerate(chunk):
            n = (pn - 1) * a.per_page + i + 1
            cands = [with_sidecar(c, base) for c in beat.get('candidates', [])]
            risky += sum(1 for c in cands if badge(c, own)[1] == RED)
            d.line((MARGIN, y - 18, W - MARGIN, y - 18), fill=(220, 220, 220), width=2)
            y += draw_beat(page, y, n, beat, cands, tmp, own) + 30
        page = page.crop((0, 0, W, y))
        path = out if len(pages) == 1 else re.sub(r'(\.\w+)$', rf'-{pn}\1', out)
        page.save(path)
        written.append(path)
    print('\n'.join(written))
    print(f'{len(beats)} beats, {sum(len(b.get("candidates", [])) for b in beats)} candidates, '
          f'{risky} marked "claim risk"', file=sys.stderr)

if __name__ == '__main__':
    main()
