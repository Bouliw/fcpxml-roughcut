"""Tests of the time maths, the FCPXML, the subtitles and the transcript, without footage: ffprobe is
replaced by fixed clip properties. ProbeTest runs ffprobe for real on generated files (skipped without ffmpeg);
FinalCutProDtdTest checks generated timelines against the DTDs of the installed Final Cut Pro (skipped without it).
Run: python3 -m unittest discover -s tests -v"""
import array
import contextlib
import importlib.util
import io
import json
import math
import os
import plistlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import unittest
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
import xml.etree.ElementTree as ET
from fractions import Fraction
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import auto_edit  # noqa: E402
import brain  # noqa: E402
import broll  # noqa: E402
import captions  # noqa: E402
import datetime  # noqa: E402
import dressing  # noqa: E402
import edl_from_ranges  # noqa: E402
import fcp  # noqa: E402
import fcpxml_merge  # noqa: E402
import hesitations  # noqa: E402
import install_quick_action  # noqa: E402
import languages  # noqa: E402
import loudness  # noqa: E402
import make_fcpxml  # noqa: E402
import make_srt  # noqa: E402
import music  # noqa: E402
import organize  # noqa: E402
import pauses  # noqa: E402
import probe as probe_script  # noqa: E402
import reframe  # noqa: E402
import review  # noqa: E402
import render_preview  # noqa: E402
import script_align  # noqa: E402
import settings  # noqa: E402
import splitedit  # noqa: E402
import subtitles  # noqa: E402
import text_edit  # noqa: E402
import verify_edit  # noqa: E402
import timeline  # noqa: E402
import words  # noqa: E402
import zoom  # noqa: E402

NTSC = Fraction(30000, 1001)
REAL_PROBE = timeline.probe
REAL_MEDIA_PROBLEMS = fcp.media_problems
REAL_FCP_APP = fcp.find_app()
CLIPS = {  # what ffprobe reports for each fake clip
    'cam.mp4': {'duration': 30.0, 'fps': NTSC, 'width': 1920, 'height': 1080, 'timecode': '01:00:00:00'},
    'dji.mp4': {'duration': 40.0, 'fps': Fraction(60000, 1001), 'width': 3840, 'height': 2160, 'timecode': None},
    'df.mov': {'duration': 20.0, 'fps': NTSC, 'width': 1920, 'height': 1080, 'timecode': '01:00:00;00'},
    'hlg.mov': {'duration': 10.0, 'fps': Fraction(60000, 1001), 'width': 3840, 'height': 2160, 'timecode': None, 'hdr': 'HLG'},
    'rec96.mov': {'duration': 10.0, 'fps': Fraction(25), 'width': 1920, 'height': 1080, 'timecode': None, 'audio_rate': 96000},
    'vidéo.mov': {'duration': 10.0, 'fps': Fraction(25), 'width': 1920, 'height': 1080, 'timecode': None},
    'captions.mov': {'duration': 60.0, 'fps': NTSC, 'width': 1920, 'height': 1080, 'timecode': None, 'audio_channels': 0},
    'captions-01.mov': {'duration': 4.5, 'fps': NTSC, 'width': 1920, 'height': 154, 'timecode': None, 'audio_channels': 0},
    'captions-02.mov': {'duration': 2.5, 'fps': NTSC, 'width': 1920, 'height': 154, 'timecode': None, 'audio_channels': 0},
}


def fake_probe(path):
    return {'file': os.path.abspath(path), 'audio_channels': 2, 'audio_rate': 48000, **CLIPS[os.path.basename(path)]}


def secs(rational):
    """FCPXML time ('1001/30000s', '0s') -> Fraction of a second."""
    return Fraction(rational[:-1])


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        patcher = mock.patch.object(timeline, 'probe', fake_probe)
        patcher.start()
        self.addCleanup(patcher.stop)
        # the clips are made up: only their files are missing, every other media check stays
        media = mock.patch.object(fcp, 'media_problems', lambda p: [x for x in REAL_MEDIA_PROBLEMS(p) if 'is missing' not in x])
        media.start()
        self.addCleanup(media.stop)
        # The same results with or without Final Cut Pro on the machine: FinalCutProDtdTest uses the real one
        env = mock.patch.dict(os.environ, {'FCP_APP': self.path('no Final Cut Pro.app')})
        env.start()
        self.addCleanup(env.stop)

    def path(self, name):
        return os.path.join(self.dir, name)

    def write_edl(self, **edl):
        with open(self.path('edl.json'), 'w', encoding='utf-8') as f:
            json.dump(edl, f)
        return self.path('edl.json')

    def run_script(self, module, *args):
        with mock.patch.object(sys, 'argv', [module.__file__, *args]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            module.main()
        return out.getvalue()


class TimeMathsTest(unittest.TestCase):
    def test_measured_rates_snap_to_standard_rates(self):
        self.assertEqual(timeline.snap_fps(Fraction('59.9401')), Fraction(60000, 1001))
        self.assertEqual(timeline.snap_fps(Fraction('29.97')), NTSC)
        self.assertEqual(timeline.snap_fps(Fraction(25)), 25)
        self.assertEqual(timeline.snap_fps(Fraction('12.5')), Fraction('12.5'))  # no standard rate nearby

    def test_rational_time(self):
        self.assertEqual(timeline.rt(1, NTSC), '1001/30000s')
        self.assertEqual(timeline.rt(0, NTSC), '0s')
        self.assertEqual(timeline.rt(30, 30), '1s')

    def test_timecode_to_frames(self):
        self.assertEqual(timeline.tc_frames('01:00:00:00', NTSC), (108000, False))
        self.assertEqual(timeline.tc_frames('01:00:00;00', NTSC), (107892, True))  # drop-frame hour
        self.assertEqual(timeline.tc_frames('00:10:00;00', NTSC), (17982, True))
        self.assertEqual(timeline.tc_frames(None, NTSC), (0, False))


class BuildTest(Base):
    def test_clips_follow_each_other_on_the_frame_grid(self):
        edl = self.write_edl(clips=[{'file': 'cam.mp4', 'in': 1.0, 'out': 4.5},
                                    {'file': 'cam.mp4', 'in': 10.2, 'out': 12.0}])
        _, tl, _, clips = timeline.build(edl)
        self.assertEqual(clips[0]['in_f'], timeline.frames(1.0, NTSC))
        self.assertEqual(clips[1]['off_f'], clips[0]['dur_f'])
        self.assertEqual(tl['total_f'], clips[0]['dur_f'] + clips[1]['dur_f'])

    def test_forced_frame_rate_is_snapped(self):
        for fps in ('59.94', 59.94, '60000/1001'):
            edl = self.write_edl(format={'fps': fps}, clips=[{'file': 'dji.mp4', 'in': 1, 'out': 5}])
            self.assertEqual(timeline.build(edl)[1]['fps'], Fraction(60000, 1001), fps)

    def test_end_margin_past_the_source_stops_on_its_last_frame(self):
        edl = self.write_edl(clips=[{'file': 'cam.mp4', 'in': 28.0, 'out': 30.3}])
        c = timeline.build(edl)[3][0]
        self.assertLessEqual(c['in_f'] + c['dur_f'], timeline.frames(30.0, NTSC))

    def test_back_to_back_cuts_neither_drop_nor_repeat_a_frame(self):
        for fps_clip, points in (('dji.mp4', (10.0, 20.0, 30.0)), ('cam.mp4', (0.21, 0.84, 2.0)), ('dji.mp4', (1.013, 2.517, 3.3))):
            a, b, c = points
            edl = self.write_edl(clips=[{'file': fps_clip, 'in': a, 'out': b}, {'file': fps_clip, 'in': b, 'out': c}])
            first, second = timeline.build(edl)[3]
            self.assertEqual(first['in_f'] + first['dur_f'], second['in_f'], points)

    def test_cut_shorter_than_a_frame_is_refused(self):
        for cut in ({'in': 5.0, 'out': 5.005}, {'in': 40.0, 'out': 40.2}):
            edl = self.write_edl(clips=[{'file': 'dji.mp4', **cut}])
            with self.assertRaises(SystemExit):
                timeline.build(edl)

    def test_edl_without_clips_is_refused(self):
        with self.assertRaises(SystemExit):
            timeline.build(self.write_edl(clips=[]))

    def test_accented_names_keep_their_spelling_on_disk(self):
        nfd, nfc = unicodedata.normalize('NFD', 'Vidéo été'), unicodedata.normalize('NFC', 'Vidéo été')
        os.makedirs(os.path.join(self.dir, nfd))
        open(os.path.join(self.dir, nfd, nfd + '.mp4'), 'w').close()  # as macOS stores them
        found = timeline.disk_path(os.path.join(self.dir, nfc, nfc + '.mp4'))  # as typed
        self.assertEqual(found, os.path.join(self.dir, nfd, nfd + '.mp4'))
        self.assertEqual(timeline.disk_path(self.path('missing.mp4')), self.path('missing.mp4'))

    def test_malformed_edl_gives_a_message(self):
        broken = [{'clips': [{'file': 'cam.mp4', 'in': 1}]}, {'clips': [{'file': 'cam.mp4', 'in': '0:01', 'out': 3}]},
                  {'clips': [{'in': 1, 'out': 3}]}, {'clips': [{'file': 'cam.mp4', 'in': 1, 'out': 3}], 'markers': [{'text': 'x'}]}]
        for edl in broken:
            with self.assertRaises(SystemExit, msg=edl):
                timeline.build(self.write_edl(**edl))
        with open(self.path('bad.json'), 'w') as f:
            f.write('{"clips": [')
        for path in (self.path('bad.json'), self.path('missing.json')):
            with self.assertRaises(SystemExit):
                timeline.build(path)

    def test_cut_outside_the_source_is_refused(self):
        edl = self.write_edl(clips=[{'file': 'cam.mp4', 'in': 31.0, 'out': 35.0}])
        with self.assertRaises(SystemExit):
            timeline.build(edl)


class FcpxmlTest(Base):
    def fcpxml(self, **edl):
        self.run_script(make_fcpxml, self.write_edl(**edl), '-o', self.path('out.fcpxml'))
        return ET.parse(self.path('out.fcpxml')).getroot()

    def test_timeline_is_contiguous_and_keeps_the_camera_timecode(self):
        root = self.fcpxml(project='Vlog "été" & <test>', clips=[
            {'file': 'cam.mp4', 'in': 0.5, 'out': 11.0, 'chapter': 'Intro'},
            {'file': 'cam.mp4', 'in': 12.0, 'out': 22.4, 'chapter': 'Market', 'marker': 'B-roll'},
            {'file': 'cam.mp4', 'in': 23.0, 'out': 29.9, 'chapter': 'End'}],
            markers=[{'at': 2.0, 'text': 'zoom'}])
        seq = root.find('.//sequence')
        spine = seq.findall('spine/asset-clip')
        offset = Fraction(0)
        for clip in spine:
            self.assertEqual(secs(clip.get('offset')), offset)
            offset += secs(clip.get('duration'))
        self.assertEqual(secs(seq.get('duration')), offset)
        self.assertEqual(root.find('.//format').get('name'), 'FFVideoFormat1080p2997')  # Final Cut Pro's own name
        self.assertEqual(root.find('.//asset').get('start'), timeline.rt(108000, NTSC))  # 01:00:00:00
        self.assertEqual(root.find('.//project').get('name'), 'Vlog "été" & <test>')
        self.assertEqual(len(root.findall('.//chapter-marker')), 3)
        self.assertEqual(len(root.findall('.//marker')), 2)

    def test_vertical_short(self):
        root = self.fcpxml(vertical=True, clips=[{'file': 'dji.mp4', 'in': 1, 'out': 5}])
        seq_format = root.find(".//format[@id='%s']" % root.find('.//sequence').get('format'))
        self.assertEqual((seq_format.get('width'), seq_format.get('height')), ('1080', '1920'))
        # Fitted, then scaled up by hand: FCP letterboxed a "fill" conform on real footage
        self.assertEqual(root.find('.//asset-clip/adjust-conform').get('type'), 'fit')
        self.assertEqual(root.find('.//asset-clip/adjust-transform').get('scale'), '3.1605 3.1605')
        self.assertEqual(make_fcpxml.fill_scale(1920, 1080, 1080, 1920), 3.1605)
        self.assertEqual(make_fcpxml.fill_scale(1080, 1920, 1080, 1920), 1)

    def test_drop_frame_timecode(self):
        root = self.fcpxml(clips=[{'file': 'df.mov', 'in': 2.5, 'out': 9.1}])
        self.assertEqual(root.find('.//asset-clip').get('tcFormat'), 'DF')
        self.assertEqual(root.find('.//asset').get('start'), timeline.rt(107892, NTSC))

    def test_mixed_frame_rates_use_the_first_clip(self):
        root = self.fcpxml(clips=[{'file': 'dji.mp4', 'in': 1, 'out': 5}, {'file': 'cam.mp4', 'in': 2, 'out': 6}])
        seq_format = root.find(".//format[@id='%s']" % root.find('.//sequence').get('format'))
        self.assertEqual(seq_format.get('frameDuration'), '1001/60000s')
        self.assertEqual(seq_format.get('name'), 'FFVideoFormat3840x2160p5994')
        self.assertEqual(len(root.findall('.//asset')), 2)

    def test_markers_outside_the_timeline_are_reported(self):
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 0, 'out': 8.0}],
                       markers=[{'at': 45.0, 'text': 'too late'}, {'at': 8.0, 'text': 'at the end'}])
        out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'))
        self.assertIn('marker at 45.0 s is outside the timeline', out)
        markers = ET.parse(self.path('out.fcpxml')).getroot().findall('.//marker')
        self.assertEqual([m.get('value') for m in markers], ['at the end'])  # kept, on the last frame

    def test_audio_rate_of_the_sequence(self):
        root = self.fcpxml(clips=[{'file': 'rec96.mov', 'in': 1, 'out': 5}])
        self.assertEqual(root.find('.//sequence').get('audioRate'), '96k')

    def test_hdr_footage_declares_its_colour_space(self):
        self.write_edl(clips=[{'file': 'hlg.mov', 'in': 1, 'out': 5}, {'file': 'cam.mp4', 'in': 1, 'out': 3}])
        out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'))
        self.assertIn('hlg.mov is HDR (HLG), declared as such', out)
        root = ET.parse(self.path('out.fcpxml')).getroot()
        spaces = {f.get('id'): f.get('colorSpace') for f in root.iter('format')}
        assets = {os.path.basename(a.find('media-rep').get('src')): spaces[a.get('format')] for a in root.iter('asset')}
        self.assertEqual(assets['hlg.mov'], '9-18-9 (Rec. 2020 HLG)')  # Final Cut Pro converts it, not taken for SDR
        self.assertEqual(assets['cam.mp4'], '1-1-1 (Rec. 709)')
        self.assertEqual(spaces[root.find('.//sequence').get('format')], '1-1-1 (Rec. 709)')  # the timeline stays SDR

    def test_fcpxml_version(self):
        self.assertEqual(self.fcpxml(clips=[{'file': 'cam.mp4', 'in': 1, 'out': 5}]).get('version'), '1.13')
        self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('old.fcpxml'), '--fcpxml-version', '1.10')
        self.assertEqual(ET.parse(self.path('old.fcpxml')).getroot().get('version'), '1.10')

    def test_invalid_timeline_is_neither_accepted_nor_opened(self):
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 1, 'out': 5}])
        with mock.patch.object(fcp, 'check', return_value=(False, 'bad')), \
                mock.patch.object(fcp.subprocess, 'run') as run, self.assertRaises(SystemExit):
            self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--open')
        run.assert_not_called()

    def test_open_hands_the_file_to_final_cut_pro(self):
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 1, 'out': 5}])
        app = self.path('Final Cut Pro Trial.app')
        os.makedirs(app)
        with mock.patch.dict(os.environ, {'FCP_APP': app}), mock.patch.object(fcp, 'check', return_value=(True, 'valid')), \
                mock.patch.object(fcp.subprocess, 'run') as run:
            out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--open')
        run.assert_called_once_with(['open', '-a', app, self.path('out.fcpxml')], check=True)
        self.assertIn('Opened in Final Cut Pro Trial', out)

    def test_youtube_chapter_rules_are_checked(self):
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 0, 'out': 5, 'chapter': 'A'},
                              {'file': 'cam.mp4', 'in': 6, 'out': 9, 'chapter': 'B'}])
        out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'))
        self.assertIn('at least 3 chapters', out)
        self.assertIn('at least 10 s', out)


@unittest.skipUnless(shutil.which('xmllint'), 'needs xmllint')
class DtdCheckTest(Base):
    """fcp.check with a stand-in app, whose name has spaces like the real one."""
    def setUp(self):
        super().setUp()
        self.app = self.path('Final Cut Pro Trial.app')
        dtd_dir = os.path.join(self.app, fcp.DTD_DIR)
        os.makedirs(dtd_dir)
        with open(os.path.join(dtd_dir, 'FCPXMLv1_13.dtd'), 'w') as f:
            f.write('<!ELEMENT fcpxml (resources)>\n<!ATTLIST fcpxml version CDATA #FIXED "1.13">\n<!ELEMENT resources EMPTY>\n')
        env = mock.patch.dict(os.environ, {'FCP_APP': self.app})
        env.start()
        self.addCleanup(env.stop)

    def check(self, xml, name='t.fcpxml'):
        with open(self.path(name), 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + xml)
        return fcp.check(self.path(name))

    def test_valid_file(self):
        ok, msg = self.check('<fcpxml version="1.13"><resources/></fcpxml>')
        self.assertTrue(ok, msg)
        self.assertIn('1.13 DTD of Final Cut Pro Trial', msg)

    def test_invalid_file(self):
        ok, msg = self.check('<fcpxml version="1.13"><spine/></fcpxml>')
        self.assertFalse(ok)
        self.assertIn('spine', msg)

    def test_version_without_a_dtd_is_not_checked(self):
        ok, msg = self.check('<fcpxml version="1.10"><resources/></fcpxml>')
        self.assertIsNone(ok)
        self.assertIn('no DTD for FCPXML 1.10', msg)

    def test_bundle_exported_by_final_cut_pro(self):
        os.makedirs(self.path('export.fcpxmld'))
        self.check('<fcpxml version="1.13"><resources/></fcpxml>', 'export.fcpxmld/Info.fcpxml')
        self.assertTrue(fcp.check(self.path('export.fcpxmld'))[0])

    def test_invalid_file_is_not_opened(self):
        self.check('<fcpxml version="1.13"><spine/></fcpxml>')
        with mock.patch.object(fcp.subprocess, 'run', wraps=subprocess.run) as run, self.assertRaises(SystemExit):
            fcp.open_in_fcp(self.path('t.fcpxml'))
        self.assertNotIn('open', [c.args[0][0] for c in run.call_args_list])


@unittest.skipUnless(REAL_FCP_APP and shutil.which('xmllint'), 'needs Final Cut Pro and xmllint')
class FinalCutProDtdTest(Base):
    """Generated timelines against the DTDs shipped with the installed Final Cut Pro."""
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {'FCP_APP': REAL_FCP_APP})
        env.start()
        self.addCleanup(env.stop)

    def test_logged_event(self):
        test = OrganizeTest('test_keyword_ranges_ratings_and_markers')
        test.dir, test.path = self.dir, self.path
        test.run_script = self.run_script
        _, out = test.log(gps=(1.5, 2.5))
        self.assertIn('valid against the FCPXML 1.13 DTD', out)

    def test_every_feature_in_every_version(self):
        edls = {
            'jump cuts': dict(clips=[{'file': 'dji.mp4', 'in': 1, 'out': 5}, {'file': 'dji.mp4', 'in': 6, 'out': 9, 'marker': 'm'}]),
            'landscape': dict(project='Vlog "été" & <test>', clips=[
                {'file': 'cam.mp4', 'in': 0.5, 'out': 11.0, 'chapter': 'Intro'},
                {'file': 'dji.mp4', 'in': 12.0, 'out': 22.4, 'chapter': 'Market', 'marker': 'B-roll', 'split': 'j'},
                {'file': 'df.mov', 'in': 2.0, 'out': 9.9, 'chapter': 'End', 'split': 'l'},
                {'file': 'rec96.mov', 'in': 1.0, 'out': 4.0}], markers=[{'at': 2.0, 'text': 'zoom'}]),
            'levelled': dict(clips=[{'file': 'cam.mp4', 'in': 0.5, 'out': 11.0, 'marker': 'x'},
                                    {'file': 'dji.mp4', 'in': 1, 'out': 5, 'role': 'effects'}], vertical=True),
            'followed': dict(vertical=True, clips=[{'file': 'dji.mp4', 'in': 1, 'out': 6, 'marker': 'm'}]),
            'captioned': dict(clips=[{'file': 'cam.mp4', 'in': 1, 'out': 5, 'chapter': 'c', 'marker': 'm'}],
                              broll=[{'file': 'dji.mp4', 'in': 2, 'out': 4, 'at': 1.0, 'note': 'b'},
                                     {'file': 'rec96.mov', 'in': 1, 'out': 2, 'at': 1.5}], music={'file': 'song.m4a', 'in': 5}),
            'short': dict(vertical=True, clips=[{'file': 'dji.mp4', 'in': 1, 'out': 5, 'marker': 'hook'},
                                                {'file': 'hlg.mov', 'in': 1, 'out': 3}]),
        }
        ReframeTest.analysis(self)
        for name, edl in edls.items():
            for version in make_fcpxml.VERSIONS:
                with self.subTest(edl=name, version=version):
                    song = {'file': self.path('song.m4a'), 'duration': 60.0, 'audio_channels': 2, 'audio_rate': 44100, 'audio_only': True}
                    with mock.patch.object(loudness, 'measure', return_value=-21.0), mock.patch.object(music, 'probe_audio', return_value=song):
                        out = self.run_script(make_fcpxml, self.write_edl(**edl), '-o', self.path('out.fcpxml'),
                                              '--fcpxml-version', version, '--level-audio', '--zoom-jump-cuts', '--follow-face', '--split-edits',
                                              *(['--overlay', self.path('captions.mov')] if name == 'captioned' else []))
                    self.assertIn(f'valid against the FCPXML {version} DTD', out)


