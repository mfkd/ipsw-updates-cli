# IPSW Updates CLI

`ipsw_updates.py` is a tiny Python script for viewing the latest firmware releases published on [ipsw.me](https://ipsw.me/timeline.rss) directly in your terminal. It fetches the RSS feed, formats it into a column-aligned table, and can optionally remember what you have already seen.

## Features

- Fetches the ipsw.me timeline RSS feed with no external dependencies (stdlib only).
- Width-aware table output with optional link column.
- Filter entries by substring via `--contains`.
- Remember the last-seen GUID (`--remember`) and only show newer items on future runs (`--only-new`).
- Configurable feed URL, timeout, state file location, and max results.

## Requirements

- Python 3.8+ (only uses the standard library).

## Usage

Basic usage:

```bash
python3 ipsw_updates.py                      # show latest 15 entries
python3 ipsw_updates.py --limit 5            # show only 5 entries
python3 ipsw_updates.py --show-links         # include the link column
python3 ipsw_updates.py --contains "iOS 17"  # filter to entries containing "iOS 17" in the title
```

Tracking unread items:

```bash
python3 ipsw_updates.py --only-new --remember
```

The command above keeps a JSON file at `~/.ipsw_timeline_state.json` (override with `--state-file`) storing the latest GUID so future runs with `--only-new` only display new releases.

## Notes

- The script defaults to a 10 second network timeout. Adjust via `--timeout` if your connection is slow.
- Set `--feed-url` to point at a different RSS feed if you want to reuse the script elsewhere.
- Because it is stdlib-only, there is no virtual environment or `requirements.txt`. Add a venv later if you introduce third-party libraries (e.g., `rich` or `feedparser`).

## Development

Run the formatter manually by editing the script; no additional tooling is required. To make the script directly executable, you can `chmod +x ipsw_updates.py` and run it via `./ipsw_updates.py`.
