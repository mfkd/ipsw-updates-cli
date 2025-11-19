# IPSW Updates CLI

`ipsw_updates.py` prints the latest firmware releases from [ipsw.me](https://ipsw.me/timeline.rss) in a neat table. It uses only the Python standard library.

## Requirements

- Python 3.8 or newer.

## Usage

```bash
python3 ipsw_updates.py            # show newest 15 entries
python3 ipsw_updates.py -l 5       # limit results
python3 ipsw_updates.py -c "iOS"   # filter titles
python3 ipsw_updates.py -k         # include link column
python3 ipsw_updates.py --only-new --remember  # track unread items
```

Entries are colorized for quick scanning (iOS red, macOS green, iPadOS cyan, everything else magenta). Colors auto-disable when stdout is not a TTY; override with `-C auto|always|never`.

The CLI remembers the last seen GUID when `--remember` is set, storing it in `~/.ipsw_timeline_state.json` by default (override via `--state-file`).