class SrtTest(Base):
    def srt(self, word_list, clips):
        os.makedirs(self.path('transcripts'))
        with open(self.path('transcripts/cam.words.json'), 'w', encoding='utf-8') as f:
            json.dump({'words': [{'w': w, 'start': s, 'end': e, 'filler': w.lower().strip('.') in ('um', 'euh')}
                                 for w, s, e in word_list]}, f)
        self.run_script(make_srt, self.write_edl(clips=clips), '-o', self.path('out.srt'))
        with open(self.path('out.srt'), encoding='utf-8') as f:
            text = f.read()

        def t(ts):
            h, m, s = ts.replace(',', '.').split(':')
            return int(h) * 3600 + int(m) * 60 + float(s)
        return [(t(a), t(b), txt) for a, b, txt in re.findall(r'(\S+) --> (\S+)\n(.+)', text)]

    def test_short_sentences_never_overlap(self):
        cues = self.srt([('Okay.', 3.00, 3.20), ('So', 3.45, 3.60), ('here', 3.60, 3.80), ('we', 3.80, 3.90),
                         ('go.', 3.90, 4.10), ('Yes.', 4.25, 4.40), ('Next', 4.60, 4.90)],
                        [{'file': 'cam.mp4', 'in': 2.5, 'out': 9.1}])
        self.assertEqual(len(cues), 4)
        for (s0, e0, _), (s1, _, _) in zip(cues, cues[1:]):
            self.assertLess(s0, e0)
            self.assertLessEqual(e0, s1)

    def test_transcript_found_whatever_the_accent_encoding(self):
        os.makedirs(self.path('transcripts'))
        name = unicodedata.normalize('NFD', 'vidéo') + '.words.json'  # written from a name read on disk
        with open(self.path(os.path.join('transcripts', name)), 'w', encoding='utf-8') as f:
            json.dump({'words': [{'w': 'Bonjour.', 'start': 1.0, 'end': 1.5}]}, f)
        self.run_script(make_srt, self.write_edl(clips=[{'file': unicodedata.normalize('NFC', 'vidéo.mov'), 'in': 0.5, 'out': 2}]),
                        '-o', self.path('out.srt'))
        with open(self.path('out.srt'), encoding='utf-8') as f:
            self.assertIn('Bonjour.', f.read())

    def test_clips_sharing_a_name_are_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self.srt([], [{'file': 'card1/cam.mp4', 'in': 0, 'out': 2}, {'file': 'card2/cam.mp4', 'in': 0, 'out': 2}])
        self.assertIn('share the name cam', str(cm.exception))

    def test_invented_words_stay_out_of_the_subtitles(self):
        os.makedirs(self.path('transcripts'))
        with open(self.path('transcripts/cam.words.json'), 'w', encoding='utf-8') as f:
            json.dump({'words': [{'w': 'Real.', 'start': 1.0, 'end': 1.4},
                                 {'w': 'Twice.', 'start': 1.5, 'end': 1.5, 'suspect': True}]}, f)
        self.run_script(make_srt, self.write_edl(clips=[{'file': 'cam.mp4', 'in': 0.5, 'out': 3}]), '-o', self.path('out.srt'))
        with open(self.path('out.srt'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('Real.', text)
        self.assertNotIn('Twice', text)

    def test_words_follow_the_cuts(self):
        cues = self.srt([('Cut', 1.0, 1.3), ('away.', 1.3, 1.6), ('Um.', 10.1, 10.3),
                         ('Kept', 10.5, 10.8), ('words.', 10.8, 11.2)],
                        [{'file': 'cam.mp4', 'in': 10.0, 'out': 12.0}])
        self.assertEqual([c[2] for c in cues], ['Kept words.'])  # outside the cut and fillers are left out
        self.assertAlmostEqual(cues[0][0], 0.5, delta=0.05)  # 10.5 s in the source -> 0.5 s on the timeline


class EdlFromRangesTest(Base):
    WORDS = [('Hi', 1.00, 1.20), ('there.', 1.20, 1.60), ('Um.', 1.70, 1.90), ('So', 2.00, 2.20), ('today', 2.20, 2.60),
             ('we', 3.60, 3.70), ('cook.', 3.70, 4.10), ('Twice.', 4.15, 4.15), ('Next', 4.30, 4.60), ('bit.', 4.60, 5.00)]

    def cuts(self, **spec):
        os.makedirs(self.path('transcripts'), exist_ok=True)
        with open(self.path('transcripts/cam.words.json'), 'w', encoding='utf-8') as f:
            json.dump({'words': [{'w': w, 'start': a, 'end': b, 'filler': w == 'Um.', 'suspect': w == 'Twice.'}
                                 for w, a, b in self.WORDS]}, f)
        with open(self.path('ranges.json'), 'w', encoding='utf-8') as f:
            json.dump({'file': 'cam.mp4', **spec}, f)
        self.run_script(edl_from_ranges, self.path('ranges.json'), '-o', self.path('edl.json'))
        with open(self.path('edl.json'), encoding='utf-8') as f:
            return json.load(f)

    def test_pauses_hesitations_and_invented_words_are_cut_out(self):
        edl = self.cuts(project='P', ranges=[{'from': 1.0, 'to': 5.0, 'chapter': 'Intro'}])
        got = [(c['in'], c['out']) for c in edl['clips']]
        # "Um." and the 1 s pause split the range; "Twice." (invented) too; margins stop halfway to the next word
        self.assertEqual(got, [(0.92, 1.65), (1.95, 2.72), (3.52, 4.125), (4.225, 5.12)])
        self.assertEqual(edl['clips'][0]['chapter'], 'Intro')
        self.assertNotIn('chapter', edl['clips'][1])
        self.assertEqual(edl['project'], 'P')
        hook = self.cuts(ranges=[{'from': 1.0, 'to': 5.0, 'note': 'hook'}])
        self.assertEqual({c.get('note') for c in hook['clips']}, {'hook'})  # every piece of the hook

    def test_words_said_again_are_cut_out(self):
        self.WORDS = [('we', 1.0, 1.2), ('went', 1.2, 1.5), ('we', 1.5, 1.7), ('went', 1.7, 2.0), ('there.', 2.0, 2.4)]
        os.makedirs(self.path('transcripts'), exist_ok=True)
        ws = [{'w': w, 'start': a, 'end': b} for w, a, b in self.WORDS]
        ws[0]['repeat'] = ws[1]['repeat'] = True
        with open(self.path('transcripts/cam.words.json'), 'w', encoding='utf-8') as f:
            json.dump({'words': ws}, f)
        with open(self.path('ranges.json'), 'w', encoding='utf-8') as f:
            json.dump({'file': 'cam.mp4', 'ranges': [{'from': 1.0, 'to': 2.4}]}, f)
        self.run_script(edl_from_ranges, self.path('ranges.json'), '-o', self.path('edl.json'))
        with open(self.path('edl.json'), encoding='utf-8') as f:
            self.assertEqual([(c['in'], c['out']) for c in json.load(f)['clips']], [(1.5, 2.52)])

    def test_margins_can_be_set_per_range(self):
        edl = self.cuts(ranges=[{'from': 3.6, 'to': 4.1, 'pre': 0.0, 'post': 0.02}])
        self.assertEqual([(c['in'], c['out']) for c in edl['clips']], [(3.6, 4.12)])

    def test_the_timeline_follows_from_the_ranges(self):
        self.cuts(ranges=[{'from': 1.0, 'to': 1.6}, {'from': 3.6, 'to': 4.1}])
        tl = timeline.build(self.path('edl.json'))[1]
        self.assertAlmostEqual(float(tl['total_f'] * timeline.fd(tl['fps'])), (1.65 - 0.92) + (4.125 - 3.52), delta=0.07)

    def test_range_without_words_or_transcript(self):
        with self.assertRaises(SystemExit) as cm:
            self.cuts(ranges=[{'from': 10, 'to': 12}])
        self.assertIn('No cut', str(cm.exception))
        with self.assertRaises(SystemExit) as cm:
            self.cuts(ranges=[{'file': 'other.mp4', 'from': 1, 'to': 2}])
        self.assertIn('Cannot read the transcript', str(cm.exception))


class SettingsTest(Base):
    def test_defaults_then_parent_folders_then_explicit_file(self):
        self.assertEqual(settings.load()['audio']['dialogue_target_lufs'], -16)
        os.makedirs(self.path('videos/vlog'))
        with open(self.path('videos/roughcut.json'), 'w') as f:
            json.dump({'audio': {'dialogue_target_lufs': -18, 'max_gain_db': 6}}, f)
        with open(self.path('videos/vlog/roughcut.json'), 'w') as f:
            json.dump({'audio': {'max_gain_db': 9}}, f)
        with open(self.path('mine.json'), 'w') as f:
            json.dump({'roles': {'default_audio': 'effects'}}, f)
        cfg = settings.load(self.path('videos/vlog'), self.path('mine.json'))
        self.assertEqual((cfg['audio']['dialogue_target_lufs'], cfg['audio']['max_gain_db']), (-18, 9))
        self.assertEqual(cfg['audio']['clip_deviation_lu'], 2)  # untouched default
        self.assertEqual(cfg['roles']['default_audio'], 'effects')

    def test_typo_is_refused(self):
        with open(self.path('roughcut.json'), 'w') as f:
            json.dump({'audio': {'dialog_target_lufs': -18}}, f)
        with self.assertRaises(SystemExit) as cm:
            settings.load(self.dir)
        self.assertIn('dialog_target_lufs', str(cm.exception))


class LevelTest(Base):
    """Dialogue gains from made-up measurements."""
    def gains(self, clips, measured):
        edl = self.write_edl(clips=clips)
        _, _, _, built = timeline.build(edl)
        with mock.patch.object(loudness, 'measure', side_effect=measured):
            return [g for g, _, _ in loudness.gains(built, settings.load(), loudness.Cache(None))]

    def test_one_gain_per_source_and_its_own_for_a_cut_that_stands_out(self):
        clips = [{'file': 'cam.mp4', 'in': 0, 'out': 4}, {'file': 'cam.mp4', 'in': 5, 'out': 9},
                 {'file': 'cam.mp4', 'in': 10, 'out': 14}, {'file': 'dji.mp4', 'in': 0, 'out': 4},
                 {'file': 'cam.mp4', 'in': 15, 'out': 15.5}, {'file': 'dji.mp4', 'in': 5, 'out': 9, 'role': 'effects'}]
        # cam: two cuts at -25/-26 and one far quieter (-33); dji: -40, beyond the 12 dB limit; a 0.5 s cut is not
        # measured; the last one, played for its own sound, is wind at -12
        g = self.gains(clips, [-25.0, -26.0, -33.0, -40.0, -12.0])
        cam = loudness.combined([(-25.0, 4), (-26.0, 4), (-33.0, 4)])
        self.assertEqual(g[0], round(-16 - cam, 1))
        self.assertEqual(g[0], g[1])  # the same take keeps the same level
        self.assertEqual(g[2], 12.0)  # -33 stands out: its own gain, capped at 12 dB
        self.assertEqual(g[3], 12.0)
        self.assertEqual(g[4], g[0])  # too short to measure: the source's gain
        self.assertEqual(g[5], -13.0)  # not dialogue: towards -25 LUFS, under the voice
        quiet = self.gains([{'file': 'dji.mp4', 'in': 5, 'out': 9, 'role': 'effects'}, {'file': 'dji.mp4', 'in': 10, 'out': 14, 'role': 'effects'}], [-50.0, -5.0])
        self.assertEqual(quiet, [6.0, -15.0])  # a quiet room raised a little only, and never cut past 15 dB

    def test_combined_loudness(self):
        self.assertAlmostEqual(loudness.combined([(-20, 1), (-20, 3)]), -20)
        self.assertAlmostEqual(loudness.combined([(-20, 1), (-30, 1)]), 10 * math.log10((0.01 + 0.001) / 2))
        self.assertEqual(loudness.db(3.24), '+3.2dB')
        self.assertEqual(loudness.db(0.01), '0dB')

    def test_fcpxml_carries_gains_and_roles(self):
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 0, 'out': 4}, {'file': 'cam.mp4', 'in': 5, 'out': 9, 'role': 'effects'}])
        with mock.patch.object(loudness, 'measure', return_value=-22.0):
            out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--level-audio')
        clips = ET.parse(self.path('out.fcpxml')).getroot().findall('.//spine/asset-clip')
        self.assertEqual([c.get('audioRole') for c in clips], ['dialogue', 'effects'])
        self.assertEqual(clips[0].find('adjust-volume').get('amount'), '+6.0dB')
        self.assertEqual(clips[1].find('adjust-volume').get('amount'), '-3.0dB')  # towards -25 LUFS
        self.assertIn('gain +6.0dB', out)
        self.assertIsNone(clips[0].find('adjust-volume/param'))  # no fades unless asked

    def test_fades_at_every_cut_and_longer_for_a_moment_heard_for_its_sound(self):
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 0, 'out': 4}, {'file': 'cam.mp4', 'in': 5, 'out': 9, 'role': 'effects'},
                              {'file': 'cam.mp4', 'in': 10, 'out': 10.5, 'role': 'effects'}])
        self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--fades')
        clips = ET.parse(self.path('out.fcpxml')).getroot().findall('.//spine/asset-clip')
        fades = [[(f.tag, f.get('duration')) for f in c.find('adjust-volume/param')] for c in clips[:2]]
        self.assertEqual(fades, [[('fadeIn', '1001/30000s'), ('fadeOut', '1001/30000s')],  # 0.03 s: one frame at 29.97
                                 [('fadeIn', '7007/30000s'), ('fadeOut', '7007/30000s')]])  # 0.25 s
        self.assertIsNone(clips[0].find('adjust-volume').get('amount'))  # no gain: 0 dB
        self.assertEqual(clips[2].find('adjust-volume/param/fadeIn').get('duration'), '1001/6000s')  # 5 frames: a third of the cut at most


class ZoomTest(Base):
    def analysis(self, face):
        os.makedirs(self.path('analysis'), exist_ok=True)
        samples = [{'t': t / 2, 'faces': [face] if face else []} for t in range(0, 80)]
        with open(self.path('analysis/dji.analysis.json'), 'w') as f:
            json.dump({'samples': samples}, f)

    def test_every_other_cut_of_a_take_is_zoomed_on_the_face(self):
        self.analysis([0.65, 0.4, 0.2, 0.2, 0.5])  # face centred at (0.75, 0.5)
        self.write_edl(clips=[{'file': 'dji.mp4', 'in': 1, 'out': 5}, {'file': 'dji.mp4', 'in': 6, 'out': 9},
                              {'file': 'dji.mp4', 'in': 10, 'out': 14}, {'file': 'cam.mp4', 'in': 1, 'out': 4},
                              {'file': 'dji.mp4', 'in': 20, 'out': 25}, {'file': 'dji.mp4', 'in': 26, 'out': 26.5}])
        out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--zoom-jump-cuts')
        clips = ET.parse(self.path('out.fcpxml')).getroot().findall('.//spine/asset-clip')
        zoomed = [c.find('adjust-transform') is not None for c in clips]
        # take 1: cut 2 zoomed; the source changes (cam), then a new take of dji starts at 100 %; a 0.5 s cut is left alone
        self.assertEqual(zoomed, [False, True, False, False, False, False])
        t = clips[1].find('adjust-transform')
        self.assertEqual(t.get('scale'), '1.12 1.12')
        x, y = map(float, t.get('position').split())
        self.assertAlmostEqual(x, -0.12 * 25 * 16 / 9, places=1)  # the face is 25 % right of centre: the frame moves left
        self.assertAlmostEqual(y, 0, places=2)
        self.assertIn('on the face', out)

    def test_no_face_no_zoom_and_verticals_are_left_alone(self):
        self.analysis(None)
        clips = [{'file': 'dji.mp4', 'in': 1, 'out': 5}, {'file': 'dji.mp4', 'in': 6, 'out': 9}]
        self.write_edl(clips=clips)
        out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--zoom-jump-cuts')
        self.assertIn('1 left alone: no face found', out)
        self.assertIsNone(ET.parse(self.path('out.fcpxml')).getroot().find('.//adjust-transform'))
        self.analysis([0.4, 0.3, 0.2, 0.4, 0.5])
        self.write_edl(clips=clips, vertical=True)
        self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--zoom-jump-cuts')
        scales = {m.get('scale') for m in ET.parse(self.path('out.fcpxml')).getroot().iter('adjust-transform')}
        self.assertEqual(scales, {'3.1605 3.1605'})  # filled, never zoomed on top

    def test_preview_crop_matches_the_move(self):
        move = (-0.12 * 25 * 16 / 9, 0)
        f = zoom.crop_filter(1.12, move, 1920, 1080)
        # the visible window is centred (s-1)/s of the way towards the face: 0.12/1.12 * 480 px
        self.assertIn(f'+{0.12 / 1.12 * 480:.2f}*iw/1920', f)
        self.assertEqual(zoom.crop_filter(1, (0, 0), 1920, 1080), '')


class OrganizeTest(Base):
    def log(self, gps=None):
        os.makedirs(self.path('transcripts'))
        os.makedirs(self.path('analysis'))
        words = [{'w': 'a', 'start': t, 'end': t + 0.4} for t in [1, 2, 3, 4.5] + [12 + i for i in range(8)]]
        with open(self.path('transcripts/cam.words.json'), 'w') as f:
            json.dump({'words': words}, f)
        face = [0.4, 0.3, 0.25, 0.4, 0.5]
        samples = [{'t': i / 3, 'faces': [face] if 2 <= i / 3 <= 4.3 or 13 <= i / 3 <= 19.3 else []} for i in range(90)]
        with open(self.path('analysis/cam.analysis.json'), 'w') as f:
            json.dump({'samples': samples, 'rejects': [{'from': 22, 'to': 25, 'why': ['blurred']}],
                       'broll': [{'from': 6, 'to': 10, 'score': 0.8}], 'thumbnails': [{'t': 14, 'score': 1}]}, f)
        with mock.patch.object(organize, 'place', return_value=gps):
            out = self.run_script(organize, self.path('cam.mp4'), '-o', self.path('logged.fcpxml'))
        return ET.parse(self.path('logged.fcpxml')).getroot(), out

    def spans(self, root, tag, **attrs):
        out, origin = [], secs(root.find('.//asset').get('start'))  # the camera timecode (01:00:00:00)
        for e in root.iter(tag):
            if all(e.get(k) == v for k, v in attrs.items()):
                a = float(secs(e.get('start')) - origin)
                out.append((round(a, 1), round(a + float(secs(e.get('duration'))), 1)))
        return out

    def test_keyword_ranges_ratings_and_markers(self):
        root, out = self.log()
        self.assertIsNone(root.find('.//project'))  # an event of clips to browse, no timeline
        self.assertEqual(root.find('.//event').get('name'), 'Logged – ' + os.path.basename(self.dir))
        self.assertEqual(self.spans(root, 'keyword', value='Face cam'), [(2.0, 4.3), (13.0, 19.3)])
        self.assertEqual(self.spans(root, 'keyword', value='B-roll'), [(4.9, 12.0), (19.4, 30.0)])
        self.assertEqual(self.spans(root, 'rating', value='reject'), [(22.0, 25.0)])
        self.assertEqual(root.find('.//rating[@value="reject"]').get('note'), 'blurred')
        self.assertEqual(self.spans(root, 'rating', value='favorite'), [(6.0, 10.0)])
        self.assertEqual([m.get('value') for m in root.iter('marker')], ['Thumbnail candidate'])
        self.assertIn('2 face cam', out)

    def test_place_from_the_gps_tag(self):
        root, _ = self.log(gps=(-12.34567, -123.45678))
        kw = [k for k in root.iter('keyword') if k.get('value').startswith('Place')]
        self.assertEqual(kw[0].get('value'), 'Place -12.35, -123.46')
        with mock.patch.object(organize, 'ffprobe', return_value={'format': {'tags': {
                'com.apple.quicktime.location.ISO6709': '-12.3457-123.4568+012.345/'}}}):
            self.assertEqual(organize.place('x.mov'), (-12.3457, -123.4568))
        with mock.patch.object(organize, 'ffprobe', return_value={'format': {}}):
            self.assertIsNone(organize.place('x.mov'))


class ReframeTest(Base):
    @staticmethod
    def face_at(t):  # a face at 30 % of the width, then at 70 % from 2 s on
        x = 0.3 if t < 2 else 0.7
        return [x - 0.05, 0.4, 0.1, 0.2, 0.5]

    def analysis(self, faces=True):
        os.makedirs(self.path('analysis'), exist_ok=True)
        with open(self.path('analysis/dji.analysis.json'), 'w') as f:
            json.dump({'samples': [{'t': 1 + i / 3, 'faces': [ReframeTest.face_at(i / 3)] if faces else []} for i in range(15)]}, f)

    def test_the_frame_holds_then_moves_to_the_face(self):
        self.analysis()
        self.write_edl(vertical=True, clips=[{'file': 'dji.mp4', 'in': 1, 'out': 6}])
        _, _, _, clips = timeline.build(self.path('edl.json'))
        with open(self.path('analysis/dji.analysis.json')) as f:
            samples = json.load(f)['samples']
        keys = reframe.follow(clips[0], samples, 1080, 1920, settings.load())
        shown = 3840 * 1920 / 2160
        left, right = round(0.2 * shown / 19.2, 2), round(-0.2 * shown / 19.2, 2)
        self.assertEqual(keys[0], (0.0, left))  # the face at 30 %: the picture moves right to centre it
        self.assertEqual([u for _, u in keys], [left, left, right, right])  # hold, then move, then hold to the end
        self.assertLess(keys[2][0] - keys[1][0], 0.7)  # the move itself is short
        self.assertAlmostEqual(keys[-1][0], 5.0, delta=0.02)
        for _, u in keys:
            self.assertLessEqual(abs(u) * 19.2, (shown - 1080) / 2)  # never past the edge of the picture

    def test_no_face_follows_the_main_subject(self):
        os.makedirs(self.path('analysis'), exist_ok=True)
        samples = [{'t': 1 + i / 3, 'faces': [], 'subject': [0.6, 0.2, 0.2, 0.5]} for i in range(15)]
        self.write_edl(vertical=True, clips=[{'file': 'dji.mp4', 'in': 1, 'out': 6}])
        _, _, _, clips = timeline.build(self.path('edl.json'))
        keys = reframe.follow(clips[0], samples, 1080, 1920, settings.load())
        self.assertEqual(keys[0], (0.0, round(-0.2 * 3840 * 1920 / 2160 / 19.2, 2)))  # the subject at 70 %: centred
        wide = [dict(s, subject=[0.02, 0.0, 0.95, 1.0]) for s in samples]  # the whole picture is no subject
        self.assertIsNone(reframe.follow(clips[0], wide, 1080, 1920, settings.load()))

    def test_fcpxml_keyframes_and_no_face(self):
        self.analysis()
        self.write_edl(vertical=True, clips=[{'file': 'dji.mp4', 'in': 1, 'out': 6}])
        out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--follow-face')
        keys = ET.parse(self.path('out.fcpxml')).getroot().findall('.//adjust-transform/param/keyframeAnimation/keyframe')
        self.assertEqual(len(keys), 4)
        self.assertIn('1 of 1 cuts', out)
        self.analysis(faces=False)
        out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--follow-face')
        move = ET.parse(self.path('out.fcpxml')).getroot().find('.//adjust-transform')
        self.assertEqual((move.get('scale'), move.get('position'), move.find('param')), ('3.1605 3.1605', '0 0', None))
        self.write_edl(clips=[{'file': 'dji.mp4', 'in': 1, 'out': 6}])
        self.assertIn('only acts on a vertical Short', self.run_script(make_fcpxml, self.path('edl.json'), '-o',
                                                                         self.path('out.fcpxml'), '--follow-face'))


class CaptionsTest(Base):
    def test_one_event_per_word_with_that_word_highlighted(self):
        cues = [[(0.5, 0.8, 'Hi'), (0.8, 1.1, 'there,'), (1.1, 1.5, 'friends.')], [(3.0, 3.4, 'Next')]]
        text = captions.ass(cues, 1080, 1920, 10.0, settings.load()['captions'])
        events = [l for l in text.splitlines() if l.startswith('Dialogue:')]
        self.assertEqual(len(events), 4)
        self.assertIn('0:00:00.50,0:00:00.80', events[0])  # from the first word to the next one...
        self.assertIn('0:00:01.10,0:00:01.65', events[2])  # ...and the last word stays 0.15 s after it ends
        self.assertIn('Hi {\\c&H0000D4FF\\fscx112\\fscy112}there,{\\r} friends.', events[1])
        self.assertIn('PlayResY: 1920', text)
        self.assertIn(f',{round(1920 * 0.3)},1', text)  # a vertical Short keeps its captions above the app buttons
        cfg = dict(settings.load()['captions'], uppercase=True)
        self.assertIn('NEXT', captions.ass(cues, 1080, 1920, 10.0, cfg))

    @unittest.skipUnless(shutil.which('ffmpeg') and 'ass' in subprocess.run(['ffmpeg', '-hide_banner', '-filters'], capture_output=True,
                         text=True).stdout if shutil.which('ffmpeg') else False, 'needs ffmpeg with libass')
    def test_captions_render_in_a_folder_with_an_apostrophe_a_colon_and_a_comma(self):
        folder = self.path("Projets d'été: 1, essai")
        os.makedirs(folder)
        sub = os.path.join(folder, 'captions.ass')
        with open(sub, 'w', encoding='utf-8') as f:
            f.write(captions.ass([[(0.1, 0.5, 'Salut')]], 320, 240, 1.0, settings.load()['captions']))
        out = os.path.join(folder, 'captions.mov')
        captions.render(sub, out, 320, 240, Fraction(25), 0, 25)
        self.assertGreater(os.path.getsize(out), 0)

    def test_overlay_in_parts_placed_in_the_frame(self):
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 1, 'out': 5}, {'file': 'cam.mp4', 'in': 6, 'out': 9}])
        with open(self.path('captions.json'), 'w') as f:
            json.dump({'videos': [{'file': 'captions-01.mov', 'at': 0.0}, {'file': 'captions-02.mov', 'at': 4.5}],
                       'position': [0, -38.5]}, f)
        self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--overlay', self.path('captions.json'))
        spine = ET.parse(self.path('out.fcpxml')).getroot().findall('.//spine/asset-clip')
        parts = [(i, o) for i, c in enumerate(spine) for o in c.findall('asset-clip')]
        self.assertEqual([i for i, _ in parts], [0, 1])  # the second part on the clip playing at 4.5 s
        self.assertEqual({o.find('adjust-transform').get('position') for _, o in parts}, {'0 -38.5'})  # the strip, low
        self.assertEqual(secs(parts[1][1].get('offset')) - secs(spine[1].get('start')), Fraction(15015, 30000))

    def test_overlay_is_a_connected_title_over_the_whole_timeline(self):
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 1, 'out': 5, 'marker': 'm'}, {'file': 'cam.mp4', 'in': 6, 'out': 9}])
        self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--overlay', self.path('captions.mov'))
        root = ET.parse(self.path('out.fcpxml')).getroot()
        first = root.find('.//spine/asset-clip')
        over = first.find('asset-clip')
        self.assertEqual((over.get('lane'), over.get('videoRole')), ('1', 'titles'))
        self.assertEqual(over.get('offset'), first.get('start'))  # it starts with the timeline
        self.assertEqual(secs(over.get('duration')), secs(root.find('.//sequence').get('duration')))
        children = [c.tag for c in first]
        self.assertLess(children.index('asset-clip'), children.index('marker'))  # the order the DTD asks for


class BrollTest(Base):
    def edit(self, **extra):
        return self.write_edl(clips=[{'file': 'cam.mp4', 'in': 1, 'out': 5}, {'file': 'cam.mp4', 'in': 10, 'out': 20, 'marker': 'm'}], **extra)

    def test_placed_where_the_talking_head_says_it(self):
        self.edit()
        _, tl, _, clips = timeline.build(self.path('edl.json'))
        edl = {'broll': [{'file': 'dji.mp4', 'in': 2, 'out': 6, 'at_source': 12.0, 'note': 'lake'},
                         {'file': 'dji.mp4', 'in': 8, 'out': 10, 'at_source': 13.0},
                         {'file': 'dji.mp4', 'in': 0, 'out': 1, 'at': 1.0}]}
        places = broll.placements(edl, clips, self.dir, tl['fps'])
        by_note = {p[5]: p for p in places}
        # 12 s in the source is 2 s into the second cut, which starts 4 s into the timeline: 6 s
        self.assertAlmostEqual(float(by_note['lake'][0] * timeline.fd(tl['fps'])), 6.0, delta=0.04)
        self.assertEqual(by_note['lake'][1], 1)  # connected to the second cut
        self.assertEqual([p[6] for p in places], [1, 1, 2])  # the one starting under "lake" goes a lane up
        with self.assertRaises(SystemExit) as cm:
            broll.placements({'broll': [{'file': 'dji.mp4', 'in': 0, 'out': 1, 'at_source': 7.0}]}, clips, self.dir, tl['fps'])
        self.assertIn('cut out', str(cm.exception))

    def test_connected_clips_with_their_sound_down_and_a_marker(self):
        self.edit(broll=[{'file': 'dji.mp4', 'in': 2, 'out': 6, 'at_source': 12.0, 'note': 'lake'}])
        out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--overlay', self.path('captions.mov'))
        root = ET.parse(self.path('out.fcpxml')).getroot()
        second = root.findall('.//spine/asset-clip')[1]
        b = [c for c in second.findall('asset-clip') if c.get('name') == 'dji'][0]
        self.assertEqual((b.get('lane'), b.get('audioRole')), ('1', 'effects'))
        self.assertEqual(b.find('adjust-volume').get('amount'), '-96dB')
        self.assertEqual(b.find('marker').get('value'), 'B-roll: lake')
        self.assertAlmostEqual(float(secs(b.get('offset')) - secs(second.get('start'))), 2.0, delta=0.04)
        self.assertAlmostEqual(float(secs(b.get('duration'))), 4.0, delta=0.04)
        self.assertEqual(root.find('.//spine/asset-clip/asset-clip[@name="captions"]').get('lane'), '2')  # captions on top
        self.assertIn('B-roll over the edit: 1', out)


