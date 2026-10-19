
#!/usr/bin/env python3
"""VladTV RSS: one entry per interviewee per month."""

import json
import os
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "playlists.json"
FEED = ROOT / "docs" / "rss.xml"

API_ROOT = "https://www.googleapis.com/youtube/v3/"
MAX_ENTRIES = 75
MEDIA_NS = "http://search.yahoo.com/mrss/"

ET.register_namespace("media", MEDIA_NS)

MONTHS = (
    "january", "february", "march", "april",
    "may", "june", "july", "august",
    "september", "october", "november", "december"
)

MONTH_PATTERN = re.compile(
    r"\b(" + "|".join(MONTHS) + r")\b"
    r"[\s,\-/]*(20\d{2}|\d{2})?\b",
    re.IGNORECASE
)


def get_json(endpoint, **params):
    params["key"] = os.environ["YOUTUBE_API_KEY"]
    url = API_ROOT + endpoint + "?" + urlencode(params)

    with urlopen(url, timeout=30) as response:
        return json.load(response)


def fetch_playlists():
    channel = get_json(
        "channels",
        part="id",
        forHandle="@vladtv"
    )

    if not channel.get("items"):
        raise RuntimeError("Cannot resolve @vladtv")

    channel_id = channel["items"][0]["id"]

    playlists = {}
    token = None

    while True:
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "maxResults": 50
        }

        if token:
            params["pageToken"] = token

        page = get_json("playlists", **params)

        for item in page.get("items", []):
            snippet = item.get("snippet", {})
            pid = item.get("id")

            if not pid or not snippet.get("publishedAt"):
                continue

            thumbnails = snippet.get("thumbnails", {})
            thumbnail = ""

            for size in (
                "maxres", "standard", "high",
                "medium", "default"
            ):
                candidate = thumbnails.get(size, {}).get("url")
                if candidate:
                    thumbnail = candidate
                    break

            playlists[pid] = {
                "id": pid,
                "title": snippet.get("title") or "Untitled",
                "published": snippet["publishedAt"],
                "description": snippet.get(
                    "description", ""
                )[:900],
                "thumbnail": thumbnail,
                "url": (
                    "https://www.youtube.com/playlist?list="
                    + pid
                )
            }

        token = page.get("nextPageToken")

        if not token:
            break

    return playlists


def interview_key(playlist):
    """
    Normalise interview title plus month/year.

    Examples:
    Vlad Tenev - October 2026
    Vlad Tenev - October 26
    Vlad Tenev October 2026

    All become: vlad tenev|2026-10
    """

    title = playlist.get("title", "").strip()
    match = MONTH_PATTERN.search(title)

    if match:
        month_name = match.group(1).lower()
        month = MONTHS.index(month_name) + 1

        year = match.group(2)

        if year:
            year = int(year)
            if year < 100:
                year += 2000
        else:
            year = datetime.fromisoformat(
                playlist["published"].replace(
                    "Z", "+00:00"
                )
            ).year

        name = (
            title[:match.start()] + " " +
            title[match.end():]
        )
        date_key = f"{year:04d}-{month:02d}"

    else:
        # For titles without a month, use the title
        # only. This avoids repeated generic playlists.
        name = title
        date_key = "undated"

    name = name.lower()

    # Ignore punctuation and formatting differences.
    name = re.sub(r"[^a-z0-9]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name + "|" + date_key


def utc_date(value):
    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    ).astimezone(timezone.utc)


def deduplicate(entries):
    """
    Keep the earliest-created playlist for each
    interviewee/month. Existing GUIDs are preserved.
    """

    unique = {}

    for playlist in sorted(
        entries,
        key=lambda x: (x["published"], x["id"])
    ):
        key = interview_key(playlist)

        if key not in unique:
            unique[key] = playlist

    return list(unique.values())


