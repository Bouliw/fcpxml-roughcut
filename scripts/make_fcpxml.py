#!/usr/bin/env python3
"""Builds a frame-accurate FCPXML timeline (1.13 by default) from edl.json, checks it against the DTD shipped
with Final Cut Pro, and can open it there.
Usage: make_fcpxml.py edl.json -o project.fcpxml [--fcpxml-version 1.10] [--level-audio] [--fades] [--zoom-jump-cuts] [--follow-face] [--overlay captions.mov|captions.json] [--split-edits]
       [--dressing none|light|full] [--language fr] [--config file.json] [--open]"""
import argparse, json, sys, os
from fractions import Fraction
from urllib.parse import quote
from xml.sax.saxutils import quoteattr as qa
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timeline
from timeline import build, rt, fd, frames, tc_frames, disk_path, COLOR_SPACES, REC709
import broll, dressing, fcp, loudness, reframe, settings, splitedit, zoom
import music as tracks

# Final Cut Pro's own format names: FFVideoFormat1080p2997, FFVideoFormat720p5994, FFVideoFormat3840x2160p5994...
NAMES = {Fraction(24000, 1001): '2398', Fraction(24): '24', Fraction(25): '25', Fraction(30000, 1001): '2997',
         Fraction(30): '30', Fraction(50): '50', Fraction(60000, 1001): '5994', Fraction(60): '60'}
SIZES = {(1920, 1080): '1080', (1280, 720): '720', (3840, 2160): '3840x2160', (4096, 2160): '4096x2160'}
AUDIO_RATES = {32000: '32k', 44100: '44.1k', 48000: '48k', 88200: '88.2k', 96000: '96k', 176400: '176.4k', 192000: '192k'}
# Everything written here exists in all these versions: 1.10 opens in Final Cut Pro 10.6 and later, 1.13 in 11
VERSIONS = ('1.10', '1.11', '1.12', '1.13')

def fmt_name(w, h, fps):
    if (w, h) in SIZES and Fraction(fps) in NAMES:
        return f' name="FFVideoFormat{SIZES[w, h]}p{NAMES[Fraction(fps)]}"'
    return ''  # other sizes (vertical Shorts...): Final Cut Pro builds a custom format from width, height and rate

class Resources:
    """The formats and assets of an FCPXML, with their ids. Also used by organize.py."""
    def __init__(self):
        self.lines, self.formats, self.assets, self.next = [], {}, {}, 1

    def _id(self):
        self.next += 1
        return f'r{self.next - 1}'

    def format(self, w, h, f, space=REC709):
        """A format, shared by the timeline and the clips that match it; a clip in another colour space gets its own."""
        key = (w, h, Fraction(f), space)
        if key not in self.formats:
            self.formats[key] = self._id()
            self.lines.append(f'    <format id="{self.formats[key]}"{fmt_name(w, h, f)} frameDuration="{rt(1, f)}" '
                              f'width="{w}" height="{h}" colorSpace="{space}"/>')
        return self.formats[key]

    def effect(self, name, uid):
        """A Motion template or an effect of Final Cut Pro's, once."""
        if uid not in self.assets:
            self.assets[uid] = self._id()
            self.lines.append(f'    <effect id="{self.assets[uid]}" name={qa(name)} uid={qa(uid)}/>')
        return self.assets[uid]

    def audio_asset(self, a):
        """Adds an audio-only source (music), timed in its own samples."""
        aid = self.assets[a['file']] = self._id()
        a['start_f'], a['tcf'] = 0, 'NDF'
        dur = Fraction(round(a['duration'] * a['audio_rate']), a['audio_rate'])
        self.lines.append(f'    <asset id="{aid}" name={qa(os.path.splitext(os.path.basename(a["file"]))[0])} start="0s" '
                          f'duration="{dur.numerator}/{dur.denominator}s" hasAudio="1" audioSources="1" '
                          f'audioChannels="{a["audio_channels"]}" audioRate="{a["audio_rate"]}">\n'
                          f'      <media-rep kind="original-media" src="file://{quote(a["file"])}"/>\n    </asset>')
        return aid

    def asset(self, a):
        """Adds a probed clip; sets a['start_f'] (camera timecode), a['tcf'], a['dur_f'] in its own frames and a['format_id']."""
        space = a.get('color_space') or COLOR_SPACES.get(a.get('hdr'), REC709)  # the clip's own: Final Cut Pro converts it
        af = a['format_id'] = self.format(a['width'], a['height'], a['fps'], space)
        start_f, drop = tc_frames(a['timecode'], a['fps'])
        a['start_f'], a['tcf'] = start_f, 'DF' if drop else 'NDF'
        a['dur_f'] = a.get('frames') or frames(a['duration'], a['fps'])
        aid = self.assets[a['file']] = self._id()
        audio = (f' hasAudio="1" audioSources="1" audioChannels="{a["audio_channels"]}" audioRate="{a["audio_rate"]}"'
                 if a['audio_channels'] else '')
        self.lines.append(f'    <asset id="{aid}" name={qa(os.path.splitext(os.path.basename(a["file"]))[0])} '
                          f'start="{rt(start_f, a["fps"])}" duration="{rt(a["dur_f"], a["fps"])}" hasVideo="1" format="{af}"{audio}>\n'
                          f'      <media-rep kind="original-media" src="file://{quote(a["file"])}"/>\n    </asset>')
        return aid