class MusicTest(Base):
    def test_the_music_dips_under_speech(self):
        cfg = settings.load()
        keys = music.envelope([[2.0, 5.0], [5.5, 6.0], [10.0, 12.0]], 15.0, cfg)
        # -14 dB, down in 0.3 s before the first words, back up 0.3 s after; a 0.5 s gap stays down
        self.assertEqual(keys, [(0.0, -14), (1.7, -14), (2.0, -28), (6.0, -28), (6.3, -14), (9.7, -14), (10.0, -28),
                                (12.0, -28), (12.3, -14), (15.0, -14)])
        self.assertEqual(music.envelope([[0.0, 3.0]], 5.0, cfg)[:2], [(0.0, -28), (3.0, -28)])  # speech from the start
        self.assertEqual(music.speech_spans([(0, 1, 'a'), (1.5, 2, 'b'), (4, 5, 'c')]), [[0, 2], [4, 5]])

    @unittest.skipUnless(shutil.which('ffmpeg') and importlib.util.find_spec('numpy'), 'needs ffmpeg and numpy')
    def test_beats_found_without_librosa(self):
        track = self.path('clicks.wav')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        "aevalsrc='0.8*sin(2*PI*1000*t)*exp(-40*mod(t,0.5))':d=20:s=22050", track], check=True)
        got = music._numpy_beats(track)
        self.assertAlmostEqual(got['bpm'], 120, delta=1.5)
        inside = [b for b in got['beats'] if 1 <= b <= 19]
        self.assertGreaterEqual(len(inside), 34)
        self.assertTrue(all(abs(b - round(b * 2) / 2) < 0.05 for b in inside), inside)  # on the clicks, every 0.5 s

    def test_slow_ramps_and_a_gap_too_short_for_a_full_rise(self):
        cfg = settings.load()
        cfg['music'].update(duck_db=-20, ramp_down_seconds=0.8, ramp_up_seconds=1.5, min_gap_seconds=2.0)
        keys = music.envelope([[2, 5], [6, 8], [10.1, 12], [15, 17]], 20, cfg)
        self.assertEqual(keys[:4], [(0.0, -14), (1.2, -14), (2, -20), (8, -20)])  # down in 0.8 s, done as speech starts; 1 s gap stays down
        t, v = keys[4]  # 2.1 s gap: up for 1.5 s and down for 0.8 s cannot both fit, they meet part of the way up
        self.assertAlmostEqual(t, 9.37, places=2)
        self.assertTrue(-20 < v < -14)
        self.assertEqual(keys[5:10], [(10.1, -20), (12, -20), (13.5, -14), (14.2, -14), (15, -20)])  # a full 1.5 s rise

    def test_broll_cuts_land_on_beats(self):
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 0, 'out': 20}])
        _, tl, _, clips = timeline.build(self.path('edl.json'))
        grid = [i * 0.5 for i in range(40)]  # 120 bpm
        edl = {'broll': [{'file': 'dji.mp4', 'in': 1, 'out': 3.3, 'at': 4.2}, {'file': 'dji.mp4', 'in': 0, 'out': 2, 'at': 9.7}]}
        (a_f, _, _, a_in, a_out, _, _), (b_f, _, _, b_in, b_out, _, _) = broll.placements(edl, clips, self.dir, tl['fps'], grid, 0.35)
        fd = timeline.fd(tl['fps'])
        self.assertAlmostEqual(float(a_f * fd), 4.0, delta=0.04)  # 4.2 -> the beat at 4.0
        self.assertAlmostEqual(a_out - a_in, 2.5, delta=0.01)  # ends on 6.5 instead of 6.3
        self.assertAlmostEqual(float(b_f * fd), 9.5, delta=0.04)

    def test_fcpxml_music_under_the_edit(self):
        os.makedirs(self.path('transcripts'))
        with open(self.path('transcripts/cam.words.json'), 'w') as f:
            json.dump({'words': [{'w': 'Hi', 'start': 2.0, 'end': 2.5}, {'w': 'there.', 'start': 2.5, 'end': 3.0}]}, f)
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 0, 'out': 8}], music={'file': 'song.m4a', 'in': 10})
        with mock.patch.object(music, 'probe_audio', return_value={'file': self.path('song.m4a'), 'duration': 120.0,
                                                                   'audio_channels': 2, 'audio_rate': 44100, 'audio_only': True}):
            out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'))
        root = ET.parse(self.path('out.fcpxml')).getroot()
        song = root.find('.//spine/asset-clip/asset-clip[@lane="-1"]')
        self.assertEqual(song.get('audioRole'), 'music')
        self.assertAlmostEqual(float(secs(song.get('start'))), 10.0, delta=0.04)
        values = [k.get('value') for k in song.iter('keyframe')]
        self.assertEqual(values[:3], ['-14dB', '-14dB', '-28dB'])
        asset = root.find(f".//asset[@id='{song.get('ref')}']")
        self.assertIsNone(asset.get('hasVideo'))
        self.assertIn('ducked under 1 stretches', out)


class ScriptAlignTest(Base):
    SCRIPT = ("The bakery is investing heavily in ovens. Bread is the key to this strategy.\n\n"
              "Yet the debt worries local farmers.\n\n"
              "The miller renegotiated his loans. Nobody knows what the baker will do.\n")

    def said(self, start, text, fillers=(), pause_after=None):
        out, t = [], start
        for k, w in enumerate(text.split()):
            out.append({'w': w, 'start': round(t, 2), 'end': round(t + 0.3, 2), 'filler': w.lower().strip('.') in ('um',)})
            t += 0.35 + (1.5 if pause_after == k else 0)
        return out

    def test_best_take_per_paragraph_and_sentences_never_said(self):
        words = (self.said(1.0, 'The bakery is um investing heavily in ovens.', pause_after=4)  # a rough, incomplete take
                 + self.said(10.0, 'The bakery is investing heavily in ovens. Bread is the key to this strategy.')
                 + self.said(20.0, 'Yet the debt worries the local farmers.')
                 + self.said(30.0, 'The miller renegotiated his loans.'))
        os.makedirs(self.path('transcripts'))
        with open(self.path('transcripts/cam.words.json'), 'w') as f:
            json.dump({'words': words}, f)
        with open(self.path('script.txt'), 'w') as f:
            f.write(self.SCRIPT)
        out = self.run_script(script_align, self.path('script.txt'), self.path('cam.mp4'), '-o', self.dir)
        with open(self.path('ranges.json')) as f:
            ranges = json.load(f)['ranges']
        self.assertEqual([round(r['from']) for r in ranges], [10, 20, 30])  # the complete take of paragraph 1
        self.assertIn('2 found', ranges[0]['note'])
        self.assertIn('never said in any take: "Nobody knows what the baker will do."', out)
        self.assertNotIn('never said in any take: "The miller', out)
        with open(self.path('visuals.txt')) as f:
            self.assertEqual(f.read().count('Visuals:'), 3)
        # the ranges become cuts, with the hesitation left out
        self.run_script(edl_from_ranges, self.path('ranges.json'), '-o', self.path('edl.json'))
        with open(self.path('edl.json')) as f:
            self.assertEqual(len(json.load(f)['clips']), 3)


class SplitEditTest(Base):
    def plan(self, clips, words):
        self.write_edl(clips=clips)
        _, tl, _, built = timeline.build(self.path('edl.json'))
        return splitedit.plan(built, settings.load(), words, float(tl['fps'])), float(tl['fps']), built

    def test_j_cut_where_both_sides_are_free_of_speech(self):
        cam, dji = self.path('cam.mp4'), self.path('dji.mp4')
        (splits, notes), fps, built = self.plan([{'file': 'cam.mp4', 'in': 0, 'out': 4}, {'file': 'dji.mp4', 'in': 5, 'out': 9},
                                                 {'file': 'dji.mp4', 'in': 12, 'out': 15}],
                                                {cam: [(1.0, 2.9)], dji: [(10.0, 11.5), (12.2, 14.0)]})
        x = int(0.6 * fps)
        self.assertEqual(splits, [(0, -x), (x, 0), (0, 0)])  # the next sound 0.6 s early; nothing inside one source
        self.assertEqual(notes, [(2, 'J', x / fps)])

    def test_speech_on_both_sides_leaves_the_cut_straight(self):
        cam, dji = self.path('cam.mp4'), self.path('dji.mp4')
        (splits, notes), _, _ = self.plan([{'file': 'cam.mp4', 'in': 0, 'out': 3}, {'file': 'dji.mp4', 'in': 5, 'out': 9}],
                                          {cam: [(1.0, 2.95), (3.05, 5.0)], dji: [(4.0, 4.95), (5.05, 6.0)]})
        self.assertEqual(splits, [(0, 0), (0, 0)])
        self.assertIn('speech on both sides', notes[0][2])

    def test_l_cut_when_only_the_next_start_is_free(self):
        cam, dji = self.path('cam.mp4'), self.path('dji.mp4')
        (splits, notes), fps, _ = self.plan([{'file': 'cam.mp4', 'in': 0, 'out': 4}, {'file': 'dji.mp4', 'in': 5, 'out': 9}],
                                            {cam: [(1.0, 3.95)], dji: [(4.0, 4.98), (6.0, 8.0)]})
        x = int(0.6 * fps)
        self.assertEqual(splits, [(0, x), (-x, 0)])  # the previous sound runs 0.6 s over the next picture
        self.assertEqual(notes[0][1], 'L')

    def test_asked_for_and_unknown_words(self):
        (splits, notes), fps, _ = self.plan([{'file': 'cam.mp4', 'in': 0, 'out': 4},
                                             {'file': 'dji.mp4', 'in': 5, 'out': 9, 'split': 'l', 'split_seconds': 1.0}], {})
        self.assertEqual(splits[0][1], int(1.0 * fps))  # asked for in edl.json: applied without a transcript
        (splits, notes), _, _ = self.plan([{'file': 'cam.mp4', 'in': 0, 'out': 4}, {'file': 'dji.mp4', 'in': 5, 'out': 9}], {})
        self.assertEqual(splits, [(0, 0), (0, 0)])
        self.assertIn('no transcript', notes[0][2])

    def test_fcpxml_attributes(self):
        os.makedirs(self.path('transcripts'))
        for name, ws in (('cam', [(1.0, 2.9)]), ('dji', [(10.0, 11.5)])):
            with open(self.path(f'transcripts/{name}.words.json'), 'w') as f:
                json.dump({'words': [{'w': 'x', 'start': a, 'end': b} for a, b in ws]}, f)
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 0, 'out': 4}, {'file': 'dji.mp4', 'in': 5, 'out': 9}])
        out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--split-edits')
        a, b = ET.parse(self.path('out.fcpxml')).getroot().findall('.//spine/asset-clip')
        self.assertIsNone(a.get('audioStart'))
        self.assertAlmostEqual(float(secs(a.get('duration')) - secs(a.get('audioDuration'))), 17 * 1001 / 30000, places=4)
        self.assertAlmostEqual(float(secs(b.get('start')) - secs(b.get('audioStart'))), 17 * 1001 / 30000, delta=0.01)
        self.assertAlmostEqual(float(secs(b.get('audioDuration')) - secs(b.get('duration'))), 17 * 1001 / 30000, places=4)
        self.assertIn('cut 2: J cut, 0.57 s', out)


class FakeServer:
    """A local HTTP server answering POSTs with `reply(path, body, headers) -> (status, content_type, bytes)`."""
    def __init__(self, reply):
        seen = self.seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])) or b'{}')
                seen.append((self.path, body, {k.lower(): v for k, v in self.headers.items()}))
                status, ctype, data = reply(self.path, body, self.headers)
                self.send_response(status)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass
        self.httpd = HTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.httpd.server_port}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def chat_reply(text):
    return lambda path, body, headers: (200, 'application/json', json.dumps(
        {'choices': [{'message': {'role': 'assistant', 'content': text}}]}).encode())


class BrainTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.bin = os.path.join(self.dir, 'bin')
        os.makedirs(self.bin)
        env = mock.patch.dict(os.environ, {'PATH': self.bin + os.pathsep + os.environ['PATH']})
        env.start()
        self.addCleanup(env.stop)

    def tool(self, name, script):
        path = os.path.join(self.bin, name)
        with open(path, 'w') as f:
            f.write('#!/bin/sh\n' + script)
        os.chmod(path, 0o755)

    def cfg(self, **brain_settings):
        cfg = settings.load()
        cfg['brain'].update(brain_settings)
        return cfg

    def server(self, reply):
        srv = FakeServer(reply)
        self.addCleanup(srv.close)
        return srv

    def test_claude_answers(self):
        self.tool('claude', 'cat > /dev/null; echo \'{"type": "result", "is_error": false, "result": "Voici : {\\"ok\\": true}"}\'\n')
        answer, engine, note = brain.ask_json('question', self.cfg(engine='claude'))
        self.assertEqual((answer, engine, note), ({'ok': True}, 'claude', None))

    def test_the_reason_given_is_plain(self):
        t = lambda e: brain.handover_note(e, 'local', 'fr')  # noqa: E731
        self.assertIn('(trop lent)', t(brain.BrainError('claude did not answer within 900 s', slow=True)))
        self.assertIn('(pas installé)', t(brain.BrainError('Claude Code (claude) is not installed')))
        self.assertIn('(une erreur)', t(brain.BrainError('claude: some internal message')))

    def test_usage_limit_hands_over_to_the_local_brain(self):
        self.tool('claude', 'cat > /dev/null; echo \'{"type": "result", "is_error": true, "result": "Claude AI usage limit reached"}\'; exit 1\n')
        local = self.server(chat_reply('<think>hmm</think>```json\n{"ok": true}\n```'))
        answer, engine, note = brain.ask_json('question', self.cfg(engine='claude', local_url=local.url + '/v1', local_model='m'))
        self.assertEqual((answer, engine), ({'ok': True}, 'local'))
        self.assertIn('usage limit', note)
        self.assertEqual(local.seen[0][0], '/v1/chat/completions')
        self.assertEqual(local.seen[0][1]['model'], 'm')
        cfg = self.cfg(engine='claude', local_url=local.url + '/v1', local_model='m')
        cfg['ui'] = {'language': 'fr'}  # the note speaks the language of the settings
        self.assertIn('quota atteint', brain.ask_json('question', cfg)[2])

    def test_nothing_answers(self):
        self.tool('claude', 'exit 1\n')
        with self.assertRaises(brain.BrainError):
            brain.ask('question', self.cfg(engine='claude', local_url='http://127.0.0.1:9/v1', local_model='m'))

    def test_openai_key_from_the_keychain(self):
        self.tool('security', 'echo sk-test\n')  # stands in for the macOS keychain
        api = self.server(chat_reply('{"ok": 1}'))
        answer, engine, _ = brain.ask_json('q', self.cfg(engine='api', api_provider='openai', api_model='gpt-x', api_url=api.url))
        self.assertEqual((answer, engine), ({'ok': 1}, 'api'))
        self.assertEqual(api.seen[0][2].get('authorization'), 'Bearer sk-test')

    def test_the_key_given_by_the_app(self):
        self.tool('security', 'exit 44\n')  # nothing in the keychain: the app read it and passes it on
        api = self.server(chat_reply('{"ok": 1}'))
        with mock.patch.dict(os.environ, {'ROUGHCUT_API_KEY': 'sk-from-app'}):
            brain.ask_json('q', self.cfg(engine='api', api_provider='openai', api_model='gpt-x', api_url=api.url))
        self.assertEqual(api.seen[0][2].get('authorization'), 'Bearer sk-from-app')

    def test_questions_keep_their_accents(self):
        self.assertEqual(brain.applescript('demandé « x » "y" \\'), '"demandé « x » \\"y\\" \\\\"')
        self.tool('osascript', 'printf "%s" "$4" > "$(dirname "$0")/script.txt"; echo /tmp/music\n')  # -e activate -e script
        status, folder = brain.dialog_folder('Choisissez votre dossier (demandé une seule fois)')
        self.assertEqual((status, folder), ('ok', '/tmp/music'))
        with open(os.path.join(self.bin, 'script.txt'), encoding='utf-8') as f:
            self.assertIn('prompt "Choisissez votre dossier (demandé une seule fois)"', f.read())  # never \\u00e9

    def test_no_key_says_how_to_add_one(self):
        self.tool('security', 'exit 44\n')
        with self.assertRaises(brain.BrainError) as cm:
            brain.ask('q', self.cfg(engine='api', api_provider='anthropic'))
        self.assertIn('set-key anthropic', str(cm.exception))

    @unittest.skipUnless(importlib.util.find_spec('anthropic'), 'needs the anthropic SDK')
    def test_anthropic_api_through_the_sdk(self):
        self.tool('security', 'echo sk-ant-test\n')
        def sse(path, body, headers):
            events = [('message_start', {'type': 'message_start', 'message': {'id': 'msg_1', 'type': 'message', 'role': 'assistant',
                       'model': body['model'], 'content': [], 'stop_reason': None, 'stop_sequence': None,
                       'usage': {'input_tokens': 10, 'output_tokens': 1}}}),
                      ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
                      ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': '{"ok": '}}),
                      ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'true}'}}),
                      ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
                      ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                                         'usage': {'output_tokens': 5}}),
                      ('message_stop', {'type': 'message_stop'})]
            return 200, 'text/event-stream', ''.join(f'event: {e}\ndata: {json.dumps(d)}\n\n' for e, d in events).encode()
        api = self.server(sse)
        answer, engine, _ = brain.ask_json('q', self.cfg(engine='api', api_provider='anthropic', api_url=api.url))
        self.assertEqual((answer, engine), ({'ok': True}, 'api'))
        path, body, headers = api.seen[0]
        self.assertEqual((path.split('?')[0], body['model'], body['thinking']), ('/v1/messages', 'claude-opus-5', {'type': 'adaptive'}))
        self.assertEqual(headers.get('x-api-key'), 'sk-ant-test')

    def test_first_launch_choice_is_saved(self):
        with mock.patch.object(brain, 'dialog_choose', side_effect=lambda items, *a, **k: items[2] if len(items) == 3 else 'OpenAI'), \
                mock.patch.object(brain, 'dialog_text', side_effect=['sk-secret', 'gpt-x']), \
                mock.patch.object(brain, 'keychain_set') as stored:
            self.assertEqual(brain.choose(self.dir, 'fr'), 'api')
        stored.assert_called_once_with('openai', 'sk-secret')
        with open(os.path.join(self.dir, 'roughcut.json')) as f:
            saved = json.load(f)
        self.assertEqual(saved['brain'], {'engine': 'api', 'api_provider': 'openai', 'api_model': 'gpt-x'})
        self.assertNotIn('sk-secret', json.dumps(saved))  # the key never goes to a file

    def test_a_question_nobody_answers_takes_the_default(self):
        with mock.patch.object(brain.subprocess, 'run', side_effect=subprocess.TimeoutExpired('osascript', 1)):
            self.assertEqual(brain.dialog_choose(['a', 'b'], 'q?', 'a', timeout=1), 'a')

    def test_json_in_an_answer(self):
        self.assertEqual(brain.extract_json('Sure! ```json\n{"a": [1, 2]}\n``` Done.'), {'a': [1, 2]})
        with self.assertRaises(brain.BrainError):
            brain.extract_json('no json here')


class MergeTest(Base):
    def test_one_event_shared_sources_once(self):
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 1, 'out': 5}, {'file': 'dji.mp4', 'in': 1, 'out': 4}], project='Edit')
        self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('a.fcpxml'))
        self.write_edl(clips=[{'file': 'dji.mp4', 'in': 6, 'out': 9}], project='Short', vertical=True)
        self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('b.fcpxml'))
        root = ET.fromstring(fcpxml_merge.merge([self.path('a.fcpxml'), self.path('b.fcpxml')], 'Trip – 2026-09-27 14h00')
                             .split('\n', 2)[2])
        self.assertEqual([e.get('name') for e in root.iter('event')], ['Trip – 2026-09-27 14h00'])
        self.assertEqual([p.get('name') for p in root.iter('project')], ['Edit', 'Short'])
        self.assertEqual(len(root.findall('.//asset')), 2)  # dji.mp4 once
        ids = [r.get('id') for r in root.find('resources')]
        self.assertEqual(len(ids), len(set(ids)))
        refs = [e.get(a) for e in root.iter() for a in ('ref', 'format') if e.get(a)]
        self.assertTrue(refs and all(r in ids for r in refs))  # every reference points to a resource of the merged file


