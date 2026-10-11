#!/usr/bin/env python3
"""Lays the picked b-roll over a Roughcut edit: each visual becomes a connected clip above the talking head in a new
FCPXML, landing just after its word (read in the rush's word-level transcript). Nothing is burned into a video.
Roughcut's own files stay untouched: this writes edl-broll.json next to the project's edl.json and builds the
project with Roughcut's make_fcpxml.py, with the options the edit was made with (read in montage.log): same levels,
J/L cuts, zooms, captions on top, music. The project is named "<edit> – b-roll" and lands in the edit's event.

Usage: place_broll.py PROJECT picks.json [--roughcut PATH] [--lead 0.3] [--connect 2.0] [--drop-roughcut-broll]
                      [--map LOCAL=MAC]... [--open] [--dry-run]
PROJECT: the Roughcut project folder (edl.json, transcripts/, montage.log).
picks.json: {"beats": [{"id": "b01", "clip": "clips/b01.mp4", "word": "Bercy", "at": 84.2, "seconds": 3.0,
             "source": "/path/rush.MP4", "note": "Bercy, the ministry", "licence": "CC BY-SA 4.0", "credit": "..."}],
             "removed": ["b05 — cut by the user, too literal"]}
  clip     prepared by prepare_clip.py (path relative to picks.json); "in": where to start in it (default 0)
  word     the word (or words) the cut lands on; "at" is roughly when it is said in the rush (s), from the
           transcript, to find the right one; "source" names the rush when the edit uses several
  seconds  time on screen; stretched to meet the next beat when the gap is shorter than --connect
--map rewrites media paths in the FCPXML (built elsewhere than on the Mac that opens it): --map /tmp/proj=/path/on/the/Mac/proj
Writes next to picks.json: BROLL-MANIFEST.md (every beat, placed or dropped, and why) and CREDITS.txt (for the
video description: the credit each free licence asks for)."""
import argparse, glob, html, json, os, re, subprocess, sys, unicodedata
from fractions import Fraction
from urllib.parse import quote

# ---------------------------------------------------------------- Roughcut's engine
APP = 'Roughcut.app/Contents/Resources/engine/roughcut'
HERE = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..', '..')  # the fcpxml-roughcut it ships in
CLONES = ('~/fcpxml-roughcut', '~/Developer/fcpxml-roughcut', '~/Projects/fcpxml-roughcut', '~/code/fcpxml-roughcut',
          '~/GitHub/fcpxml-roughcut', '~/Documents/GitHub/fcpxml-roughcut')

class Engine:
    """Runs Roughcut's scripts: through the app's launcher (its own Python and tools) or from a clone."""
    def __init__(self, given=None):
        found = None
        for c in [given, os.environ.get('ROUGHCUT_ENGINE'), HERE, f'/Applications/{APP}', f'~/Applications/{APP}', *CLONES]:
            if not c:
                continue
            c = os.path.abspath(os.path.expanduser(c))
            if os.path.isfile(c):
                found = ('launcher', c, os.path.join(os.path.dirname(c), 'scripts'))
            elif os.path.isfile(os.path.join(c, 'make_fcpxml.py')):
                found = ('scripts', None, c)
            elif os.path.isfile(os.path.join(c, 'scripts', 'make_fcpxml.py')):
                found = ('scripts', None, os.path.join(c, 'scripts'))
            if found:
                break
        if not found:
            raise SystemExit('Roughcut not found: install the app, or pass --roughcut /path/to/fcpxml-roughcut '
                             '(or set ROUGHCUT_ENGINE)')
        self.kind, self.launcher, self.scripts = found
        sys.path.insert(0, self.scripts)  # timeline.py: the same frame arithmetic as the FCPXML

    def run(self, script, *args):
        cmd = (['bash', self.launcher, script] if self.kind == 'launcher'
               else [sys.executable, os.path.join(self.scripts, script)]) + [str(a) for a in args]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f'{script} failed:\n{(r.stdout + r.stderr).strip()[-3000:]}')
        return r.stdout + r.stderr

