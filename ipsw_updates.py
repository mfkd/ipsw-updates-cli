#!/usr/bin/env python3
"""Small CLI to show ipsw.me timeline RSS updates in the terminal."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Optional
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

DEFAULT_FEED_URL = "https://ipsw.me/timeline.rss"
DEFAULT_STATE_FILE = os.path.expanduser("~/.ipsw_timeline_state.json")
USER_AGENT = "ipsw-timeline-cli/1.0 (+https://ipsw.me)"


def fetch_feed(url: str, timeout: float) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


@dataclass
class FeedEntry:
    title: str
    link: str
    published: datetime
    published_raw: str
    guid: str
    description: str

    @property
    def published_display(self) -> str:
        dt = self.published.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")


def parse_feed(content: bytes) -> List[FeedEntry]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed to parse RSS: {exc}") from exc

    channel = root.find("channel")
    if channel is None:
        raise RuntimeError("RSS feed missing <channel>")

    entries: List[FeedEntry] = []
    for item in channel.findall("item"):
        title = _text(item, "title", default="(untitled)")
        link = _text(item, "link", default="")
        guid = _text(item, "guid", default=link or title)
        description = _text(item, "description", default="")
        pub_raw = _text(item, "pubDate", default="")
        published = _parse_pub_date(pub_raw)
        entries.append(
            FeedEntry(
                title=title.strip(),
                link=link.strip(),
                published=published,
                published_raw=pub_raw.strip(),
                guid=guid.strip(),
                description=description.strip(),
            )
        )

    # Feed is newest first already, but sort defensively by published date desc.
    entries.sort(key=lambda e: e.published, reverse=True)
    return entries


def _text(parent: ET.Element, tag: str, default: str = "") -> str:
    elem = parent.find(tag)
    return elem.text if elem is not None and elem.text is not None else default


def _parse_pub_date(raw: str) -> datetime:
    if not raw:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=timezone.utc)


def filter_entries(
    entries: Iterable[FeedEntry],
    *,
    contains: Optional[str] = None,
    newer_than_guid: Optional[str] = None,
) -> List[FeedEntry]:
    contains_lower = contains.lower() if contains else None
    filtered: List[FeedEntry] = []
    for entry in entries:
        if contains_lower and contains_lower not in entry.title.lower():
            continue
        if newer_than_guid and entry.guid == newer_than_guid:
            break
        filtered.append(entry)
    return filtered


def load_state(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            last_guid = data.get("last_guid")
            if last_guid:
                return str(last_guid)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    return None


def save_state(path: str, latest_guid: str) -> None:
    data = {"last_guid": latest_guid, "updated_at": datetime.now(timezone.utc).isoformat()}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def render(entries: List[FeedEntry], *, show_links: bool) -> str:
    if not entries:
        return "No entries to display."

    term_width = shutil.get_terminal_size((100, 20)).columns
    date_col_width = 20
    link_col_width = 36 if show_links else 0
    title_col_width = max(20, term_width - date_col_width - 4 - link_col_width)

    def shorten(text: str, width: int) -> str:
        if len(text) <= width:
            return text
        return text[: width - 1] + "…"

    lines = []
    header_parts = [
        _pad("Published", date_col_width),
        _pad("Title", title_col_width),
    ]
    if show_links:
        header_parts.append(_pad("Link", link_col_width))
    lines.append("  ".join(header_parts))
    lines.append("-" * term_width)

    for entry in entries:
        row = [
            _pad(entry.published_display, date_col_width),
            _pad(shorten(entry.title, title_col_width), title_col_width),
        ]
        if show_links:
            row.append(_pad(shorten(entry.link or entry.guid, link_col_width), link_col_width))
        lines.append("  ".join(row))

    return "\n".join(lines)


def _pad(text: str, width: int) -> str:
    if len(text) >= width:
        return text[:width]
    return text + " " * (width - len(text))


def run_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Show ipsw.me timeline updates in your terminal")
    parser.add_argument("--feed-url", default=DEFAULT_FEED_URL, help="RSS feed to read")
    parser.add_argument("--limit", type=int, default=15, help="Maximum number of entries to show")
    parser.add_argument("--contains", help="Only show entries whose title includes this string (case-insensitive)")
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help="Where to store the last seen GUID (default: %(default)s)",
    )
    parser.add_argument(
        "--only-new",
        action="store_true",
        help="Only display entries newer than the last saved GUID",
    )
    parser.add_argument(
        "--remember",
        action="store_true",
        help="Persist the newest GUID after displaying entries",
    )
    parser.add_argument("--show-links", action="store_true", help="Show entry links in the table")
    parser.add_argument("--timeout", type=float, default=10.0, help="Network timeout in seconds")

    args = parser.parse_args(argv)

    try:
        raw_feed = fetch_feed(args.feed_url, args.timeout)
    except (URLError, HTTPError) as exc:
        print(f"Failed to download feed: {exc}", file=sys.stderr)
        return 1

    entries = parse_feed(raw_feed)

    last_guid: Optional[str] = None
    if args.only_new or args.remember:
        last_guid = load_state(args.state_file)

    filtered = filter_entries(entries, contains=args.contains, newer_than_guid=last_guid)
    if args.limit:
        filtered = filtered[: args.limit]

    print(render(filtered, show_links=args.show_links))

    if filtered and args.remember:
        try:
            save_state(args.state_file, filtered[0].guid)
        except OSError as exc:
            print(f"Warning: failed to save state to {args.state_file}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