class QuickActionTest(unittest.TestCase):
    def test_workflow_files(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = install_quick_action.install('Edit this footage', '/usr/local/bin/python3', '/tmp/Rough cuts', services=tmp)
        import plistlib
        with open(os.path.join(path, 'Contents', 'Info.plist'), 'rb') as f:
            info = plistlib.load(f)
        with open(os.path.join(path, 'Contents', 'document.wflow'), 'rb') as f:
            doc = plistlib.load(f)
        self.assertEqual(info['NSServices'][0]['NSMenuItem']['default'], 'Edit this footage')
        self.assertEqual(doc['workflowMetaData']['serviceInputTypeIdentifier'], 'com.apple.Automator.fileSystemObject.folder')
        script = doc['actions'][0]['action']['ActionParameters']['COMMAND_STRING']
        self.assertIn("auto_edit.py", script)
        self.assertIn("--work-dir '/tmp/Rough cuts'", script)
        self.assertIn('nohup', script)  # runs in the background: the Finder is free at once


@unittest.skipUnless(sys.platform == 'darwin', 'needs macOS (defaults, PlistBuddy, zsh)')
class LauncherTest(unittest.TestCase):
    """app/engine/roughcut: the one way into the engine, for the app, the Quick Action and scripts run by hand."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.app = os.path.join(self.tmp, 'Roughcut.app')
        engine = os.path.join(self.app, 'Contents', 'Resources', 'engine')
        os.makedirs(os.path.join(engine, 'scripts'))
        with open(os.path.join(self.app, 'Contents', 'Info.plist'), 'wb') as f:
            plistlib.dump({'CFBundleShortVersionString': '9.9.9', 'RoughcutEngineVersion': '7'}, f)
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
        shutil.copy(os.path.join(root, 'app', 'engine', 'roughcut'), engine)
        self.launcher = os.path.join(engine, 'roughcut')
        with open(os.path.join(engine, 'scripts', 'show.py'), 'w') as f:
            f.write('import json, os, sys\nprint(json.dumps({"args": sys.argv[1:], "env": dict(os.environ)}))\n')
        self.home = os.path.join(self.tmp, 'home')
        self.support = os.path.join(self.home, 'Library', 'Application Support', 'Roughcut')
        self.env = {'HOME': self.home, 'PATH': '/usr/bin:/bin', 'ROUGHCUT_PREFS': 'roughcut-launcher-test'}

    def run_launcher(self, *args):
        return subprocess.run(['/bin/bash', self.launcher, *args], capture_output=True, text=True, env=self.env)

    def test_the_engine_of_the_app_installed(self):
        r = self.run_launcher('show.py', 'x')
        self.assertEqual(r.returncode, 3)  # no tools yet: said plainly
        self.assertIn('open Roughcut once', r.stderr)
        runtime = os.path.join(self.support, 'engine-7')  # the engine version the app says
        os.makedirs(os.path.join(runtime, 'python', 'bin'))
        os.makedirs(os.path.join(runtime, 'bin'))
        os.symlink(sys.executable, os.path.join(runtime, 'python', 'bin', 'python3'))
        os.makedirs(os.path.join(self.home, '.cache', 'whisper-cpp'))
        open(os.path.join(self.home, '.cache', 'whisper-cpp', 'ggml-large-v3-turbo.bin'), 'w').close()
        r = self.run_launcher('show.py', "L'été 🎬 $(touch x)")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        env = out['env']
        self.assertEqual(out['args'], ["L'été 🎬 $(touch x)"])
        self.assertTrue(env['PATH'].startswith(os.path.join(runtime, 'bin') + ':'))
        self.assertNotIn('/opt/homebrew', env['PATH'])
        self.assertEqual((env['WHISPER_MODEL'], env['WHISPER_MODEL_DIR']), ('ggml-large-v3-turbo.bin', os.path.join(self.home, '.cache', 'whisper-cpp')))
        self.assertEqual(env['ROUGHCUT_PROJECTS'], os.path.join(self.home, 'Movies', 'Roughcut'))
        self.assertEqual(env['ROUGHCUT_VERSION'], '9.9.9')
        self.assertIn(env['ROUGHCUT_LANG'], ('fr', 'en'))
        self.assertEqual(self.run_launcher('--version').stdout.strip(), 'Roughcut 9.9.9, engine 7')
        self.env.update(ROUGHCUT_PROJECTS='/elsewhere', WHISPER_MODEL='ggml-large-v3.bin', ROUGHCUT_LANG='fr')  # what the app sets wins
        env = json.loads(self.run_launcher('show.py').stdout)['env']
        self.assertEqual((env['ROUGHCUT_PROJECTS'], env['WHISPER_MODEL'], env['ROUGHCUT_LANG']), ('/elsewhere', 'ggml-large-v3.bin', 'fr'))

    def test_the_quick_action_runs_the_launcher_of_the_app(self):
        record = os.path.join(self.tmp, 'called.txt')
        with open(self.launcher, 'w') as f:  # a stand-in launcher that notes how it was called
            f.write(f'printf "%s\\n" "$@" > {shlex.quote(record)}\n')
        path = install_quick_action.install('Edit', '/usr/bin/python3', '/unused', services=self.tmp, app=self.app)
        with open(os.path.join(path, 'Contents', 'document.wflow'), 'rb') as f:
            script = plistlib.load(f)['actions'][0]['action']['ActionParameters']['COMMAND_STRING']
        folder = os.path.join(self.tmp, "L'été 🎬 $(touch pwned) ; -n")
        subprocess.run(['/bin/zsh', '-c', script, 'quick-action', folder], check=True, capture_output=True)
        for _ in range(50):  # it runs in the background
            if os.path.exists(record):
                break
            time.sleep(0.1)
        time.sleep(0.2)
        with open(record) as f:
            self.assertEqual(f.read().splitlines(), ['auto_edit.py', folder])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'pwned')))


class WordsTest(unittest.TestCase):
    def test_transcript_flags_pauses_and_hesitations(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        seg = lambda t, a, b: {'text': ' ' + t, 'offsets': {'from': a, 'to': b}}  # noqa: E731
        path = os.path.join(tmp, 'clip.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'transcription': [seg('Hello', 0, 400), seg('there.', 400, 900), seg('[Music]', 900, 2000),
                                         seg('Euh...', 2000, 2300), seg('Next.', 3500, 3900),
                                         seg('Thank', 4000, 23000), seg('you.', 23000, 33000)]}, f)
        with mock.patch.object(sys, 'argv', ['words.py', path]), contextlib.redirect_stdout(io.StringIO()):
            words.main()
        with open(os.path.join(tmp, 'clip.txt'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('Hello there.', text)
        self.assertIn('(pause 1.1 s)', text)
        self.assertIn('[Euh...]', text)
        self.assertNotIn('Music', text)
        self.assertIn('{Thank} {you.}', text)  # words lasting 19 s and 10 s: invented on silence

    def test_words_sharing_one_timestamp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        seg = lambda t, a, b: {'text': ' ' + t, 'offsets': {'from': a, 'to': b}}  # noqa: E731
        said = [seg(w, 1000 + 300 * i, 1300 + 300 * i) for i, w in enumerate('it is not that different here'.split())]
        twice = [seg(w, 4000, 4000) for w in 'is not that different here'.split()]  # the same words again, no duration
        short = [seg(w, 4700, 4700) for w in 'it is not'.split()]  # three words: may really have been said twice
        late = [seg(w, 9000, 9000) for w in 'After the long pause'.split()] + [seg('then.', 9000, 9400)]
        path = os.path.join(tmp, 'clip.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'transcription': said + twice + [seg('Right.', 4200, 4600)] + short + [seg('so.', 4800, 5000)] + late}, f)
        with mock.patch.object(sys, 'argv', ['words.py', path]), contextlib.redirect_stdout(io.StringIO()):
            words.main()
        with open(os.path.join(tmp, 'clip.words.json'), encoding='utf-8') as f:
            got = json.load(f)['words']
        flags = {w['w']: (w['suspect'], w.get('untimed', False)) for w in got[6:]}
        self.assertEqual(flags['different'], (True, True))  # written twice by Whisper
        self.assertEqual(flags['Right.'], (False, False))
        self.assertEqual(flags['it'], (False, True))
        self.assertEqual(flags['pause'], (False, True))  # real speech, timing lost
        with open(os.path.join(tmp, 'clip.txt'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('{is} {not} {that} {different} {here}', text)
        self.assertIn('≈After the long pause', text)

    def test_french_punctuation_and_names(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        seg = lambda t, a, b: {'text': ' ' + t, 'offsets': {'from': a, 'to': b}}  # noqa: E731
        path = os.path.join(tmp, 'clip.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'transcription': [seg('Merci', 0, 300), seg('Ben', 300, 600), seg('!', 600, 650),
                                         seg('ben', 1000, 1200), seg('ça', 1200, 1400), seg('va', 1400, 1600),
                                         seg('?', 1600, 1700)]}, f)
        with mock.patch.object(sys, 'argv', ['words.py', path]), contextlib.redirect_stdout(io.StringIO()):
            words.main()
        with open(os.path.join(tmp, 'clip.words.json'), encoding='utf-8') as f:
            got = [(w['w'], w['filler']) for w in json.load(f)['words']]
        # "Ben" is a name, lowercase "ben" is a hesitation; " !" and " ?" belong to the previous word
        self.assertEqual(got, [('Merci', False), ('Ben !', False), ('ben', True), ('ça', False), ('va ?', False)])


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs ffmpeg')
def said(text, start=0.0, step=0.3, gaps=None):
    """Words of `text` one after the other, `step` seconds each; gaps: {word index: pause before it}."""
    out, t = [], start
    for i, w in enumerate(text.split()):
        t += (gaps or {}).get(i, 0.0)
        out.append({'w': w, 'start': round(t, 3), 'end': round(t + step, 3), 'filler': words.is_filler(w)})
        t += step
    return out


class CutTest(unittest.TestCase):
    """Cutting inside what is kept: words said again, long sentences, pauses heard in the sound."""
    def marked(self, text):
        ws = said(text)
        words.flag_repeats(ws)
        return ' '.join(f'[{w["w"]}]' if w.get('repeat') else w['w'] for w in ws)

    def test_words_said_again_at_once(self):
        self.assertEqual(self.marked('le le chat'), '[le] le chat')
        self.assertEqual(self.marked('on va euh on va aller voir'), '[on] [va] euh on va aller voir')  # hesitation aside
        self.assertEqual(self.marked('un petit peu un petit peu tout'), '[un] [petit] [peu] un petit peu tout')
        self.assertEqual(self.marked('très très bien'), 'très très bien')  # on purpose
        self.assertEqual(self.marked('nous nous sommes vus'), 'nous nous sommes vus')
        self.assertEqual(self.marked('je montre un petit peu tout ça un petit peu'), 'je montre un petit peu tout ça un petit peu')

    def test_a_long_sentence_is_cut_where_the_speaker_breathes(self):
        ws = said(' '.join(f'w{i}' for i in range(90)), gaps={31: 0.25, 62: 0.3})  # 27 s, no punctuation
        got = auto_edit.sentences(ws, max_seconds=12)
        self.assertEqual(len(got), 3)
        self.assertTrue(got[1][2].startswith('w31') and got[2][2].startswith('w62'), got)
        self.assertTrue(all(b - a <= 12 for a, b, _ in got))
        self.assertEqual(len(auto_edit.sentences(ws, max_seconds=60)), 1)

    def test_repeated_words_are_bracketed_for_the_brain(self):
        ws = said('on va on va voir le lac.')
        words.flag_repeats(ws)
        self.assertEqual(auto_edit.sentences(ws)[0][2], '[on] [va] on va voir le lac.')

    def test_words_fitted_to_the_pauses_heard(self):
        spans = [(2.0, 3.0), (6.0, 9.0)]
        ws = [{'w': 'spills', 'start': 0.5, 'end': 2.8},     # runs on into the pause
              {'w': 'early', 'start': 2.7, 'end': 2.95},     # placed inside the pause, near its end
              {'w': 'next', 'start': 3.1, 'end': 3.5},
              {'w': 'late', 'start': 5.5, 'end': 6.4},       # starts before the pause, ends inside
              {'w': 'said', 'start': 6.5, 'end': 6.9},       # inside, nearer the start: said just before it
              {'w': 'invented', 'start': 8.2, 'end': 8.6}]   # inside, with nothing said after: left alone
        pauses.tighten(ws, spans)
        got = {w['w']: (w['start'], w['end']) for w in ws}
        self.assertEqual(got['spills'], (0.5, 2.02))
        self.assertEqual(got['early'], (2.98, 3.1))
        self.assertEqual(got['next'], (3.1, 3.5))
        self.assertEqual(got['late'], (5.5, 6.02))
        self.assertEqual(got['said'], (5.97, 6.02))  # squeezed in, not over the word before
        self.assertEqual(got['invented'], (8.2, 8.6))

    @unittest.skipUnless(shutil.which('ffmpeg') and importlib.util.find_spec('numpy'), 'needs ffmpeg and numpy')
    def test_pauses_heard_in_the_sound(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        clip = os.path.join(tmp, 'talk.wav')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        "aevalsrc='0.3*sin(2*PI*220*t)*(between(t,0,2)+between(t,3,5)+between(t,5.4,7))+0.002*random(0)':d=7:s=16000",
                        clip], check=True)
        spans = pauses.quiet_spans(pauses.levels(clip), 0.35, 0.2)
        self.assertEqual(len(spans), 2, spans)
        for (a, b), (x, y) in zip(spans, [(2.0, 3.0), (5.0, 5.4)]):
            self.assertAlmostEqual(a, x, delta=0.04)
            self.assertAlmostEqual(b, y, delta=0.04)


class ICloudTest(unittest.TestCase):
    def test_clips_still_in_icloud_are_found_before_reading_them(self):
        flags = {'here.mp4': 0, 'cloud.mp4': auto_edit.DATALESS | 0x20}
        fake = lambda f: type('S', (), {'st_flags': flags[f]})()  # noqa: E731
        self.assertEqual(auto_edit.not_downloaded(list(flags), stat=fake), ['cloud.mp4'])
        self.assertIn('Télécharger maintenant', auto_edit.UI['fr']['icloud'])


class DressingTest(Base):
    """The main edit dressed in Final Cut Pro's own titles and dissolves."""
    def edl(self, **more):
        clips = [{'file': 'dji.mp4', 'in': 30, 'out': 33, 'note': 'hook'},
                 {'file': 'dji.mp4', 'in': 1, 'out': 26, 'chapter': 'Le matin'},
                 {'file': 'cam.mp4', 'in': 2, 'out': 8, 'chapter': 'Le parc'},
                 {'file': 'dji.mp4', 'in': 27, 'out': 33},
                 {'file': 'dji.mp4', 'in': 0, 'out': 5, 'chapter': 'Le marché'}]  # from its first frame: no dissolve
        return self.write_edl(clips=clips, **more)

    def made(self, level, **more):
        self.edl(**more)
        out = self.run_script(make_fcpxml, self.path('edl.json'), '-o', self.path('out.fcpxml'), '--dressing', level, '--language', 'fr')
        return ET.parse(self.path('out.fcpxml')).getroot(), out

    def test_full_dressing(self):
        root, out = self.made('full', interviews=[{'file': self.path('cam.mp4'), 'at': 5.0, 'who': "l'invité"}])
        titles = [(t.get('name'), [x.text for x in t.findall('text')]) for t in root.iter('title')]
        self.assertEqual(titles, [('Le matin', ['Le matin']), ('Le parc', ['Le parc']),
                                  ("Bandeau – l'invité", ['Prénom Nom', 'Pays']), ('Le marché', ['Le marché'])])
        uids = {e.get('name'): e.get('uid') for e in root.iter('effect')}
        self.assertEqual(uids, {'Basic Title': dressing.TITLE[1], 'Basic Lower Third': dressing.LOWER_THIRD[1],
                                'Cross Dissolve': dressing.CROSS_DISSOLVE[1]})
        spine = list(root.find('.//spine'))
        self.assertEqual([e.tag for e in spine], ['asset-clip', 'asset-clip', 'transition', 'asset-clip', 'asset-clip', 'asset-clip'])
        dissolve, park = spine[2], spine[3]
        self.assertEqual(secs(dissolve.get('offset')) + secs(dissolve.get('duration')) / 2, secs(park.get('offset')))
        lower = [t for t in root.iter('title') if t.get('name').startswith('Bandeau')][0]
        parent = [c for c in spine if lower in list(c)][0]
        self.assertAlmostEqual(float(secs(lower.get('offset')) - secs(parent.get('start'))), 3.0, delta=0.05)  # 5 s: 3 s into its clip
        self.assertIn('Dressing (full): 3 chapter titles, 0 times of day, 1 lower thirds, 1 dissolves (0 shorter, for want of media; '
                      '1 chapters left as cuts)', out)
        self.assertEqual(secs(dissolve.get('duration')), Fraction(30 * 1001, 60000))  # half a second: 30 frames at 59.94

    def test_light_and_none(self):
        root, _ = self.made('light')
        self.assertEqual(len(list(root.iter('title'))), 3)
        self.assertEqual(len(list(root.iter('transition'))), 1)
        root, _ = self.made('none')
        self.assertEqual((len(list(root.iter('title'))), len(list(root.iter('transition')))), (0, 0))

    def test_a_dissolve_without_media_is_refused_by_the_check(self):
        self.made('light')
        root = ET.parse(self.path('out.fcpxml'))
        root.find('.//transition').set('duration', '20s')  # 10 s before a clip that starts 2 s into its file
        root.write(self.path('bad.fcpxml'))
        self.assertTrue(any('transition' in p for p in REAL_MEDIA_PROBLEMS(self.path('bad.fcpxml'))))

    def test_a_shorter_dissolve_where_media_is_short(self):
        self.edl()
        _, _, _, clips = timeline.build(self.path('edl.json'))
        clips[2]['in_s'] = 0.25  # the park clip starts 0.25 s into its file: 6 frames each side (one kept spare)
        clips[4]['in_s'] = 0.1  # too short to be seen, the cut stays
        self.assertEqual(dressing.dissolves(clips, 0.5, 30000 / 1001), {2: 12})

    def test_times_of_day(self):
        self.edl()
        _, _, _, clips = timeline.build(self.path('edl.json'))
        when = lambda f: datetime.datetime(2026, 1, 15, 10, 15) if 'dji' in f else datetime.datetime(2026, 1, 15, 18, 50)  # noqa: E731
        cfg = settings.load()['dressing']
        # not during the hook; at the start of the story; with a chapter filmed later; a jump of the clock 6 s after
        # the last time shown, no
        self.assertEqual(dressing.time_labels(clips, cfg, 'fr', when), [(1, '10h15'), (2, '18h50'), (4, '10h15')])
        self.assertEqual(dressing.time_labels(clips, cfg, 'en', when)[1], (2, '6:50 PM'))

    def test_when_a_clip_was_filmed(self):
        self.assertEqual(dressing.recorded_at('/x/DJI_20260115101520_0001_D.MP4', probe=lambda p: None),
                         datetime.datetime(2026, 1, 15, 10, 15, 20))
        self.assertEqual(dressing.recorded_at('/x/IMG_1234.MOV', probe=lambda p: '2026-01-15T08:05:10-0500'),
                         datetime.datetime(2026, 1, 15, 8, 5, 10))  # the time where it was filmed, not UTC
        self.assertIsNone(dressing.recorded_at('/x/clip.mp4', probe=lambda p: None))

    def test_chapters_start_on_the_way_there(self):
        ranges = [{'file': 'a', 'from': 5, 'to': 9, 'note': 'hook'}, {'file': 'a', 'from': 0, 'to': 4},
                  {'file': 'b', 'from': 0, 'to': 4, 'whole': True}, {'file': 'b', 'from': 9, 'to': 13, 'whole': True},
                  {'file': 'c', 'from': 1, 'to': 8, 'chapter': 'Le marché'}]
        out = auto_edit.chapters_first(ranges, 'Introduction')
        self.assertEqual([r.get('chapter') for r in out], [None, 'Introduction', 'Le marché', None, None])
        morning = [{'file': 'a', 'from': 5, 'to': 9, 'note': 'hook'}] + [{'file': 'b', 'from': 4 * n, 'to': 4 * n + 4, 'whole': True}
                                                                          for n in range(4)] + [{'file': 'c', 'from': 1, 'to': 8, 'chapter': 'En route'}]
        out = auto_edit.chapters_first(morning, 'Introduction')
        self.assertEqual([r.get('chapter') for r in out], [None, 'Introduction', None, 'En route', None, None])  # two moments before


class SubtitlesTest(Base):
    """SRT files in two languages, slips fixed, words to check, and corrections kept."""
    WORDS = [('on', 0.2), ('est', 0.5), ('allés', 0.8), ('à', 1.2), ('paris', 1.4), ('hier.', 2.0),
             ('It', 4.0), ('was', 4.3), ('really', 4.6), ('nice.', 5.0),
             ('le', 7.0), ('conte', 7.3), ('est', 7.8), ('bon', 8.2), ('merci.', 8.5)]

    def setUp(self):
        super().setUp()
        os.makedirs(self.path('transcripts'))
        with open(self.path('transcripts/cam.words.json'), 'w', encoding='utf-8') as f:
            json.dump({'words': [{'w': w, 'start': t, 'end': t + 0.25} for w, t in self.WORDS]}, f)
        with open(self.path('transcripts/cam.json'), 'w', encoding='utf-8') as f:
            json.dump({'result': {'language': 'fr'}, 'languages': [[0, 3.5, 'fr'], [3.5, 6.5, 'en'], [6.5, 11, 'fr']]}, f)
        self.write_edl(clips=[{'file': 'cam.mp4', 'in': 0, 'out': 11}])
        self.asked = []

    def brain(self, prompt, cfg):
        self.asked.append(re.findall(r'^(\d+) \[(\w+)\] (.*)$', prompt, re.M))
        lines, fixes, checks = {}, [], []
        for i, lang, text in self.asked[-1]:
            if 'paris' in text:
                lines[i] = {'lang': 'fr', 'en': 'We went to Paris yesterday.'}
                fixes.append({'id': int(i), 'word': 'paris', 'now': 'Paris'})
            elif 'nice' in text:
                lines[i] = {'lang': 'en', 'fr': "C'était vraiment bien."}
            else:
                lines[i] = {'lang': 'fr', 'en': ('The total' if 'compte' in text else 'The tale') + ' is right, thanks.'}
                if 'conte' in text:
                    fixes.append({'id': int(i), 'word': 'conte', 'now': 'compte'})  # not a slip of spelling: asked
                    checks.append({'id': int(i), 'word': 'conte', 'why': 'sans doute « compte »'})
        return {'lines': lines, 'fix': fixes, 'check': checks}, 'claude', None

    def build(self):
        cfg = settings.load(self.dir)
        cfg['ui']['language'] = 'fr'
        with mock.patch.object(brain, 'ask_json', self.brain):
            subtitles.build_all(self.dir, cfg, log=lambda *a: None)

    def read(self, name):
        with open(self.path(name), encoding='utf-8') as f:
            return f.read()

    def test_two_languages_slips_fixed_and_words_to_check(self):
        self.build()
        self.assertEqual(len(self.asked), 1)  # one question; the fixed line keeps its answer
        fr, en = self.read('subtitles.fr.srt'), self.read('subtitles.en.srt')
        self.assertIn('on est allés à Paris hier.', fr)  # the slip fixed
        self.assertIn("C'était vraiment bien.", fr)  # the English line, translated
        self.assertIn('It was really nice.', en)
        self.assertIn('We went to Paris yesterday.', en)
        self.assertIn('le conte est bon', fr)  # a word that may be wrong is never changed without a person
        checks = json.loads(self.read('checks.json'))
        self.assertEqual([(c['word'], c['at']) for c in checks['checks']], [('conte', 7.3)])
        self.assertIn('conte: sans doute « compte »', self.read('to-check.txt'))
        self.assertIn('Paris', self.read('captions.txt'))
        self.assertEqual(subtitles.guess_language('et ça, c\'est vraiment bien', 'en'), 'fr')
        self.assertEqual(subtitles.guess_language('we are free to do what we want', 'fr'), 'en')
        self.assertEqual(subtitles.guess_language('你好', 'fr'), 'zh')
        self.assertEqual(subtitles.guess_language('Tokyo', 'fr'), 'fr')  # nothing to go on: Whisper's

    def test_a_word_fixed_in_captions_txt_is_kept(self):
        self.build()
        text = self.read('captions.txt').replace('le conte est bon', 'le compte est bon')
        with open(self.path('captions.txt'), 'w', encoding='utf-8') as f:
            f.write(text)
        self.assertEqual(subtitles.keep_corrections([self.dir]), 1)
        self.build()
        self.assertEqual([[x[2] for x in q if not x[2].startswith('(context)')] for q in self.asked[1:]],
                         [['le compte est bon merci.']])  # only the line changed
        self.assertIn('The total is right, thanks.', self.read('subtitles.en.srt'))
        _, _, _, clips = timeline.build(self.path('edl.json'))
        said = [w for _, _, w in make_srt.timeline_words(clips, self.path('transcripts'))]
        self.assertIn('compte', said)  # for the animated captions, and every later version of the edit
        self.assertNotIn('conte', said)

    def test_changes_to_a_line(self):
        cue = [(0, 1, w, 'cam', s) for w, s in (('un', 1.0), ('petit', 1.5), ('cha', 2.0), ('en', 2.5), ('plus', 3.0))]
        self.assertEqual(subtitles.changes(cue, 'un petit chat en plus'.split()), [('cam', 2.0, 'cha', 'chat')])
        self.assertEqual(subtitles.changes(cue, 'un chat en plus'.split()), [('cam', 1.5, 'petit', 'chat'), ('cam', 2.0, 'cha', '')])
        self.assertEqual(subtitles.changes(cue, 'un petit cha tout en plus'.split()), [('cam', 2.0, 'cha', 'cha tout')])
        self.assertTrue(subtitles.safe_fix('il ya', 'il y a'))
        self.assertFalse(subtitles.safe_fix('vers', 'verre'))
        self.assertEqual(subtitles.wrapped('a' * 20 + ' ' + 'b' * 30), 'a' * 20 + '\n' + 'b' * 30)


class HesitationTest(unittest.TestCase):
    """The held vowels of hesitations, and the silences to cut in, read in a sound made for the test."""
    RATE = hesitations.RATE

    def sound(self, parts):
        """[(kind, seconds)] with kind 'silence', 'word' (a voice changing pitch) or 'euh' (one note held)."""
        import numpy as np
        rng, out = np.random.default_rng(1), []
        for kind, secs in parts:
            t = np.arange(int(secs * self.RATE)) / self.RATE
            if kind == 'silence':
                out.append(rng.normal(0, 0.0005, len(t)))
            else:
                f0 = 120 + (np.zeros_like(t) if kind == 'euh' else 60 * np.sin(2 * np.pi * 7 * t))  # a word glides, "euh" holds
                phase = 2 * np.pi * np.cumsum(f0) / self.RATE
                voice = sum(np.sin(k * phase) / k for k in range(1, 12))
                if kind == 'word':
                    voice *= 0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 5 * t))  # syllables
                out.append(0.1 * voice)
        return hesitations.Sound(np.concatenate(out).astype(np.float32))

    def test_a_held_note_between_two_words(self):
        snd = self.sound([('silence', 0.5), ('word', 0.6), ('silence', 0.15), ('euh', 0.5), ('silence', 0.15), ('word', 0.6), ('silence', 0.5)])
        vowels = snd.held_vowels()
        self.assertEqual(len(vowels), 1)
        a, b, pitch = vowels[0]
        self.assertAlmostEqual(a, 1.25, delta=0.06)
        self.assertAlmostEqual(b, 1.75, delta=0.06)
        self.assertAlmostEqual(pitch, 120, delta=5)
        words = [{'w': 'donc', 'start': 0.5, 'end': 1.1}, {'w': 'voilà.', 'start': 1.9, 'end': 2.5}]
        found = hesitations.found(words, snd)
        self.assertEqual([(h['by'], h['word']) for h in found], [('sound', None)])  # Whisper left it out
        whisper = words[:1] + [{'w': 'euh', 'start': 1.0, 'end': 1.9, 'filler': True}] + words[1:]
        found = hesitations.found(whisper, snd)
        self.assertEqual([(h['by'], h['word']) for h in found], [('both', 1)])  # written, and put where it is heard
        self.assertAlmostEqual(found[0]['start'], 1.25, delta=0.06)
        quiet = snd.quiet_between(1.0, 2.0)
        self.assertTrue(any(q[0] < 1.2 and q[1] > 1.12 for q in quiet))  # the silences on either side

    def test_a_word_said_slowly_is_not_a_hesitation(self):
        snd = self.sound([('silence', 0.5), ('euh', 0.6), ('silence', 0.5)])
        self.assertEqual(hesitations.found([{'w': 'ouiii', 'start': 0.45, 'end': 0.6}, {'w': 'bon', 'start': 1.2, 'end': 1.5}], snd), [])
        # a word Whisper stretched over the hesitation next to it: the held vowel is past what the word takes
        self.assertTrue(hesitations.clear_of((0.65, 1.0), [{'w': 'et', 'start': 0.4, 'end': 1.0}]))
        self.assertFalse(hesitations.clear_of((0.45, 0.7), [{'w': 'et', 'start': 0.4, 'end': 1.0}]))

    def test_cuts_fall_in_true_silences(self):
        snd = self.sound([('silence', 0.5), ('word', 0.6), ('silence', 0.3), ('euh', 0.5), ('silence', 0.3), ('word', 0.6), ('silence', 0.5)])
        words = [{'w': 'donc', 'start': 0.55, 'end': 1.0},  # Whisper ends it early: the word runs to 1.1
                 {'w': 'euh', 'start': 1.4, 'end': 1.9, 'filler': True},
                 {'w': 'voilà.', 'start': 2.3, 'end': 2.8}]
        found = edl_from_ranges.cuts(words, 0, 3.5, 0.5, 0.08, 0.12)
        placed = edl_from_ranges.on_silence(found, words, snd)
        self.assertEqual(len(placed), 2)  # the hesitation cut out
        (a1, b1, _), (a2, b2, _) = placed
        self.assertTrue(1.1 <= b1 <= 1.4, b1)  # after the whole word, before the hesitation
        self.assertTrue(1.9 <= a2 <= 2.2, a2)  # after the hesitation, before the next word
        self.assertLessEqual(a1, 0.5)

    def test_a_hesitation_that_would_break_the_sentence_stays(self):
        words = [{'w': 'et', 'start': 1.0, 'end': 1.15}, {'w': 'euh', 'start': 1.2, 'end': 1.5, 'filler': True},
                 {'w': 'donc', 'start': 1.55, 'end': 1.8}, {'w': 'euh', 'start': 1.85, 'end': 2.2, 'filler': True},
                 {'w': 'voilà', 'start': 2.25, 'end': 2.6}]
        found = edl_from_ranges.cuts(words, 0, 3, 0.5, 0.05, 0.05)
        # "donc" alone between two cuts would flash by: the hesitation before it stays; the one after is cut
        self.assertEqual([[w['w'] for w in g] for _, _, g in found], [['et', 'donc'], ['voilà']])

    def test_a_hesitation_touching_the_words_is_cut_only_when_the_join_sounds_right(self):
        snd = self.sound([('silence', 0.5), ('word', 0.6), ('euh', 0.4), ('word', 0.6), ('silence', 0.5)])
        words = [{'w': 'donc', 'start': 0.5, 'end': 1.1}, {'w': 'euh', 'start': 1.1, 'end': 1.5, 'filler': True},
                 {'w': 'voilà', 'start': 1.5, 'end': 2.1}]
        found = [(0.45, 1.1, [words[0]]), (1.5, 2.15, [words[2]])]  # cut out, with no silence on either side
        kept, dips = edl_from_ranges.splices(found, words, snd, 'clean')
        self.assertEqual(len(kept), 1)  # "clean only": it stays
        kept, dips = edl_from_ranges.splices(found, words, snd, 'all')
        self.assertEqual((len(kept), dips), (2, {1}))  # two words of one voice: the join passes, listened to later
        kept, dips = edl_from_ranges.splices(found, words, snd, 'all', level=0.1, timbre=0.01)
        self.assertEqual(len(kept), 1)  # a join that jumps more than allowed: the hesitation stays

    def test_whisper_repeating_its_prompt_is_invented(self):
        ws = [{'w': x, 'start': i, 'end': i + 0.5, 'suspect': False} for i, x in enumerate('Bon, ben, euh, on y va, hein.'.split())]
        self.assertGreaterEqual(words.flag_prompt_echo(ws), 4)
        said = [{'w': x, 'start': i, 'end': i + 0.5, 'suspect': False} for i, x in enumerate('on y va au lac'.split())]
        self.assertEqual(words.flag_prompt_echo(said), 0)


class VoiceIsolationTest(Base):
    def test_only_where_the_background_is_loud(self):
        cfg = settings.load()
        clip = {'file': self.path('cam.mp4')}
        for snr, want in ((20.0, 40.0), (44.0, 0)):
            with mock.patch.object(hesitations, 'speech_over_background', return_value=snr), \
                    mock.patch.object(hesitations, 'read', return_value=[0.0] * 16000), \
                    mock.patch('builtins.open', mock.mock_open(read_data='{"words": []}')):
                self.assertEqual(make_fcpxml.voice_isolation(clip, cfg, {}, self.dir), want)
        cfg['audio']['voice_isolation'] = 'off'
        self.assertEqual(make_fcpxml.voice_isolation(clip, cfg, {}, self.dir), 0)
        cfg['audio'].update(voice_isolation='always', voice_isolation_amount=150)
        self.assertEqual(make_fcpxml.voice_isolation(clip, cfg, {}, self.dir), 100.0)  # never past full

    def test_how_loud_the_background_is(self):
        import numpy as np
        t = HesitationTest('test_a_held_note_between_two_words')
        snd = t.sound([('silence', 1.0)] + [('word', 0.6), ('silence', 0.4)] * 12)
        words = [{'w': 'mot', 'start': 1.0 + k, 'end': 1.6 + k} for k in range(12)]
        self.assertGreater(hesitations.speech_over_background(snd, words), 30)  # a quiet room
        self.assertIsNone(hesitations.speech_over_background(snd, words[:3]))  # too little said to tell