# ---------------------------------------------------------------- words
def norm(text):
    """'L’Élysée,' -> 'lelysee': case, accents, apostrophes and punctuation do not count."""
    t = unicodedata.normalize('NFKD', text.lower())
    return re.sub(r'[^0-9a-z]', '', ''.join(ch for ch in t if not unicodedata.combining(ch)))

def same_name(folder, name):
    """The file named so in folder, whatever the accents' encoding (macOS stores them decomposed)."""
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    want = unicodedata.normalize('NFC', name)
    return next((os.path.join(folder, e) for e in entries if unicodedata.normalize('NFC', e) == want), None)

def load_words(project, rush, extra_dirs=()):
    """[(word, start, end)] of a rush: Roughcut's <rush>.words.json, else whisper-cli's own JSON (-ml 1 -sow -oj)."""
    base = os.path.splitext(os.path.basename(rush))[0]
    for d in (os.path.join(project, 'transcripts'), *extra_dirs, os.path.dirname(rush)):
        p = same_name(d, base + '.words.json')
        if p:
            with open(p, encoding='utf-8') as f:
                return [(w['w'], w['start'], w['end']) for w in json.load(f)['words']
                        if not (w.get('suspect') or w.get('removed'))], p
        p = same_name(d, base + '.json')
        if p:
            with open(p, encoding='utf-8') as f:
                d_ = json.load(f)
            if 'transcription' in d_:
                return [(s['text'].strip(), s['offsets']['from'] / 1000, s['offsets']['to'] / 1000)
                        for s in d_['transcription'] if norm(s.get('text', ''))], p
    raise SystemExit(f'No word-level transcript for {os.path.basename(rush)} (looked for {base}.words.json and '
                     f'{base}.json in {project}/transcripts): transcribe it first (SKILL.md, step 3)')

def find_word(words, phrase, at=None, window=6.0):
    """Start of `phrase` in the words, the occurrence nearest `at`; (None, why) when absent or ambiguous."""
    want = [norm(x) for x in re.split(r"[\s’']+", phrase) if norm(x)]
    if not want:
        return None, 'empty word'
    def match(tok, w):
        return tok == w or (len(w) >= 4 and tok.endswith(w))  # "l'Élysée" holds "elysee"
    toks = [norm(w[0]) for w in words]
    hits = [i for i in range(len(words) - len(want) + 1)
            if match(toks[i], want[0]) and all(toks[i + k] == want[k] for k in range(1, len(want)))]
    if at is not None:
        hits = [i for i in hits if abs(words[i][1] - at) <= window]
    if not hits:
        near = ' '.join(w[0] for w in words if at is not None and abs(w[1] - at) <= 3)
        return None, f'"{phrase}" not said' + (f' near {at:.1f} s (heard there: {near[:120]})' if at is not None else '')
    if at is None and len(hits) > 1:
        return None, f'"{phrase}" said {len(hits)} times: give "at" to pick one (' + \
            ', '.join(f'{words[i][1]:.1f} s' for i in hits[:6]) + ')'
    best = min(hits, key=lambda i: abs(words[i][1] - (at if at is not None else words[i][1])))
    return words[best][1], None

# ---------------------------------------------------------------- the edit
def montage_flags(project):
    """The options make_fcpxml.py built the edit with, read in montage.log (paths there may hold spaces: only the
    flags and single-word values are read; the captions are found again in the project)."""
    line = None
    log = os.path.join(project, 'montage.log')
    if os.path.exists(log):
        with open(log, encoding='utf-8', errors='replace') as f:
            for l in f:
                if l.startswith('$ ') and ' make_fcpxml.py ' in l and 'edit.fcpxml' in l:
                    line = l  # the last one: a retry without dressing replaces the first
    if not line:
        flags = ['--level-audio', '--fades', '--split-edits']
        for name in ('edit.fcpxml',):
            v = fcpxml_version(os.path.join(project, name))
            if v:
                flags += ['--fcpxml-version', v]
        return flags, 'defaults (no montage.log)'
    tokens = line.split()
    flags = [f for f in ('--level-audio', '--fades', '--split-edits', '--zoom-jump-cuts') if f in tokens]
    for opt in ('--fcpxml-version', '--language', '--dressing'):
        if opt in tokens and tokens.index(opt) + 1 < len(tokens):
            flags += [opt, tokens[tokens.index(opt) + 1]]
    if '--overlay' in tokens:
        for name in ('captions.json', 'captions.mov'):
            if os.path.exists(os.path.join(project, name)):
                flags += ['--overlay', os.path.join(project, name)]
                break
    return flags, 'montage.log'

