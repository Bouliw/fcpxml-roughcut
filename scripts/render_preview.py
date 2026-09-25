#!/usr/bin/env python3
"""Quick preview of edl.json (1080p, or 1080x1920 if vertical), audio normalized to -14 LUFS.
Usage: render_preview.py edl.json -o preview.mp4"""
import sys, os, subprocess, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timeline import build

def encoder():
    enc = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True).stdout
    if 'h264_videotoolbox' in enc:
        return ['-c:v', 'h264_videotoolbox', '-b:v', '10M']
    return ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22']

def main():
    edl_path = sys.argv[1]
    out = sys.argv[sys.argv.index('-o') + 1]
    edl, tl, _, clips = build(edl_path)
    fps = tl['fps']
    vf = ('crop=ih*9/16:ih,scale=1080:1920' if edl.get('vertical') else 'scale=-2:1080') + f',fps={fps.numerator}/{fps.denominator},format=yuv420p'
    tmp = tempfile.mkdtemp(prefix='preview_')
    venc = encoder()
    try:
        listing = []
        for i, c in enumerate(clips):
            seg = os.path.join(tmp, f'seg_{i:04d}.mov')
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{c["in_s"]:.6f}', '-t', f'{c["dur_s"]:.6f}',
                            '-i', c['file'], '-map', '0:v:0', '-map', '0:a:0?', '-vf', vf, *venc,
                            '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2', seg], check=True)
            listing.append(f"file '{seg}'")
            print(f'\rsegments: {i + 1}/{len(clips)}', end='', flush=True)
        print()
        lst = os.path.join(tmp, 'list.txt')
        open(lst, 'w').write('\n'.join(listing) + '\n')
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst,
                        '-c:v', 'copy', '-af', 'loudnorm=I=-14:TP=-1.5:LRA=11', '-c:a', 'aac', '-b:a', '192k',
                        '-movflags', '+faststart', out], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    d = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', out],
                             capture_output=True, text=True).stdout)
    expected = sum(c['dur_s'] for c in clips)
    print(f'{out}: {d:.2f} s (expected {expected:.2f} s, difference {d - expected:+.2f} s)')

if __name__ == '__main__':
    main()
