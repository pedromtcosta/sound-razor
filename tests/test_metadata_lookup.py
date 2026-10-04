import json
from pathlib import Path
import tempfile
import unittest

from master_track.metadata_lookup import MetadataLookup, candidates, quote
from master_track.projects import save_metadata, cover_path, describe_project


class MetadataTests(unittest.TestCase):
    def test_search_escapes_query_operators(self):
        self.assertEqual(quote('Song "live"'), '"Song \\"live\\""')
        self.assertEqual(quote('AC/DC'), '"AC\\/DC"')

    def test_studio_match_and_earliest_release_precede_live(self):
        group = {'id': '11111111-1111-1111-1111-111111111111', 'primary-type': 'Album'}
        releases = [{'id': '22222222-2222-2222-2222-222222222222', 'title': 'Album', 'date': '2021', 'release-group': group},
                    {'id': '33333333-3333-3333-3333-333333333333', 'title': 'Album', 'date': '1990', 'release-group': group}]
        base = {'title': 'Song', 'score': 100, 'artist-credit': [{'name': 'Artist'}], 'releases': releases}
        result = candidates({'recordings': [dict(base, disambiguation='live'), base]}, {'album': 'Album'})
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['recording_note'], '')
        self.assertEqual(result[0]['year'], '1990')

    def test_cache_is_bounded_and_unknown_tokens_rejected(self):
        lookup = MetadataLookup()
        first = lookup.remember({'title': 'first'})
        for i in range(48):
            lookup.remember({'title': str(i)})
        with self.assertRaises(ValueError):
            lookup.get(first)
        with self.assertRaises(ValueError):
            lookup.get({'unexpected': 'type'})

    def test_exact_title_precedes_alternate_mix(self):
        release = {'id': '22222222-2222-2222-2222-222222222222', 'title': 'Album'}
        result = candidates({'recordings': [
            {'title': 'Song (5.1 mix)', 'score': 100, 'releases': [release]},
            {'title': 'Song', 'score': 98, 'releases': [release]},
        ]}, {'title': 'Song', 'album': 'Album'})
        self.assertEqual(result[0]['title'], 'Song')

    def test_cover_persists_and_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project/'metadata.json').write_text(json.dumps({'custom': 'keep'}))
            enrichment = {'prepared': True, 'cover': b'\xff\xd8\xfftest', 'cover_type': 'image/jpeg', 'release_id': 'release'}
            result = save_metadata(project, {'title': 'Song', 'album': 'Album'}, enrichment)
            self.assertTrue(cover_path(project).is_file())
            self.assertEqual(result['cover'], cover_path(project).name)
            self.assertEqual(json.loads((project/'metadata.json').read_text())['custom'], 'keep')
            # Ordinary edits retain artwork; selecting a release without artwork clears its reference.
            save_metadata(project, {'title': 'Renamed'})
            self.assertIsNotNone(cover_path(project))
            save_metadata(project, {'title': 'Other'}, {'prepared': True, 'cover': None})
            self.assertIsNone(cover_path(project))
            (project/'metadata.json').write_text(json.dumps({'cover_file': '../outside.jpg'}))
            self.assertIsNone(cover_path(project))

    def test_unprepared_match_does_not_change_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            save_metadata(project, {'title': 'Original'})
            with self.assertRaises(ValueError):
                save_metadata(project, {'title': 'Changed'}, {})
            self.assertEqual(describe_project(project)['title'], 'Original')


if __name__ == '__main__':
    unittest.main()
