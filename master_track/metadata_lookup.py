"""Explicit, text-only MusicBrainz searches and Cover Art Archive downloads."""
from collections import OrderedDict
import json
import re
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .projects import metadata_fields

_network_lock = threading.Lock()
_last_request = 0.0
USER_AGENT = 'MasterTrack/0.1 (local personal music library; Python urllib)'


def fetch(url, maximum=2_000_000):
    global _last_request
    # Serialize requests across browser tabs and respect MusicBrainz's 1/sec limit.
    with _network_lock:
        time.sleep(max(0, 1.05 - (time.monotonic() - _last_request)))
        _last_request = time.monotonic()
        try:
            with urlopen(Request(url, headers={'User-Agent': USER_AGENT}), timeout=20) as response:
                data = response.read(maximum + 1)
                if len(data) > maximum:
                    raise ValueError('The metadata service returned an oversized response.')
                return data
        except HTTPError as error:
            if error.code == 404:
                return None
            raise ValueError(f'Metadata service returned HTTP {error.code}. Try again later.') from error
        except (URLError, TimeoutError) as error:
            raise ValueError('Could not reach the metadata service. Check your connection and try again.') from error


def mbid(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value)


def quote(value):
    return '"' + re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', value) + '"'


def candidates(data, fields):
    result = []
    seen = set()
    for recording in data.get('recordings', []):
        artist = ''.join(c.get('name', c.get('artist', {}).get('name', '')) + c.get('joinphrase', '')
                         for c in recording.get('artist-credit', []) if isinstance(c, dict))
        for release in sorted(recording.get('releases', []), key=lambda r: r.get('date') or '9999'):
            group = release.get('release-group', {})
            if not mbid(release.get('id')):
                continue
            key = (recording.get('title'), group.get('id') or release['id'], recording.get('disambiguation', ''))
            if key in seen:
                continue
            seen.add(key)
            result.append({'title': recording.get('title', ''), 'artist': artist,
                           'album': release.get('title', ''), 'year': release.get('date', '')[:4],
                           'release_id': release['id'], 'group_id': group.get('id'),
                           'recording_id': recording.get('id'), 'type': group.get('primary-type', ''),
                           'edition': release.get('disambiguation', ''),
                           'recording_note': recording.get('disambiguation', ''),
                           'score': int(recording.get('score', 0))})
    # Prefer studio album matches, but keep singles/live/compilation alternatives visible.
    result.sort(key=lambda c: (c['title'].casefold() != fields.get('title', '').casefold() if fields.get('title') else False,
                              c['album'].casefold() != fields['album'].casefold() if fields['album'] else False,
                              bool(c['recording_note']), -c['score'], c['type'] != 'Album', c['year'] or '9999'))
    return result[:12]


class MetadataLookup:
    def __init__(self):
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def remember(self, value):
        with self.lock:
            token = uuid.uuid4().hex
            self.items[token] = (time.monotonic(), value)
            while len(self.items) > 48:
                self.items.popitem(last=False)
            return token

    def get(self, token):
        with self.lock:
            item = self.items.get(token) if isinstance(token, str) else None
            if not item or time.monotonic() - item[0] > 3600:
                raise ValueError('This lookup expired. Find details again.')
            return dict(item[1])

    def search(self, data):
        fields = metadata_fields(data)
        if not fields['title']:
            raise ValueError('Enter a song title; artist and album help narrow the matches.')
        terms = ['recording:' + quote(fields['title'])]
        for field, search_field in [('artist', 'artist'), ('album', 'release')]:
            if fields[field]:
                terms.append(search_field + ':' + quote(fields[field]))
        url = 'https://musicbrainz.org/ws/2/recording/?' + urlencode({'query': ' AND '.join(terms), 'fmt': 'json', 'limit': 25})
        data = json.loads(fetch(url) or b'{}')
        return [dict(item, token=self.remember(item)) for item in candidates(data, fields)]

    def prepare(self, token):
        item = self.get(token)
        warning = ''
        cover = None
        if mbid(item.get('group_id')):
            try:
                group = json.loads(fetch(f"https://musicbrainz.org/ws/2/release-group/{item['group_id']}?fmt=json") or b'{}')
                if group.get('first-release-date'):
                    item['year'] = group['first-release-date'][:4]
            except ValueError:
                pass  # The selected edition's year is still usable.
        try:
            for kind, identifier in [('release', item['release_id']), ('release-group', item.get('group_id'))]:
                if not mbid(identifier):
                    continue
                cover = fetch(f'https://coverartarchive.org/{kind}/{identifier}/front-500', maximum=5_000_000)
                if cover:
                    break
        except ValueError as error:
            warning = str(error) + ' You can still save the metadata.'
        if cover and not (cover.startswith(b'\xff\xd8\xff') or cover.startswith(b'\x89PNG\r\n\x1a\n')):
            cover = None
            warning = 'This cover format is unsupported. Metadata is still available.'
        item['cover'] = cover
        item['cover_type'] = 'image/png' if cover and cover.startswith(b'\x89PNG') else 'image/jpeg'
        item['prepared'] = True
        return {'token': self.remember(item), 'has_cover': bool(cover),
                'fields': {key: item[key] for key in ('title', 'artist', 'album', 'year')},
                'warning': warning or ('' if cover else 'No cover found for this release.'),
                'source_url': 'https://musicbrainz.org/release/' + item['release_id']}
