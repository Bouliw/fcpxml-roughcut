#!/usr/bin/env python3
"""Word-by-word animated captions, the Shorts way: a few words on screen, the word being said highlighted and
slightly bigger. Rendered by libass into a transparent video (HEVC with alpha) as long as the timeline, to lay over it
in Final Cut Pro (make_fcpxml.py --overlay captions.mov puts it there as a connected clip); a long timeline is
rendered in parts, side by side. The style is `captions` for a vertical Short, `captions_main` for the main edit. The
words are the transcript's with the corrections made in captions.txt (subtitles.py).
Usage: captions.py edl.json -o captions.mov [--transcripts dir] [--words 3] [--config file.json]"""
import argparse, concurrent.futures, hashlib, json, os, re, shutil, subprocess, sys, tempfile
from fractions import Fraction
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timeline import build, fd
from make_srt import edit_words, split_cues
import settings

def ass_time(t):
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f'{h}:{m:02d}:{s:02d}.{cs:02d}'

def colour(hex_rgb):
    """'#FFD700' -> ASS '&H0000D7FF' (alpha, blue, green, red)."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f'&H00{b}{g}{r}'.upper()

def clean(word):
    return word.replace('{', '(').replace('}', ')').replace('\\', '/')

def band(width, height, c):
    """The strip of the frame the captions of the main edit take: (its height, the margin under the text in it, its
    position in Final Cut Pro units, where 100 is the height of the frame and up is positive). Rendering this strip
    rather than the whole 4K frame is six times faster: the Mac's encoder is what limits it."""
    size = round(height * c['font_size'])
    strip = 2 * round(size * 1.6)  # two lines of text, the word being said a little bigger
    pad = round(size * 0.3)
    bottom = max(0, round(height * c['bottom_margin']) - pad)  # the strip's lower edge, from the bottom of the frame
    return strip, pad, round((bottom + strip / 2 - height / 2) / (height / 100), 3)

def ass(cues, width, height, total, c, strip=None):
    """The .ass text: one event per word, each showing the whole group with that word highlighted. With `strip`
    (band()), laid out in that strip of the frame rather than the whole of it."""
    size = round(height * c['font_size'])
    margin = round(height * (c['bottom_margin_vertical'] if height > width else c['bottom_margin']))
    if strip:
        height, margin = strip[0], strip[1]
    lines = ['[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {width}', f'PlayResY: {height}', 'WrapStyle: 0',
             'ScaledBorderAndShadow: yes', '', '[V4+ Styles]',
             'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, '
             'Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
             f'Style: Word,{c["font"]},{size},{colour(c["colour"])},{colour(c["colour"])},{colour(c["outline_colour"])},'
             f'&H64000000,-1,0,0,0,100,100,0,0,1,{max(1, round(size * c["outline"]))},0,2,{round(width * 0.08)},'
             f'{round(width * 0.08)},{margin},1',
             '', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    hi, pop = colour(c['highlight_colour']), round(100 * c['pop'])
    for n, cue in enumerate(cues):
        end_of_cue = max(cue[-1][1] + 0.15, cue[0][0] + 0.5)
        if n + 1 < len(cues):
            end_of_cue = min(end_of_cue, cues[n + 1][0][0])
        end_of_cue = min(end_of_cue, total)
        for i, (t0, _, _) in enumerate(cue):
            start = cue[0][0] if i == 0 else t0
            end = cue[i + 1][0] if i + 1 < len(cue) else end_of_cue
            if end <= start:
                continue
            # the word being said highlighted and bigger; in karaoke, the words already said stay highlighted
            words = [(f'{{\\c{hi}\\fscx{pop}\\fscy{pop}}}{clean(w)}{{\\r}}' if j == i else
                      f'{{\\c{hi}}}{clean(w)}{{\\r}}' if j < i and c.get('karaoke') else clean(w)) for j, (_, _, w) in enumerate(cue)]
            text = ' '.join(words).upper() if c['uppercase'] else ' '.join(words)
            lines.append(f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Word,,0,0,0,,{text}')
    return '\n'.join(lines) + '\n'

PART_SECONDS = 180  # a long timeline is rendered in parts of this length, side by side
WORKERS = 2  # the Mac's video encoder is the limit, not the processors

def encoders(width, height):
    """HEVC with transparency first: the Mac's encoder, and 40 times smaller than ProRes 4444 for the same picture
    (a 27-second Short: 3.5 MB instead of 154 MB; a 22-minute edit in 4K would have taken 30 GB). ProRes 4444 where
    it is missing."""
    rate = f'{max(4, round(8 * width * height / (3840 * 2160)))}M'
    return [['-c:v', 'hevc_videotoolbox', '-alpha_quality', '0.9', '-pix_fmt', 'bgra', '-tag:v', 'hvc1', '-b:v', rate],
            ['-c:v', 'prores_videotoolbox', '-profile:v', '4444', '-pix_fmt', 'bgra'],
            ['-c:v', 'prores_ks', '-profile:v', '4444', '-pix_fmt', 'yuva444p10le', '-vendor', 'apl0']]

def render(ass_path, out, width, height, fps, first_f=0, count_f=None, total=None):
    """Transparent video of the captions: frames first_f to first_f + count_f of the timeline (all of it by
    default). ffmpeg reads the subtitles under a plain name in a folder of its own: a path inside a filter breaks on
    an apostrophe, a colon or a comma (a folder "L'été")."""
    if count_f is None:
        count_f = int(round(total * fps))
    shift = first_f / fps
    src = f'color=c=black@0.0:s={width}x{height}:r={fps.numerator}/{fps.denominator},format=rgba'
    vf = f'setpts=PTS+{float(shift):.6f}/TB,ass=captions.ass:alpha=1,setpts=PTS-STARTPTS' if first_f else 'ass=captions.ass:alpha=1'
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(ass_path, os.path.join(tmp, 'captions.ass'))
        part = os.path.join(tmp, 'out' + os.path.splitext(out)[1])
        codecs = encoders(width, height)
        for i, codec in enumerate(codecs):
            r = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', src, '-frames:v', str(count_f), '-vf', vf,
                                *codec, part], cwd=tmp, capture_output=True, text=True)
            if r.returncode == 0:
                os.replace(part, os.path.abspath(out))  # in one step: Final Cut Pro never reads half a file
                return
            if i == len(codecs) - 1:
                raise SystemExit(f'Cannot render the captions: {r.stderr.strip()[-300:]}')

