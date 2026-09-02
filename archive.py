#!/usr/bin/env python3
"""How a thread is written down: the format the importers have to agree on.

A thread is one JSON file under `threads/<year>/`, named for the moment it
opened, holding its dates the way Google Groups displayed them. Four tools
write these files -- `import_mbox.py` from a Usenet mbox, `import_forum.py`
from the V:EKN forum, `sync_forum.py` through it, and `import_bgg.py` from
BoardGameGeek -- and `merge_mbox.py` rewrites them. What they have to agree on
lives here, so that a thread reads the same whichever of them wrote it.

`build.py` deliberately does not import this. The site generator is one
self-contained file with nothing but the standard library behind it, so that
the archive can still be rendered by anyone with a Python interpreter; the
three date shapes it reads are three lines, and worth repeating to keep that.
"""

import datetime
import json
import pathlib
import re

#: The narrow no-break space before AM/PM is the one Google Groups wrote, and
#: keeping it means every date in the archive is written the same way.
NARROW = "\u202f"
#: The two shapes a date comes in. Google Groups and BoardGameGeek record the
#: second and the V:EKN forum does not, and neither is padded with a second
#: nobody wrote down.
DATE_FORMATS = ("%b %d, %Y, %I:%M:%S %p", "%b %d, %Y, %I:%M %p")


def displayed(when: datetime.datetime, *, seconds: bool = True) -> str:
    """Write a date the way the archive displays one.

    Built by hand rather than handed to `strftime`: `%-d` and `%-I` are not on
    every platform, and `%p` follows the locale, which a file format must not.
    """
    clock = f"{when.hour % 12 or 12}:{when:%M}"
    if seconds:
        clock += f":{when:%S}"
    marker = "AM" if when.hour < 12 else "PM"
    return f"{when:%b} {when.day}, {when.year}, {clock}{NARROW}{marker}"


def read(text: str) -> datetime.datetime:
    """Read back a date the archive displays, in whichever shape it was written."""
    stamp = text.replace(NARROW, " ").strip()
    for shape in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(stamp, shape)
        except ValueError:
            continue
    raise ValueError(f"unreadable date: {text!r}")


def started(thread: dict) -> datetime.datetime:
    """When a thread opened, read back off its first post.

    The file a thread lives in is named for that moment, so the name comes from
    the same string the thread carries rather than from a second reading of the
    source -- one date, written once.
    """
    return read(thread["Messages"][0]["Date"])


def dump(thread: dict) -> str:
    """Serialise a thread the way the ones already in the archive are written.

    The archive was first written by another tool, and rewriting a thread in a
    different style would show up as a diff on every line of every file
    touched, burying the one post that actually changed. Two spaces of indent,
    CRLF, upper case in the escapes, no trailing newline.
    """
    text = json.dumps(thread, indent=2)
    text = re.sub(r"\\u([0-9a-f]{4})", lambda m: "\\u" + m.group(1).upper(), text)
    return text.replace("\n", "\r\n")


def path_for(thread: dict, root: pathlib.Path) -> pathlib.Path:
    """Where the archive keeps a thread."""
    start = started(thread)
    return (
        root
        / "threads"
        / start.strftime("%Y")
        / f"{start.strftime('%Y%m%d_%H%M')}_{thread['ThreadId']}.json"
    )


def write_thread(thread: dict, root: pathlib.Path) -> pathlib.Path:
    """Put a thread where the archive keeps threads."""
    path = path_for(thread, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(thread), encoding="utf-8")
    return path


def threads(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """Every thread the archive holds, by the id its file is named for."""
    return {path.stem.split("_", 2)[-1]: path for path in root.glob("threads/*/*.json")}
