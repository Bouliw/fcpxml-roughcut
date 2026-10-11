#!/usr/bin/env python3
"""One chosen visual -> a clip Final Cut Pro takes as it is: H.264, full-bleed (cover-crop, or blurred fill), the
timeline's size and frame rate, silent unless asked, Rec. 709, with HANDLE seconds past the beat so place_broll.py
can stretch it to meet the next one. Stills get the sub-pixel zoom of zoom_still.py (or none with --static).
Usage: prepare_clip.py SRC OUT.mp4 --seconds 3 [--in 12.5] [--match rush.mp4 | --fps 25 --size 1920x1080]
                       [--blurfill] [--static] [--rate 0.015] [--handle 3] [--credit "Text"] [--credit-color auto]
                       [--keep-audio]
--match reads the frame rate and size of the talking-head rush (what Roughcut builds its timeline from).
--credit lays the source's name bottom-right (white or black picked for contrast with --credit-color auto)."""
import argparse, json, os, subprocess, sys, tempfile
from fractions import Fraction
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zoom_still import parse_fps, render as render_still

STILL_CODECS = {'png', 'mjpeg', 'jpeg2000', 'webp', 'bmp', 'tiff', 'gif', 'svg'}
STANDARD = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
            Fraction(50), Fraction(60000, 1001), Fraction(60)]

def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', path],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'ffprobe cannot read {path}: {r.stderr.strip()}')
    d = json.loads(r.stdout)
    v = next((s for s in d['streams'] if s.get('codec_type') == 'video'), None)
    if not v:
        raise SystemExit(f'No picture in {path}')
    return d, v

def is_still(path, d, v):
    if os.path.splitext(path)[1].lower() in ('.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.bmp', '.heic'):
        return True
    return v.get('codec_name') in STILL_CODECS and float(d['format'].get('duration') or 0) < 0.5

def timeline_of(path):
    """(fps, (w, h)) of a talking-head rush, upright, its rate snapped to a standard one."""
    _, v = probe(path)
    rate = Fraction(v.get('r_frame_rate') or '30')
    fps = min(STANDARD, key=lambda s: abs(s - rate))
    w, h = int(v['width']), int(v['height'])
    rot = next((int(sd['rotation']) for sd in v.get('side_data_list') or [] if 'rotation' in sd), 0)
    return fps, ((h, w) if abs(rot) in (90, 270) else (w, h))

def credit_png(text, size, color, sample=None):
    """Transparent frame-size PNG with the credit bottom-right, ~70 % opacity; colour picked on the sample frame."""
    from PIL import Image, ImageDraw, ImageFont, ImageStat
    W, H = size
    fs = max(18, round(H * 0.022))
    font = None
    for f in ('/System/Library/Fonts/Supplemental/Arial.ttf', '/System/Library/Fonts/Helvetica.ttc',
              '/Library/Fonts/Arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if os.path.exists(f):
            font = ImageFont.truetype(f, fs)
            break
    font = font or ImageFont.load_default(size=fs)
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=font)
    m = round(H * 0.03)
    x, y = W - m - (x1 - x0), H - m - (y1 - y0)
    if color == 'auto':
        lum = 128
        if sample:
            region = Image.open(sample).convert('L').resize((W, H)).crop((x - 10, y - 10, W - m + 10, H - m + 10))
            lum = ImageStat.Stat(region).mean[0]
        color = 'black' if lum > 150 else 'white'
    rgb = (255, 255, 255) if color == 'white' else (0, 0, 0)
    d.text((x - x0, y - y0), text, font=font, fill=rgb + (178,))
    return im

def main():
    p = argparse.ArgumentParser(description='A chosen visual as a clip Final Cut Pro takes as it is.')
    p.add_argument('src')
    p.add_argument('out')
    p.add_argument('--seconds', type=float, required=True, help='time on screen')
    p.add_argument('--in', dest='start', type=float, default=0.0, help='start in the source video (s)')
    p.add_argument('--handle', type=float, default=3.0, help='extra seconds past the beat (default 3)')
    p.add_argument('--match', help='talking-head rush whose frame rate and size to use')
    p.add_argument('--fps', default='30')
    p.add_argument('--size', default='1920x1080')
    p.add_argument('--blurfill', action='store_true', help='fit over a blurred copy instead of cover-crop')
    p.add_argument('--static', action='store_true', help='stills: no zoom')
    p.add_argument('--rate', type=float, default=0.015, help='stills: zoom per second (default 1.5 %%, cap 2 %%)')
    p.add_argument('--credit', help='source name to show bottom-right')
    p.add_argument('--credit-color', choices=('auto', 'white', 'black'), default='auto')
    p.add_argument('--keep-audio', action='store_true', help="keep the clip's sound (Roughcut lays it at -96 dB anyway)")
    a = p.parse_args()
    fps, (W, H) = timeline_of(a.match) if a.match else (parse_fps(a.fps), tuple(map(int, a.size.split('x'))))
    d, v = probe(a.src)
    still = is_still(a.src, d, v)
    length = a.seconds + a.handle
    if not still:
        avail = float(d['format'].get('duration') or 0) - a.start
        if avail < a.seconds:
            raise SystemExit(f'{a.src}: only {avail:.2f} s after {a.start} s, {a.seconds} s needed')
        length = min(length, avail)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = tempfile.mkdtemp()
    overlay = None
    if a.credit:
        sample = os.path.join(tmp, 'sample.png')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', *(['-ss', str(a.start + a.seconds / 2)] if not still else []),
                        '-i', a.src, '-frames:v', '1', sample], check=True)
        overlay = os.path.join(tmp, 'credit.png')
        credit_png(a.credit, (W, H), a.credit_color, sample).save(overlay)
    if still:
        src = a.src
        if os.path.splitext(src)[1].lower() in ('.heic', '.svg', '.tif', '.tiff', '.webp'):
            src = os.path.join(tmp, 'still.png')  # through ffmpeg/ImageMagick first: PIL may not read it
            if subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', a.src, '-frames:v', '1', src]).returncode:
                subprocess.run(['convert', a.src + '[0]', src], check=True)
        render_still(src, a.out, length, 0 if a.static else min(a.rate, 0.02), fps, (W, H), a.blurfill, overlay)
    else:
        fit = (f'[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=40[bg];'
               f'[b]scale={round(W*0.88)}:{round(H*0.88)}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2'
               if a.blurfill else f'[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}')
        graph = f'{fit},fps={fps.numerator}/{fps.denominator},setsar=1,format=yuv420p[v0]'
        graph += ';[v0][1:v]overlay=0:0[v]' if overlay else ';[v0]null[v]'
        cmd = ['ffmpeg', '-v', 'error', '-y', '-ss', str(a.start), '-t', f'{length:.3f}', '-i', a.src,
               *(['-i', overlay] if overlay else []), '-filter_complex', graph, '-map', '[v]',
               *(['-map', '0:a?', '-c:a', 'aac', '-b:a', '192k'] if a.keep_audio else ['-an']),
               '-c:v', 'libx264', '-crf', '16', '-preset', 'slow', '-pix_fmt', 'yuv420p',
               '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-movflags', '+faststart', a.out]
        subprocess.run(cmd, check=True)
    print(f'{a.out}  {length:.2f} s at {float(fps):.3f} fps, {W}x{H}' + (' (still)' if still else ''))

if __name__ == '__main__':
    main()
