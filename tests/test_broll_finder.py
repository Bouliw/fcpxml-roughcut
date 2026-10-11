"""Tests of the b-roll-finder module (modules/b-roll-finder): words found in the transcript, licences sorted, the
contact-sheet badges (with Pillow), and a beat placed by Roughcut's own scripts in a real FCPXML (with ffmpeg).
Run: python3 -m unittest discover -s tests -v"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'modules', 'b-roll-finder', 'scripts'))
import commons  # noqa: E402
import place_broll  # noqa: E402


def secs(rational):
    return Fraction(rational[:-1])


class WordsTest(unittest.TestCase):
    WORDS = [('We', 1.0, 1.2), ('saw', 1.2, 1.4), ("l'Élysée,", 1.5, 2.0), ('then', 5.0, 5.2), ("l'Élysée", 9.0, 9.5)]

    def test_accents_apostrophes_and_the_occurrence_nearest_at(self):
        self.assertEqual(place_broll.find_word(self.WORDS, 'Elysee', 8.0), (9.0, None))
        self.assertEqual(place_broll.find_word(self.WORDS, 'élysée', 1.0), (1.5, None))

    def test_a_word_absent_or_said_twice_says_why(self):
        self.assertIn('not said near 1.0 s', place_broll.find_word(self.WORDS, 'Matignon', 1.0)[1])
        self.assertIn('said 2 times', place_broll.find_word(self.WORDS, 'Élysée')[1])


class LicenceTest(unittest.TestCase):
    def test_only_licences_free_for_a_monetised_video(self):
        got = [commons.family(x) for x in ('pd-old', 'cc0', 'cc-by-4.0', 'cc-by-sa-3.0', 'cc-by-nc-4.0', 'cc-by-nd-2.0', 'gfdl', '')]
        self.assertEqual(got, ['pd', 'cc0', 'cc-by', 'cc-by-sa', None, None, None, None])

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'needs Pillow')
    def test_the_badge_is_worked_out_not_typed(self):
        import contact_sheet
        def badge(**c):
            return contact_sheet.badge(c, ['My Channel'])[0]
        self.assertEqual(badge(source='YouTube', channel='Someone else'), 'CLAIM RISK')
        self.assertEqual(badge(source='YouTube', channel='my channel '), 'Your channel')
        self.assertEqual(badge(source='Wikimedia Commons', licence='CC BY-SA 4.0'), 'Free · credit (BY-SA)')
        self.assertEqual(badge(source='Wikimedia Commons', licence='Public domain'), 'Free · public domain')
        self.assertEqual(badge(source='a ministry', licence='capture'), 'Official page capture')
        self.assertEqual(badge(source='Hyperframes', licence='own'), 'Own graphic')
        self.assertEqual(badge(source='a blog', licence='unknown'), 'Licence to check')  # "unknown" is not "own"


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs ffmpeg')
class PlaceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def path(self, *names):
        return os.path.join(self.dir, *names)

    def write(self, name, data):
        with open(self.path(name), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

    def test_a_beat_lands_just_after_its_word_and_a_cut_word_is_dropped(self):
        ff = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i']
        subprocess.run(ff + ['testsrc2=size=320x180:rate=25:duration=10', '-f', 'lavfi', '-i', 'sine=f=440:duration=10',
                             '-c:v', 'mpeg4', '-c:a', 'aac', self.path('head.mp4')], check=True)
        os.makedirs(self.path('b-roll', 'clips'))
        subprocess.run(ff + ['color=c=blue:size=320x180:rate=25:duration=6', '-c:v', 'mpeg4', self.path('b-roll', 'clips', 'b01.mp4')],
                       check=True)
        edl = {'project': 'Vlog', 'clips': [{'file': 'head.mp4', 'in': 1, 'out': 4}, {'file': 'head.mp4', 'in': 6, 'out': 9}]}
        self.write('edl.json', edl)
        os.makedirs(self.path('transcripts'))
        self.write(os.path.join('transcripts', 'head.words.json'), {'words': [
            {'w': 'Hello', 'start': 1.2, 'end': 1.6}, {'w': 'Paris', 'start': 4.5, 'end': 4.9}, {'w': "l'Élysée", 'start': 6.5, 'end': 7.0}]})
        self.write(os.path.join('b-roll', 'picks.json'), {'beats': [
            {'id': 'b01', 'clip': 'clips/b01.mp4', 'word': 'Élysée', 'at': 6.6, 'seconds': 2, 'note': 'the palace',
             'licence': 'CC BY 4.0', 'credit': '"Palace" by someone, CC BY 4.0, via Wikimedia Commons'},
            {'id': 'b02', 'clip': 'clips/b01.mp4', 'word': 'Paris', 'at': 4.5}]})
        env = {k: v for k, v in os.environ.items() if k != 'ROUGHCUT_ENGINE'}  # found as the checkout it ships in
        r = subprocess.run([sys.executable, place_broll.__file__, self.dir, self.path('b-roll', 'picks.json')],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('DROPPED b02 "Paris": "Paris" at 0:04.5 of the rush was cut out of the edit', r.stdout)

        root = ET.parse(self.path('edit-broll.fcpxml')).getroot()
        self.assertEqual(root.find('.//project').get('name'), 'Vlog – b-roll')
        second = root.findall('.//spine/asset-clip')[1]
        b = [c for c in second.findall('asset-clip') if c.get('name') == 'b01'][0]
        self.assertEqual(b.get('lane'), '1')
        # "Élysée" is said at 6.5 s, 0.5 s into the second cut: the b-roll starts 0.3 s later
        self.assertAlmostEqual(float(secs(b.get('offset')) - secs(second.get('start'))), 0.8, delta=0.04)
        self.assertAlmostEqual(float(secs(b.get('duration'))), 2.0, delta=0.04)
        self.assertEqual(b.find('marker').get('value'), 'B-roll: b01 the palace')
        with open(self.path('b-roll', 'CREDITS.txt'), encoding='utf-8') as f:
            self.assertEqual(f.read(), 'Image credits\n0:03.8 — "Palace" by someone, CC BY 4.0, via Wikimedia Commons\n')
        with open(self.path('b-roll', 'BROLL-MANIFEST.md'), encoding='utf-8') as f:
            self.assertIn('| b02 | Paris |', f.read())
        with open(self.path('edl.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f), edl)  # Roughcut's own edit is never touched


if __name__ == '__main__':
    unittest.main()
