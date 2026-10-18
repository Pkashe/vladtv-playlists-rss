#!/usr/bin/env python3
"""Create an RSS feed for public playlists belonging to @vladtv."""
import json
import os
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'data' / 'playlists.json'
FEED = ROOT / 'docs' / 'rss.xml'
API_ROOT = 'https://www.googleapis.com/youtube/v3/'
MAX_ENTRIES = 75
MEDIA_NS = 'http://search.yahoo.com/mrss/'
ET.register_namespace('media', MEDIA_NS)


def get_json(endpoint, **params):
    params['key'] = os.environ['YOUTUBE_API_KEY']
    url = API_ROOT + endpoint + '?' + urlencode(params)
    with urlopen(url, timeout=30) as res:
        return json.load(res)


def fetch_playlists():
    channel = get_json('channels', part='id', forHandle='@vladtv')
    if not channel.get('items'):
        raise RuntimeError('Cannot resolve YouTube handle @vladtv')
    channel_id = channel['items'][0]['id']
    playlists = {}
    token = None
    while True:
        args = {'part': 'snippet', 'channelId': channel_id, 'maxResults': 50}
        if token:
            args['pageToken'] = token
        page = get_json('playlists', **args)
        for item in page.get('items', []):
            snippet = item.get('snippet', {})
            pid = item.get('id')
            if pid and snippet.get('publishedAt'):
                playlists[pid] = {
                    'id': pid,
                    'title': snippet.get('title') or 'Untitled playlist',
                    'published': snippet['publishedAt'],
                    'description': snippet.get('description', '')[:900],
                    'thumbnail': next((snippet.get('thumbnails', {}).get(size, {}).get('url', '') for size in ('maxres', 'standard', 'high', 'medium', 'default') if snippet.get('thumbnails', {}).get(size, {}).get('url')), ''),
                    'url': 'https://www.youtube.com/playlist?list=' + pid,
                }
        token = page.get('nextPageToken')
        if not token:
            break
    return playlists


def utc_date(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def main():
    if not os.environ.get('YOUTUBE_API_KEY'):
        raise RuntimeError('Missing YOUTUBE_API_KEY GitHub Actions secret')
    fetched = fetch_playlists()
    if not fetched:
        raise RuntimeError('No playlists returned; leaving existing feed untouched')
    if STATE.exists():
        saved = json.loads(STATE.read_text(encoding='utf-8'))
        seen = set(saved.get('seen', []))
        entries = saved.get('entries', [])
    else:
        # Bootstrap with recent playlists so Feedly can be tested immediately.
        seen = set(fetched.keys())
        entries = sorted(fetched.values(), key=lambda x: x['published'], reverse=True)[:30]
    # Refresh titles and thumbnail URLs for existing entries without changing GUIDs or publish dates.
    for item in entries:
        current = fetched.get(item['id'])
        if current:
            item['title'] = current['title']
            item['description'] = current['description']
            item['thumbnail'] = current['thumbnail']
    existing_entry_ids = {item['id'] for item in entries}
    for pid, playlist in fetched.items():
        if pid not in seen and pid not in existing_entry_ids:
            entries.append(playlist)
    entries.sort(key=lambda x: x['published'], reverse=True)
    entries = entries[:MAX_ENTRIES]
    seen.update(fetched.keys())

    rss = ET.Element('rss', {'version': '2.0'})
    channel = ET.SubElement(rss, 'channel')
    for key, value in [
        ('title', 'VladTV — New Playlists'),
        ('link', 'https://www.youtube.com/@vladtv/playlists'),
        ('description', 'One RSS entry per newly discovered public VladTV playlist; individual video uploads excluded.'),
        ('language', 'en'),
    ]:
        ET.SubElement(channel, key).text = value
    ET.SubElement(channel, 'lastBuildDate').text = format_datetime(datetime.now(timezone.utc))
    for playlist in entries:
        entry = ET.SubElement(channel, 'item')
        ET.SubElement(entry, 'title').text = playlist['title']
        ET.SubElement(entry, 'link').text = playlist['url']
        ET.SubElement(entry, 'guid', {'isPermaLink': 'false'}).text = 'youtube-playlist:' + playlist['id']
        ET.SubElement(entry, 'pubDate').text = format_datetime(utc_date(playlist['published']))
        details = playlist.get('description', '') or playlist['title']
        thumbnail = playlist.get('thumbnail', '')
        if thumbnail:
            ET.SubElement(entry, f'{{{MEDIA_NS}}}thumbnail', {'url': thumbnail})
            # Feedly commonly extracts the first image from HTML in RSS descriptions.
            ET.SubElement(entry, 'description').text = f'<p><img src="{escape(thumbnail, quote=True)}" alt="{escape(playlist["title"], quote=True)}" /></p><p>{escape(details)}</p>'
        else:
            ET.SubElement(entry, 'description').text = details
    ET.indent(rss, space='  ')
    FEED.parent.mkdir(parents=True, exist_ok=True)
    FEED.write_bytes(ET.tostring(rss, encoding='utf-8', xml_declaration=True))
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({'seen': sorted(seen), 'entries': entries}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Found {len(fetched)} playlists; RSS contains {len(entries)} entries')


if __name__ == '__main__':
    main()
