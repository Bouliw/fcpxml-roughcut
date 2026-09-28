#!/usr/bin/env python3
"""Final Cut Pro helpers: check an FCPXML against the DTD shipped inside Final Cut Pro, and open it there.
Usage: fcp.py check file.fcpxml [...]   |   fcp.py open file.fcpxml
The app is Final Cut Pro, else Final Cut Pro Trial, in /Applications; $FCP_APP (name or .app path) overrides it."""
import argparse, os, re, shutil, subprocess, sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from urllib.parse import quote, unquote, urlparse

APPS = ('Final Cut Pro', 'Final Cut Pro Trial')
DTD_DIR = 'Contents/Frameworks/Interchange.framework/Versions/A/Resources'

def find_app():
    """Path of the Final Cut Pro app, or None."""
    names = [os.environ['FCP_APP']] if os.environ.get('FCP_APP') else APPS
    for n in names:
        for p in (n, f'/Applications/{n}.app'):
            if p.endswith('.app') and os.path.isdir(p):
                return p
    return None

WRITTEN = ('1.10', '1.11', '1.12', '1.13')  # the versions the scripts write: 1.10 is Final Cut Pro 10.6

def app_version(app):
    """'11.2' for an installed Final Cut Pro, or None."""
    import plistlib
    try:
        with open(os.path.join(app, 'Contents', 'Info.plist'), 'rb') as f:
            return plistlib.load(f).get('CFBundleShortVersionString')
    except (OSError, ValueError):
        return None

def best_version(app=None):
    """(version to write, app): the newest FCPXML this Final Cut Pro imports (it carries a DTD for each one) among those
    the scripts write; '1.13' when Final Cut Pro is not installed; None when it is too old for any of them."""
    app = app or find_app()
    if not app:
        return '1.13', None
    known = [v for v in WRITTEN if os.path.isfile(os.path.join(app, DTD_DIR, f'FCPXMLv{v.replace(".", "_")}.dtd'))]
    return (known[-1] if known else None), app

def fcpxml_file(path):
    """A .fcpxmld bundle exported by Final Cut Pro keeps its XML in Info.fcpxml."""
    return os.path.join(path, 'Info.fcpxml') if os.path.isdir(path) else path

def version_of(path):
    with open(path, encoding='utf-8') as f:
        m = re.search(r'<fcpxml\s[^>]*version="([\d.]+)"', f.read(4096))
    return m.group(1) if m else None

NTSC = (Fraction(30000, 1001), Fraction(60000, 1001), Fraction(24000, 1001))
STANDARD_RATES = NTSC + tuple(Fraction(n) for n in (24, 25, 30, 48, 50, 60, 100, 120))

def seconds(value):
    """FCPXML time ("3003/30000s", "12s") in seconds, exactly."""
    v = (value or '0s').rstrip('s')
    return Fraction(*map(int, v.split('/'))) if '/' in v else Fraction(v)

def media_problems(path):
    """What the DTD cannot see and makes Final Cut Pro import clips with no video ("Invalid edit with no respective
    media", "unexpected value tcFormat"): every clip must use an asset whose file exists, stay within the media that
    asset holds (picture and sound, split edits included), and a drop-frame timecode needs an NTSC frame rate. A list
    of plain problems, empty when the file is sound."""
    root = ET.parse(fcpxml_file(path)).getroot()
    rate = {f.get('id'): 1 / seconds(f.get('frameDuration')) for f in root.iter('format') if f.get('frameDuration')}
    assets, out = {}, []
    for a in root.iter('asset'):
        rep = a.find('media-rep')
        src = unquote(urlparse(rep.get('src')).path) if rep is not None else None
        if src and not os.path.exists(src):
            out.append(f'{a.get("name")}: its file is missing ({src})')
        assets[a.get('id')] = (seconds(a.get('start')), seconds(a.get('duration')), rate.get(a.get('format')), a.get('name'))
    for e in root.iter():
        if e.tag not in ('asset-clip', 'clip', 'video', 'audio') or e.get('ref') not in assets:
            continue
        start, length, fps, name = assets[e.get('ref')]
        slack = 1 / fps if fps else Fraction(1, 100)  # one frame of rounding
        a = seconds(e.get('start')) if e.get('start') else start
        b = a + seconds(e.get('duration'))
        where = f'{e.tag} "{e.get("name") or name}"'
        if a < start - slack or b > start + length + slack:
            out.append(f'{where}: uses {float(a - start):.3f}-{float(b - start):.3f} s of a clip of {float(length):.3f} s')
        if e.get('audioStart') is not None:
            a2 = seconds(e.get('audioStart'))
            b2 = a2 + seconds(e.get('audioDuration') or e.get('duration'))
            if a2 < start - slack or b2 > start + length + slack:
                out.append(f'{where}: its sound uses {float(a2 - start):.3f}-{float(b2 - start):.3f} s of a clip of {float(length):.3f} s')
        if e.get('tcFormat') == 'DF' and fps not in NTSC:
            out.append(f'{where}: drop-frame timecode on a clip at {float(fps or 0):.3f} fps (only NTSC rates have it)')
    probe = shutil.which('ffprobe')
    for a in root.iter('asset'):  # the rate declared must be the file's, or the cuts fall between its frames
        rep, fps = a.find('media-rep'), rate.get(a.get('format'))
        src = unquote(urlparse(rep.get('src')).path) if rep is not None else None
        if not (probe and fps and src and os.path.exists(src) and a.get('hasVideo') == '1'):
            continue
        r = subprocess.run([probe, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=r_frame_rate',
                            '-of', 'csv=p=0', src], capture_output=True, text=True)
        nominal = r.stdout.strip()
        if re.fullmatch(r'\d+/\d+', nominal) and Fraction(nominal) != fps and Fraction(nominal) in STANDARD_RATES:
            out.append(f'{a.get("name")}: declared at {float(fps):.3f} fps, the file is {float(Fraction(nominal)):.3f} fps')
    for s in root.iter('sequence'):
        fps = rate.get(s.get('format'))
        if s.get('tcFormat') == 'DF' and fps not in NTSC:
            out.append(f'a timeline at {float(fps or 0):.3f} fps has a drop-frame timecode')
    for spine in root.iter('spine'):  # a transition takes half its length of media past each clip it joins
        items = list(spine)
        for k, e in enumerate(items):
            if e.tag != 'transition' or not 0 < k < len(items) - 1:
                continue
            half = seconds(e.get('duration')) / 2
            before, after = items[k - 1], items[k + 1]
            for clip, need in ((before, 'after'), (after, 'before')):
                if clip.get('ref') not in assets:
                    continue
                start, length, fps, name = assets[clip.get('ref')]
                slack = 1 / fps if fps else Fraction(1, 100)
                a = seconds(clip.get('start')) if clip.get('start') else start
                b = a + seconds(clip.get('duration'))
                if (need == 'after' and b + half > start + length + slack) or (need == 'before' and a - half < start - slack):
                    out.append(f'transition at {float(seconds(e.get("offset"))):.2f} s: "{clip.get("name") or name}" has no '
                               f'media {need} the cut for it')
    return out + effect_problems(root)

