"""Check real FFmpeg mixes with known audio samples, without model downloads."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave

from sound_razor.pipeline import combine_other, MODEL_STEMS, DEFAULT_MODEL


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
class OtherMixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = {}

    def audio(self, name, value, frames=100):
        path = self.root / f'{name}.wav'
        with wave.open(str(path), 'wb') as output:
            output.setparams((2, 2, 44100, frames, 'NONE', 'not compressed'))
            output.writeframes(struct.pack('<h', round(value * 32768)) * frames * 2)
        self.paths[name] = path
        return path

    def samples(self):
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(self.paths['other']),
                                       '-f', 'f32le', '-c:a', 'pcm_f32le', '-'])
        return struct.unpack(f'<{len(raw)//4}f', raw)

    def test_unselected_piano_added_without_selected_instruments_or_gain_change(self):
        self.audio('other', .75)
        piano = self.audio('piano', .75)
        original_piano = piano.read_bytes()
        combine_other(self.paths, MODEL_STEMS[DEFAULT_MODEL],
                      ('guitar', 'bass', 'vocals', 'drums', 'other'))
        self.assertEqual(len(self.samples()), 200)
        for value in self.samples():
            self.assertAlmostEqual(value, 1.5, places=6)
        self.assertEqual(piano.read_bytes(), original_piano)

    def test_other_alone_includes_every_output_and_keeps_longest_duration(self):
        for name in MODEL_STEMS[DEFAULT_MODEL]:
            self.audio(name, .125, frames=200 if name == 'piano' else 100)
        combine_other(self.paths, MODEL_STEMS[DEFAULT_MODEL], ('other',))
        samples = self.samples()
        self.assertEqual(len(samples), 400)
        self.assertAlmostEqual(samples[0], .75, places=6)
        self.assertAlmostEqual(samples[-1], .125, places=6)

    def test_full_selection_or_no_other_does_not_change_output(self):
        original = self.audio('other', .25).read_bytes()
        for selection in (MODEL_STEMS[DEFAULT_MODEL], ('guitar',)):
            combine_other(self.paths, MODEL_STEMS[DEFAULT_MODEL], selection)
            self.assertEqual(self.paths['other'].read_bytes(), original)

    def test_four_stem_model_merges_unselected_vocals_and_drums(self):
        for name in ('other', 'vocals', 'drums'):
            self.audio(name, .125)
        combine_other(self.paths, MODEL_STEMS['htdemucs.yaml'], ('bass', 'other'))
        self.assertAlmostEqual(self.samples()[0], .375, places=6)

    def test_selected_guitar_is_not_added_to_other(self):
        for name in ('other', 'piano'):
            self.audio(name, .125)
        self.audio('guitar', .75)
        combine_other(self.paths, MODEL_STEMS[DEFAULT_MODEL],
                      ('guitar', 'bass', 'vocals', 'drums', 'other'))
        self.assertAlmostEqual(self.samples()[0], .25, places=6)

    def test_missing_unselected_stem_fails_without_replacing_other(self):
        original = self.audio('other', .25).read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'missing stems'):
            combine_other(self.paths, MODEL_STEMS[DEFAULT_MODEL], ('guitar', 'other'))
        self.assertEqual(self.paths['other'].read_bytes(), original)
