#!/usr/bin/env python3
"""Quick preview of edl.json (1080p, or 1080x1920 if vertical), audio normalized to -14 LUFS.
Usage: render_preview.py edl.json -o preview.mp4"""
import argparse, sys, os, subprocess, tempfile, shutil, json, re, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timeline import build
import broll, loudness, reframe, settings, splitedit, zoom
import music as tracks
from timeline import fd

def encoder():
    enc = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True).stdout
    if 'h264_videotoolbox' in enc:
        return ['-c:v', 'h264_videotoolbox', '-b:v', '10M']
    return ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22']

LOUDNORM = 'loudnorm=I=-14:TP=-1.5:LRA=11'

def loudnorm_filter(lst):
    """Two-pass loudnorm: measure the whole preview, then correct it with one linear gain that lands on the
    target. A single pass adapts on the fly and can end a dB short on real speech. `lst` is a concat list, or an
    audio file."""
    source = ['-i', lst] if not lst.endswith('.txt') else ['-f', 'concat', '-safe', '0', '-i', lst]
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', *source, '-vn',
                        '-af', LOUDNORM + ':print_format=json', '-f', 'null', '-'], capture_output=True, text=True)
    m = re.search(r'\{[^{}]*"input_i"[^{}]*\}', r.stderr)
    if r.returncode or not m:
        return LOUDNORM
    d = json.loads(m.group(0))
    if not all(math.isfinite(float(d[k])) for k in ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset')):
        return LOUDNORM  # silence: nothing to measure
    return (f"{LOUDNORM}:measured_I={d['input_i']}:measured_TP={d['input_tp']}:measured_LRA={d['input_lra']}"
            f":measured_thresh={d['input_thresh']}:offset={d['target_offset']}:linear=true")

def main():
    parser = argparse.ArgumentParser(description='Quick preview of edl.json, audio normalized to -14 LUFS.')
    parser.add_argument('edl', help='edl.json')
    parser.add_argument('-o', required=True, dest='out', help='preview file to write (.mp4)')
    parser.add_argument('--level-audio', action='store_true', help='apply the dialogue gains of make_fcpxml.py --level-audio')
    parser.add_argument('--zoom-jump-cuts', action='store_true', help='show the zooms of make_fcpxml.py --zoom-jump-cuts')
    parser.add_argument('--follow-face', action='store_true', help='show the reframing of make_fcpxml.py --follow-face')
    parser.add_argument('--split-edits', action='store_true', help='play the J and L cuts of make_fcpxml.py --split-edits')
    parser.add_argument('--overlay', help='video with transparency laid over the preview, as make_fcpxml.py --overlay does (captions.py)')
    parser.add_argument('--analysis', help='folder of the analyze.py results (default: analysis/ next to edl.json)')
    parser.add_argument('--config', help='settings file on top of config/defaults.json and the roughcut.json files found')
    args = parser.parse_args()
    out = args.out
    edl, tl, _, clips = build(args.edl)
    gains = [None] * len(clips)
    if args.level_audio:
        cache = loudness.Cache(os.path.join(os.path.dirname(os.path.abspath(args.edl)), 'cache', 'loudness.json'))
        gains = [g for g, _, _ in loudness.gains(clips, settings.load(os.path.dirname(os.path.abspath(args.edl)), args.config), cache)]
        cache.save()
    zooms, follows = [(1, (0.0, 0.0), None)] * len(clips), [None] * len(clips)
    if args.zoom_jump_cuts or args.follow_face:
        cfg = settings.load(os.path.dirname(os.path.abspath(args.edl)), args.config)
        folder = args.analysis or os.path.join(os.path.dirname(os.path.abspath(args.edl)), 'analysis')
        analyses = zoom.load_analyses(folder, {c['file'] for c in clips})
        if args.zoom_jump_cuts:
            zooms = zoom.plan(clips, cfg, analyses, tl['width'], tl['height'], edl.get('vertical'))
        if args.follow_face and edl.get('vertical'):
            follows = [reframe.follow(c, analyses.get(c['file'], []), tl['width'], tl['height'], cfg) for c in clips]
    expected = sum(c['dur_s'] for c in clips)
    fps = tl['fps']
    # Every clip ends up at the same size: a clip of another shape (a phone screen in a horizontal video) is
    # letterboxed, or cropped to fill a vertical Short, as Final Cut Pro does
    w, h = (1080, 1920) if edl.get('vertical') else (2 * round(tl['width'] * 540 / tl['height']), 1080)
    fit = f'increase,crop={w}:{h}' if edl.get('vertical') else f'decrease,pad={w}:{h}:-1:-1'
    vf = f'scale={w}:{h}:force_original_aspect_ratio={fit},setsar=1,fps={fps.numerator}/{fps.denominator},format=yuv420p'
    def follow_vf(keys, c):  # the vertical crop moves with the keyframes of --follow-face
        x = reframe.crop_x(keys, w, h, c['asset']['width'], c['asset']['height'])
        return (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}:x='{x}':y=(ih-{h})/2,"
                f"setsar=1,fps={fps.numerator}/{fps.denominator},format=yuv420p")
    splits = [(0, 0)] * len(clips)
    if args.split_edits:
        cfg_ = settings.load(os.path.dirname(os.path.abspath(args.edl)), args.config)
        tdir = os.path.join(os.path.dirname(os.path.abspath(args.edl)), 'transcripts')
        splits, _ = splitedit.plan(clips, cfg_, splitedit.load_words(clips, tdir), float(fps))
    split = any(l or t for l, t in splits)  # then picture and sound are cut in different places: rendered apart
    tmp = tempfile.mkdtemp(prefix='preview_')
    venc = encoder()
    try:
        listing, sounds = [], []
        for i, (c, gain, (scale, move, _), keys) in enumerate(zip(clips, gains, zooms, follows)):
            seg = os.path.join(tmp, f'seg_{i:04d}.mov')
            level = ['-af', f'volume={gain}dB'] if gain else []  # the same gain as in the FCPXML
            dur = f'{c["dur_s"]:.6f}'
            picture = zoom.crop_filter(scale, move, tl['width'], tl['height']) + (follow_vf(keys, c) if keys else vf)
            if split:
                lead, tail = (x / float(fps) for x in splits[i])
                a_in, a_dur = c['in_s'] - lead, c['dur_s'] + lead + tail
                subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{c["in_s"]:.6f}', '-t', dur,
                                '-i', c['file'], '-map', '0:v:0', '-an', '-vf', picture, *venc, seg], check=True)
                wav = os.path.join(tmp, f'snd_{i:04d}.wav')
                source = (['-ss', f'{a_in:.6f}', '-t', f'{a_dur:.6f}', '-i', c['file'], '-map', '0:a:0'] if c['asset']['audio_channels']
                          else ['-f', 'lavfi', '-t', f'{a_dur:.6f}', '-i', 'anullsrc=r=48000:cl=stereo'])
                subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *source, *level,
                                '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2', wav], check=True)
                listing.append(f"file '{seg}'")
                sounds.append(f"file '{wav}'")
                print(f'\rsegments: {i + 1}/{len(clips)}', end='', flush=True)
                continue
            if c['asset']['audio_channels']:
                silence, audio = [], '0:a:0'
            else:  # a clip without sound gets silence, otherwise the sound of the next clips would play early
                silence, audio = ['-f', 'lavfi', '-t', dur, '-i', 'anullsrc=r=48000:cl=stereo'], '1:a:0'
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{c["in_s"]:.6f}', '-t', dur,
                            '-i', c['file'], *silence, '-map', '0:v:0', '-map', audio, '-vf', picture, *venc,
                            *level, '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2', seg], check=True)
            listing.append(f"file '{seg}'")
            print(f'\rsegments: {i + 1}/{len(clips)}', end='', flush=True)
        print()
        lst = os.path.join(tmp, 'list.txt')
        with open(lst, 'w', encoding='utf-8') as f:
            f.write('\n'.join(listing) + '\n')
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        # loudnorm works at 192 kHz internally and pads the end: back to 48 kHz, cut at the timeline length
        video = ['-c:v', 'copy']
        folder = os.path.dirname(os.path.abspath(args.edl))
        cfg = settings.load(folder, args.config)
        music = tracks.plan(edl, clips, tl, folder, cfg)
        places = broll.placements(edl, clips, folder, fps, music and music['beats'], cfg['music']['snap_window_seconds'])
        sound, audio_map = lst, '0:a'
        if split:  # the sound, cut where the J and L cuts put it
            alst = os.path.join(tmp, 'sounds.txt')
            with open(alst, 'w', encoding='utf-8') as f:
                f.write('\n'.join(sounds) + '\n')
            sound = os.path.join(tmp, 'split.wav')
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', alst,
                            '-c:a', 'pcm_s16le', sound], check=True)
        if music:  # the music mixed under the talking head, ducking as in the FCPXML
            base_sound = ['-f', 'concat', '-safe', '0', '-i', lst] if sound == lst else ['-i', sound]
            sound = os.path.join(tmp, 'mix.wav')
            keys = [(music['at'] + t, v) for t, v in music['keys']]
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *base_sound,
                            '-ss', f'{music["in"]:.6f}', '-t', f'{music["dur"]:.6f}', '-i', music['file'], '-filter_complex',
                            f'[1:a]aresample=48000,aformat=channel_layouts=stereo,adelay={int(music["at"] * 1000)}:all=1,'
                            f"volume='{tracks.volume_expr(keys)}':eval=frame[m];[0:a][m]amix=inputs=2:normalize=0:duration=first[a]",
                            '-map', '[a]', '-c:a', 'pcm_s16le', sound], check=True)
        if places or args.overlay:
            # B-roll over the picture (its sound left out, as in the FCPXML), then the captions on top
            inputs, graph, last = [], [], '0:v'
            for k, (at_f, _, path, b_in, b_out, _, _) in enumerate(places, 1):
                at, dur = float(at_f * fd(fps)), b_out - b_in
                inputs += ['-ss', f'{b_in:.6f}', '-t', f'{dur:.6f}', '-i', path]
                graph.append(f'[{k}:v]{vf},setpts=PTS-STARTPTS+{at:.6f}/TB[b{k}];'
                             f"[{last}][b{k}]overlay=enable='between(t,{at:.6f},{at + dur:.6f})':eof_action=pass[v{k}]")
                last = f'v{k}'
            if args.overlay:
                n = len(places) + 1
                inputs += ['-i', args.overlay]
                graph.append(f'[{n}:v]scale={w}:{h},format=rgba[o];[{last}][o]overlay=format=auto:eof_action=pass[vo]')
                last = 'vo'
            video = [*inputs, '-filter_complex', ';'.join(graph), '-map', f'[{last}]', *venc]
        else:
            video = ['-map', '0:v', *video]
        if sound != lst:
            n_inputs = 1 + len(places) + (1 if args.overlay else 0)
            video = [*video[:video.index('-map')], '-i', sound, *video[video.index('-map'):]]
            audio_map = f'{n_inputs}:a'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst,
                        *video, '-map', audio_map, '-af', loudnorm_filter(sound), '-ar', '48000', '-c:a', 'aac', '-b:a', '192k',
                        '-t', f'{expected:.6f}', '-movflags', '+faststart', out], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    d = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', out],
                             capture_output=True, text=True).stdout)
    print(f'{out}: {d:.2f} s (expected {expected:.2f} s, difference {d - expected:+.2f} s)')

if __name__ == '__main__':
    main()
