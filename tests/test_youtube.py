import tempfile
from pathlib import Path
import unittest
from sound_razor.sources import youtube_url, youtube_command
from sound_razor.ui import Library

class YouTubeTests(unittest.TestCase):
    def test_single_video_urls_are_canonicalized(self):
        expected = 'https://www.youtube.com/watch?v=BaW_jenozKc'
        for url in ['https://youtu.be/BaW_jenozKc?t=5', expected+'&list=anything',
                    'https://music.youtube.com/watch?v=BaW_jenozKc',
                    'https://www.youtube.com/shorts/BaW_jenozKc']:
            self.assertEqual(youtube_url(url), expected)
        for url in [None, 'file:///tmp/audio', 'https://example.com/watch?v=BaW_jenozKc',
                    'https://youtube.com/playlist?list=anything', 'https://youtube.com/@artist',
                    'https://user:pass@youtube.com/watch?v=BaW_jenozKc']:
            with self.assertRaises(ValueError): youtube_url(url)

    def test_download_command_ignores_user_config_and_playlist(self):
        command = youtube_command('https://youtu.be/BaW_jenozKc', Path('/tmp/work'))
        self.assertIn('--ignore-config', command)
        self.assertIn('--no-playlist', command)
        self.assertIn('--write-info-json', command)
        self.assertEqual(command[-2], '--')
        self.assertEqual(command[command.index('--audio-format')+1], 'wav')

    def test_invalid_url_and_changed_root_never_create_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = Library(root/'projects', root/'ui.json', root/'models')
            for data in [{'url': 'https://example.com'}, {'url': 'https://youtu.be/BaW_jenozKc', 'root': '/wrong'}]:
                with self.assertRaises(ValueError): library.import_youtube(data)
                self.assertIsNone(library.job)
                self.assertEqual(list(library.root.iterdir()), [])