def events_between(ass_text, a, b):
    """The lines of the .ass shown between a and b seconds, with its styles: what a part shows."""
    head, _, events = ass_text.partition('[Events]')
    keep = []
    for line in events.splitlines():
        m = re.match(r'Dialogue: \d+,(\d+):(\d+):([\d.]+),(\d+):(\d+):([\d.]+),', line)
        if m:
            t0 = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            t1 = int(m.group(4)) * 3600 + int(m.group(5)) * 60 + float(m.group(6))
            if t1 > a and t0 < b:
                keep.append(line)
    return head + '\n'.join(keep)

def render_parts(ass_path, out, width, height, fps, total_f, position=None):
    """A long timeline in parts rendered side by side: <name>-01.mov... and <name>.json, the list of them and where
    each starts, for make_fcpxml.py --overlay. A part whose captions did not change is not rendered again."""
    stem, ext = os.path.splitext(os.path.abspath(out))
    manifest = stem + '.json'
    try:
        with open(manifest, encoding='utf-8') as f:
            before = {v['file']: v.get('hash') for v in json.load(f)['videos']}
    except (OSError, ValueError, KeyError):
        before = {}
    with open(ass_path, encoding='utf-8') as f:
        text = f.read()
    part_f = total_f if total_f / fps <= 1.5 * PART_SECONDS else int(round(PART_SECONDS * fps))
    parts = []
    for n, first in enumerate(range(0, total_f, part_f), 1):
        count = min(part_f, total_f - first)
        name = f'{os.path.basename(stem)}-{n:02d}{ext}' if part_f < total_f else os.path.basename(stem) + ext
        digest = hashlib.sha1(f'{width}x{height}|{fps}|{count}|'.encode() +
                              events_between(text, float(first / fps), float((first + count) / fps)).encode()).hexdigest()
        parts.append({'file': name, 'at': float(Fraction(first) / fps), 'first_f': first, 'count_f': count, 'hash': digest})
    todo = [p for p in parts if before.get(p['file']) != p['hash'] or not os.path.exists(os.path.join(os.path.dirname(stem), p['file']))]
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(lambda p: render(ass_path, os.path.join(os.path.dirname(stem), p['file']), width, height, fps,
                                       p['first_f'], p['count_f']), todo))
    with open(manifest, 'w', encoding='utf-8') as f:
        json.dump({'videos': [{k: p[k] for k in ('file', 'at', 'hash')} for p in parts],
                   **({'position': [0, position]} if position is not None else {})}, f, indent=1)
    return manifest, len(parts), len(todo)

def main():
    parser = argparse.ArgumentParser(description='Word-by-word animated captions as a transparent video.')
    parser.add_argument('edl', help='edl.json')
    parser.add_argument('-o', required=True, dest='out', help='video to write (.mov); a long timeline is written in parts, '
                        'listed in a .json of the same name')
    parser.add_argument('--transcripts', help='folder of the <clip>.words.json files (default: transcripts/ next to edl.json)')
    parser.add_argument('--words', type=int, help='at most N words on screen (default: captions.words, 3)')
    parser.add_argument('--config', help='settings file on top of config/defaults.json and the roughcut.json files found')
    args = parser.parse_args()
    folder = os.path.dirname(os.path.abspath(args.edl))
    cfg = settings.load(folder, args.config)
    edl, tl, _, clips = build(args.edl)
    style = cfg['captions'] if tl['height'] > tl['width'] else cfg['captions_main']  # a Short, or the main edit
    tdir = args.transcripts or os.path.join(folder, 'transcripts')
    cues = split_cues(edit_words(args.edl, clips, tdir), args.words or style['words'])  # timed on the sound of the edit
    total = float(tl['total_f'] * fd(tl['fps']))
    ass_path = os.path.splitext(os.path.abspath(args.out))[0] + '.ass'
    vertical = tl['height'] > tl['width']
    strip = None if vertical else band(tl['width'], tl['height'], style)  # a Short: the whole frame, as before
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(ass(cues, tl['width'], tl['height'], total, style, strip))
    done = ''
    if strip:  # the strip, in parts when the edit is long, listed with its place in the frame
        manifest, n, redone = render_parts(ass_path, args.out, tl['width'], strip[0], Fraction(tl['fps']), tl['total_f'], strip[2])
        done = f' (a strip of {strip[0]} px) in {n} part(s) ({redone} rendered), listed in {os.path.basename(manifest)}'
    else:
        render(ass_path, args.out, tl['width'], tl['height'], Fraction(tl['fps']), 0, tl['total_f'])
    print(f'{args.out}: {len(cues)} captions, {sum(len(c) for c in cues)} words, {total:.2f} s, '
          f'{tl["width"]}x{tl["height"]} with transparency{done} (and {os.path.basename(ass_path)})')

if __name__ == '__main__':
    main()