def mmss(sec):
    m, s = divmod(int(round(sec)), 60)
    h, m = divmod(m, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'

def fill_scale(w, h, width, height):
    """The scale that makes a w x h picture, fitted into a width x height frame, fill it (4K into a vertical frame:
    3.1605)."""
    fit, fill = min(width / w, height / h), max(width / w, height / h)
    return round(fill / fit, 4)

def fade_frames(c, cfg, fps):
    """Frames of the fade in and out of a cut's sound: a few at every cut, so no cut clicks nor jumps in room tone;
    longer for a moment played for its own sound, which comes in and out softly under the hard cut of the picture."""
    a = cfg['audio']
    seconds = a['ambient_fade_seconds'] if c.get('role') == 'effects' else a['fade_seconds']
    return min(max(1, round(seconds * fps)), c['dur_f'] // 3)

def voice_isolation(c, cfg, cache, tdir):
    """The amount of Final Cut Pro's Voice Isolation for a cut, 0 for none: audio.voice_isolation "auto" (the
    default) sets it on a clip whose background is loud (the voice less than voice_isolation_below_db above it:
    walking, among people, not an interview somewhere quiet), at voice_isolation_amount, never more; "always"
    on every one; "off", or 0, on none; a number is that amount everywhere."""
    a = cfg['audio']
    mode = a['voice_isolation']
    if mode in ('off', 0, False, None, ''):
        return 0
    if isinstance(mode, (int, float)) and not isinstance(mode, bool):
        return float(mode)
    amount = min(float(a['voice_isolation_amount']), 100.0)
    if mode == 'always':
        return amount
    if c['file'] not in cache:  # "auto": how loud the background of this clip is
        cache[c['file']] = None
        try:
            import hesitations
            path = timeline.transcript_path(tdir, c['file'])
            with open(path, encoding='utf-8') as f:
                words = json.load(f)['words']
            cache[c['file']] = hesitations.speech_over_background(hesitations.Sound(hesitations.read(c['file'])), words)
        except (ImportError, OSError, ValueError, KeyError, SystemExit):
            pass
    snr = cache[c['file']]
    return amount if snr is not None and snr < a['voice_isolation_below_db'] else 0

def main():
    parser = argparse.ArgumentParser(description='Builds a frame-accurate FCPXML timeline from edl.json.')
    parser.add_argument('edl', help='edl.json')
    parser.add_argument('-o', required=True, dest='out', help='FCPXML file to write')
    parser.add_argument('--fcpxml-version', choices=VERSIONS, default='1.13',
                        help='FCPXML version (default 1.13, Final Cut Pro 11 or later; 1.10 for 10.6 and later)')
    parser.add_argument('--fades', action='store_true',
                        help='short audio fades at every cut, longer on moments played for their own sound (role effects)')
    parser.add_argument('--level-audio', action='store_true',
                        help='measure the dialogue of every cut and set a gain per cut towards one level (adjust-volume)')
    parser.add_argument('--zoom-jump-cuts', action='store_true',
                        help='scale up every other cut of a take, anchored on the face (adjust-transform)')
    parser.add_argument('--follow-face', action='store_true',
                        help='vertical Short: the frame follows the face across the horizontal picture (position keyframes)')
    parser.add_argument('--split-edits', action='store_true',
                        help='J and L cuts where the source changes, never cutting or bringing in a word')
    parser.add_argument('--overlay', help='video with transparency laid over the whole timeline as a connected clip, or '
                        'the .json list of such videos and where each starts (captions.py)')
    parser.add_argument('--dressing', choices=sorted(dressing.LEVELS), default='none',
                        help='the main edit dressed in Final Cut Pro\'s own titles and dissolves: light (a title and a '
                        'dissolve where each chapter starts) or full (also the time of day and the lower thirds)')
    parser.add_argument('--language', help='language of the titles written (the time of day, the lower thirds\' '
                        'placeholders); default: ui.language')
    parser.add_argument('--analysis', help='folder of the analyze.py results, for the faces (default: analysis/ next to edl.json)')
    parser.add_argument('--config', help='settings file on top of config/defaults.json and the roughcut.json files found')
    parser.add_argument('--open', action='store_true', help='open the file in Final Cut Pro, which starts the import '
                        '(Final Cut Pro asks which library to import into)')
    args = parser.parse_args()
    out = args.out
    edl, tl, assets, clips = build(args.edl)
    fps = tl['fps']
    cfg = settings.load(os.path.dirname(os.path.abspath(args.edl)), args.config)
    levels = [(None, None, None)] * len(clips)
    if args.level_audio:
        cache = loudness.Cache(os.path.join(os.path.dirname(os.path.abspath(args.edl)), 'cache', 'loudness.json'))
        levels = loudness.gains(clips, cfg, cache)
        cache.save()
    zooms = [(1, (0.0, 0.0), None)] * len(clips)
    folder = args.analysis or os.path.join(os.path.dirname(os.path.abspath(args.edl)), 'analysis')
    analyses = zoom.load_analyses(folder, [a['file'] for a in assets]) if args.zoom_jump_cuts or args.follow_face else {}
    if args.zoom_jump_cuts:
        zooms = zoom.plan(clips, cfg, analyses, tl['width'], tl['height'], edl.get('vertical'))
    splits, split_notes = [(0, 0)] * len(clips), []
    if args.split_edits:
        folder_ = os.path.dirname(os.path.abspath(args.edl))
        splits, split_notes = splitedit.plan(clips, cfg, splitedit.load_words(clips, os.path.join(folder_, 'transcripts')), float(fps))
    follows = [None] * len(clips)
    if args.follow_face and edl.get('vertical'):
        follows = [reframe.follow(c, analyses.get(c['file'], []), tl['width'], tl['height'], cfg) for c in clips]
    res = Resources()
    seq_fmt = res.format(tl['width'], tl['height'], fps)
    for a in assets:
        res.asset(a)
    asset_ids = res.assets
    folder = os.path.dirname(os.path.abspath(args.edl))
    music = tracks.plan(edl, clips, tl, folder, cfg)
    beats_tl = music and music['beats']
    if music:
        music['at_f'] = frames(music['at'], fps)
        music['parent'] = max(i for i, c in enumerate(clips) if c['off_f'] <= music['at_f'])
        res.audio_asset(music['asset'])
    places = broll.placements(edl, clips, folder, fps, beats_tl, cfg['music']['snap_window_seconds'])
    broll_assets = {}
    for p in places:
        if p[2] not in broll_assets:
            broll_assets[p[2]] = timeline.probe(p[2])
            res.asset(broll_assets[p[2]])
    top_lane = max([p[6] for p in places] or [0])
    overlays, overlay_at = [], None  # [(probed video, timeline frame it starts at, index of the clip it is connected to)]
    if args.overlay:
        path = disk_path(os.path.abspath(args.overlay))
        if path.endswith('.json'):  # parts, and where in the frame (a strip of it)
            with open(path, encoding='utf-8') as f:
                listed = json.load(f)
            parts = [(os.path.join(os.path.dirname(path), o['file']), o['at']) for o in listed['videos']]
            overlay_at = listed.get('position')
        else:
            parts = [(path, 0.0)]
        for file, at in parts:
            o = timeline.probe(file)
            res.asset(o)
            at_f = frames(at, fps)
            overlays.append((o, at_f, max(i for i, c in enumerate(clips) if c['off_f'] <= max(at_f, clips[0]['off_f']))))

    # the dressing of the main edit: titles and dissolves of Final Cut Pro's own
    feats = set() if edl.get('vertical') else dressing.features(args.dressing)
    dcfg, lang = cfg['dressing'], args.language or cfg['ui']['language']
    fade_at = dressing.dissolves(clips, dcfg['dissolve_seconds'], float(fps)) if 'fades' in feats else {}  # {clip: frames}
    for i in fade_at:  # a dissolve blends the sound too: no split edit on either side of it
        splits[i - 1] = (splits[i - 1][0], 0)
        splits[i] = (0, splits[i][1])
    titles = []  # (clip index, frames into it, seconds, template, texts, name, position, scale, lane above the rest)
    if 'titles' in feats:
        titles += [(i, 0, dcfg['title_seconds'], dressing.TITLE, [c['chapter']], c['chapter'], None, None, 1)
                   for i, c in enumerate(clips) if c.get('chapter') and i >= dressing.story_start(clips)]
    if 'times' in feats:
        titles += [(i, 0, dcfg['time_seconds'], dressing.TITLE, [text], text, dcfg['time_position'], dcfg['time_scale'], 2)
                   for i, text in dressing.time_labels(clips, dcfg, lang)]
    if 'lower_thirds' in feats:
        name, place = dressing.PLACEHOLDERS.get(lang, dressing.PLACEHOLDERS['en'])
        titles += [(i, frames(at, fps), dcfg['lower_third_seconds'], dressing.LOWER_THIRD, [name, place],
                    ('Bandeau' if lang == 'fr' else 'Lower third') + (f' – {who}' if who else ''), None, None, 3)
                   for i, at, who in dressing.lower_thirds(clips, edl.get('interviews', []))]
    for t in titles:
        res.effect(*t[3])
    if fade_at:
        res.effect(*dressing.CROSS_DISSOLVE)

    spine, chapters = [], []
    tl_markers = []
    for m in sorted(edl.get('markers', []), key=lambda m: m['at']):
        mf = frames(m['at'], fps)
        if mf == tl['total_f']:
            mf -= 1  # a marker "at the end" goes on the last frame
        if 0 <= mf < tl['total_f']:
            tl_markers.append((mf, m.get('text', ''), bool(m.get('todo'))))
        else:
            print(f"WARNING: marker at {m['at']} s is outside the timeline (0-{float(tl['total_f'] * fd(fps)):.2f} s), left out: {m.get('text', '')}")
    voice_cache = {}
    for idx, (c, (gain, _, _), (scale, move, _), keys, (lead_f, tail_f)) in enumerate(zip(clips, levels, zooms, follows, splits)):
        a = c['asset']
        src_start_f = a['start_f'] + c['in_f']  # in asset frames
        src_start = src_start_f * fd(a['fps'])
        def src_time(t_tl_f, src_start=src_start, c=c, a=a):  # timeline time (frames) -> source rational time, snapped to the asset frame grid
            t = src_start + (t_tl_f - c['off_f']) * fd(fps)
            n = round(t / fd(a['fps']))
            return rt(n, a['fps'])
        inner = []
        fill = 1
        if edl.get('vertical'):
            # a picture of another shape fills the vertical frame by an explicit scale over Final Cut Pro's default
            # conform ("fit"): imported with conform "fill", Shorts came out small in the middle of the frame
            inner.append('          <adjust-conform type="fit"/>')
            fill = fill_scale(a['width'], a['height'], tl['width'], tl['height'])
        if keys:
            frames_ = '\n'.join(f'                <keyframe time="{rt(src_start_f + frames(t, a["fps"]), a["fps"])}" value="{u:g} 0"/>'
                                for t, u in keys)
            inner.append(f'          <adjust-transform position="{keys[0][1]:g} 0" scale="{fill:g} {fill:g}">\n            <param name="position">\n'
                         f'              <keyframeAnimation>\n{frames_}\n              </keyframeAnimation>\n'
                         f'            </param>\n          </adjust-transform>')
        elif scale * fill != 1:
            inner.append(f'          <adjust-transform position="{move[0]:g} {move[1]:g}" scale="{scale * fill:g} {scale * fill:g}"/>')
        fade = fade_frames(c, cfg, float(fps)) if args.fades and a['audio_channels'] else 0
        fade_in, fade_out = (0 if idx in fade_at else fade), (0 if idx + 1 in fade_at else fade)  # a dissolve blends the sound
        amount = f' amount="{loudness.db(gain)}"' if gain is not None else ''
        if fade_in or fade_out:
            inner.append(f'          <adjust-volume{amount}>\n            <param name="amount">\n'
                         + (f'              <fadeIn duration="{rt(fade_in, fps)}"/>\n' if fade_in else '')
                         + (f'              <fadeOut duration="{rt(fade_out, fps)}"/>\n' if fade_out else '')
                         + '            </param>\n          </adjust-volume>')
        elif gain is not None:
            inner.append(f'          <adjust-volume{amount}/>')
        for at_f, parent, path, b_in, b_out, note, lane in places:
            if clips[parent] is not c:
                continue
            b = broll_assets[path]
            b_start = b['start_f'] + frames(b_in, b['fps'])
            sound = (f' audioRole="effects">\n            <adjust-volume amount="{cfg["broll"]["volume_db"]}dB"/>'
                     if b['audio_channels'] else '>')
            inner.append(f'          <asset-clip ref="{asset_ids[path]}" lane="{lane}" offset="{src_time(at_f)}" '
                         f'name={qa(os.path.splitext(os.path.basename(path))[0])} start="{rt(b_start, b["fps"])}" '
                         f'duration="{rt(frames(b_out - b_in, fps), fps)}" tcFormat="{b["tcf"]}"{sound}\n'
                         f'            <marker start="{rt(b_start, b["fps"])}" duration="{rt(1, b["fps"])}" value={qa("B-roll: " + (note or "check"))}/>\n'
                         f'          </asset-clip>')
        if music and clips[music['parent']] is c:  # under the edit, its volume dipping under speech
            m_start = frames(music['in'], fps)
            keys = music['keys']
            if len(keys) > 1:
                kf = '\n'.join(f'                  <keyframe time="{rt(m_start + frames(t, fps), fps)}" value="{v:g}dB"/>' for t, v in keys)
                volume = (f'            <adjust-volume amount="{keys[0][1]:g}dB">\n              <param name="amount">\n'
                          f'                <keyframeAnimation>\n{kf}\n                </keyframeAnimation>\n'
                          f'              </param>\n            </adjust-volume>')
            else:
                volume = f'            <adjust-volume amount="{keys[0][1]:g}dB"/>'
            inner.append(f'          <asset-clip ref="{asset_ids[music["file"]]}" lane="-1" offset="{src_time(music["at_f"])}" '
                         f'name={qa(os.path.splitext(os.path.basename(music["file"]))[0])} start="{rt(m_start, fps)}" '
                         f'duration="{rt(frames(music["dur"], fps), fps)}" audioRole="music">\n{volume}\n          </asset-clip>')
        for o, at_f, parent in overlays:  # over the timeline, each part from where it starts
            if parent != idx:
                continue
            start_f = max(at_f, c['off_f'])
            inner.append(f'          <asset-clip ref="{asset_ids[o["file"]]}" lane="{top_lane + 1}" offset="{src_time(start_f)}" '
                         f'name={qa(os.path.splitext(os.path.basename(o["file"]))[0])} start="{rt(o["start_f"], o["fps"])}" '
                         f'duration="{rt(min(tl["total_f"] - start_f, frames(o["duration"], fps)), fps)}" tcFormat="NDF" videoRole="titles"'
                         + (f'>\n            <adjust-transform position="{overlay_at[0]:g} {overlay_at[1]:g}"/>\n          </asset-clip>'
                            if overlay_at else '/>'))
        for t_idx, into_f, seconds, (t_name, t_uid), texts, name_, position, t_scale, above in titles:
            if t_idx != idx:
                continue
            start_f = c['off_f'] + min(into_f, c['dur_f'] - 1)
            inner.append(dressing.title_xml(res.assets[t_uid], top_lane + 1 + above, src_time(start_f),
                                            rt(min(frames(seconds, fps), tl['total_f'] - start_f), fps), name_, texts,
                                            position, t_scale))
        if c.get('chapter'):
            inner.append(f'          <chapter-marker start="{rt(src_start_f, a["fps"])}" duration="{rt(1, a["fps"])}" value={qa(c["chapter"])} posterOffset="0s"/>')
            chapters.append((c['off_s'], c['chapter']))
        if c.get('marker'):
            inner.append(f'          <marker start="{rt(src_start_f, a["fps"])}" duration="{rt(1, a["fps"])}" value={qa(c["marker"])}/>')
        for mf, text, todo in tl_markers:  # a to-do marker (red in Final Cut Pro) for what is to check
            if c['off_f'] <= mf < c['off_f'] + c['dur_f']:
                inner.append(f'          <marker start="{src_time(mf)}" duration="{rt(1, a["fps"])}" value={qa(text)}'
                             + (' completed="0"' if todo else '') + '/>')
        voice = voice_isolation(c, cfg, voice_cache, os.path.join(os.path.dirname(os.path.abspath(args.edl)), 'transcripts'))
        if voice and a['audio_channels'] and c.get('role', cfg['roles']['default_audio']) == 'dialogue':
            # Final Cut Pro's own Voice Isolation, set on the speech (its Audio Enhancements, clip by clip)
            channels = ', '.join(str(n) for n in range(1, a['audio_channels'] + 1))
            inner.append(f'          <audio-channel-source srcCh="{channels}">\n            <adjust-voiceIsolation amount="{voice:g}"/>\n'
                         '          </audio-channel-source>')
        name = os.path.splitext(os.path.basename(c['file']))[0]
        body = ('>\n' + '\n'.join(inner) + '\n        </asset-clip>') if inner else '/>'
        role = f' audioRole={qa(c.get("role", cfg["roles"]["default_audio"]))}' if a['audio_channels'] else ''
        if lead_f or tail_f:  # split edit: the sound starts before the picture (J) or runs after it (L)
            role += (f' audioStart="{src_time(c["off_f"] - lead_f)}"' if lead_f else '') + \
                    f' audioDuration="{rt(c["dur_f"] + lead_f + tail_f, fps)}"'
        if idx in fade_at:  # centred on the cut: half of it over the end of the clip before, half over this one
            d_f = fade_at[idx]
            spine.append(f'        <transition name="Cross Dissolve" offset="{rt(c["off_f"] - d_f // 2, fps)}" duration="{rt(d_f, fps)}">\n'
                         f'          <filter-video ref="{res.assets[dressing.CROSS_DISSOLVE[1]]}" name="Cross Dissolve"/>\n        </transition>')
        spine.append(f'        <asset-clip ref="{asset_ids[c["file"]]}" offset="{rt(c["off_f"], fps)}" name={qa(name)} '
                     f'start="{rt(src_start_f, a["fps"])}" duration="{rt(c["dur_f"], fps)}" tcFormat="{a["tcf"]}"{role}{body}')

    project = edl.get('project', 'Rough cut')
    rate = AUDIO_RATES.get(assets[0]['audio_rate'], '48k')
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="{args.fcpxml_version}">
  <resources>
{chr(10).join(res.lines)}
  </resources>
  <library>
    <event name={qa("Rough cut – " + project)}>
      <project name={qa(project)}>
        <sequence format="{seq_fmt}" duration="{rt(tl['total_f'], fps)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="{rate}">
          <spine>
{chr(10).join(spine)}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
'''
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(xml)
    total = float(tl['total_f'] * fd(fps))
    print(f'{out}\nTotal duration: {mmss(total)} ({total:.2f} s, {tl["total_f"]} frames at {float(fps):.3f} fps) | {len(clips)} clips | '
          f'format {tl["width"]}x{tl["height"]}')
    if args.level_audio:
        target = cfg['audio']['dialogue_target_lufs']
        print(f'\nDialogue levels (target {target} LUFS; a cut gets its own gain beyond '
              f'{cfg["audio"]["clip_deviation_lu"]} LU from its source; a moment played for its own sound goes '
              f'towards {cfg["audio"]["ambient_target_lufs"]} LUFS):')
        for n, (c, (gain, own, source)) in enumerate(zip(clips, levels), 1):
            show = lambda v: '   -  ' if v is None else f'{v:6.1f}'  # noqa: E731
            print(f'{n:3d}  {mmss(c["off_s"])}  cut {show(own)}  source {show(source)}  gain {loudness.db(gain) if gain is not None else "none"}')
    if args.split_edits:
        made = [n for n in split_notes if n[1]]
        print(f'\nSplit edits where the scene changes: {len(made)}')
        for cut, kind, what in split_notes:
            print(f'  cut {cut}: ' + (f'{kind} cut, {what:.2f} s' if kind else what))
    if music:
        print(f'\nMusic: {os.path.basename(music["file"])} from {mmss(music["in"])} in the track, {mmss(music["dur"])} long, '
              f'ducked under {music["ducked"]} stretches of speech ({len(music["keys"])} volume keyframes)'
              + (f'; B-roll cuts on its beats ({music["bpm"]:.0f} bpm)' if music['bpm'] else ''))
    if places:
        print(f'\nB-roll over the edit: {len(places)}')
        for at_f, parent, path, b_in, b_out, note, lane in places:
            print(f'  {mmss(float(at_f * fd(fps)))}  {b_out - b_in:4.1f} s  lane {lane}  {os.path.basename(path)} {b_in:.1f}-{b_out:.1f}  {note}')
    if feats:
        count = lambda kind: sum(1 for t in titles if t[8] == kind)  # noqa: E731
        print(f'\nDressing ({args.dressing}): {count(1)} chapter titles, {count(2)} times of day, {count(3)} lower thirds, '
              f'{len(fade_at)} dissolves' + (f' ({sum(1 for n in fade_at.values() if n < frames(dcfg["dissolve_seconds"], fps))} '
              f'shorter, for want of media; {sum(1 for i, c in enumerate(clips) if c.get("chapter") and i > dressing.story_start(clips)) - len(fade_at)} '
              'chapters left as cuts)' if 'fades' in feats else ''))
    if overlays:
        print(f'Overlay: {len(overlays)} video(s) with transparency over the timeline')
    if args.follow_face:
        if not edl.get('vertical'):
            print('\n--follow-face only acts on a vertical Short ("vertical": true): nothing done')
        else:
            moving = sum(1 for k in follows if k)
            print(f'\nFrame following the face or the main subject: {moving} of {len(clips)} cuts (the others stay centred: neither most of the time)')
    if args.zoom_jump_cuts:
        done = [(n, c, z) for n, (c, z) in enumerate(zip(clips, zooms), 1) if z[0] != 1]
        faceless = sum(1 for z in zooms if z[2] == 'no face found')
        print(f'\nPunch-in zooms on jump cuts: {len(done)}' + (f' ({faceless} left alone: no face found)' if faceless else ''))
        for n, c, (scale, move, note) in done:
            print(f'{n:3d}  {mmss(c["off_s"])}  x{scale:g}  {note}')
    for a in assets:
        if a.get('hdr'):
            print(f"NOTE: {os.path.basename(a['file'])} is HDR ({a['hdr']}), declared as such; Final Cut Pro converts it to the "
                  f"Rec. 709 timeline. For an HDR video, set the library to Wide Gamut HDR and the project to Rec. 2020 {a['hdr']}")
    if chapters:
        print('\nYouTube chapters:')
        for t, title in chapters:
            print(f'{mmss(t)} {title}')
        issues = []
        if chapters[0][0] > 0.5: issues.append('the first chapter must start at 0:00')
        if len(chapters) < 3: issues.append('YouTube needs at least 3 chapters')
        ends = [t for t, _ in chapters[1:]] + [total]
        if any(e - t < 10 for (t, _), e in zip(chapters, ends)): issues.append('each chapter must last at least 10 s')
        if issues: print('WARNING, chapters:', '; '.join(issues))
    ok, msg = fcp.check(out)
    print(f'\nDTD check: {msg}')
    if ok is False:
        raise SystemExit('The FCPXML does not validate: Final Cut Pro would refuse it.')
    if args.open:
        print(f'Opened in {os.path.basename(fcp.open_in_fcp(out))[:-4]}: the import starts there.')

if __name__ == '__main__':
    main()
