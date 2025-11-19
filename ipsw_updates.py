#!/usr/bin/env python3
"""Small CLI to show ipsw.me timeline RSS updates in the terminal."""
from __future__ import annotations

import argparse
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
USER_AGENT = "ipsw-timeline-cli/1.0 (+https://ipsw.me)"
ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_BOLD_OFF = "\033[22m"
ANSI_DIM = "\033[2m"
STRIPE_CHAR = "▌"


@dataclass
class TableLayout:
    date_col: int
    stripe_col: int
    platform_col: int
    version_col: int
    device_col: int
    gap: int

    @property
    def column_count(self) -> int:
        return 5

    @property
    def total_gap(self) -> int:
        if self.column_count <= 1:
            return 0
        return self.gap * (self.column_count - 1)

    def join(self, parts: List[str]) -> str:
        return (" " * self.gap).join(parts)

PLATFORM_COLOR_MAP = {
    "ios": "\033[31m",  # red
    "ipados": "\033[36m",  # cyan
    "macos": "\033[32m",  # green
    "other": "\033[35m",  # magenta fallback for everything else
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
) -> List[FeedEntry]:
    contains_lower = contains.lower() if contains else None
    filtered: List[FeedEntry] = []
    for entry in entries:
        if contains_lower and contains_lower not in entry.title.lower():
            continue
        filtered.append(entry)
    return filtered


@dataclass
class EntryMetadata:
    platform_label: str
    platform_key: str
    version: str
    build: str
    device: str
    description_summary: str
    is_prerelease: bool
    release_channel: str


def normalize_platform_key(label: str) -> str:
    key = label.lower()
    return PLATFORM_ALIASES.get(key, key if key in PLATFORM_COLOR_MAP else "other")


def detect_release_channel(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(rc|release candidate)\b", lowered):
        return "rc"
    if "beta" in lowered:
        return "beta"
    return "stable"


RELEASE_SUFFIX_RE = re.compile(r"\s+released\s*$", re.IGNORECASE)
DESCRIPTION_SUMMARY_RE = re.compile(r"has been released\s+(?P<info>.+)$", re.IGNORECASE)


def _strip_release_suffix(text: str) -> str:
    return RELEASE_SUFFIX_RE.sub("", text.strip())


def _summarize_description(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    match = DESCRIPTION_SUMMARY_RE.search(cleaned)
    if match:
        summary = match.group("info").strip().rstrip(".")
        return re.sub(r"\s+", " ", summary)
    return cleaned


def extract_entry_metadata(entry: FeedEntry) -> EntryMetadata:
    raw_title = _strip_release_suffix(entry.title)
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
    channel = detect_release_channel(raw_title)
    is_prerelease = channel != "stable"
    description_summary = _summarize_description(entry.description)

    return EntryMetadata(
        platform_label=platform_label or "Unknown",
        platform_key=platform_key,
        version=version,
        build=build,
        device=device,
        description_summary=description_summary,
        is_prerelease=is_prerelease,
        release_channel=channel,
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

    def platform_style(self, text: str, platform_key: str, *extra_codes: str) -> str:
        return self.style(text, self.platform_color(platform_key), *extra_codes)

    def stripe(self, platform_key: str, width: int) -> str:
        return self.platform_style(_pad(STRIPE_CHAR, width), platform_key)


NUMERIC_PATTERN = re.compile(r"\d[\w.\-]*")


def emphasize_numbers(text: str) -> str:
    result = []
    last = 0
    for match in NUMERIC_PATTERN.finditer(text):
        start, end = match.span()
        result.append(text[last:start])
        result.append(f"{ANSI_BOLD}{match.group(0)}{ANSI_BOLD_OFF}")
        last = end
    result.append(text[last:])
    return "".join(result)


def _compute_layout(term_width: int) -> TableLayout:
    date_col_width = 20
    stripe_col_width = 1
    platform_col_width = 12
    version_col_width = 24
    gap = 2
    column_count = 5
    total_gap = gap * (column_count - 1)
    device_min_width = 16
    consumed = date_col_width + stripe_col_width + platform_col_width + version_col_width
    device_col_width = max(device_min_width, term_width - (consumed + total_gap))
    return TableLayout(
        date_col=date_col_width,
        stripe_col=stripe_col_width,
        platform_col=platform_col_width,
        version_col=version_col_width,
        device_col=device_col_width,
        gap=gap,
    )


def _shorten(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def render(entries: List[FeedEntry], *, color_mode: str) -> str:
    if not entries:
        return "No entries to display."

    term_width = shutil.get_terminal_size((100, 20)).columns
    layout = _compute_layout(term_width)
    colorizer = Colorizer(mode=color_mode, stream=sys.stdout)

    lines = []
    header_parts = [
        _pad("Published", layout.date_col),
        _pad("│", layout.stripe_col),
        _pad("Platform", layout.platform_col),
        _pad("Version (Build)", layout.version_col),
        _pad("Device / Notes", layout.device_col),
    ]
    lines.append(layout.join(header_parts))
    header_width = sum(len(part) for part in header_parts) + layout.total_gap
    lines.append("-" * min(term_width, header_width))

    current_day = None
    for entry in entries:
        entry_day = entry.published_display.split(" ", 1)[0]
        if entry_day != current_day:
            current_day = entry_day
            lines.append(_day_divider(entry_day, term_width))

        meta = extract_entry_metadata(entry)
        stripe_cell = colorizer.stripe(meta.platform_key, layout.stripe_col)

        platform_cell = _pad(meta.platform_label or "?", layout.platform_col)
        platform_cell = colorizer.platform_style(platform_cell, meta.platform_key)

        version_text = meta.version or meta.platform_label or entry.title
        if meta.build:
            version_text = f"{version_text} ({meta.build})"
        version_display = _pad(_shorten(version_text, layout.version_col), layout.version_col)
        if colorizer.enabled:
            version_display = emphasize_numbers(version_display)
        extra = (ANSI_BOLD,) if meta.is_prerelease else tuple()
        version_cell = colorizer.platform_style(version_display, meta.platform_key, *extra)

        device_text = meta.device or meta.description_summary or meta.platform_label
        device_cell = _pad(_shorten(device_text, layout.device_col), layout.device_col)
        device_cell = colorizer.style(device_cell, ANSI_DIM)

        row = [
            _pad(entry.published_display, layout.date_col),
            stripe_cell,
            platform_cell,
            version_cell,
            device_cell,
        ]
        lines.append(layout.join(row))

    return "\n".join(lines)


def _day_divider(day_text: str, width: int) -> str:
    label = f" {day_text} "
    filler_width = max(0, width - len(label))
    return f"{label}{'-' * filler_width}"


def _pad(text: str, width: int) -> str:
    if len(text) >= width:
        return text[:width]
    return text + " " * (width - len(text))


def run_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Show ipsw.me timeline updates in your terminal")
    parser.add_argument("-f", "--feed-url", default=DEFAULT_FEED_URL, help="RSS feed to read")
    parser.add_argument("-l", "--limit", type=int, default=15, help="Maximum number of entries to show")
    parser.add_argument("-c", "--contains", help="Only show entries whose title includes this string (case-insensitive)")
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

    filtered = filter_entries(entries, contains=args.contains)
    if args.limit:
        filtered = filtered[: args.limit]

    print(render(filtered, color_mode=args.color))

    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