TEMPLATE_FOLDERS = ('Templates.localized', 'PETemplates.localized', 'METemplates.localized')

def effect_problems(root, app=None):
    """Titles and effects the file names that the installed Final Cut Pro does not have (it would import them as
    missing, in red): a template is looked for among the ones it ships, an FxPlug effect among its own."""
    app = app or find_app()
    if not app:
        return []
    base = os.path.join(app, 'Contents', 'PlugIns', 'MediaProviders', 'MotionEffect.fxp', 'Contents', 'Resources')
    fxplugs = None
    out = []
    for e in root.iter('effect'):
        uid = e.get('uid') or ''
        if uid.startswith('.../'):
            if not any(os.path.exists(os.path.join(base, f, uid[4:])) for f in TEMPLATE_FOLDERS):
                out.append(f'{e.get("name")}: this Final Cut Pro has no such template ({uid})')
        elif uid.startswith('FxPlug:'):
            if fxplugs is None:
                try:
                    with open(os.path.join(app, 'Contents', 'Resources', 'PEFxPlugSettings.plist'), 'rb') as f:
                        fxplugs = f.read().decode('utf-8', 'replace')
                except OSError:
                    fxplugs = ''
            if fxplugs and uid[7:] not in fxplugs:
                out.append(f'{e.get("name")}: this Final Cut Pro has no such effect ({uid})')
    return out

def check(path):
    """(True, message) if valid, (False, errors) if not, (None, reason) if the DTD cannot be checked. Beyond the DTD,
    every clip must use existing media within its limits (media_problems)."""
    path = fcpxml_file(path)
    version, app = version_of(path), find_app()
    if not version:
        return False, f'{path}: no <fcpxml version="..."> element'
    problems = media_problems(path)
    if problems:
        return False, 'clips Final Cut Pro would import without media:\n' + '\n'.join(problems[:20]) + \
            (f'\n... and {len(problems) - 20} more' if len(problems) > 20 else '')
    if not app:
        return None, 'not checked: Final Cut Pro not found (set FCP_APP)'
    dtd = os.path.join(app, DTD_DIR, f'FCPXMLv{version.replace(".", "_")}.dtd')
    if not os.path.isfile(dtd):
        return None, f'not checked: {os.path.basename(app)} has no DTD for FCPXML {version}'
    if not shutil.which('xmllint'):
        return None, 'not checked: xmllint not found'
    # xmllint reads the DTD as a URL: the spaces in "Final Cut Pro.app" must be escaped
    r = subprocess.run(['xmllint', '--noout', '--dtdvalid', 'file://' + quote(dtd), path], capture_output=True, text=True)
    if r.returncode:
        return False, r.stderr.strip()
    return True, f'valid against the FCPXML {version} DTD of {os.path.basename(app)[:-4]}, media checked'

def open_in_fcp(path):
    """Opens the file in Final Cut Pro, which starts the import. Refuses a file that fails the DTD check."""
    ok, msg = check(path)
    if ok is False:
        raise SystemExit(f'Not opened, the FCPXML is invalid:\n{msg}')
    app = find_app()
    if not app:
        raise SystemExit('Final Cut Pro not found (set FCP_APP to its name or path)')
    subprocess.run(['open', '-a', app, path], check=True)
    return app

def main():
    parser = argparse.ArgumentParser(description='Checks FCPXML files against the DTD shipped with Final Cut Pro, or opens one there.')
    parser.add_argument('action', choices=['check', 'open'])
    parser.add_argument('files', nargs='+', help='.fcpxml files or .fcpxmld bundles')
    args = parser.parse_args()
    status = 0
    for p in args.files:
        if args.action == 'open':
            print(f'{p}: opened in {os.path.basename(open_in_fcp(p))[:-4]}')
            continue
        ok, msg = check(p)
        print(f'{p}: {msg}')
        status = max(status, {True: 0, None: 2, False: 1}[ok])
    sys.exit(status)

if __name__ == '__main__':
    main()