def main():
    if not os.environ.get("YOUTUBE_API_KEY"):
        raise RuntimeError(
            "Missing YOUTUBE_API_KEY GitHub secret"
        )

    fetched = fetch_playlists()

    if not fetched:
        raise RuntimeError(
            "No playlists returned; feed left unchanged"
        )

    if STATE.exists():
        saved = json.loads(
            STATE.read_text(encoding="utf-8")
        )

        seen = set(saved.get("seen", []))
        entries = saved.get("entries", [])

    else:
        # First-time setup.
        seen = set(fetched.keys())

        entries = sorted(
            fetched.values(),
            key=lambda x: x["published"],
            reverse=True
        )[:30]

    # Retain the oldest playlist already represented
    # for each interview/month.
    entries = deduplicate(entries)

    known_interviews = {
        interview_key(item) for item in entries
    }

    # Also remember interview groups from earlier
    # saved playlist IDs when they remain accessible.
    # This prevents a different playlist ID for the
    # same interview becoming a new article.
    for pid in seen:
        playlist = fetched.get(pid)
        if playlist:
            known_interviews.add(
                interview_key(playlist)
            )

    # Only add genuinely new interview/month groups.
    new_playlists = [
        playlist
        for pid, playlist in fetched.items()
        if pid not in seen
    ]

    new_playlists.sort(
        key=lambda x: (x["published"], x["id"])
    )

    added = 0

    for playlist in new_playlists:
        key = interview_key(playlist)

        if key not in known_interviews:
            entries.append(playlist)
            known_interviews.add(key)
            added += 1

    # Remember every playlist ID, including duplicates.
    seen.update(fetched.keys())

    # Keep the saved RSS entries unchanged when existing
    # playlists receive additional videos or updates.
    entries.sort(
        key=lambda x: x["published"],
        reverse=True
    )

    entries = entries[:MAX_ENTRIES]

    rss = ET.Element("rss", {"version": "2.0"})

    channel = ET.SubElement(rss, "channel")

    for key, value in [
        ("title", "VladTV — New Interviews"),
        (
            "link",
            "https://www.youtube.com/@vladtv/playlists"
        ),
        (
            "description",
            "One RSS entry per new VladTV interviewee "
            "per month. Interview parts and duplicate "
            "playlists are excluded."
        ),
        ("language", "en")
    ]:
        ET.SubElement(channel, key).text = value

    ET.SubElement(
        channel, "lastBuildDate"
    ).text = format_datetime(
        datetime.now(timezone.utc)
    )

    for playlist in entries:
        item = ET.SubElement(channel, "item")

        ET.SubElement(
            item, "title"
        ).text = playlist["title"]

        ET.SubElement(
            item, "link"
        ).text = playlist["url"]

        # Do not change the original playlist GUID.
        ET.SubElement(
            item,
            "guid",
            {"isPermaLink": "false"}
        ).text = "youtube-playlist:" + playlist["id"]

        ET.SubElement(
            item, "pubDate"
        ).text = format_datetime(
            utc_date(playlist["published"])
        )

        thumbnail = playlist.get("thumbnail", "")
        details = (
            playlist.get("description")
            or playlist["title"]
        )

        if thumbnail:
            ET.SubElement(
                item,
                f"{{{MEDIA_NS}}}thumbnail",
                {"url": thumbnail}
            )

            description_html = (
                '<p><img src="'
                + escape(thumbnail, quote=True)
                + '" alt="'
                + escape(playlist["title"], quote=True)
                + '" /></p><p>'
                + escape(details)
                + "</p>"
            )

            ET.SubElement(
                item, "description"
            ).text = description_html

        else:
            ET.SubElement(
                item, "description"
            ).text = details

    ET.indent(rss, space="  ")

    FEED.parent.mkdir(
        parents=True, exist_ok=True
    )

    FEED.write_bytes(
        ET.tostring(
            rss,
            encoding="utf-8",
            xml_declaration=True
        )
    )

    STATE.parent.mkdir(
        parents=True, exist_ok=True
    )

    STATE.write_text(
        json.dumps(
            {
                "seen": sorted(seen),
                "entries": entries
            },
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    print(
        f"Found {len(fetched)} playlists; "
        f"added {added} new interviews; "
        f"RSS contains {len(entries)} entries"
    )


if __name__ == "__main__":
    main()
