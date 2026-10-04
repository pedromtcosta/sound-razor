from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
import wave

from sound_razor.projects import (describe_project, import_project, list_projects,
                                   project_path, save_metadata)


def wav_bytes():
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x01\x00" * 800)
    return output.getvalue()


class ProjectTests(unittest.TestCase):
    def test_lead_rhythm_split_preserves_but_hides_combined_guitar(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            stems = folder / 'stems'
            stems.mkdir()
            for name in ('guitar', 'lead', 'rhythm', 'bass'):
                (stems / f'{name}.wav').write_bytes(wav_bytes())
            (stems/'separation.json').write_text(json.dumps({'lead_rhythm_split': {'overlap': .5}}))
            self.assertEqual({s['file'] for s in describe_project(folder)['stems']},
                             {'lead.wav', 'rhythm.wav', 'bass.wav'})
            self.assertTrue((stems/'guitar.wav').exists())
            (stems/'rhythm.wav').unlink()
            self.assertIn('guitar.wav', {s['file'] for s in describe_project(folder)['stems']})

    def test_custom_guitar_titles_are_labels_not_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "original.wav").write_bytes(wav_bytes())
            stems = project / "stems"
            stems.mkdir()
            for name in ("guitar-target", "guitar-remainder", "bass"):
                (stems / f"{name}.wav").write_bytes(wav_bytes())
            title = "Solo / <guitar>"
            (stems / "separation.json").write_text(json.dumps({"guitar_split": {"title": title, "prompt": "guitar"}}))
            result = describe_project(project)
            self.assertEqual({stem["file"]: stem["name"] for stem in result["stems"]},
                             {"guitar-target.wav": title, "guitar-remainder.wav": "Guitar remainder", "bass.wav": "bass"})
            self.assertEqual(result["separation"]["guitar_split"][0]["prompt"], "guitar")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.library = self.root / "library"
        self.library.mkdir()
        self.source = self.root / "source.wav"
        self.source.write_bytes(wav_bytes())

    def test_import_copies_source_and_preserves_metadata(self):
        project = import_project(self.library, self.source, "Song.wav", {"artist": "An Artist", "title": "A Song"})
        folder = self.library / project["id"]
        self.assertEqual((folder / "Song.wav").read_bytes(), self.source.read_bytes())
        self.assertEqual(project["artist"], "An Artist")
        self.assertTrue(self.source.exists())
        data = json.loads((folder / "metadata.json").read_text())
        data["custom"] = {"notes": "Keep me"}
        (folder / "metadata.json").write_text(json.dumps(data))
        save_metadata(folder, {"title": "New title", "album": "Album"})
        updated = json.loads((folder / "metadata.json").read_text())
        self.assertEqual(updated["original_file"], "Song.wav")
        self.assertEqual(updated["custom"], data["custom"])

    def test_duplicate_import_creates_separate_project(self):
        first = import_project(self.library, self.source, "Song.wav", {})
        second = import_project(self.library, self.source, "Song.wav", {})
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(list_projects(self.library)), 2)

    def test_folder_without_metadata_is_discovered(self):
        folder = self.library / "Existing"
        folder.mkdir()
        (folder / "Track.wav").write_bytes(wav_bytes())
        info = describe_project(folder)
        self.assertEqual(info["original"], "Track.wav")
        self.assertEqual(info["title"], "Track")
        self.assertFalse(info["warnings"])
        (folder / "Another.wav").write_bytes(wav_bytes())
        self.assertIsNone(describe_project(folder)["original"])

    def test_malformed_metadata_does_not_hide_library(self):
        folder = self.library / "Bad metadata"
        folder.mkdir()
        (folder / "metadata.json").write_text("not json")
        self.assertTrue(list_projects(self.library)[0]["warnings"])
        with self.assertRaises(ValueError):
            save_metadata(folder, {"title": "Don't destroy malformed data"})

    def test_paths_and_symlinks_cannot_escape_library(self):
        for value in ("../escape", "/tmp", "nested/song", "..", "a\\b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                project_path(self.library, value)
        (self.library / "external").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(list_projects(self.library), [])
        with self.assertRaises(ValueError):
            project_path(self.library, "external")
        with self.assertRaises(ValueError):
            import_project(self.library, self.source, "../escape.wav", {})

    def test_invalid_audio_is_not_published(self):
        self.source.write_text("not audio")
        with self.assertRaises(Exception):
            import_project(self.library, self.source, "Bad.wav", {})
        self.assertEqual(list(self.library.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