def fcpxml_version(path):
    try:
        with open(path, encoding='utf-8') as f:
            m = re.search(r'<fcpxml\s[^>]*version="([\d.]+)"', f.read(4096))
        return m.group(1) if m else None
    except OSError:
        return None

def event_of(project):
    """Name of the event of the edit's merged FCPXML (what Final Cut Pro imported), or None."""
    for p in sorted(glob.glob(os.path.join(project, '*.fcpxml'))):
        if os.path.basename(p) in ('edit.fcpxml', 'logged.fcpxml', 'edit-broll.fcpxml') or p.endswith(' – b-roll.fcpxml'):
            continue
        with open(p, encoding='utf-8') as f:
            m = re.search(r'<event name="([^"]*)"', f.read())
        if m:
            return html.unescape(m.group(1)), p
    return None, None

def mmss(t):
    m, s = divmod(max(0.0, float(t)), 60)
    return f'{int(m)}:{s:04.1f}'

# ---------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description='Lays the picked b-roll over a Roughcut edit, in a new FCPXML.')
    p.add_argument('project', help='Roughcut project folder (or its edl.json)')
    p.add_argument('picks', help='picks.json')
    p.add_argument('--roughcut', help="Roughcut's launcher, or a folder with its scripts (default: this checkout, the app, then clones)")
    p.add_argument('--transcripts', action='append', default=[], help='another folder with <rush>.words.json')
    p.add_argument('--lead', type=float, default=0.3, help='seconds after the start of the word (default 0.3)')
    p.add_argument('--connect', type=float, default=2.0,
                   help='a gap shorter than this between two beats is closed by stretching the first (default 2 s)')
    p.add_argument('--drop-roughcut-broll', action='store_true', help="leave out the b-roll Roughcut itself placed")
    p.add_argument('--map', action='append', default=[], help='LOCAL=TARGET: rewrite media paths in the FCPXML')
    p.add_argument('--open', action='store_true', help='open the result in Final Cut Pro')
    p.add_argument('--dry-run', action='store_true', help='show where everything lands, write nothing')
    a = p.parse_args()

    project = os.path.abspath(os.path.expanduser(a.project))
    if project.endswith('.json'):
        project = os.path.dirname(project)
    edl_path = os.path.join(project, 'edl.json')
    if not os.path.exists(edl_path):
        raise SystemExit(f'No edl.json in {project}: give the Roughcut project folder')
    picks_path = os.path.abspath(a.picks)
    here = os.path.dirname(picks_path)
    with open(picks_path, encoding='utf-8') as f:
        picks = json.load(f)
    engine = Engine(a.roughcut)
    import timeline  # Roughcut's
    edl, tl, assets, clips = timeline.build(edl_path)
    fps = tl['fps']
    fd = timeline.fd(fps)
    sources = sorted({c['file'] for c in clips})

    lead, reserve_f = a.lead, 0
    music = edl.get('music') or {}
    if music.get('snap_broll'):  # Roughcut moves each start to a beat up to 0.35 s away: never before the word
        try:
            import settings
            window = settings.load(project)['music']['snap_window_seconds']
        except Exception:
            window = 0.35
        lead = max(lead, window + 0.05)
        reserve_f = timeline.frames(window, fps)  # and each end too: keep that much of the clip in hand

    words_cache, placed, dropped = {}, [], []
    for i, b in enumerate(picks.get('beats', [])):
        bid = b.get('id') or f'b{i + 1:02d}'
        if not (b.get('word') and b.get('clip')):
            dropped.append((b, bid, 'needs "word" and "clip"'))
            continue
        rush = b.get('source')
        if rush:
            rush = timeline.disk_path(os.path.abspath(os.path.join(project, os.path.expanduser(rush))))
            if rush not in sources:  # given by its name only: the edit's rush of that name
                name = unicodedata.normalize('NFC', os.path.basename(rush))
                same = [s_ for s_ in sources if unicodedata.normalize('NFC', os.path.basename(s_)) == name]
                if len(same) != 1:
                    dropped.append((b, bid, f'"{b["source"]}" is not a rush of this edit'))
                    continue
                rush = same[0]
        elif len(sources) > 1:
            dropped.append((b, bid, f'the edit uses {len(sources)} rushes: say which in "source"'))
            continue
        else:
            rush = sources[0]
        if rush not in words_cache:
            words_cache[rush] = load_words(project, rush, a.transcripts)
        words, tpath = words_cache[rush]
        start, why = find_word(words, b['word'], b.get('at'))
        if start is None:
            dropped.append((b, bid, why))
            continue
        t = start + lead
        hits = [c for c in clips if c['file'] == rush and c['in_s'] - 0.05 <= t <= c['in_s'] + c['dur_s'] + 0.05]
        if not hits:
            dropped.append((b, bid, f'"{b["word"]}" at {mmss(start)} of the rush was cut out of the edit'))
            continue
        c = hits[0]
        at_f = c['off_f'] + timeline.frames(max(0.0, t - c['in_s']), fps)
        clip = os.path.abspath(os.path.join(here, os.path.expanduser(b['clip'])))
        if not os.path.exists(clip):
            dropped.append((b, bid, f'clip missing: {clip}'))
            continue
        info = timeline.probe(clip)
        b_in = float(b.get('in', 0))
        room_f = timeline.frames(info['duration'] - b_in, fps) - reserve_f
        want_f = timeline.frames(float(b.get('seconds', 3.0)), fps)
        if room_f < want_f:
            print(f'NOTE {bid}: the clip holds {room_f * float(fd):.2f} s, {want_f * float(fd):.2f} s asked: shortened')
        placed.append({'b': b, 'id': bid, 'rush': rush, 'word_s': start, 'at_source': t, 'at_f': at_f,
                       'dur_f': min(want_f, room_f), 'room_f': room_f, 'clip': clip, 'in': b_in})

    # beats in timeline order; a short gap closed by the earlier one, an overlap trimmed (no stacking, no flash)
    placed.sort(key=lambda x: x['at_f'])
    for x, y in zip(placed, placed[1:]):
        gap = y['at_f'] - (x['at_f'] + x['dur_f'])
        if gap < 0:
            x['dur_f'] = y['at_f'] - x['at_f']
            x['fix'] = 'trimmed to meet the next'
        elif 0 < gap < a.connect / float(fd):
            if x['at_f'] + x['room_f'] >= y['at_f']:
                x['dur_f'] = y['at_f'] - x['at_f']
                x['fix'] = f'stretched {gap * float(fd):.2f} s to meet the next'
            else:
                x['fix'] = f'WARNING: {gap * float(fd):.2f} s of face before the next, the clip is too short to stretch'
    for x in [x for x in placed if x['dur_f'] < 1]:  # two beats on one frame: the later one wins
        placed.remove(x)
        dropped.append((x['b'], x['id'], 'lands on the same frame as the next beat'))
    for x in placed:
        if x['dur_f'] * fd < Fraction(6, 5):
            x['fix'] = (x.get('fix', '') + '; ' if x.get('fix') else '') + 'WARNING: under 1.2 s, hard to read'

    theirs = [] if a.drop_roughcut_broll else list(edl.get('broll', []))
    print(f'Edit: {os.path.basename(project)} — {len(clips)} cuts, {float(tl["total_f"] * fd):.1f} s at {float(fps):.3f} fps'
          f'; anchor: word start + {lead:.2f} s')
    print(f'{"beat":<6}{"word":<22}{"rush":>8}{"edit":>9}{"length":>8}  clip')
    for x in placed:
        print(f'{x["id"]:<6}{x["b"]["word"][:21]:<22}{mmss(x["word_s"]):>8}{mmss(x["at_f"] * fd):>9}'
              f'{float(x["dur_f"] * fd):>7.2f}s  {os.path.basename(x["clip"])}' + (f'  ({x["fix"]})' if x.get('fix') else ''))
    for b, bid, why in dropped:
        print(f'DROPPED {bid} "{b.get("word") or "?"}": {why}')
    if theirs:
        print(f'Kept: {len(theirs)} b-roll shots Roughcut placed itself (--drop-roughcut-broll to leave them out)')
        import broll  # Roughcut's: where its own shots land
        for at_f, _, path, b_in, b_out, note, _ in broll.placements({'broll': theirs}, clips, project, fps):
            end_f = at_f + timeline.frames(b_out - b_in, fps)
            for x in placed:
                if at_f < x['at_f'] + x['dur_f'] and x['at_f'] < end_f:
                    print(f'WARNING: Roughcut\'s shot "{note or os.path.basename(path)}" ({mmss(at_f * fd)}) overlaps '
                          f'{x["id"]}: the later one covers the other')
    if not placed:
        raise SystemExit('Nothing to place.')
    if a.dry_run:
        return

    out_edl = dict(edl, project=f'{edl.get("project", "Montage")} – b-roll')
    out_edl['broll'] = theirs + [{'file': x['clip'], 'in': x['in'], 'out': x['in'] + float(x['dur_f'] * fd),
                                  'at_source': x['at_source'], 'source': x['rush'],
                                  'note': f'{x["id"]} {x["b"].get("note") or x["b"]["word"]}'} for x in placed]
    broll_edl = os.path.join(project, 'edl-broll.json')
    with open(broll_edl, 'w', encoding='utf-8') as f:
        json.dump(out_edl, f, ensure_ascii=False, indent=1)
    flags, origin = montage_flags(project)
    edit = os.path.join(project, 'edit-broll.fcpxml')
    report = engine.run('make_fcpxml.py', broll_edl, '-o', edit, *flags)
    event, merged = event_of(project)
    final = edit
    if event:  # into the edit's own event, next to it
        final = re.sub(r'\.fcpxml$', ' – b-roll.fcpxml', merged)
        version = fcpxml_version(edit)
        engine.run('fcpxml_merge.py', edit, '-o', final, '--event', event, *(['--version', version] if version else []))
    for m in a.map:
        local, target = m.split('=', 1)
        with open(final, encoding='utf-8') as f:
            xml = f.read()
        xml = xml.replace('file://' + quote(os.path.abspath(local)), 'file://' + quote(target.rstrip('/')))
        with open(final, 'w', encoding='utf-8') as f:
            f.write(xml)
    print(f'\nOptions of the edit ({origin}): {" ".join(f for f in flags if not f.startswith("/"))}')
    print('\n'.join(l for l in report.splitlines() if l.startswith(('B-roll', '  ', 'DTD', 'WARNING'))))
    print(f'\n{final}' + (f'\n(event "{event}")' if event else ''))

    # the manifest and the credits, next to picks.json
    rows = ['# B-roll manifest', '', f'Edit: `{os.path.basename(project)}` → `{os.path.basename(final)}`', '',
            '| beat | word | rush | edit | length | clip | licence | status |', '|---|---|---|---|---|---|---|---|']
    for x in placed:
        rows.append(f'| {x["id"]} | {x["b"]["word"]} | {mmss(x["word_s"])} | {mmss(x["at_f"] * fd)} | '
                    f'{float(x["dur_f"] * fd):.1f} s | {os.path.basename(x["clip"])} | {x["b"].get("licence", "")} | '
                    f'placed{" — " + x["fix"] if x.get("fix") else ""} |')
    for b, bid, why in dropped:
        rows.append(f'| {bid} | {b.get("word", "")} | | | | {os.path.basename(b.get("clip", ""))} | '
                    f'{b.get("licence", "")} | DROPPED — {why} |')
    if picks.get('removed'):
        rows += ['', '## Removed (do not re-add)', ''] + [f'- {r}' for r in picks['removed']]
    with open(os.path.join(here, 'BROLL-MANIFEST.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(rows) + '\n')
    credits = [f'{mmss(x["at_f"] * fd)} — {x["b"]["credit"]}' for x in placed if x['b'].get('credit')]
    if credits:
        with open(os.path.join(here, 'CREDITS.txt'), 'w', encoding='utf-8') as f:
            f.write('Image credits\n' + '\n'.join(credits) + '\n')
    if a.open:
        print(engine.run('fcp.py', 'open', final).strip())

if __name__ == '__main__':
    main()
