#!/usr/bin/env python3
"""Import a thread from a Usenet mbox into the archive.

The rulings database cites a handful of posts that predate
rec.games.trading-cards.jyhad: before the group existed, Jyhad was discussed in
rec.games.deckmaster, and that is where the rules team posted its first rulings
lists. Google's copy of those is gone, but the Internet Archive keeps whole
newsgroups as mbox files:

    curl -LO https://archive.org/download/usenet-rec/rec.games.deckmaster.mbox.zip

    python3 import_mbox.py rec.games.deckmaster.mbox \\
        --thread ffa097a0a4d22e7c --id _6CXoKTSLnw --group rec.games.deckmaster

`--thread` is the mbox's own X-Google-Thread id; `--id` is the identifier Google
Groups used in its URLs, which is what the archive's own URLs are built on and
can only be read off a surviving link. Times are normalised to UTC so that the
thread reads in order whatever timezone its posters were in.
"""

import argparse
import datetime
import email.utils
import json
import pathlib
import re
import sys

import merge_mbox

RE_THREAD = re.compile(r"^X-Google-Thread: \w+,(\w+)", re.M)


def header(message: str, name: str) -> str:
    found = re.search(rf"^{name}: (.*)", message, re.M)
    return found.group(1).strip() if found else ""


#: Google's own export sometimes replaced the Date header with a bare day.
RE_PLAIN_DATE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")


def posted(message: str) -> datetime.datetime:
    """When a post was made, in UTC.

    Most messages keep their original Date header. Where Google's export replaced
    it with a bare day, the arrival time it stamped is the better record.
    """
    raw = header(message, "Date")
    try:
        stamp = email.utils.parsedate_to_datetime(raw)
        # A `-0000` offset means "UTC, and the sender's own zone is unknown", which
        # the parser reports as a date carrying no timezone at all. Reading that as
        # local time moves the post by however far this machine is from Greenwich.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=datetime.timezone.utc)
        return stamp.astimezone(datetime.timezone.utc)
    except (TypeError, ValueError):
        pass
    arrival = header(message, "X-Google-ArrivalTime")
    if arrival:
        stamp = datetime.datetime.strptime(arrival[:19], "%Y-%m-%d %H:%M:%S")
        return stamp.replace(tzinfo=datetime.timezone.utc)
    plain = RE_PLAIN_DATE.match(raw)
    if plain:
        return datetime.datetime(
            *map(int, plain.groups()), tzinfo=datetime.timezone.utc
        )
    raise ValueError(f"unparsable date: {raw!r}")


def author(raw: str) -> str:
    """`aahz@hal.COM (Tom Wylie)` and `"A B" <a@b>` both carry a display name."""
    name, address = email.utils.parseaddr(raw)
    parenthesised = re.search(r"\(([^)]+)\)", raw)
    return name or (parenthesised.group(1) if parenthesised else address)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mbox", type=pathlib.Path)
    parser.add_argument("--thread", help="X-Google-Thread id of a single thread")
    parser.add_argument("--id", help="thread id used in Google URLs, if one is known")
    parser.add_argument(
        "--match", help="import every thread whose subject matches this regex"
    )
    parser.add_argument(
        "--author", help="...and in which someone matching this regex posted"
    )
    parser.add_argument("--group", required=True)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args()
    if not (args.thread or args.match):
        print("give --thread or --match", file=sys.stderr)
        return 1

    root = pathlib.Path(__file__).parent
    known = {path.stem.split("_", 2)[-1] for path in root.glob("threads/*/*.json")}
    # The same thread can be known under a Google id and under the mbox's own, so
    # recognise it by its opening post as well as by its identifier.
    openings = set()
    for path in root.glob("threads/*/*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["Messages"]:
            first = data["Messages"][0]
            openings.add((first["Author"], first["Date"], data["Title"]))
    subject = re.compile(args.match, re.I) if args.match else None
    poster = re.compile(args.author, re.I) if args.author else None

    threads: dict[str, list[dict]] = {}
    for raw in args.mbox.read_text(encoding="latin-1").split("\nFrom "):
        # A crossposted message carries one X-Google-Thread per group it went to.
        ids = RE_THREAD.findall(raw)
        if args.thread and args.thread not in ids:
            continue
        for thread_id in [args.thread] if args.thread else ids:
            threads.setdefault(thread_id, []).append(
                {
                    "when": posted(raw),
                    "Author": author(header(raw, "From")),
                    "Subject": header(raw, "Subject"),
                    "Message-ID": header(raw, "Message-ID"),
                    # Headers end at the first blank line; the rest is the post,
                    # which a message with no body at all simply does not have.
                    "Body": (raw.split("\n\n", 1) + [""])[1].strip("\n"),
                }
            )

    written = messages = 0
    for thread_id, posts in sorted(threads.items()):
        posts.sort(key=lambda m: m["when"])
        title = re.sub(r"^(Re|Fwd):\s*", "", posts[0]["Subject"], flags=re.I).strip()
        if subject and not subject.search(posts[0]["Subject"]):
            continue
        if poster and not any(poster.search(p["Author"]) for p in posts):
            continue
        # The thread keeps the identifier Google gave it when a surviving link
        # names one; otherwise the mbox's own thread id has to serve.
        ident = args.id or thread_id
        opening = (
            posts[0]["Author"],
            posts[0]["when"].strftime(merge_mbox.DISPLAY_FORMAT),
            title or "(no subject)",
        )
        if not args.id and (ident in known or opening in openings):
            continue
        thread = {
            "ThreadId": ident,
            "Group": args.group,
            "Url": (
                f"https://groups.google.com/g/{args.group}/c/{ident}"
                if args.id
                else "https://archive.org/details/usenet-rec"
            ),
            "Title": title or "(no subject)",
            "Messages": [
                {
                    "Author": p["Author"],
                    # The format the rest of the archive uses, as Google showed it.
                    "Date": p["when"].strftime(merge_mbox.DISPLAY_FORMAT),
                    "Body": p["Body"],
                }
                for p in posts
            ],
        }
        start = posts[0]["when"]
        out = args.out or (
            root
            / "threads"
            / start.strftime("%Y")
            / f"{start.strftime('%Y%m%d_%H%M')}_{ident}.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(merge_mbox.dump(thread), encoding="utf-8")
        written += 1
        messages += len(posts)
    print(f"{written} threads, {messages} messages imported from {args.group}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
