import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_projects import wav_bytes


class OutputSafetyTests(unittest.TestCase):
    def check_rejected(self, command, source, output, expected="overwrite"):
        before = source.read_bytes()
        cache = output / "unused-cache"
        result = subprocess.run([sys.executable, "-m", "sound_razor", command,
                                 "--file", str(source), "--output", str(output), "--cache", str(cache)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(expected, result.stderr)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(cache.exists(), "Reject the collision before downloads or inference.")

    def test_both_commands_refuse_to_replace_their_input(self):
        for command, filename in [("separate", "guitar.wav"), ("split-guitar", "lead.wav"),
                                  ("split-guitar", "rhythm.wav"), ("separate", "separation.json")]:
            with self.subTest(command=command, filename=filename), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                source = folder / filename
                source.write_bytes(wav_bytes())
                self.check_rejected(command, source, folder)
                self.assertEqual(list(folder.iterdir()), [source])

    def test_links_cannot_hide_an_input_output_collision(self):
        for command, filename in [("separate", "guitar.wav"), ("split-guitar", "lead.wav")]:
            for kind in ("symlink", "hardlink"):
                with self.subTest(command=command, kind=kind), tempfile.TemporaryDirectory() as temporary:
                    folder = Path(temporary)
                    source = folder / "original.wav"
                    source.write_bytes(wav_bytes())
                    alias = folder / filename
                    try:
                        if kind == "symlink":
                            alias.symlink_to(source)
                        else:
                            os.link(source, alias)
                    except OSError as error:
                        self.skipTest(f"Filesystem does not support {kind}: {error}")
                    self.check_rejected(command, source, folder)
                    self.assertEqual(alias.read_bytes(), source.read_bytes())

    def test_settings_symlink_is_rejected_before_any_output_changes(self):
        for command in ("separate", "split-guitar"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                source = folder / "original.wav"
                source.write_bytes(wav_bytes())
                outside = folder / "keep.json"
                outside.write_text('{"keep": true}')
                try:
                    (folder / "separation.json").symlink_to(outside)
                except OSError as error:
                    self.skipTest(f"Filesystem does not support symlinks: {error}")
                self.check_rejected(command, source, folder, "symlinks")
                self.assertEqual(outside.read_text(), '{"keep": true}')
