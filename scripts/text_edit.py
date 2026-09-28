#!/usr/bin/env python3
"""Editing by the text: the transcript of all the footage, with what an edit keeps, for the app to show (export);
then a new version of the edit from the words taken out or put back (apply). The new version goes through the whole
engine again (auto_edit.py --decisions: the cuts on silences, the listening, the captions and the subtitles on its
sound), without asking the brain anything: only the words chosen change. What was changed by hand in Final Cut Pro is
not carried over: editing by the text comes before the finishing there.
Usage: text_edit.py export project_folder              -> project_folder/text.json
       text_edit.py apply project_folder edits.json     -> project_folder/text-decisions.json
edits.json: {"words": [{"file": "/path/clip.mp4", "start": 12.34, "kept": false}, ...]} (the words changed)."""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timeline

VIDEO = ('.mp4', '.mov', '.m4v', '.mts', '.mxf')
LINE_WORDS = 16  # a line of the transcript: a sentence, or this many words at most
LINE_PAUSE = 0.8  # or up to a pause this long

def footage(project):
    """The clips of the footage the edit was made from, in the order the engine names them (R1, R2...)."""
    with open(os.path.join(project, 'edl.json'), encoding='utf-8') as f:
        edl = json.load(f)
    folders = {os.path.dirname(c['file']) for c in edl['clips']}
    files = sorted(os.path.join(d, x) for d in folders for x in os.listdir(d) if x.lower().endswith(VIDEO) and not x.startswith('.'))
    return edl, files

def said(words):
    """The words said (hesitations, words said twice and invented words left out: the engine cuts them anyway)."""
    return [w for w in words if not (w.get('filler') or w.get('suspect') or w.get('repeat'))]

def lines_of(words):
    lines, cur = [], []
    for w in words:
        if cur and (w['start'] - cur[-1]['end'] > LINE_PAUSE or len(cur) >= LINE_WORDS):
            lines.append(cur)
            cur = []
        cur.append(w)
        if re.search(r'[.!?…]$', w['w']):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return lines

def state(w, clips, file):
    """'hook', 'kept' or 'cut': where the word is in the edit (its middle inside a cut of the same clip)."""
    mid = (w['start'] + w['end']) / 2
    for c in clips:
        if c['file'] == file and c.get('role') != 'effects' and c['in'] <= mid <= c['out']:
            return 'hook' if c.get('note') == 'hook' else 'kept'
    return 'cut'

def export(project):
    edl, files = footage(project)
    tdir = os.path.join(project, 'transcripts')
    out = {'project': project, 'clips': []}
    for n, f in enumerate(files, 1):
        try:
            with open(timeline.transcript_path(tdir, f), encoding='utf-8') as fh:
                words = said(json.load(fh)['words'])
        except (OSError, ValueError):
            continue
        if not words:
            continue
        lines = [{'start': round(l[0]['start'], 2), 'end': round(l[-1]['end'], 2),
                  'words': [{'w': w['w'], 'start': round(w['start'], 3), 'end': round(w['end'], 3),
                             'state': state(w, edl['clips'], f)} for w in l]} for l in lines_of(words)]
        out['clips'].append({'rush': f'R{n}', 'file': f, 'name': os.path.splitext(os.path.basename(f))[0], 'lines': lines})
    path = os.path.join(project, 'text.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False)
    kept = sum(1 for c in out['clips'] for l in c['lines'] for w in l['words'] if w['state'] != 'cut')
    total = sum(len(l['words']) for c in out['clips'] for l in c['lines'])
    print(f'{path}: {total} words said, {kept} in the edit')
    return path

def runs(words, keep):
    """[(first word, last word)]: the stretches of consecutive words kept."""
    out, cur = [], []
    for w in words:
        if keep(w):
            cur.append(w)
        elif cur:
            out.append((cur[0], cur[-1]))
            cur = []
    if cur:
        out.append((cur[0], cur[-1]))
    return out

def apply(project, edits_path):
    with open(os.path.join(project, 'text.json'), encoding='utf-8') as f:
        text = json.load(f)
    with open(edits_path, encoding='utf-8') as f:
        edits = {(e['file'], round(float(e['start']), 2)): bool(e['kept']) for e in json.load(f)['words']}
    with open(os.path.join(project, 'brain-answer.json'), encoding='utf-8') as f:
        decisions = json.load(f)
    try:
        with open(os.path.join(project, 'ranges.json'), encoding='utf-8') as f:
            chapters = [(r['file'], r['from'], r['chapter']) for r in json.load(f)['ranges'] if r.get('chapter')]
    except (OSError, ValueError, KeyError):
        chapters = []

    def kept(c, w, hook=False):
        was = w['state'] == ('hook' if hook else 'kept')
        return edits.get((c['file'], round(w['start'], 2)), was) if (hook or w['state'] != 'hook') else False

    main, hook = [], None
    for c in text['clips']:
        words = [w for l in c['lines'] for w in l['words']]
        for a, b in runs(words, lambda w: kept(c, w)):
            main.append({'rush': c['rush'], 'from': round(max(0.0, a['start'] - 0.02), 3), 'to': round(b['end'] + 0.02, 3), '_file': c['file']})
        hooked = runs(words, lambda w: w['state'] == 'hook' and kept(c, w, hook=True))
        if hooked and hook is None:  # the hook is one passage: its longest stretch still kept
            a, b = max(hooked, key=lambda r: r[1]['end'] - r[0]['start'])
            hook = {'rush': c['rush'], 'from': round(max(0.0, a['start'] - 0.02), 3), 'to': round(b['end'] + 0.02, 3)}
    order = {c['rush']: n for n, c in enumerate(text['clips'])}
    main.sort(key=lambda r: (order[r['rush']], r['from']))
    rush_of = {c['file']: c['rush'] for c in text['clips']}
    for file, start, title in chapters:  # each chapter on the first range from where it started
        if file not in rush_of:
            continue
        key = (order[rush_of[file]], start)
        target = next((r for r in main if (order[r['rush']], r['to']) > key), None)
        if target is not None and 'chapter' not in target:
            target['chapter'] = title
    for r in main:
        r.pop('_file')
    decisions.update({'hook': hook, 'main': main, 'review': {}, 'text_edited': True})
    path = os.path.join(project, 'text-decisions.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(decisions, f, ensure_ascii=False, indent=1)
    print(path)
    return path

def main():
    parser = argparse.ArgumentParser(description='Editing by the text: the transcript with what the edit keeps, and a new version from the words chosen.')
    parser.add_argument('action', choices=('export', 'apply'))
    parser.add_argument('project', help='the project folder of the edit')
    parser.add_argument('edits', nargs='?', help='apply: the words changed (edits.json)')
    args = parser.parse_args()
    project = os.path.abspath(args.project)
    if args.action == 'export':
        export(project)
    else:
        if not args.edits:
            raise SystemExit('apply needs the edits.json file')
        apply(project, args.edits)

if __name__ == '__main__':
    main()
