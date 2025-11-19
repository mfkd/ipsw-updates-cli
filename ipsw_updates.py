#!/usr/bin/env python3
"""Small CLI to show ipsw.me timeline RSS updates in the terminal."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

DEFAULT_FEED_URL = "https://ipsw.me/timeline.rss"
DEFAULT_STATE_FILE = os.path.expanduser("~/.ipsw_timeline_state.json")
USER_AGENT = "ipsw-timeline-cli/1.0 (+https://ipsw.me)"
ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"

PLATFORM_COLOR_MAP = {
    "ios": "\033[33m",  # yellow
    "ipados": "\033[34m",  # blue
    "macos": "\033[32m",  # green
    "watchos": "\033[35m",  # magenta
    "tvos": "\033[36m",  # cyan
    "visionos": "\033[97m",  # bright white
    "other": "\033[90m",  # gray
}

PLATFORM_ALIASES = {
    "ios": "ios",
    "iphone": "ios",
    "ipados": "ipados",
    "ipad": "ipados",
    "macos": "macos",
    "mac": "macos",
    "watchos": "watchos",
    "watch": "watchos",
    "tvos": "tvos",
    "audioos": "tvos",
    "homepod": "tvos",
    "appletv": "tvos",
    "visionos": "visionos",
    "vision": "visionos",
}


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


@dataclass
class EntryMetadata:
    platform_label: str
    platform_key: str
    version: str
    build: str
    device: str
    is_prerelease: bool


def normalize_platform_key(label: str) -> str:
    key = label.lower()
    return PLATFORM_ALIASES.get(key, key if key in PLATFORM_COLOR_MAP else "other")


def extract_entry_metadata(title: str) -> EntryMetadata:
    raw_title = title.strip()
    main_part, _, device_part = raw_title.partition(" for ")
    device = device_part.strip()

    build = ""
    match = re.search(r"\(([^)]+)\)\s*$", main_part)
    if match:
        build = match.group(1).strip()
        main_part = main_part[: match.start()].strip()

    tokens = main_part.split(" ", 1)
    platform_label = tokens[0].strip() if tokens else main_part
    version = tokens[1].strip() if len(tokens) > 1 else ""
    platform_key = normalize_platform_key(platform_label)
    is_prerelease = bool(re.search(r"\b(beta|rc|release candidate)\b", raw_title, flags=re.IGNORECASE))

    return EntryMetadata(
        platform_label=platform_label or "Unknown",
        platform_key=platform_key,
        version=version,
        build=build,
        device=device,
        is_prerelease=is_prerelease,
    )


class Colorizer:
    def __init__(self, mode: str, stream) -> None:
        if mode == "always":
            self.enabled = True
        elif mode == "never":
            self.enabled = False
        else:
            self.enabled = hasattr(stream, "isatty") and stream.isatty()

    def style(self, text: str, *codes: str) -> str:
        code = "".join(c for c in codes if c)
        if not code or not self.enabled:
            return text
        return f"{code}{text}{ANSI_RESET}"

    def platform_color(self, platform_key: str) -> str:
        return PLATFORM_COLOR_MAP.get(platform_key.lower(), PLATFORM_COLOR_MAP["other"])


def render(entries: List[FeedEntry], *, show_links: bool, color_mode: str) -> str:
    if not entries:
        return "No entries to display."

    term_width = shutil.get_terminal_size((100, 20)).columns
    date_col_width = 20
    platform_col_width = 12
    version_col_width = 24
    link_col_width = 36 if show_links else 0
    gap = 2
    column_count = 4 + (1 if show_links else 0)
    total_gap = gap * (column_count - 1)
    device_col_width = max(
        16, term_width - (date_col_width + platform_col_width + version_col_width + link_col_width + total_gap)
    )

    def shorten(text: str, width: int) -> str:
        if len(text) <= width:
            return text
        return text[: width - 1] + "…"

    colorizer = Colorizer(mode=color_mode, stream=sys.stdout)

    lines = []
    header_parts = [
        _pad("Published", date_col_width),
        _pad("Platform", platform_col_width),
        _pad("Version (Build)", version_col_width),
        _pad("Device / Notes", device_col_width),
    ]
    if show_links:
        header_parts.append(_pad("Link", link_col_width))
    lines.append((" " * gap).join(header_parts))
    header_width = sum(len(part) for part in header_parts) + total_gap
    lines.append("-" * min(term_width, header_width))

    for entry in entries:
        meta = extract_entry_metadata(entry.title)
        platform_color = colorizer.platform_color(meta.platform_key)
        platform_cell = _pad(meta.platform_label or "?", platform_col_width)
        platform_cell = colorizer.style(platform_cell, platform_color)

        version_text = meta.version or meta.platform_label or entry.title
        if meta.build:
            version_text = f"{version_text} ({meta.build})"
        version_cell = _pad(shorten(version_text, version_col_width), version_col_width)
        version_codes = [platform_color]
        if meta.is_prerelease:
            version_codes.append(ANSI_BOLD)
        version_cell = colorizer.style(version_cell, *version_codes)

        device_text = meta.device or entry.description or meta.platform_label
        device_cell = _pad(shorten(device_text, device_col_width), device_col_width)
        device_cell = colorizer.style(device_cell, ANSI_DIM)

        row = [
            _pad(entry.published_display, date_col_width),
            platform_cell,
            version_cell,
            device_cell,
        ]
        if show_links:
            link_text = shorten(entry.link or entry.guid, link_col_width)
            row.append(_pad(link_text, link_col_width))
        lines.append((" " * gap).join(row))

    return "\n".join(lines)


def _pad(text: str, width: int) -> str:
    if len(text) >= width:
        return text[:width]
    return text + " " * (width - len(text))


def run_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Show ipsw.me timeline updates in your terminal")
    parser.add_argument("-f", "--feed-url", default=DEFAULT_FEED_URL, help="RSS feed to read")
    parser.add_argument("-l", "--limit", type=int, default=15, help="Maximum number of entries to show")
    parser.add_argument("-c", "--contains", help="Only show entries whose title includes this string (case-insensitive)")
    parser.add_argument(
        "-s",
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help="Where to store the last seen GUID (default: %(default)s)",
    )
    parser.add_argument(
        "-u",
        "--only-new",
        action="store_true",
        help="Only display entries newer than the last saved GUID",
    )
    parser.add_argument(
        "-r",
        "--remember",
        action="store_true",
        help="Persist the newest GUID after displaying entries",
    )
    parser.add_argument("-k", "--show-links", action="store_true", help="Show entry links in the table")
    parser.add_argument("-t", "--timeout", type=float, default=10.0, help="Network timeout in seconds")
    parser.add_argument(
        "-C",
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="Colorize output: auto (default), always, or never",
    )

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

    print(render(filtered, show_links=args.show_links, color_mode=args.color))

    if filtered and args.remember:
        try:
            save_state(args.state_file, filtered[0].guid)
        except OSError as exc:
            print(f"Warning: failed to save state to {args.state_file}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