class VerifyTest(unittest.TestCase):
    """The edit listened to once assembled: what is heard set against what was to be kept."""
    def test_compare_and_time_on_the_edit(self):
        expected = [(0.0, 0.3, 'on', 'cam', 5.0), (0.3, 0.6, 'y', 'cam', 5.3), (0.6, 1.0, 'va', 'cam', 5.6),
                    (1.2, 1.6, 'au', 'cam', 8.0), (1.6, 2.0, 'lac.', 'cam', 8.4)]
        heard = [{'w': 'On', 'start': 0.05, 'end': 0.3}, {'w': 'y', 'start': 0.3, 'end': 0.55}, {'w': 'va,', 'start': 0.55, 'end': 0.95},
                 {'w': 'euh,', 'start': 1.0, 'end': 1.2, 'filler': True}, {'w': 'lac.', 'start': 1.7, 'end': 2.0}]
        hes, missing, scraps = verify_edit.compare(expected, heard)
        self.assertEqual([h[2] for h in hes], ['euh,'])
        self.assertEqual([m[2] for m in missing], ['au'])
        self.assertEqual(scraps, [])
        timed = verify_edit.timed(expected, heard)
        self.assertEqual([round(w[0], 2) for w in timed], [0.05, 0.3, 0.55, 1.23, 1.7])  # "au", not heard: moved like its neighbours
        self.assertEqual(timed[3][2:], ['au', 'cam', 8.0])

    def test_a_word_matched_elsewhere_keeps_its_place(self):
        clips = [{'off_s': 0.0, 'dur_s': 2.0, 'in_s': 5.0}, {'off_s': 2.0, 'dur_s': 2.0, 'in_s': 20.0}]
        expected = [(0.1, 0.4, 'et', 'cam', 5.1), (2.2, 2.5, 'voilà', 'cam', 20.2)]
        heard = [{'w': 'Et', 'start': 3.5, 'end': 3.7}, {'w': 'voilà', 'start': 2.25, 'end': 2.5}]  # "et" heard in the next cut
        timed = verify_edit.timed(expected, heard, clips)
        self.assertLess(timed[0][0], 2.0)  # never out of its own cut
        self.assertEqual(timed[1][0], 2.25)

    def test_a_join_whose_words_are_not_heard_whole_goes_back(self):
        edl = {'clips': [{'file': 'cam.mp4', 'in': 5.0, 'out': 6.0}, {'file': 'cam.mp4', 'in': 6.5, 'out': 8.0, 'splice': 'dip'}]}
        clips = [{'off_s': 0.0, 'dur_s': 1.0, 'in_s': 5.0}, {'off_s': 1.0, 'dur_s': 1.5, 'in_s': 6.5}]
        expected = [(0.2, 0.5, 'on', 'cam', 5.2), (0.5, 0.9, 'va', 'cam', 5.5), (1.0, 1.4, 'au', 'cam', 6.5), (1.4, 1.9, 'lac', 'cam', 6.9)]
        whole = [{'w': w, 'start': t, 'end': t + 0.3} for w, t in (('on', 0.2), ('va', 0.5), ('au', 1.0), ('lac', 1.4))]
        self.assertEqual(verify_edit.joins_heard(edl, clips, expected, whole), [])
        clipped = [{'w': w, 'start': t, 'end': t + 0.3} for w, t in (('on', 0.2), ('vaou', 0.6), ('lac', 1.4))]
        self.assertEqual(verify_edit.joins_heard(edl, clips, expected, clipped), [1])

    @unittest.skipUnless(importlib.util.find_spec('numpy'), 'needs numpy')
    def test_a_hesitation_cut_on_listening_again_passes_the_same_join_checks(self):
        snd = HesitationTest('test_a_held_note_between_two_words').sound(
            [('silence', 0.5), ('word', 0.6), ('euh', 0.4), ('word', 0.6), ('silence', 0.5)])
        clips = [{'file': 'cam.mp4', 'in_s': 0.45, 'dur_s': 1.7, 'off_s': 0.0}]
        todo = [(0, 'cut', 1.1, 1.5, '"euh"')]
        edl = {'clips': [{'file': 'cam.mp4', 'in': 0.45, 'out': 2.15}]}
        done, left = verify_edit.apply(edl, clips, todo, lambda f: snd, lambda f: [], level=0.1, timbre=0.01)
        self.assertEqual((done, len(edl['clips'])), ([], 1))  # a join that jumps: the hesitation stays
        self.assertEqual(left[0][2], 'its join would jump in level or sound')
        edl = {'clips': [{'file': 'cam.mp4', 'in': 0.45, 'out': 2.15}]}
        done, left = verify_edit.apply(edl, clips, todo, lambda f: snd, lambda f: [])
        self.assertEqual([c.get('splice') for c in edl['clips']], [None, 'dip'])  # cut; its join checked next time

    def test_a_join_failing_at_the_last_listening_still_goes_back(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, 'edl.json')
        with open(path, 'w') as f:
            json.dump({'clips': [{'file': 'cam.mp4', 'in': 5.0, 'out': 6.0}, {'file': 'cam.mp4', 'in': 6.5, 'out': 8.0, 'splice': 'dip'}]}, f)

        def build(p):
            with open(p) as f:
                edl = json.load(f)
            clips, off = [], 0.0
            for c in edl['clips']:
                clips.append({**c, 'in_s': c['in'], 'dur_s': c['out'] - c['in'], 'off_s': off})
                off += c['out'] - c['in']
            return edl, {'fps': '30', 'total_f': int(off * 30)}, [], clips
        joins = lambda edl, clips, expected, heard: [1] if len(edl['clips']) == 2 else []  # noqa: E731
        with mock.patch.object(verify_edit.timeline, 'build', build), mock.patch.object(verify_edit, 'render'), \
                mock.patch.object(verify_edit, 'transcribe', return_value=[]), mock.patch.object(verify_edit, 'timeline_words', return_value=[]), \
                mock.patch.object(verify_edit, 'joins_heard', joins), mock.patch.object(verify_edit, 'fixes', return_value=[]), \
                mock.patch.object(sys, 'argv', ['verify_edit.py', path, '--rounds', '1', '--hesitations', 'all']):
            verify_edit.main()
        with open(path) as f:
            edl = json.load(f)
        self.assertEqual([(c['in'], c['out']) for c in edl['clips']], [(5.0, 8.0)])  # one cut again, the hesitation back in
        with open(os.path.join(d, 'final-words.json')) as f:
            self.assertEqual(json.load(f)['edl'], verify_edit.fingerprint(edl))  # and the words timed on that edit
        with open(os.path.join(d, 'verify.json')) as f:
            self.assertEqual([x['kind'] for r in json.load(f)['rounds'] for x in r.get('fixes', [])], ['rejoin'])

    def test_only_hesitations_cut_out_of_a_passage_are_listed(self):
        clips = [{'file': 'cam', 'in_s': 0.0, 'dur_s': 1.0, 'off_s': 0.0}, {'file': 'cam', 'in_s': 1.5, 'dur_s': 1.0, 'off_s': 1.0, 'splice': 'dip'},
                 {'file': 'cam', 'in_s': 10.0, 'dur_s': 1.0, 'off_s': 2.0}]
        words = [{'w': 'on', 'start': 0.2, 'end': 0.9}, {'w': 'euh', 'start': 1.05, 'end': 1.45, 'filler': True},
                 {'w': 'va', 'start': 1.6, 'end': 2.4}, {'w': 'mais', 'start': 3.0, 'end': 3.4},
                 {'w': 'euh', 'start': 5.0, 'end': 5.3, 'filler': True}, {'w': 'lac', 'start': 10.1, 'end': 10.9}]
        got = verify_edit.moments(clips, lambda f: words, [])
        self.assertEqual([(m['at'], m['kind']) for m in got], [(1.0, 'dip')])  # not the passage left out, with words in it

    def test_a_subtitle_timed_on_the_next_one_still_shows(self):
        cues = [[(10.0, 11.5, 'un')], [(12.0, 12.0, 'deux.')], [(12.0, 13.0, 'Trois')]]
        times = make_srt.cue_times(cues)
        self.assertTrue(all(b > a for a, b in times), times)  # each on screen for a while, in order
        self.assertLessEqual(times[0][1], times[1][0])
        self.assertLessEqual(times[1][1], times[2][0])
        # no time at all before it: joined to the one before rather than shown for no time
        cues = [[(1.0, 1.2, 'a')], [(1.0, 1.0, 'b')], [(1.0, 1.5, 'c')]]
        text = make_srt.srt(cues)
        self.assertNotIn('00:00:01,000 --> 00:00:01,000', text)
        self.assertEqual(text.count(' --> '), len([b for b in text.split('\n\n') if b.strip()]))

    def test_a_scrap_is_part_of_a_word_cut_out(self):
        clips = [{'file': 'cam.mp4', 'in_s': 10.0, 'dur_s': 3.0, 'off_s': 0.0}]
        words_of = lambda f: [{'w': 'pas', 'start': 9.7, 'end': 10.1}, {'w': 'bon', 'start': 10.3, 'end': 10.6}]  # noqa: E731
        self.assertTrue(verify_edit.fragment('pa', clips, 0.0, 0.1, words_of))
        self.assertFalse(verify_edit.fragment('bon', clips, 0.3, 0.6, words_of))  # a whole word, said there

    def test_the_words_of_an_edit_follow_its_sound(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        os.makedirs(os.path.join(d, 'transcripts'))
        with open(os.path.join(d, 'transcripts', 'cam.words.json'), 'w') as f:
            json.dump({'words': [{'w': 'on', 'start': 5.0, 'end': 5.3}, {'w': 'va', 'start': 5.6, 'end': 6.0}]}, f)
        edl = {'clips': [{'file': 'cam.mp4', 'in': 5.0, 'out': 6.0}]}
        with open(os.path.join(d, 'edl.json'), 'w') as f:
            json.dump(edl, f)
        with open(os.path.join(d, 'final-words.json'), 'w') as f:
            json.dump({'edl': verify_edit.fingerprint(edl), 'words': [[0.02, 0.28, 'on', 'cam', 5.0], [0.61, 0.97, 'va', 'cam', 5.6]]}, f)
        got = make_srt.edit_words(os.path.join(d, 'edl.json'), [], os.path.join(d, 'transcripts'))
        self.assertEqual(got, [(0.02, 0.28, 'on'), (0.61, 0.97, 'va')])
        with open(os.path.join(d, 'transcripts', 'corrections.json'), 'w') as f:
            json.dump({'cam': {'5.60': {'was': 'va', 'now': 'vas'}}}, f)
        self.assertEqual(make_srt.edit_words(os.path.join(d, 'edl.json'), [], os.path.join(d, 'transcripts'))[1][2], 'vas')
        # Whisper gave three words one start: each keeps its own
        with open(os.path.join(d, 'transcripts', 'cam.words.json'), 'w') as f:
            json.dump({'words': [{'w': w, 'start': 5.0, 'end': 5.0} for w in ('il', 'y', 'a')] + [{'w': 'va', 'start': 5.6, 'end': 6.0}]}, f)
        with open(os.path.join(d, 'final-words.json'), 'w') as f:
            json.dump({'edl': verify_edit.fingerprint(edl), 'words': [[0.0, 0.1, 'il', 'cam', 5.0], [0.1, 0.2, 'y', 'cam', 5.0],
                                                                       [0.2, 0.3, 'a', 'cam', 5.0], [0.6, 1.0, 'va', 'cam', 5.6]]}, f)
        got = make_srt.edit_words(os.path.join(d, 'edl.json'), [], os.path.join(d, 'transcripts'))
        self.assertEqual([w[2] for w in got], ['il', 'y', 'a', 'vas'])


class TextEditTest(unittest.TestCase):
    """Editing by the text: the transcript with what the edit keeps, and the choices made from the words."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.rushes = os.path.join(self.dir, 'rushes')
        os.makedirs(self.rushes)
        self.project = os.path.join(self.dir, 'project')
        os.makedirs(os.path.join(self.project, 'transcripts'))
        for name in ('a', 'b'):
            open(os.path.join(self.rushes, name + '.mp4'), 'w').close()
        said = {'a': 'Bonjour à tous. On va au lac. Il fait beau.', 'b': 'Voilà le lac. Merci.'}
        for name, text in said.items():
            with open(os.path.join(self.project, 'transcripts', name + '.words.json'), 'w') as f:
                json.dump({'words': [{'w': w, 'start': i * 0.5, 'end': i * 0.5 + 0.4} for i, w in enumerate(text.split())]}, f)
        a, b = os.path.join(self.rushes, 'a.mp4'), os.path.join(self.rushes, 'b.mp4')
        with open(os.path.join(self.project, 'edl.json'), 'w') as f:
            json.dump({'clips': [{'file': b, 'in': 0.0, 'out': 1.4, 'note': 'hook'}, {'file': a, 'in': 0.0, 'out': 3.4, 'chapter': 'Début'},
                                 {'file': b, 'in': 1.5, 'out': 2.4}]}, f)
        with open(os.path.join(self.project, 'ranges.json'), 'w') as f:
            json.dump({'ranges': [{'file': a, 'from': 0.0, 'to': 3.4, 'chapter': 'Début'}]}, f)
        with open(os.path.join(self.project, 'brain-answer.json'), 'w') as f:
            json.dump({'hook': {'rush': 'R2', 'from': 0.0, 'to': 1.4}, 'main': [], 'broll': [], 'irl': [], 'review': {'add': []}}, f)
        self.a, self.b = a, b

    def test_export_then_a_word_out_and_a_passage_back(self):
        with contextlib.redirect_stdout(io.StringIO()):
            text_edit.export(self.project)
        with open(os.path.join(self.project, 'text.json')) as f:
            clips = json.load(f)['clips']
        states = {c['rush']: [(w['w'], w['state']) for l in c['lines'] for w in l['words']] for c in clips}
        self.assertEqual(states['R1'][:3], [('Bonjour', 'kept'), ('à', 'kept'), ('tous.', 'kept')])
        self.assertEqual(states['R1'][-3:], [('Il', 'cut'), ('fait', 'cut'), ('beau.', 'cut')])
        self.assertEqual(states['R2'][:3], [('Voilà', 'hook'), ('le', 'hook'), ('lac.', 'hook')])
        edits = os.path.join(self.dir, 'edits.json')
        with open(edits, 'w') as f:
            json.dump({'words': [{'file': self.a, 'start': 1.5, 'kept': False},  # "On" out
                                 {'file': self.a, 'start': 3.5, 'kept': True}, {'file': self.a, 'start': 4.0, 'kept': True},
                                 {'file': self.a, 'start': 4.5, 'kept': True}]}, f)  # "Il fait beau." back
        with contextlib.redirect_stdout(io.StringIO()):
            path = text_edit.apply(self.project, edits)
        with open(path) as f:
            d = json.load(f)
        self.assertEqual([(r['rush'], r['from'], r['to'], r.get('chapter')) for r in d['main']],
                         [('R1', 0.0, 1.42, 'Début'), ('R1', 1.98, 4.92, None), ('R2', 1.48, 1.92, None)])
        self.assertEqual(d['hook'], {'rush': 'R2', 'from': 0.0, 'to': 1.42})
        self.assertEqual((d['review'], d['text_edited']), ({}, True))  # the review is in the words already


class ReviewTest(unittest.TestCase):
    """The edit read again as a viewer: what the brain sees, and its fixes applied within limits."""
    def setUp(self):
        self.sentences = {'a.mp4': [(0.0, 3.0, 'Intro.'), (3.0, 6.0, 'As I said before.'), (6.0, 9.0, 'Where are we?'),
                                    (9.0, 12.0, 'At the lake.')],
                          'b.mp4': [(0.0, 4.0, 'The hook.'), (4.0, 8.0, 'Goodbye.')]}
        self.rush_of, self.files = {'a.mp4': 'R1', 'b.mp4': 'R2'}, {'R1': 'a.mp4', 'R2': 'b.mp4'}
        self.durations = {'R1': 60.0, 'R2': 10.0}
        self.ranges = [{'file': 'b.mp4', 'from': 0.0, 'to': 4.0, 'note': 'hook'},
                       {'file': 'a.mp4', 'from': 0.0, 'to': 9.0, 'chapter': 'Start'},
                       {'file': 'a.mp4', 'from': 14.0, 'to': 18.0, 'whole': True, 'role': 'effects', 'marker': 'IRL lake'},
                       {'file': 'b.mp4', 'from': 4.0, 'to': 8.0}]
        self.lines = review.lines_of(self.ranges, self.sentences, self.rush_of)

    def apply(self, answer, shots=None):
        return review.apply(self.ranges, self.lines, answer, self.files, self.durations, shots or {}, auto_edit.irl_moment)

    def test_the_brain_reads_the_edit_in_order_next_to_what_was_cut(self):
        self.assertEqual([(x['id'], x['text']) for x in self.lines],
                         [('E1', 'The hook.'), ('E2', 'Intro.'), ('E3', 'As I said before.'), ('E4', 'Where are we?'),
                          ('E5', 'IRL lake'), ('E6', 'Goodbye.')])
        prompt = review.build_prompt(self.ranges, self.lines, self.sentences, self.rush_of, [
            {'id': 'B002', 'rush': 'R1', 'from': 30.0, 'to': 40.0, 'labels': ['water']}])
        self.assertIn('E1 [R2 0.00-4.00] (hook) The hook.', prompt)
        self.assertIn('E5 [shot R1 14.00-18.00] IRL lake', prompt)
        self.assertIn('[3.0-6.0] = E3', prompt)
        self.assertIn('[9.0-12.0] At the lake.', prompt)  # left out: its text
        self.assertIn('B002: R1 30.0-40.0 s, water', prompt)

    def test_fixes_are_put_where_the_brain_says(self):
        with mock.patch.object(review, 'REMOVED_SHARE', 0.5):
            out, done = self.apply({'remove': [{'line': 'E3', 'why': 'refers to a cut passage'}, {'line': 'E1'}],
                                    'add': [{'rush': 'R1', 'from': 9.0, 'to': 12.0, 'after': 'E4', 'why': 'the answer'},
                                            {'rush': 'R1', 'from': 20.0, 'to': 22.0, 'after': 'E0', 'why': 'first'},
                                            {'rush': 'R2', 'from': 0.0, 'to': 4.0, 'after': 'E5'}]})  # the hook: played already
        self.assertEqual([(r['file'], r['from'], r['to'], r.get('chapter')) for r in out],
                         [('b.mp4', 0.0, 4.0, None), ('a.mp4', 20.0, 22.0, 'Start'), ('a.mp4', 0.0, 3.0, None),
                          ('a.mp4', 6.0, 9.0, None), ('a.mp4', 9.0, 12.0, None), ('a.mp4', 14.0, 18.0, None),
                          ('b.mp4', 4.0, 8.0, None)])
        self.assertEqual(out[0]['note'], 'hook')  # the hook is never taken out
        self.assertEqual(out[4]['marker'], 'Review: the answer')  # Final Cut Pro shows what the review added
        self.assertEqual([k for k, _, _ in done], ['removed', 'added', 'added'])

    def test_a_shot_to_show_the_way_there(self):
        shots = {'B002': {'id': 'B002', 'file': 'a.mp4', 'from': 30.0, 'to': 40.0, 'best': 35.0}}
        out, _ = self.apply({'add_shot': [{'shot': 'B002', 'after': 'E4', 'why': 'arriving'}, {'shot': 'B002', 'after': 'E2'}]}, shots)
        self.assertEqual([(r['from'], r.get('whole')) for r in out if r['file'] == 'a.mp4'],
                         [(0.0, None), (33.0, True), (14.0, True)])  # once, with its own sound
        self.assertEqual(out[2]['role'], 'effects')

    def test_the_review_mends_but_never_remakes_the_edit(self):
        out, done = self.apply({'remove': [{'line': 'E3'}],  # 3 s of a 21-second edit: more than 10 %
                                'add': [{'rush': 'R1', 'from': 0.0, 'to': 50.0, 'after': 'E2'},  # too long at once
                                        {'rush': 'R9', 'from': 1.0, 'to': 2.0, 'after': 'E2'}, 'nonsense']})
        self.assertEqual(out, self.ranges)
        self.assertEqual([k for k, _, _ in done], ['skipped'])
        self.assertEqual(self.apply('not a dict'), (self.ranges, []))
        with mock.patch.object(review, 'ADDED_MIN', 30.0):
            out, done = self.apply({'add': [{'rush': 'R1', 'from': 20.0, 'to': 59.0, 'after': 'E2'}]})
        self.assertEqual(done[0][0], 'skipped')


class ShortChoiceTest(unittest.TestCase):
    """The Short never comes from the opening of the video."""
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cache)
        os.makedirs(os.path.join(self.cache, 'transcripts'))
        self.rushes = {'R1': 'R1.mp4', 'R2': 'R2.mp4'}
        for rid in self.rushes:  # 80 s of sentences of 4 s each, from 1 s
            ws = [w for i in range(20) for w in said(f'{rid} sentence number {i} is here.', start=1 + 4 * i, step=0.5)]
            with open(os.path.join(self.cache, 'transcripts', rid + '.words.json'), 'w') as f:
                json.dump({'words': ws}, f)

    def check(self, *shorts, hook=None, count=3):
        return auto_edit.check_shorts({'hook': hook, 'shorts': [list(x) for x in shorts]}, self.rushes, self.cache, 12, 30, count)

    def test_a_short_from_the_opening_is_rebuilt_around_the_hook(self):
        shorts, why = self.check([{'rush': 'R1', 'from': 1.0, 'to': 40.0}], hook={'rush': 'R1', 'from': 45.0, 'to': 52.5})
        self.assertEqual(why, ['around the hook'])
        self.assertEqual(len(shorts), 1)
        self.assertEqual(len(shorts[0]), 1)
        r = shorts[0][0]
        self.assertTrue(r['rush'] == 'R1' and r['from'] >= 31 and r['from'] <= 45 and r['to'] >= 52.5, r)
        self.assertTrue(30 <= r['to'] - r['from'] <= 60, r)

    def test_a_short_elsewhere_is_kept(self):
        short = [{'rush': 'R2', 'from': 1.0, 'to': 40.0}]
        self.assertEqual(self.check(short), ([short], []))
        self.assertEqual(self.check(), ([], []))

    def test_without_a_hook_the_densest_minute_after_the_opening(self):
        shorts, why = self.check([{'rush': 'R1', 'from': 5.0, 'to': 40.0}])
        self.assertEqual(why, ['the densest minute after the opening'])
        self.assertFalse(shorts[0][0]['rush'] == 'R1' and shorts[0][0]['from'] < 31, shorts)

    def test_several_shorts_each_from_a_moment_of_its_own(self):
        a, b = [{'rush': 'R2', 'from': 1.0, 'to': 40.0}], [{'rush': 'R2', 'from': 41.0, 'to': 80.0}]
        same = [{'rush': 'R2', 'from': 10.0, 'to': 45.0}]  # 30 s of its 35 s are in the first
        self.assertEqual(self.check(a, same, b)[0], [a, b])
        self.assertEqual(self.check(a, b, count=1)[0], [a])
        self.assertEqual(self.check(a, b, count=0)[0], [])
        # two taken from the opening: rebuilt from two different minutes, never the one a Short already holds
        shorts, why = self.check(a, [{'rush': 'R1', 'from': 2.0, 'to': 30.0}], [{'rush': 'R1', 'from': 3.0, 'to': 35.0}])
        self.assertEqual(len(shorts), 3, shorts)
        self.assertEqual(why, ['the densest minute after the opening'] * 2)
        for x in range(3):
            for y in range(x):
                self.assertEqual(auto_edit._shared(shorts[x], shorts[y]), 0, shorts)

    def test_the_answer_in_every_shape(self):
        durations = {'R1': 81.0, 'R2': 81.0}
        one = [{'rush': 'R1', 'from': 40.0, 'to': 70.0}]
        for answer, expected in (({'shorts': [one, [{'rush': 'R9', 'from': 1, 'to': 2}], []]}, [one]),
                                 ({'shorts': one}, [one]),  # one Short, given as its ranges
                                 ({'short': one}, [one]),  # as asked before several could be
                                 ({'shorts': 'none'}, [])):
            self.assertEqual(auto_edit.clean(dict(answer, main=[]), self.rushes, durations)['shorts'], expected, answer)
        self.assertEqual([auto_edit.shorts_count(v) for v in (True, False, 2, '1', 9, None)], [3, 0, 2, 1, 5, 3])
        # a range ending on a word Whisper timed past the end of its clip: kept, up to the end
        d = auto_edit.clean({'hook': {'rush': 'R1', 'from': 70.0, 'to': 81.6}, 'main': [{'rush': 'R2', 'from': 1.0, 'to': 84.0}]},
                            self.rushes, durations)
        self.assertEqual((d['hook']['to'], d['main']), (81.0, []))

    def test_as_many_shorts_asked_as_the_speech_allows(self):
        prompt = lambda n: auto_edit.build_prompt(self.rushes, self.cache, [], {}, 12, n)  # noqa: E731
        self.assertIn('up to 1 vertical Short of', prompt(3))  # 120 s of speech: room for one
        self.assertIn('"shorts": [],', prompt(0))
        self.assertIn('the clips hold 120 seconds of speech, and a good edit of them lasts about 60 seconds', prompt(3))

    def test_three_ways_of_cutting(self):
        prompt = lambda cut: auto_edit.build_prompt(self.rushes, self.cache, [], {}, 12, 3, cut)  # noqa: E731
        self.assertIn('Cut lightly, for a vlog', prompt('light'))
        self.assertIn('The spontaneous talk while\nfilming stays', prompt('light'))
        self.assertNotIn('small talk', prompt('light'))
        self.assertIn('small talk', prompt('normal'))
        self.assertIn('lasts about 84 seconds', prompt('light'))
        self.assertIn('lasts about 60 seconds', prompt('normal'))
        self.assertIn('Cut very hard', prompt('tight'))
        self.assertIn('lasts about 42 seconds', prompt('tight'))
        self.assertEqual(prompt('unknown'), prompt('normal'))
        for cut in ('light', 'normal', 'tight'):  # the rules of the hook and the Shorts are the same
            self.assertIn('ONE subject told by ONE', prompt(cut))
        self.assertEqual((settings.load()['auto']['style'], settings.load()['auto']['cut']), ('vlog', ''))
        for cut in ('light', 'normal', 'tight'):
            self.assertIn('everyone interviewed appears', prompt(cut))
            self.assertIn('keep only the one in the language of the video', prompt(cut))

    def test_irl_moments_asked_in_a_vlog_only(self):
        shots = [{'id': 'B001', 'rush': 'R1', 'from': 1.0, 'to': 9.0, 'labels': []}]
        vlog = auto_edit.build_prompt(self.rushes, self.cache, shots, {}, 12, 3, 'light', irl=True)
        self.assertIn('"irl": [{"shot": "B001"', vlog)
        self.assertIn('about 3 of them', vlog)
        talk = auto_edit.build_prompt(self.rushes, self.cache, shots, {}, 12, 3, 'normal', irl=False)
        self.assertIn('"irl": [],', talk)

    def test_irl_moments_in_the_order_of_the_day(self):
        order = {'A.mp4': 0, 'B.mp4': 1, 'C.mp4': 2}
        ranges = [{'file': 'C.mp4', 'from': 40, 'to': 50, 'note': 'hook'}, {'file': 'A.mp4', 'from': 10, 'to': 20},
                  {'file': 'B.mp4', 'from': 5, 'to': 15}, {'file': 'C.mp4', 'from': 60, 'to': 70}]
        irl = [{'file': 'B.mp4', 'from': 0.0, 'to': 10.0, 'best': 1.0, 'note': 'the gate'},  # before the passage of B
               {'file': 'A.mp4', 'from': 0.0, 'to': 3.0, 'note': 'too short'},  # 3 s: 2 s at least, kept
               {'file': 'C.mp4', 'from': 80.0, 'to': 90.0, 'note': 'after the end'}]  # the ending stays last
        out = auto_edit.with_irl(ranges, irl, order)
        self.assertEqual([(r['file'], r['from']) for r in out],
                         [('C.mp4', 40), ('A.mp4', 0.0), ('A.mp4', 10), ('B.mp4', 0.0), ('B.mp4', 5), ('C.mp4', 83.0), ('C.mp4', 60)])  # no best moment: the middle
        gate = out[3]
        self.assertEqual((gate['to'] - gate['from'], gate['role'], gate['whole']), (4.0, 'effects', True))
        self.assertTrue(gate['marker'].startswith('IRL the gate'))


FAKE_WHISPER = r"""#!{python}
# whisper-cli as the tests need it: a 300 Hz tone is French, 1000 Hz English, silence nothing. -dl names the language
# of each file (sure in proportion to the share of its sound in that language); a transcription writes one word a
# second where the tone of its language is heard ("fr3" at 3 s), whisper's JSON with -oj.
import array, json, sys, wave
args = sys.argv[1:]
files = [args[i + 1] for i, a in enumerate(args) if a == '-f']
outs = [args[i + 1] for i, a in enumerate(args) if a == '-of']
lang = args[args.index('-l') + 1] if '-l' in args else 'auto'
def windows(path):
    with wave.open(path) as w:
        a = array.array('h', w.readframes(w.getnframes()))
    for k in range(0, len(a) - 1600 + 1, 1600):  # 0.1 s
        x = a[k:k + 1600]
        if max(x) < 1000:
            yield k / 16000, None
        else:
            crossings = sum(1 for i in range(1, len(x)) if (x[i - 1] < 0) != (x[i] < 0))
            yield k / 16000, 'en' if crossings > 130 else 'fr'
for n, path in enumerate(files):
    ws = list(windows(path))
    if '-dl' in args:
        said = [l for _, l in ws if l]
        best = max(set(said), key=said.count) if said else 'en'
        p = said.count(best) / len(said) if said else 0.2
        sys.stderr.write("main: processing '%s' (1 samples)\nwhisper_full_with_state: auto-detected language: %s (p = %f)\n" % (path, best, p))
        continue
    segs, last = [], -1
    for t, l in ws:
        if l == lang and int(t) != last:
            last = int(t)
            segs.append({{'offsets': {{'from': int(t * 1000), 'to': int(t * 1000) + 300}}, 'text': ' %s%d' % (l, t)}})
    with open((outs[n] if n < len(outs) else path) + '.json', 'w') as f:
        json.dump({{'result': {{'language': lang}}, 'transcription': segs}}, f)
"""


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'needs numpy')
class SnappedCutsTest(unittest.TestCase):
    """A cut edge heard in the voice moves to the quiet moment next to it, never into the next word."""
    def sound(self, loud_spans, seconds=10.0):
        import numpy as np
        db = np.full(int(seconds / 0.01), -60.0)
        for a, b in loud_spans:
            db[int(a / 0.01):int(b / 0.01)] = -15.0
        return db, -37.5

    def test_a_loud_edge_moves_to_the_quiet_next_to_it(self):
        g = [{'w': 'bonjour', 'start': 2.0, 'end': 2.6}]
        words_ = [{'w': 'avant', 'start': 1.0, 'end': 1.4}] + g + [{'w': 'après', 'start': 3.2, 'end': 3.6}]
        sound = self.sound([(1.0, 1.4), (1.85, 2.72), (3.2, 3.6)])  # the voice starts before the word, ends after it
        (cin, cout, _), = edl_from_ranges.snapped([(1.92, 2.7, g)], words_, sound)
        self.assertTrue(1.72 <= cin <= 1.85, cin)
        self.assertTrue(2.72 <= cout <= 2.9, cout)

    def test_a_quiet_edge_stays_and_the_next_word_is_never_reached(self):
        g = [{'w': 'bonjour', 'start': 2.0, 'end': 2.6}]
        words_ = [{'w': 'avant', 'start': 1.0, 'end': 1.95}] + g
        sound = self.sound([(1.0, 1.95), (2.0, 2.6)])
        self.assertEqual(edl_from_ranges.snapped([(2.5, 2.7, g)], words_, sound)[0][1], 2.7)  # quiet: left alone
        (cin, _, _), = edl_from_ranges.snapped([(1.93, 2.7, g)], words_, self.sound([(1.0, 2.6)]))  # no quiet at all
        self.assertEqual(cin, 1.93)
        self.assertEqual(edl_from_ranges.snapped([(1.9, 2.7, g)], words_, None)[0][0], 1.9)  # without the sound


class BrollChoicesTest(unittest.TestCase):
    """A new version from an older one's choices: its B-roll shots stay the same shots when the catalogue changed."""
    def test_the_shot_chosen_not_its_number(self):
        then = [{'id': 'B037', 'file': 'A.mp4', 'from': 52.7, 'to': 57.0}]
        now = [{'id': 'B037', 'file': 'B.mp4', 'from': 2.6, 'to': 6.0}]  # the numbers moved
        choice = [{'shot': 'B037', 'rush': 'R1', 'at': 10.0}]
        self.assertEqual(auto_edit.resolve_shots(choice, now, then)[0]['file'], 'A.mp4')
        self.assertEqual(auto_edit.resolve_shots(choice, now)[0]['file'], 'B.mp4')  # a fresh run: this catalogue
        self.assertEqual(auto_edit.resolve_shots([dict(choice[0], shot='B099')], now, then), [])  # not found: left out
        kept = [dict(choice[0], file='A.mp4', **{'from': 52.7, 'to': 57.0})]  # carried by a version's answer
        self.assertEqual(auto_edit.resolve_shots(kept, now, [])[0]['from'], 52.7)


class FrameRateTest(unittest.TestCase):
    def test_a_clip_that_dropped_frames_keeps_its_nominal_rate(self):
        # seen on a Pocket 4 clip: 1978 frames in 33.07 s measure 59.82 fps for a 59.94 fps file
        self.assertEqual(timeline.frame_rate({'avg_frame_rate': '59340000/991991', 'r_frame_rate': '60000/1001'}), Fraction(60000, 1001))
        self.assertEqual(timeline.frame_rate({'avg_frame_rate': '25/1', 'r_frame_rate': '50/1'}), Fraction(25))  # far from it: measured
        self.assertEqual(timeline.frame_rate({'avg_frame_rate': '0/0', 'r_frame_rate': '30000/1001'}), Fraction(30000, 1001))

    def test_irl_moments_keep_their_sound_with_their_picture(self):
        asset = {'audio_channels': 2, 'duration': 60.0}
        clips = [{'file': 'a.mp4', 'in_s': 10.0, 'dur_s': 5.0, 'asset': asset},
                 {'file': 'b.mp4', 'in_s': 20.0, 'dur_s': 4.0, 'asset': asset, 'role': 'effects'}]
        splits, notes = splitedit.plan(clips, settings.load(), {'a.mp4': [], 'b.mp4': []}, 30.0)
        self.assertEqual((splits, notes), ([(0, 0), (0, 0)], []))


class WholeMomentTest(unittest.TestCase):
    """An IRL moment goes into the edit as it is, with its own sound (Effects role, not levelled as speech)."""
    def test_a_moment_kept_as_it_is(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        os.makedirs(os.path.join(d, 'transcripts'))
        for name, words_ in (('talk', [{'w': w, 'start': 1 + i * 0.4, 'end': 1.3 + i * 0.4} for i, w in enumerate('on arrive au parc'.split())]),
                             ('street', [])):
            with open(os.path.join(d, 'transcripts', name + '.words.json'), 'w') as f:
                json.dump({'words': words_}, f)
        with open(os.path.join(d, 'ranges.json'), 'w') as f:
            json.dump({'ranges': [{'file': 'street.mp4', 'from': 3.0, 'to': 7.0, 'whole': True, 'role': 'effects', 'marker': 'IRL gate'},
                                  {'file': 'talk.mp4', 'from': 0.5, 'to': 3.0}]}, f)
        r = subprocess.run([sys.executable, os.path.join(MediaTest.SCRIPTS, 'edl_from_ranges.py'), os.path.join(d, 'ranges.json'),
                            '-o', os.path.join(d, 'edl.json')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(d, 'edl.json')) as f:
            clips = json.load(f)['clips']
        self.assertEqual((clips[0]['file'], clips[0]['in'], clips[0]['out'], clips[0]['role'], clips[0]['marker']),
                         ('street.mp4', 3.0, 7.0, 'effects', 'IRL gate'))
        self.assertEqual(clips[1]['file'], 'talk.mp4')
        self.assertNotIn('role', clips[1])


class JoinedCutsTest(unittest.TestCase):
    """Seen on real footage: two cuts overlapping (a stutter), and one-word cuts flashing by."""
    def w(self, text, start, step=0.3, **flags):
        return [dict({'w': x, 'start': round(start + i * step, 2), 'end': round(start + i * step + 0.25, 2)}, **flags)
                for i, x in enumerate(text.split())]

    def test_overlapping_times_never_play_twice(self):
        words_ = [{'w': 'ok', 'start': 0.0, 'end': 0.15}, {'w': 'bon', 'start': 1.1, 'end': 1.15},
                  {'w': 'ben', 'start': 1.1, 'end': 1.15, 'filler': True}, {'w': 'malheureusement', 'start': 1.11, 'end': 2.15},
                  {'w': 'elle', 'start': 2.15, 'end': 2.46}]
        found = edl_from_ranges.cuts(words_, 1.1, 2.5, 0.5, 0.08, 0.12)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(all(b[0] >= a[1] for a, b in zip(found, found[1:])))

    def test_a_word_alone_joins_its_neighbour(self):
        words_ = self.w('Je', 10.0) + self.w('pense que ça devait être une erreur', 10.8)  # 0.55 s pause
        found = edl_from_ranges.cuts(words_, 9, 20, 0.5, 0.08, 0.12)
        self.assertEqual(len(found), 1)
        far = self.w('Je', 10.0) + self.w('pense que ça devait être une erreur', 11.5)  # 1.25 s: two cuts
        self.assertEqual(len(edl_from_ranges.cuts(far, 9, 20, 0.5, 0.08, 0.12)), 2)

    def test_never_across_a_word_left_out(self):
        words_ = self.w('Je', 10.0) + self.w('euh', 10.5, filler=True) + self.w('pense que ça devait être', 11.1)
        self.assertEqual(len(edl_from_ranges.cuts(words_, 9, 20, 0.5, 0.08, 0.12)), 2)


class PhantomWordsTest(unittest.TestCase):
    """What Whisper writes on silence, seen on real footage: subtitlers' credits and noises between stars."""
    def said(self, text):
        return [{'w': w, 'start': i, 'end': i + 0.5, 'suspect': False} for i, w in enumerate(text.split())]

    def test_credits_and_noises_are_suspect(self):
        for text in ("Sous-titrage ST' 501 Sous-titrage ST' 501", 'Sous-titrage Société Radio-Canada',
                     '字幕志愿者 杨茜茜', '*sad music* *laughing*', 'Sous-titres réalisés par la communauté d\'Amara.org'):
            w = self.said(text)
            self.assertEqual(words.flag_phantoms(w), len(w), text)

    def test_real_speech_is_left_alone(self):
        w = self.said("Bon, je fais le sous-titrage de mes vidéos moi-même, et Radio-Canada c'est une chaîne.")
        self.assertEqual(words.flag_phantoms(w), 0)
        w = self.said("On arrive. Sous-titrage Société Radio-Canada Merci à tous.")
        words.flag_phantoms(w)
        self.assertEqual([x['w'] for x in w if x['suspect']], ['Sous-titrage', 'Société', 'Radio-Canada'])

    def test_older_transcripts_are_made_again(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        for name, doc, fitted in (('old', {'words': [], 'fitted_to_sound': True}, False),
                                  ('new', {'words': [], 'fitted_to_sound': True, 'version': words.VERSION}, True)):
            with open(os.path.join(d, name + '.json'), 'w') as f:
                json.dump(doc, f)
            self.assertEqual(auto_edit._fitted(os.path.join(d, name + '.json')), fitted)


class OutOfOrderWordsTest(unittest.TestCase):
    """A run of words Whisper gave one timestamp, its first word moved past the others by the fit to the pauses:
    the edit used to fail at the build on a cut ending before it started."""
    def words(self):
        w = [{'w': 'pas.', 'start': 33.01, 'end': 33.53}, {'w': 'Dans', 'start': 38.18, 'end': 38.18, 'untimed': True}]
        w += [{'w': x, 'start': 37.76, 'end': 37.81, 'untimed': True} for x in 'les trois pièces que compte le'.split()]
        return w + [{'w': 'chalet,', 'start': 38.64, 'end': 39.3}]

    def test_the_words_are_put_back_in_order(self):
        w = self.words()
        self.assertEqual(words.in_order(w), 6)
        self.assertTrue(all(a['start'] <= b['start'] and b['start'] <= b['end'] for a, b in zip(w, w[1:])))
        self.assertEqual(w[2]['start'], 38.18)

    def test_no_cut_ends_before_it_starts(self):
        found = edl_from_ranges.cuts(self.words(), 30, 40, 0.5, 0.08, 0.12)
        self.assertTrue(found)
        self.assertTrue(all(b - a >= 0.04 for a, b, _ in found), found)


class LanguagesTest(unittest.TestCase):
    """Clips where several languages are spoken: each run transcribed in its own language."""
    def test_runs_from_the_cells(self):
        cells = [(0, 10, 'fr', 1), (10, 20, 'fr', 1), (20, 30, None, 0.6), (30, 40, 'en', 1), (40, 50, None, 0.5),
                 (50, 60, 'en', 1), (60, 70, 'en', 1), (70, 80, None, 0.3)]
        self.assertEqual(languages.main_language(cells, 'auto'), 'en')  # 30 s of English, 20 s of French
        self.assertEqual(languages.main_language(cells, 'fr'), 'fr')
        self.assertEqual(languages.main_language([(0, 10, None, 0.1)], 'auto'), 'auto')
        # unsure between two languages: the main one's for now; between two of one language: that one
        self.assertEqual(languages.runs(cells, 'fr'), [(0, 30, 'fr'), (30, 80, 'en')])
        self.assertEqual(languages.runs([(0, 10, None, 0.2)], 'fr'), [(0, 10, 'fr')])

    def test_unsure_slices_are_cut_at_their_pauses(self):
        spans = [(1.0, 1.4), (6.1, 6.3), (6.8, 7.6), (12.6, 13.0), (29.0, 29.4)]
        # the 1 s piece at 6.2-7.2 joins the one before it (0.2 s pause, not 0.8), the 1.2 s one at the start the one
        # after it; the 0.8 s at the end joins the piece before, then over 10 s: cut in two
        self.assertEqual(languages.pieces(0, 30, spans), [(0, 7.2), (7.2, 12.8), (12.8, 21.4), (21.4, 30)])
        self.assertEqual(languages.pieces(0, 25, []), [(0, 8.333), (8.333, 16.667), (16.667, 25)])

    def test_a_change_between_two_slices_goes_to_the_quietest_moment(self):
        class Sound:
            spans = [(59.5, 60.5)]
            def quietest(self, a, b):
                return 31.2
        runs = [(0, 30, 'fr'), (30, 60, 'en'), (60, 61, 'fr'), (61, 90, 'en')]
        # 30 is in no pause: 31.2; 60 is; the second of French left alone goes to the English before it
        self.assertEqual(languages.settle(runs, Sound()), [(0, 31.2, 'fr'), (31.2, 90, 'en')])

    def test_a_short_run_keeps_its_language_only_when_whisper_is_surer_of_it(self):
        runs = [(0, 40, 'fr'), (40, 45, 'en'), (45, 110, 'fr'), (110, 140, 'en'), (140, 146, 'fr'), (146, 200, 'en')]
        conf = {(40, 45): {'en': 0.75, 'fr': 0.88}, (140, 146): {'fr': 0.98, 'en': 0.69}}
        with mock.patch.object(languages, 'confidence', lambda m, w, piece, langs, f: conf[piece]):
            got = languages.checked('m.bin', 'a.wav', runs, '/tmp')
        # the French sentence taken for English goes back to French; the French question amid English answers stays
        self.assertEqual(got, [(0, 110, 'fr'), (110, 140, 'en'), (140, 146, 'fr'), (146, 200, 'en')])
        with mock.patch.object(languages, 'confidence', return_value={}):  # whisper-cli failed: left as it was
            self.assertEqual(languages.checked('m.bin', 'a.wav', runs, '/tmp'), runs)

    def test_what_whisper_skipped_is_found(self):
        class Sound:
            def speech(self, a, b):  # speech heard until 26 s, then silence
                return max(0.0, min(b, 26) - a) / (b - a)
        seg = lambda t: {'offsets': {'from': int(t * 1000), 'to': int(t * 1000) + 400}, 'text': ' w'}  # noqa: E731
        found = [seg(0.5), seg(1), seg(24), seg(25), seg(31)] + [{'offsets': {'from': 2000, 'to': 2000}, 'text': ' [Music]'}]
        self.assertEqual(languages.skipped(found, (0, 40, 'en'), Sound()), [(1.4, 24.0, 'en')])  # not the silence after 26 s

    @unittest.skipUnless(importlib.util.find_spec('numpy'), 'needs numpy')
    def test_each_language_in_its_own(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        cli = os.path.join(d, 'whisper-cli')
        with open(cli, 'w') as f:
            f.write(FAKE_WHISPER.format(python=sys.executable))
        os.chmod(cli, 0o755)
        audio = os.path.join(d, 'audio.wav')
        # sentences of 3 s, 0.4 s apart; the language changes after a longer pause (0.9 s): 7 in French, 7 in
        # English, 6 in French
        parts = []
        for n, freq in enumerate([300] * 7 + [1000] * 7 + [300] * 6):
            parts += [(0, 0.9 if n in (7, 14) else 0.4)] if n else []
            parts.append((freq, 3.0))
        with wave.open(audio, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            for freq, seconds in parts:
                w.writeframes(array.array('h', (int(12000 * math.sin(2 * math.pi * freq * i / 16000)) if freq else 0
                                                for i in range(int(seconds * 16000)))).tobytes())
        english = (7 * 3.4 + 0.5, 14 * 3.4 + 1.0)  # 24.3 s, and French again at 48.6 s
        out = os.path.join(d, 'transcript.json')
        r = subprocess.run([sys.executable, os.path.join(MediaTest.SCRIPTS, 'languages.py'), audio, '--model', 'm.bin',
                            '--out', out, '--language', 'fr'], env=dict(os.environ, WHISPER_CLI=cli), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out) as f:
            t = json.load(f)
        runs = t['languages']
        self.assertEqual([x[2] for x in runs], ['fr', 'en', 'fr'], runs)
        self.assertAlmostEqual(runs[0][1], english[0] - 0.45, delta=0.1)  # in the middle of the pauses
        self.assertAlmostEqual(runs[1][1], english[1] - 0.45, delta=0.1)
        self.assertEqual(t['result']['language'], 'fr')
        words_ = [(s['offsets']['from'] / 1000, s['text'].strip()) for s in t['transcription']]
        self.assertEqual(words_, sorted(words_))
        self.assertTrue(all(w.startswith('en') == (english[0] - 0.1 <= at < english[1] - 0.1) for at, w in words_), words_)
        self.assertGreaterEqual(sum(w.startswith('en') for _, w in words_), 21)  # a word a second at least
        self.assertIn('languages: fr 0:00-0:23, en 0:23-0:48, fr 0:48-1:08', r.stderr)

    @unittest.skipUnless(shutil.which('say') and shutil.which('ffmpeg') and importlib.util.find_spec('numpy')
                         and (shutil.which('whisper-cli') or os.environ.get('WHISPER_CLI'))
                         and os.path.exists(os.path.expanduser('~/.cache/whisper-cpp/ggml-large-v3-turbo.bin')),
                         'needs say, whisper-cli and the large-v3-turbo model')
    def test_a_french_video_with_an_english_answer(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        said = [('Thomas', "Bonjour à tous. Aujourd'hui nous sommes au village, et nous avons rencontré un ingénieur "
                           "américain qui a accepté de répondre à nos questions en anglais."),
                ('Samantha', 'Hi, thanks for having me. I moved here three years ago to work in engineering, and '
                             'I love the energy of the village.'),
                ('Thomas', 'Merci beaucoup pour ce témoignage. Dans la prochaine partie, nous prenons le train.')]
        for n, (voice, text) in enumerate(said):
            subprocess.run(['say', '-v', voice, '-o', os.path.join(d, f'{n}.aiff'), text], check=True)
        clip = os.path.join(d, 'clip.m4a')
        subprocess.run(['ffmpeg', '-v', 'error', '-i', os.path.join(d, '0.aiff'), '-f', 'lavfi', '-t', '0.8', '-i', 'anullsrc=r=22050',
                        '-i', os.path.join(d, '1.aiff'), '-f', 'lavfi', '-t', '0.8', '-i', 'anullsrc=r=22050', '-i', os.path.join(d, '2.aiff'),
                        '-filter_complex', '[0][1][2][3][4]concat=n=5:v=0:a=1', clip], check=True)
        r = subprocess.run(['bash', os.path.join(MediaTest.SCRIPTS, 'transcribe.sh'), clip, d, 'fr'], capture_output=True, text=True,
                           env=dict(os.environ, WHISPER_MODEL='ggml-large-v3-turbo.bin'))
        self.assertEqual(r.returncode, 0, r.stderr[-1000:])
        with open(os.path.join(d, 'clip.json')) as f:
            t = json.load(f)
        self.assertEqual([x[2] for x in t['languages']], ['fr', 'en', 'fr'], r.stderr)
        text = ''.join(s['text'] for s in t['transcription'])
        self.assertIn('engineering', text)  # English, not translated
        self.assertIn('témoignage', text)


class MusicQuestionTest(unittest.TestCase):
    """No music folder: the question is asked, and when it cannot be, the reason is said."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.cfg = settings.load(self.dir)
        self.t = auto_edit.UI['fr']

    def pick(self, dialogs=True):
        return auto_edit.pick_music('ask', self.cfg, self.dir, 'Trip', self.t, dialogs, 60)

    def fake_osascript(self, body):
        bin_dir = os.path.join(self.dir, 'bin')
        os.makedirs(bin_dir, exist_ok=True)
        with open(os.path.join(bin_dir, 'osascript'), 'w') as f:
            f.write('#!/bin/sh\n' + body)
        os.chmod(os.path.join(bin_dir, 'osascript'), 0o755)
        patcher = mock.patch.dict(os.environ, {'PATH': bin_dir + os.pathsep + os.environ['PATH']})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_folder_and_no_question_allowed(self):
        self.assertEqual(self.pick(dialogs=False), (None, 'no_folder'))

    def test_the_question_could_not_be_shown(self):
        self.fake_osascript('echo "execution error: No user interaction allowed. (-1713)" >&2; exit 1\n')
        self.assertEqual(self.pick(), (None, 'dialog'))
        self.assertIn('-1713', brain.DIALOG_ERRORS[-1])
        self.assertIn('Sans musique', self.t['music_off']['dialog'])

    def test_the_folder_is_asked_and_remembered(self):
        music_dir = os.path.join(self.dir, 'music')
        os.makedirs(music_dir)
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=d=90', os.path.join(music_dir, 'song.m4a')], check=True)
        self.fake_osascript(f'echo "{music_dir}"\n')  # answers the folder; the list of tracks then answers with the path too
        self.cfg['auto']['ask_music'] = False  # the track: the first one, without a second question
        track, why = self.pick()
        self.assertEqual((os.path.basename(track), why), ('song.m4a', None))
        with open(os.path.join(self.dir, 'roughcut.json')) as f:
            self.assertEqual(json.load(f)['auto']['music_folder'], music_dir)


class AppTextTest(unittest.TestCase):
    """The app's words (app/Sources/Roughcut/L10n.swift): every sentence in both languages, with the same blanks to
    fill, every key the code asks for present, and no sentence left unused."""
    APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app', 'Sources', 'Roughcut')

    def tables(self):
        with open(os.path.join(self.APP, 'L10n.swift'), encoding='utf-8') as f:
            text = f.read()
        en, fr = text.split('static let en:')[1].split('static let fr:')
        pairs = lambda block: dict(re.findall(r'"([\w.]+)": "((?:[^"\\]|\\.)*)"', block))  # noqa: E731
        return pairs(en), pairs(fr.split('\n    ]')[0])

    def test_both_languages_say_everything_with_the_same_blanks(self):
        en, fr = self.tables()
        self.assertEqual(set(en), set(fr))
        for key in en:
            self.assertEqual(re.findall(r'%[@d]', en[key]), re.findall(r'%[@d]', fr[key]), key)

    def test_every_key_used_exists_and_none_is_left_over(self):
        en, _ = self.tables()
        code = ''
        for name in os.listdir(self.APP):
            if name.endswith('.swift') and name != 'L10n.swift':
                with open(os.path.join(self.APP, name), encoding='utf-8') as f:
                    code += f.read()
        with open(os.path.join(self.APP, 'L10n.swift'), encoding='utf-8') as f:
            text = f.read()
        code += text.split('static let en:')[0] + text.split('static let fr:')[1].split('\n    ]', 1)[1]
        used = set(re.findall(r'L10n\.t\("([\w.]+)"', code))
        built = {k for k in en if re.search(r'"\w+\.\\\(', code) and any(k.startswith(p) for p in re.findall(r'"(\w+\.)\\\(', code))}
        self.assertEqual(used - set(en), set())
        literals = set(re.findall(r'"([\w.]+)"', code))  # a key picked by a condition counts too
        self.assertEqual(set(en) - used - built - literals, set())


@unittest.skipUnless(shutil.which('swiftc') and sys.platform == 'darwin', 'needs the Swift compiler (macOS)')
class AppLogicTest(unittest.TestCase):
    """The app's logic (download errors, time estimates, the speed limit), compiled with tests/app/main.swift."""
    def test_app_logic(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        sources = [os.path.join(root, 'app/Sources/Roughcut', f) for f in os.listdir(os.path.join(root, 'app/Sources/Roughcut'))
                   if f.endswith('.swift') and f != 'RoughcutApp.swift']  # everything but the app's entry point
        exe = os.path.join(tmp, 'checks')
        r = subprocess.run(['swiftc', '-O', '-swift-version', '5', '-o', exe, *sources, os.path.join(root, 'tests/app/main.swift')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        payload = os.urandom(3_000_000)
        srv = HTTPServer(('127.0.0.1', 0), type('H', (BaseHTTPRequestHandler,), {
            'do_GET': lambda h: (h.send_response(200), h.send_header('Content-Length', str(len(payload))), h.end_headers(), h.wfile.write(payload)),
            'log_message': lambda *a: None}))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        suite = 'roughcut-tests'  # the app's preferences for this run only
        self.addCleanup(subprocess.run, ['defaults', 'delete', suite], capture_output=True)
        r = subprocess.run([exe, f'http://127.0.0.1:{srv.server_port}/f', '1000000'], capture_output=True, text=True,
                           env=dict(os.environ, ROUGHCUT_DEFAULTS=suite))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


@unittest.skipUnless(shutil.which('clang') and sys.platform == 'darwin', 'needs clang (macOS)')
class MacOSCheckTest(unittest.TestCase):
    """app/check_macos.py: a binary built for a newer macOS than the oldest supported one stops the build."""
    def test_newer_macos_is_refused(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        src = os.path.join(tmp, 'hello.c')
        with open(src, 'w') as f:
            f.write('int main(void) { return 0; }\n')
        check = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app', 'check_macos.py')
        for version, ok in (('14.0', True), ('26.0', False)):
            folder = os.path.join(tmp, version)
            os.makedirs(folder)
            subprocess.run(['clang', '-arch', 'arm64', f'-mmacosx-version-min={version}', src, '-o', os.path.join(folder, 'hello')], check=True)
            r = subprocess.run([sys.executable, check, folder, '--target', '14.0'], capture_output=True, text=True)
            self.assertEqual(r.returncode == 0, ok, r.stdout + r.stderr)


class ProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        ff = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y']
        cls.video = os.path.join(cls.dir, 'clip.mov')
        subprocess.run(ff + ['-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30000/1001', '-f', 'lavfi', '-i',
                             'sine=sample_rate=48000', '-t', '1', '-c:v', 'mpeg4', '-c:a', 'pcm_s16le',
                             '-timecode', '01:00:00:00', cls.video], check=True)
        cover, audio = os.path.join(cls.dir, 'cover.png'), os.path.join(cls.dir, 'a.m4a')
        cls.song = os.path.join(cls.dir, 'song.m4a')
        subprocess.run(ff + ['-f', 'lavfi', '-i', 'color=c=red:s=64x64', '-frames:v', '1', cover], check=True)
        subprocess.run(ff + ['-f', 'lavfi', '-i', 'sine=sample_rate=44100', '-t', '1', '-c:a', 'aac', audio], check=True)
        subprocess.run(ff + ['-i', audio, '-i', cover, '-map', '0', '-map', '1', '-c', 'copy',
                             '-disposition:v', 'attached_pic', cls.song], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def test_video_rate_and_timecode(self):
        info = REAL_PROBE(self.video)
        self.assertEqual(info['fps'], NTSC)
        self.assertEqual(info['timecode'], '01:00:00:00')
        self.assertEqual(probe_script.probe(self.video)['fps'], '30000/1001')

    def test_missing_source_gives_a_clear_message(self):
        with self.assertRaises(SystemExit) as cm:
            REAL_PROBE(os.path.join(self.dir, 'nope.mp4'))
        self.assertIn('Source not found', str(cm.exception))

    def test_hdr_transfer_is_read(self):
        hlg = os.path.join(self.dir, 'hlg.mov')
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25',
                        '-t', '1', '-vf', 'setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc',
                        '-c:v', 'mpeg4', hlg], check=True)
        self.assertEqual(REAL_PROBE(hlg)['hdr'], 'HLG')
        self.assertEqual(REAL_PROBE(hlg)['color_space'], '9-18-9 (Rec. 2020 HLG)')
        self.assertIsNone(REAL_PROBE(self.video)['hdr'])
        self.assertEqual(REAL_PROBE(self.video)['color_space'], '1-1-1 (Rec. 709)')

    def test_media_checked_before_the_import(self):
        # what Final Cut Pro met on a real edit: "unexpected value (tcFormat="DF")" and "Invalid edit with no
        # respective media" for a clip declared at 59.82 fps; and a clip beyond its media, a file gone
        from urllib.parse import quote
        doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<fcpxml version="1.13"><resources>
<format id="f1" frameDuration="1001/30000s" width="320" height="180"/>
<format id="f2" frameDuration="1/25s" width="320" height="180"/>
<asset id="r1" name="good" start="0s" duration="1001/1000s" hasVideo="1" hasAudio="1" format="f1"><media-rep kind="original-media" src="file://{quote(self.video)}"/></asset>
<asset id="r2" name="wrong rate" start="0s" duration="1s" hasVideo="1" hasAudio="1" format="f2"><media-rep kind="original-media" src="file://{quote(self.video)}"/></asset>
<asset id="r3" name="gone" start="0s" duration="1s" hasVideo="1" format="f1"><media-rep kind="original-media" src="file:///nowhere/gone.mov"/></asset>
</resources><library><event name="e">
<asset-clip ref="r1" name="inside" start="0s" duration="1001/2000s" tcFormat="DF"/>
<asset-clip ref="r1" name="beyond" start="1001/2000s" duration="1001/1000s" tcFormat="DF"/>
<asset-clip ref="r2" name="df at 25" start="0s" duration="1/2s" tcFormat="DF"/>
</event></library></fcpxml>"""
        path = os.path.join(self.dir, 'check.fcpxml')
        with open(path, 'w') as f:
            f.write(doc)
        problems = '\n'.join(fcp.media_problems(path))
        self.assertIn('gone: its file is missing', problems)
        self.assertIn('asset-clip "beyond": uses 0.500-1.502 s of a clip of 1.001 s', problems)
        self.assertIn('asset-clip "df at 25": drop-frame timecode on a clip at 25.000 fps', problems)
        self.assertIn('wrong rate: declared at 25.000 fps, the file is 29.970 fps', problems)
        self.assertNotIn('"inside"', problems)
        self.assertFalse(fcp.check(path)[0])

    def test_cover_picture_is_not_taken_for_video(self):
        self.assertNotIn('width', probe_script.probe(self.song))  # used to crash on the 0/0 frame rate
        with self.assertRaises(SystemExit):
            REAL_PROBE(self.song)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs ffmpeg')
class MediaTest(unittest.TestCase):
    """The preview, the contact sheets and transcribe.sh on small generated files."""
    SCRIPTS = os.path.dirname(make_fcpxml.__file__)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def ffmpeg(self, *args):
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *args], check=True)

    def clip(self, name, tone=None, seconds=1):
        path = os.path.join(self.dir, name)
        video = ['-f', 'lavfi', '-i', f'testsrc2=size=320x180:rate=25:duration={seconds}']
        audio = ['-f', 'lavfi', '-i', f'sine=f={tone}:sample_rate=48000:duration={seconds}'] if tone else []
        self.ffmpeg(*video, *audio, '-c:v', 'mpeg4', *(['-c:a', 'aac'] if tone else []), path)
        return path

    def volume(self, path, start, length):
        r = subprocess.run(['ffmpeg', '-hide_banner', '-ss', str(start), '-t', str(length), '-i', path,
                            '-af', 'volumedetect', '-f', 'null', '-'], capture_output=True, text=True)
        return float(re.search(r'mean_volume: (-?[\d.]+|-inf)', r.stderr).group(1))

    def test_preview_stays_in_sync_after_a_clip_without_sound(self):
        clips = [self.clip('a.mp4', 440), self.clip('broll.mp4'), self.clip('c.mp4', 1000)]
        edl = os.path.join(self.dir, 'edl.json')
        with open(edl, 'w', encoding='utf-8') as f:
            json.dump({'clips': [{'file': c, 'in': 0, 'out': 1} for c in clips]}, f)
        out = os.path.join(self.dir, 'new folder', 'preview.mp4')
        subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'render_preview.py'), edl, '-o', out],
                       check=True, capture_output=True)
        info = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=sample_rate:format=duration',
                               '-of', 'json', out], capture_output=True, text=True, check=True).stdout
        info = json.loads(info)
        self.assertAlmostEqual(float(info['format']['duration']), 3.0, delta=0.05)
        self.assertIn('48000', [s.get('sample_rate') for s in info['streams']])
        self.assertLess(self.volume(out, 1.2, 0.6), -50)  # the B-roll stays silent
        self.assertGreater(self.volume(out, 2.2, 0.6), -40)  # the third clip is heard in its place

    def loudness(self, path):
        r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', path, '-af', 'ebur128', '-f', 'null', '-'],
                           capture_output=True, text=True)
        return float(re.findall(r'I:\s+(-?[\d.]+) LUFS', r.stderr)[-1])

    def test_preview_lands_on_minus_14_lufs(self):
        quiet = os.path.join(self.dir, 'quiet.mp4')  # a voice-like level well under the target
        self.ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25:duration=8', '-f', 'lavfi', '-i',
                    'sine=f=300:sample_rate=48000:duration=8', '-af', 'volume=-24dB,tremolo=f=3:d=0.6',
                    '-c:v', 'mpeg4', '-c:a', 'aac', quiet)
        edl = os.path.join(self.dir, 'edl.json')
        with open(edl, 'w', encoding='utf-8') as f:
            json.dump({'clips': [{'file': quiet, 'in': 0, 'out': 4}, {'file': quiet, 'in': 4.5, 'out': 8}]}, f)
        out = os.path.join(self.dir, 'preview.mp4')
        subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'render_preview.py'), edl, '-o', out],
                       check=True, capture_output=True)
        self.assertAlmostEqual(self.loudness(out), -14, delta=0.5)
        listing = os.path.join(self.dir, 'list.txt')  # the gain comes from a measurement of the whole preview
        with open(listing, 'w', encoding='utf-8') as f:
            f.write(f"file '{quiet}'\n")
        self.assertRegex(render_preview.loudnorm_filter(listing), r'measured_I=-[\d.]+:.*linear=true')

    def test_dialogue_levelling_on_real_sound(self):
        loud, quiet = os.path.join(self.dir, 'loud.mp4'), os.path.join(self.dir, 'quiet.mp4')
        for path, vol in ((loud, '10dB'), (quiet, '0dB')):  # about -14 and -24 LUFS
            self.ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25:duration=4', '-f', 'lavfi', '-i',
                        'sine=f=300:sample_rate=48000:duration=4', '-af', f'volume={vol},tremolo=f=3:d=0.5',
                        '-c:v', 'mpeg4', '-c:a', 'aac', path)
        edl = os.path.join(self.dir, 'edl.json')
        with open(edl, 'w', encoding='utf-8') as f:
            json.dump({'clips': [{'file': loud, 'in': 0, 'out': 3}, {'file': quiet, 'in': 0, 'out': 3}]}, f)
        r = subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'make_fcpxml.py'), edl, '-o',
                            os.path.join(self.dir, 'out.fcpxml'), '--level-audio'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        amounts = [float(v.get('amount')[:-2]) for v in ET.parse(os.path.join(self.dir, 'out.fcpxml')).getroot().iter('adjust-volume')]
        self.assertAlmostEqual(amounts[1] - amounts[0], 10, delta=1)  # 10 dB apart at the source
        out = os.path.join(self.dir, 'preview.mp4')
        subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'render_preview.py'), edl, '-o', out, '--level-audio'],
                       check=True, capture_output=True)
        first, second = (loudness.measure(out, t, 2.5) for t in (0.2, 3.2))
        self.assertAlmostEqual(first, second, delta=1)  # one level from cut to cut

    def test_zoom_keeps_the_face_in_place_in_the_preview(self):
        still = os.path.join(self.dir, 'face.png')  # a white square standing for a face, right of centre
        self.ffmpeg('-f', 'lavfi', '-i', 'color=gray:s=640x360', '-vf', 'drawbox=x=440:y=150:w=60:h=60:color=white:t=fill',
                    '-frames:v', '1', still)
        take = os.path.join(self.dir, 'take.mp4')
        self.ffmpeg('-loop', '1', '-framerate', '25', '-t', '4', '-i', still, '-f', 'lavfi', '-i', 'sine=sample_rate=48000:duration=4',
                    '-c:v', 'mpeg4', '-q:v', '2', '-c:a', 'aac', '-shortest', take)
        os.makedirs(os.path.join(self.dir, 'analysis'))
        face = [440 / 640, 1 - 210 / 360, 60 / 640, 60 / 360, 0.5]  # Vision's origin is at the bottom left
        with open(os.path.join(self.dir, 'analysis', 'take.analysis.json'), 'w') as f:
            json.dump({'samples': [{'t': t / 4, 'faces': [face]} for t in range(16)]}, f)
        edl = os.path.join(self.dir, 'edl.json')
        with open(edl, 'w', encoding='utf-8') as f:
            json.dump({'clips': [{'file': take, 'in': 0, 'out': 1.5}, {'file': take, 'in': 2, 'out': 3.5}]}, f)
        out = os.path.join(self.dir, 'preview.mp4')
        subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'render_preview.py'), edl, '-o', out, '--zoom-jump-cuts'],
                       check=True, capture_output=True)
        def square(t):
            raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', out, '-frames:v', '1', '-vf', 'format=gray',
                                  '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
            w, h = 1920, 1080
            bright = [i for i in range(0, len(raw), 7) if raw[i] > 230]
            return (sum(i % w for i in bright) / len(bright) / w, sum(i // w for i in bright) / len(bright) / h, len(bright))
        before, after = square(0.7), square(2.2)
        self.assertAlmostEqual(before[0], after[0], delta=0.01)  # the square has not moved...
        self.assertAlmostEqual(before[1], after[1], delta=0.01)
        self.assertAlmostEqual(after[2] / before[2], 1.12 ** 2, delta=0.1)  # ...but it is 12 % bigger

    def test_vertical_preview_follows_the_face(self):
        moving = os.path.join(self.dir, 'moving.mp4')  # a white square at 25 % of the width, then at 75 % from 2 s
        self.ffmpeg('-f', 'lavfi', '-i', 'color=gray:s=640x360:r=25:d=4', '-f', 'lavfi', '-i', 'sine=sample_rate=48000:duration=4',
                    '-vf', "drawbox=x=130:y=150:w=60:h=60:color=white:t=fill:enable='lt(t,2)',drawbox=x=450:y=150:w=60:h=60:color=white:t=fill:enable='gte(t,2)'", '-c:v', 'mpeg4', '-q:v', '2',
                    '-c:a', 'aac', '-shortest', moving)
        os.makedirs(os.path.join(self.dir, 'analysis'))
        face = lambda t: [(130 if t < 2 else 450) / 640, 0.4, 60 / 640, 60 / 360, 0.5]  # noqa: E731
        with open(os.path.join(self.dir, 'analysis', 'moving.analysis.json'), 'w') as f:
            json.dump({'samples': [{'t': i / 3, 'faces': [face(i / 3)]} for i in range(12)]}, f)
        edl = os.path.join(self.dir, 'edl.json')
        with open(edl, 'w', encoding='utf-8') as f:
            json.dump({'vertical': True, 'clips': [{'file': moving, 'in': 0, 'out': 4}]}, f)
        out = os.path.join(self.dir, 'preview.mp4')
        subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'render_preview.py'), edl, '-o', out, '--follow-face'],
                       check=True, capture_output=True)
        def square_x(t):
            raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', out, '-frames:v', '1', '-vf', 'format=gray',
                                  '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
            xs = [i % 1080 for i in range(0, len(raw), 5) if raw[i] > 230]
            return sum(xs) / len(xs) / 1080 if xs else None
        for t in (1.0, 3.5):
            self.assertAlmostEqual(square_x(t), 0.5, delta=0.1)  # centred before and after the jump

    def test_captions_render_with_transparency(self):
        take = self.clip('take.mp4', 440, seconds=4)
        os.makedirs(os.path.join(self.dir, 'transcripts'))
        with open(os.path.join(self.dir, 'transcripts', 'take.words.json'), 'w') as f:
            json.dump({'words': [{'w': 'Hello', 'start': 0.5, 'end': 0.9}, {'w': 'world.', 'start': 0.9, 'end': 1.4}]}, f)
        edl = os.path.join(self.dir, 'edl.json')
        with open(edl, 'w', encoding='utf-8') as f:
            json.dump({'clips': [{'file': take, 'in': 0, 'out': 4}]}, f)
        out = os.path.join(self.dir, 'captions.mov')
        subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'captions.py'), edl, '-o', out], check=True, capture_output=True)
        info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_tag_string,width,height:format=duration',
                                          '-of', 'json', out], capture_output=True, text=True, check=True).stdout)
        mac = 'hevc_videotoolbox' in subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True).stdout
        # HEVC with alpha on a Mac (40 times smaller than ProRes); ProRes 4444 where that encoder is missing
        self.assertEqual(info['streams'][0]['codec_tag_string'], 'hvc1' if mac else 'ap4h')
        strip, pad, up = captions.band(320, 180, settings.load()['captions_main'])
        self.assertEqual((info['streams'][0]['width'], info['streams'][0]['height']), (320, strip))  # the main edit: a strip
        self.assertAlmostEqual(float(info['format']['duration']), 4.0, delta=0.05)
        with open(os.path.join(self.dir, 'captions.json')) as f:
            listed = json.load(f)
        self.assertEqual([v['file'] for v in listed['videos']], ['captions.mov'])
        self.assertEqual(listed['position'], [0, up])
        self.assertLess(up, -30)  # low in the frame
        def opaque(t):
            raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', out, '-frames:v', '1', '-vf', 'alphaextract,format=gray',
                                  '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
            return sum(1 for x in raw if x > 128)
        self.assertGreater(opaque(0.7), 20)  # words on screen while they are said
        self.assertEqual(opaque(3.0), 0)  # nothing, and fully transparent, in the silence

    def test_broll_over_the_preview_and_its_catalogue(self):
        head = self.clip('head.mp4', 440, seconds=6)  # the talking head: a test pattern and a tone
        red = os.path.join(self.dir, 'red.mp4')
        self.ffmpeg('-f', 'lavfi', '-i', 'color=red:s=320x180:r=25:d=6', '-f', 'lavfi', '-i', 'sine=f=1000:sample_rate=48000:duration=6',
                    '-c:v', 'mpeg4', '-q:v', '2', '-c:a', 'aac', red)
        edl = os.path.join(self.dir, 'edl.json')
        with open(edl, 'w', encoding='utf-8') as f:
            json.dump({'clips': [{'file': head, 'in': 0, 'out': 6}],
                       'broll': [{'file': red, 'in': 1, 'out': 3, 'at_source': 2.0, 'note': 'red'}]}, f)
        out = os.path.join(self.dir, 'preview.mp4')
        subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'render_preview.py'), edl, '-o', out], check=True, capture_output=True)
        def redness(t):
            raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', out, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                 capture_output=True, check=True).stdout
            px = [raw[i:i + 3] for i in range(0, len(raw), 3 * 97)]
            return sum(1 for p in px if p[0] > 200 and p[1] < 60 and p[2] < 60) / len(px)
        self.assertLess(redness(1.0), 0.2)
        self.assertGreater(redness(3.0), 0.9)  # the B-roll covers the picture from 2 s to 4 s...
        self.assertLess(redness(5.0), 0.2)
        self.assertGreater(self.volume(out, 2.5, 1), -40)  # ...while the talking head is still heard
        # the catalogue: the stretch without speech or rejection, with a picture of its best moment
        os.makedirs(os.path.join(self.dir, 'analysis'))
        with open(os.path.join(self.dir, 'analysis', 'red.analysis.json'), 'w') as f:
            json.dump({'samples': [{'t': i / 3, 'aesthetic': 0.1 * (i == 12), 'labels': {'red': 0.9}} for i in range(18)],
                       'rejects': [{'from': 5.0, 'to': 6.0, 'why': ['blurred']}]}, f)
        os.makedirs(os.path.join(self.dir, 'transcripts'))
        with open(os.path.join(self.dir, 'transcripts', 'red.words.json'), 'w') as f:
            json.dump({'words': [{'w': 'Hi.', 'start': 0.2, 'end': 1.0}]}, f)
        subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'broll.py'), red, '-o', os.path.join(self.dir, 'broll.json')],
                       check=True, capture_output=True)
        with open(os.path.join(self.dir, 'broll.json')) as f:
            shots = json.load(f)['shots']
        self.assertEqual(len(shots), 1)
        self.assertGreaterEqual(shots[0]['from'], 1.3)  # after the speech and its margin
        self.assertLessEqual(shots[0]['to'], 5.0)  # before the rejected second
        self.assertEqual((shots[0]['best'], shots[0]['labels']), (4.0, ['red']))
        self.assertTrue(os.path.exists(shots[0]['frame']))

    def test_music_ducks_under_speech_in_the_preview(self):
        # the talking head: 3 s of tone, 3 s of silence; the music: a steady lower tone
        head = os.path.join(self.dir, 'head.mp4')
        self.ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25:duration=6', '-f', 'lavfi', '-i',
                    "sine=f=1000:sample_rate=48000:duration=6,volume='if(lt(t,3),1,0)':eval=frame", '-c:v', 'mpeg4', '-c:a', 'aac', head)
        song = os.path.join(self.dir, 'song.wav')
        self.ffmpeg('-f', 'lavfi', '-i', 'sine=f=80:sample_rate=48000:duration=10', song)
        os.makedirs(os.path.join(self.dir, 'transcripts'))
        with open(os.path.join(self.dir, 'transcripts', 'head.words.json'), 'w') as f:
            json.dump({'words': [{'w': 'Talk.', 'start': 0.0, 'end': 3.0}]}, f)
        edl = os.path.join(self.dir, 'edl.json')
        with open(edl, 'w', encoding='utf-8') as f:
            json.dump({'clips': [{'file': head, 'in': 0, 'out': 6}], 'music': {'file': song}}, f)
        out = os.path.join(self.dir, 'preview.mp4')
        subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'render_preview.py'), edl, '-o', out], check=True, capture_output=True)
        low = os.path.join(self.dir, 'low.wav')  # the music alone: below 150 Hz
        self.ffmpeg('-i', out, '-af', 'lowpass=f=150,lowpass=f=150,lowpass=f=150', low)
        under, alone = self.volume(low, 1.0, 1.5), self.volume(low, 4.0, 1.5)
        self.assertGreater(alone - under, 10)  # 14 dB lower under speech (-28 vs -14)

    def test_j_cut_is_heard_in_the_preview(self):
        a, b = os.path.join(self.dir, 'a.mp4'), os.path.join(self.dir, 'b.mp4')
        self.ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25:duration=4', '-f', 'lavfi', '-i',
                    "sine=f=440:sample_rate=48000:duration=4,volume='if(lt(t,2),1,0)':eval=frame", '-c:v', 'mpeg4', '-c:a', 'aac', a)
        self.ffmpeg('-f', 'lavfi', '-i', 'color=blue:s=320x180:r=25:d=6', '-f', 'lavfi', '-i', 'sine=f=1000:sample_rate=48000:duration=6',
                    '-c:v', 'mpeg4', '-c:a', 'aac', b)
        os.makedirs(os.path.join(self.dir, 'transcripts'))
        for name, ws in (('a', [{'w': 'Hello.', 'start': 0.2, 'end': 1.9}]), ('b', [])):
            with open(os.path.join(self.dir, 'transcripts', f'{name}.words.json'), 'w') as f:
                json.dump({'words': ws}, f)
        edl = os.path.join(self.dir, 'edl.json')
        with open(edl, 'w', encoding='utf-8') as f:
            json.dump({'clips': [{'file': a, 'in': 0, 'out': 3}, {'file': b, 'in': 2, 'out': 5}]}, f)
        out = os.path.join(self.dir, 'preview.mp4')
        subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'render_preview.py'), edl, '-o', out, '--split-edits'],
                       check=True, capture_output=True)
        high = os.path.join(self.dir, 'high.wav')  # the next scene's 1 kHz alone
        self.ffmpeg('-i', out, '-af', 'highpass=f=700,highpass=f=700,highpass=f=700', high)
        self.assertGreater(self.volume(high, 2.5, 0.4) - self.volume(high, 1.0, 0.8), 20)  # heard before the picture cuts at 3 s
        info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', out],
                                         capture_output=True, text=True, check=True).stdout)
        self.assertAlmostEqual(float(info['format']['duration']), 6.0, delta=0.05)

    def sizes(self, path):
        r = subprocess.run(['ffmpeg', '-hide_banner', '-i', path, '-vf', 'showinfo', '-f', 'null', '-'], capture_output=True, text=True)
        return set(re.findall(r' s:(\d+x\d+) ', r.stderr))

    def test_preview_keeps_one_size_with_clips_of_another_shape(self):
        wide = self.clip('wide.mp4', 440)
        phone = os.path.join(self.dir, 'phone.mp4')  # an upright phone screen recording
        self.ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=1170x2532:rate=25:duration=1', '-c:v', 'mpeg4', phone)
        for vertical, size in ((False, '1920x1080'), (True, '1080x1920')):
            edl = os.path.join(self.dir, 'edl.json')
            with open(edl, 'w', encoding='utf-8') as f:
                json.dump({'vertical': vertical, 'clips': [{'file': wide, 'in': 0, 'out': 1}, {'file': phone, 'in': 0, 'out': 1}]}, f)
            out = os.path.join(self.dir, f'preview-{vertical}.mp4')
            subprocess.run([sys.executable, os.path.join(self.SCRIPTS, 'render_preview.py'), edl, '-o', out],
                           check=True, capture_output=True)
            self.assertEqual(self.sizes(out), {size}, vertical)  # one size from start to end

    def test_contact_sheet_with_quotes_and_spaces(self):
        clip = self.clip("clip d'été #1.mp4", seconds=12)
        r = subprocess.run(['bash', os.path.join(self.SCRIPTS, 'contact_sheet.sh'), clip, os.path.join(self.dir, 'sheets'), '5'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.dir, 'sheets', "clip d'été #1_sheet_01.jpg")))

    def test_transcribe_never_touches_a_wav_source(self):
        out = os.path.join(self.dir, 'out')
        os.makedirs(out)
        source = os.path.join(out, 'MIC_001.WAV')  # next to the output, as a .wav: it used to be overwritten, then deleted
        self.ffmpeg('-f', 'lavfi', '-i', 'sine=f=440:sample_rate=48000:duration=1', source)
        with open(source, 'rb') as f:
            before = f.read()
        fake_bin = os.path.join(self.dir, 'bin')
        os.makedirs(fake_bin)
        with open(os.path.join(fake_bin, 'whisper-cli'), 'w') as f:
            f.write('#!/bin/sh\nexit 0\n')
        os.chmod(os.path.join(fake_bin, 'whisper-cli'), 0o755)
        open(os.path.join(self.dir, 'model.bin'), 'w').close()
        env = dict(os.environ, PATH=fake_bin + os.pathsep + os.environ['PATH'],
                   WHISPER_MODEL_DIR=self.dir, WHISPER_MODEL='model.bin')
        subprocess.run(['bash', os.path.join(self.SCRIPTS, 'transcribe.sh'), source, out, 'en'],
                       env=env, capture_output=True)  # may fail on a machine with the real whisper-cli: only the source matters
        with open(source, 'rb') as f:
            self.assertEqual(f.read(), before)



try:
    import analyze
    import numpy as np
except (ImportError, SystemExit):
    analyze = None


@unittest.skipUnless(analyze and shutil.which('ffmpeg'), 'needs ffmpeg and numpy')
class AnalyzeTest(unittest.TestCase):
    """analyze.py on a generated clip: 3 s sharp and steady, 3 s blurred, 3 s black, 3 s steady pan, 3 s shaken."""
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        ff = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y']
        still = os.path.join(cls.dir, 'still.png')
        subprocess.run(ff + ['-f', 'lavfi', '-i', 'testsrc2=size=1280x720', '-frames:v', '1', still], check=True)
        parts = {
            'sharp': 'crop=1120:630:80:45',
            'blur': 'crop=1120:630:80:45,boxblur=12',
            'pan': "crop=1120:630:'min(160,t*50)':45",
            'shake': "crop=1120:630:'80+70*sin(n*2.1)*cos(n*1.3)':'45+40*sin(n*1.7)'",
        }
        clips = []
        for name, vf in parts.items():
            out = os.path.join(cls.dir, name + '.mp4')
            subprocess.run(ff + ['-loop', '1', '-framerate', '30', '-t', '3', '-i', still, '-vf', vf + ',scale=640:360,format=yuv420p',
                                 '-c:v', 'libx264', '-crf', '18', out], check=True)
            clips.append(out)
        black = os.path.join(cls.dir, 'black.mp4')
        subprocess.run(ff + ['-f', 'lavfi', '-i', 'color=black:s=640x360:r=30:d=3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', black], check=True)
        order = [clips[0], clips[1], black, clips[2], clips[3]]
        listing = os.path.join(cls.dir, 'list.txt')
        with open(listing, 'w') as f:
            f.write(''.join(f"file '{c}'\n" for c in order))
        cls.clip = os.path.join(cls.dir, 'take.mp4')
        subprocess.run(ff + ['-f', 'concat', '-safe', '0', '-i', listing, '-c', 'copy', cls.clip], check=True)
        cls.out = os.path.join(cls.dir, 'analysis')
        with mock.patch.object(sys, 'argv', ['analyze.py', cls.clip, '-o', cls.out]), contextlib.redirect_stdout(io.StringIO()):
            analyze.main()
        with open(os.path.join(cls.out, 'take.analysis.json'), encoding='utf-8') as f:
            cls.result = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    @unittest.skipUnless(analyze and analyze.Vision is not None, 'needs Apple Vision')
    def test_memory_stays_flat_on_long_footage(self):
        # 600 frames through Vision: without an autorelease pool per frame each one kept about 2.5 MB (an hour of
        # footage then needed some 30 GB and the analysis was killed)
        cfg = os.path.join(self.dir, 'dense.json')
        with open(cfg, 'w') as f:
            json.dump({'quality': {'sample_fps': 10}}, f)
        out = os.path.join(self.dir, 'dense')
        code = ('import resource, sys, runpy; sys.argv = ["analyze.py", sys.argv[1], "-o", sys.argv[2], "--config", sys.argv[3]]; '
                'runpy.run_path(sys.argv[0] if False else "' + os.path.join(MediaTest.SCRIPTS, 'analyze.py') + '", run_name="__main__"); '
                'print("RSS", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)')
        long_clip = os.path.join(self.dir, 'long.mp4')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-stream_loop', '3', '-i', self.clip, '-c', 'copy', long_clip], check=True)
        r = subprocess.run([sys.executable, '-c', code, long_clip, out, cfg], capture_output=True, text=True)
        rss = int(r.stdout.split('RSS')[-1])
        self.assertLess(rss, 600 * 1024 * 1024, r.stderr[-500:])  # bytes on macOS

    def reasons_between(self, a, b):
        return {w for r in self.result['rejects'] if r['from'] < b and r['to'] > a for w in r['why']}

    def test_each_problem_is_found_where_it_is(self):
        self.assertEqual(self.reasons_between(0.3, 2.7), set())  # sharp and steady
        self.assertIn('blurred', self.reasons_between(3.3, 5.7))
        self.assertIn('black', self.reasons_between(6.3, 8.7))
        self.assertNotIn('shaky', self.reasons_between(9.3, 11.7))  # a steady pan is not shake
        self.assertIn('shaky', self.reasons_between(12.3, 14.7))

    def test_samples_and_cache(self):
        self.assertAlmostEqual(len(self.result['samples']), 15 * 3, delta=2)
        stamp = self.result['info']['stamp']
        with mock.patch.object(analyze, 'sample_pairs', side_effect=AssertionError('decoded again')):
            samples, info = analyze.measure(self.clip, settings.load(), self.result)
        self.assertEqual(info['stamp'], stamp)

    def test_broll_avoids_rejects_and_speech(self):
        for m in self.result['broll']:
            self.assertEqual(self.reasons_between(m['from'] + 0.1, m['to'] - 0.1), set())
        samples = self.result['samples']
        marks = analyze.flags(samples, settings.load())
        speech = [[0, 3.2]]
        for _, a, b in analyze.broll(samples, marks, speech, settings.load(), 1 / 3):
            self.assertGreaterEqual(a, 3.2)

    def frames(self, gpu, deep=False):
        return [(n, a.copy(), None if b is None else b.copy())
                for n, a, b in analyze.sample_pairs(self.clip, 30, 10, 320, 180, gpu, deep)]

    @unittest.skipUnless(sys.platform == 'darwin', 'needs VideoToolbox')
    def test_frames_kept_on_the_gpu_are_the_same_pixels(self):
        cpu = self.frames(None)
        self.assertEqual(analyze.gpu_format({'codec': 'h264', 'pix_fmt': 'yuv420p'}), 'nv12')
        with contextlib.redirect_stdout(io.StringIO()) as said:
            for gpu in ('nv12', 'p010le'):  # p010le: the decoder refuses to hand 8-bit frames that way, the CPU reads them
                got = self.frames(gpu)
                self.assertEqual(len(got), len(cpu))
                for (n, a, b), (m, c, d) in zip(got, cpu):
                    self.assertEqual(n, m)
                    self.assertTrue(np.array_equal(a, c) and (b is None and d is None or np.array_equal(b, d)), gpu)
        self.assertIn('read on the CPU', said.getvalue())
        self.assertEqual(said.getvalue().count('read on the CPU'), 1)

    def test_hdr_and_log_shown_as_sdr(self):
        def shown(code, look):
            return int(analyze.to_sdr(np.full((2, 2, 3), round(code * 65535), np.uint16), look)[0, 0, 0])
        self.assertEqual(shown(0, 'HLG'), 0)
        self.assertAlmostEqual(shown(0.75, 'HLG'), 247, delta=2)  # HDR reference white (203 nits): SDR white, rolled off
        self.assertAlmostEqual(shown(0.38, 'HLG'), 108, delta=3)  # 18 % grey (ITU-R BT.2408)
        self.assertAlmostEqual(shown(0.5806, 'PQ'), 247, delta=2)  # 203 nits
        self.assertGreaterEqual(shown(0.7518, 'PQ'), 253)  # 1000 nits: rolled off, not clipped before
        self.assertAlmostEqual(shown(0.3988, 'DLOG'), 125, delta=3)  # 18 % grey
        self.assertLess(shown(0.7, 'HLG'), shown(0.75, 'HLG'))  # the roll-off keeps the highlights apart...
        self.assertLess(shown(0.75, 'HLG'), shown(0.8, 'HLG'))
        self.assertEqual(shown(0.9, 'HLG'), 255)  # ...up to some 500 nits
        img = np.zeros((2, 2, 3), np.uint16)
        img[0, 0] = 65535
        self.assertEqual(analyze.clipped(img), 0.25)

    def test_an_hlg_clip_is_measured_on_its_sdr_picture(self):
        tagged = os.path.join(self.dir, 'hlg.mp4')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0x808080:size=320x180:rate=30:duration=1', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', '-bsf:v', 'h264_metadata=transfer_characteristics=18:colour_primaries=9:matrix_coefficients=9',
                        tagged], check=True)  # mid grey, in HLG and BT.2020
        with contextlib.redirect_stdout(io.StringIO()):
            samples, info = analyze.measure(tagged, settings.load(), None)
        self.assertEqual((info['look'], info['stamp']['look']), ('HLG', 'HLG'))
        plain = os.path.join(self.dir, 'plain.mp4')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', tagged, '-c', 'copy', '-bsf:v', 'h264_metadata=transfer_characteristics=1:colour_primaries=1:matrix_coefficients=1', plain], check=True)
        with contextlib.redirect_stdout(io.StringIO()):
            as_is, plain_info = analyze.measure(plain, settings.load(), None)
        self.assertNotIn('look', plain_info['stamp'])  # an SDR clip's cache stays valid
        self.assertAlmostEqual(as_is[0]['luma'], 128, delta=3)
        self.assertAlmostEqual(samples[0]['luma'], 143, delta=4)  # HLG 50 %: 51 nits, a quarter of reference white
        jpg = os.path.join(self.dir, 'still.jpg')
        analyze.still(tagged, 0.5, jpg, 'HLG', 320, 180)
        self.assertEqual(timeline.video_stream(timeline.ffprobe(jpg))['width'], 320)



@unittest.skipUnless(analyze and shutil.which('ffmpeg'), 'needs ffmpeg and numpy')
class AutoEditTest(unittest.TestCase):
    """The whole run on a folder of two generated clips, with a stand-in local brain."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.footage = os.path.join(self.dir, 'Weekend trip')
        os.makedirs(self.footage)
        self.work = os.path.join(self.dir, 'work')
        tdir = os.path.join(self.work, '.cache', 'Weekend trip', 'transcripts')
        os.makedirs(tdir)
        for n, tone in (('C0001', 440), ('C0002', 660)):
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25:duration=40',
                            '-f', 'lavfi', '-i', f'sine=f={tone}:sample_rate=48000:duration=40', '-c:v', 'mpeg4', '-c:a', 'aac',
                            os.path.join(self.footage, n + '.mp4')], check=True)
            words = [{'w': w, 'start': 2 + i * 0.5, 'end': 2.4 + i * 0.5} for i, w in enumerate(
                ('Hello and welcome. ' * 3 + 'Today we walk to the lake. ' * 10 + 'That is all.').split())]
            with open(os.path.join(tdir, n + '.words.json'), 'w') as f:
                json.dump({'words': words}, f)

    def settings(self, srv, **auto):
        os.makedirs(self.work, exist_ok=True)
        with open(os.path.join(self.work, 'roughcut.json'), 'w') as f:
            json.dump({'brain': {'engine': 'local', 'local_url': srv.url + '/v1', 'local_model': 'm'},
                       'ui': {'language': 'fr'}, 'auto': dict({'library': 'Test'}, **auto)}, f)

    def command(self, *more):
        return [sys.executable, os.path.join(MediaTest.SCRIPTS, 'auto_edit.py'), self.footage, '--work-dir', self.work,
                '--no-dialogs', '--no-open', '--music', 'none', *more]

    def run_edit(self, reply):
        srv = FakeServer(reply)
        self.addCleanup(srv.close)
        self.settings(srv)
        r = subprocess.run(self.command(), capture_output=True, text=True, env=dict(os.environ, ROUGHCUT_NO_NOTIFY='1'))
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return r.stdout.strip().splitlines()[-1], r.stdout

    def test_one_file_one_event_with_the_edit_the_short_and_the_logged_clips(self):
        decisions = {'hook': {'rush': 'R2', 'from': 8.0, 'to': 13.9},
                     'main': [{'rush': 'R1', 'from': 2.0, 'to': 18.0, 'chapter': 'Start'}, {'rush': 'R2', 'from': 2.0, 'to': 20.0}],
                     'broll': [], 'short': [{'rush': 'R1', 'from': 2.0, 'to': 20.0}, {'rush': 'R2', 'from': 2.0, 'to': 20.0}],
                     'title_choices': ['A walk to the lake'], 'description': 'We walk.', 'tags': ['lake']}
        final, out = self.run_edit(chat_reply(json.dumps(decisions)))
        self.assertTrue(final.endswith('.fcpxml') and os.path.exists(final), final)
        root = ET.parse(final).getroot()
        events = [e.get('name') for e in root.iter('event')]
        self.assertEqual(len(events), 1)
        self.assertRegex(events[0], r'^Weekend trip – \d{4}-\d\d-\d\d \d\dh\d\d')  # unique: date and time
        projects = [p.get('name') for p in root.iter('project')]
        self.assertEqual(len(projects), 2)
        self.assertIn(' – Short – ', projects[1])
        self.assertEqual(len(root.findall('./library/event/asset-clip')), 2)  # the two logged clips
        self.assertIn('Weekend trip est prêt', out)  # in the settings' language...
        self.assertNotIn('bibliothèque', out)  # ...and no library question: Final Cut Pro was not opened
        with open(os.path.join(os.path.dirname(final), 'publication.txt')) as f:
            self.assertIn('A walk to the lake', f.read())
        with open(os.path.join(os.path.dirname(final), 'ranges.json')) as f:
            ranges = json.load(f)['ranges']
        self.assertEqual(ranges[0]['note'], 'hook')
        self.assertFalse(any(r['file'].endswith('C0002.mp4') and r['from'] < 13.9 and r['to'] > 8.0 for r in ranges[1:]))  # hook once

    def test_the_edit_is_read_again_as_a_viewer(self):
        decisions = {'hook': None, 'main': [{'rush': 'R1', 'from': 2.0, 'to': 10.0}], 'broll': [], 'shorts': []}
        fix = {'add': [{'rush': 'R1', 'from': 20.0, 'to': 24.0, 'after': 'E1', 'why': 'la suite'}]}
        def reply(path, body, headers):
            asked = json.dumps(body)
            return chat_reply(json.dumps(fix if 'watching the rough cut' in asked else decisions))(path, body, headers)
        srv = FakeServer(reply)
        self.addCleanup(srv.close)
        self.settings(srv)
        env = dict(os.environ, ROUGHCUT_NO_NOTIFY='1')
        r = subprocess.run(self.command(), capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        project = os.path.dirname(r.stdout.strip().splitlines()[-1])
        asked = [json.dumps(b) for _, b, _ in srv.seen]
        self.assertEqual([('rough cut' in a, 'subtitles of a YouTube video' in a) for a in asked][:2], [(False, False), (True, False)])
        with open(os.path.join(project, 'ranges.json')) as f:
            added = [x for x in json.load(f)['ranges'] if x.get('marker', '').startswith('Relecture')]
        self.assertEqual([(x['from'], x['to'], x['marker']) for x in added], [(20.0, 24.0, 'Relecture: la suite')])
        # a new version from these choices re-uses the review: no question asked
        r = subprocess.run(self.command('--decisions', os.path.join(project, 'brain-answer.json')), capture_output=True,
                           text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertFalse(any('rough cut' in json.dumps(b) or 'editor of a YouTube' in json.dumps(b) for _, b, _ in srv.seen[len(asked):]))
        with open(os.path.join(os.path.dirname(r.stdout.strip().splitlines()[-1]), 'ranges.json')) as f:
            self.assertIn(20.0, [x['from'] for x in json.load(f)['ranges']])

    def test_several_shorts_each_in_its_own_project(self):
        decisions = {'hook': None, 'main': [{'rush': 'R1', 'from': 2.0, 'to': 18.0}], 'broll': [],
                     'shorts': [[{'rush': 'R1', 'from': 2.0, 'to': 20.0}], [{'rush': 'R2', 'from': 2.0, 'to': 20.0}],
                                [{'rush': 'R1', 'from': 3.0, 'to': 19.0}]]}  # the third is the first again
        final, out = self.run_edit(chat_reply(json.dumps(decisions)))
        root = ET.parse(final).getroot()
        projects = [p.get('name') for p in root.iter('project')]
        self.assertEqual(len(projects), 3, projects)
        self.assertIn(' – Short 1 – ', projects[1])
        self.assertIn(' – Short 2 – ', projects[2])
        project = os.path.dirname(final)
        for n in (1, 2):
            self.assertTrue(os.path.exists(os.path.join(project, f'short-{n}', 'captions.mov')))
        self.assertIn(', 2 Shorts (0:', out)

    def test_the_steps_for_an_app(self):
        decisions = {'hook': None, 'main': [{'rush': 'R2', 'from': 2.0, 'to': 20.0}], 'broll': [],
                     'short': [], 'title_choices': [], 'description': '', 'tags': []}
        srv = FakeServer(chat_reply(json.dumps(decisions)))
        self.addCleanup(srv.close)
        self.settings(srv, zooms=False)
        r = subprocess.run(self.command('--progress'), capture_output=True, text=True, env=dict(os.environ, ROUGHCUT_NO_NOTIFY='1'))
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        events = [(line.split(' ', 1)[0], json.loads(line.split(' ', 1)[1])) for line in r.stdout.splitlines() if line.startswith('@')]
        kinds = [k for k, _ in events]
        self.assertEqual(kinds[0], '@info')
        self.assertEqual(events[0][1]['clips'], 2)
        steps = [(e['name'], e['state']) for k, e in events if k == '@step']
        self.assertEqual(steps, [(n, s) for n in ('transcribe', 'analyze', 'choose', 'build') for s in ('start', 'end')])
        done = events[-1][1]
        self.assertEqual(kinds[-1], '@done')
        self.assertTrue(os.path.exists(done['fcpxml']))
        self.assertEqual(done['shorts'], [])
        self.assertIn('Sans musique', done['no_music'])
        self.assertEqual(done['library'], 'Test')  # the app reminds it
        with open(os.path.join(done['project'], 'edit.fcpxml')) as f:
            self.assertNotIn('<transform', f.read())  # zooms off

    def test_cancelled_from_the_app(self):
        def slow(*a):
            time.sleep(20)
            return chat_reply('{}')(*a)
        srv = FakeServer(slow)
        self.addCleanup(srv.close)
        self.settings(srv)
        p = subprocess.Popen(self.command('--progress'), stdout=subprocess.PIPE, text=True, env=dict(os.environ, ROUGHCUT_NO_NOTIFY='1'))
        project = None
        for line in p.stdout:
            if line.startswith('@info'):
                project = json.loads(line.split(' ', 1)[1])['project']
            if line.startswith('@step') and '"choose"' in line:
                if shutil.which('caffeinate'):  # the Mac is kept awake meanwhile
                    self.assertIn(f'caffeinate -i -w {p.pid}', subprocess.run(['ps', '-A', '-o', 'args='], capture_output=True, text=True).stdout)
                os.killpg(p.pid, signal.SIGTERM)  # the app stops the whole group
                break
        rest = p.stdout.read()
        p.stdout.close()
        self.assertEqual(p.wait(timeout=30), 130)
        self.assertIn('@cancelled', rest)
        self.assertFalse(os.path.exists(project))

    def test_a_folder_without_video_is_said_plainly(self):
        empty = os.path.join(self.dir, 'Nothing here')
        os.makedirs(empty)
        with open(os.path.join(empty, 'notes.txt'), 'w') as f:
            f.write('no video')
        srv = FakeServer(chat_reply('{}'))
        self.addCleanup(srv.close)
        self.settings(srv)
        cmd = self.command('--progress')
        cmd[cmd.index(self.footage)] = empty
        r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, ROUGHCUT_NO_NOTIFY='1'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('@failed {"why": "Aucune vidéo dans le dossier « Nothing here »."}', r.stdout)
        self.assertFalse(any(d.startswith('Nothing here') for d in os.listdir(self.work)))  # no project folder left

    def test_a_clip_without_sound_is_b_roll_and_a_failure_is_said_plainly(self):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25:duration=5', '-c:v', 'mpeg4',
                        os.path.join(self.footage, 'C0003.mp4')], check=True)  # no sound: never transcribed
        decisions = {'hook': None, 'main': [{'rush': 'R1', 'from': 2.0, 'to': 10.0}], 'broll': [], 'short': []}
        final, out = self.run_edit(chat_reply(json.dumps(decisions)))
        self.assertNotIn('transcribe.sh', out)
        with open(os.path.join(self.work, '.cache', 'Weekend trip', 'transcripts', 'C0003.words.json')) as f:
            self.assertEqual(json.load(f)['words'], [])
        with open(os.path.join(os.path.dirname(final), 'montage.log'), encoding='utf-8') as f:
            self.assertIn('step transcribe:', f.read())  # the length of each step, in the log
        with open(os.path.join(self.work, '.cache', 'Weekend trip', 'transcripts', 'C0001.words.json'), 'w') as f:
            f.write('{broken')  # a damaged cache: the edit stops, in plain words, the details in the log
        srv = FakeServer(chat_reply(json.dumps(decisions)))
        self.addCleanup(srv.close)
        self.settings(srv)
        r = subprocess.run(self.command('--progress'), capture_output=True, text=True, env=dict(os.environ, ROUGHCUT_NO_NOTIFY='1'))
        failed = json.loads([l for l in r.stdout.splitlines() if l.startswith('@failed')][0].split(' ', 1)[1])
        self.assertTrue(failed['why'].startswith("Le montage de Weekend trip s'est arrêté pendant la transcription."), failed['why'])
        self.assertNotIn('Traceback', failed['why'])
        self.assertTrue(failed['detail'])

    def test_footage_without_speech_is_said_plainly(self):
        for n in ('C0001', 'C0002'):  # no word said in any clip
            with open(os.path.join(self.work, '.cache', 'Weekend trip', 'transcripts', n + '.words.json'), 'w') as f:
                json.dump({'words': [], 'fitted_to_sound': True}, f)
        srv = FakeServer(chat_reply('{}'))
        self.addCleanup(srv.close)
        self.settings(srv)
        r = subprocess.run(self.command('--progress'), capture_output=True, text=True, env=dict(os.environ, ROUGHCUT_NO_NOTIFY='1'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('"why": "Aucune parole dans les rushs de « Weekend trip » : Roughcut monte à partir de ce qui est dit."', r.stdout)

    def test_two_edits_of_the_same_footage_at_once(self):
        decisions = {'hook': None, 'main': [{'rush': 'R1', 'from': 2.0, 'to': 10.0}], 'broll': [], 'short': []}
        srv = FakeServer(chat_reply(json.dumps(decisions)))
        self.addCleanup(srv.close)
        self.settings(srv)
        env = dict(os.environ, ROUGHCUT_NO_NOTIFY='1')
        runs = [subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env) for _ in range(2)]
        outs = [p.communicate(timeout=300)[0] for p in runs]
        self.assertEqual([p.returncode for p in runs], [0, 0], outs[0][-1500:] + outs[1][-1500:])
        self.assertEqual(len({o.strip().splitlines()[-1] for o in outs}), 2)  # two projects, each named for itself

    @unittest.skipUnless(REAL_FCP_APP, 'needs Final Cut Pro, to copy its DTDs')
    def test_the_fcpxml_version_follows_the_final_cut_pro_installed(self):
        decisions = {'hook': None, 'main': [{'rush': 'R1', 'from': 2.0, 'to': 10.0}], 'broll': [], 'short': []}
        srv = FakeServer(chat_reply(json.dumps(decisions)))
        self.addCleanup(srv.close)
        self.settings(srv)
        for version, dtds, ok in (('10.7', ('1_10', '1_11'), True), ('10.4', ('1_8',), False)):
            app = os.path.join(self.dir, f'FCP {version}.app')
            dtd_dir = os.path.join(app, fcp.DTD_DIR)
            os.makedirs(dtd_dir)
            with open(os.path.join(app, 'Contents', 'Info.plist'), 'wb') as f:
                plistlib.dump({'CFBundleShortVersionString': version}, f)
            for d in dtds:
                shutil.copy(os.path.join(REAL_FCP_APP, fcp.DTD_DIR, f'FCPXMLv{d}.dtd'), dtd_dir)
            r = subprocess.run(self.command('--progress'), capture_output=True, text=True,
                               env=dict(os.environ, ROUGHCUT_NO_NOTIFY='1', FCP_APP=app))
            if ok:  # Final Cut Pro 10.7 imports up to FCPXML 1.11: that is what it gets, checked against its DTD
                self.assertEqual(r.returncode, 0, r.stdout[-1500:])
                final = json.loads(r.stdout.split('@done ', 1)[1].splitlines()[0])['fcpxml']
                self.assertEqual(fcp.version_of(final), '1.11')
                self.assertEqual(fcp.check(final)[0], True)
            else:  # too old: said at once, in plain words
                self.assertEqual(r.returncode, 1)
                self.assertIn('Final Cut Pro 10.4 est trop ancien', r.stdout)

    def test_a_new_version_from_edited_choices(self):
        choices = os.path.join(self.dir, 'choices.json')
        with open(choices, 'w') as f:
            json.dump({'hook': None, 'main': [{'rush': 'R1', 'from': 2.0, 'to': 10.0}], 'short': [], 'broll': []}, f)
        srv = FakeServer(lambda *a: (500, 'text/plain', b'the brain must not be asked'))
        self.addCleanup(srv.close)
        os.makedirs(self.work, exist_ok=True)
        with open(os.path.join(self.work, 'roughcut.json'), 'w') as f:
            json.dump({'brain': {'engine': 'local', 'local_url': srv.url + '/v1', 'local_model': 'm'}}, f)
        r = subprocess.run([sys.executable, os.path.join(MediaTest.SCRIPTS, 'auto_edit.py'), self.footage, '--work-dir', self.work,
                            '--no-dialogs', '--no-open', '--music', 'none', '--decisions', choices],
                           capture_output=True, text=True, env=dict(os.environ, ROUGHCUT_NO_NOTIFY='1'))
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        # the choices given: the brain is asked only for the subtitles (and, failing, they stay untranslated)
        self.assertTrue(all('subtitles of a YouTube video' in json.dumps(b) for _, b, _ in srv.seen))
        self.assertEqual(len(list(ET.parse(r.stdout.strip().splitlines()[-1]).getroot().iter('project'))), 1)  # no Short asked for
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(r.stdout.strip().splitlines()[-1]), 'subtitles.en.srt')))

    def test_without_an_answer_all_the_speech_is_kept(self):
        final, out = self.run_edit(chat_reply('I cannot help with that.'))
        self.assertIn('aucun cerveau', out)
        self.assertEqual(len(list(ET.parse(final).getroot().iter('project'))), 2)  # the edit, and a Short from the densest stretch



if __name__ == '__main__':
    unittest.main()
