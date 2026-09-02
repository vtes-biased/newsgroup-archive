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
import json
import pathlib
import re
import sys

import archive
import merge_mbox

# Reading an mbox is `merge_mbox.py`'s trade -- headers that fold across lines,
# subjects written in an encoded word, bodies in quoted-printable -- and Usenet
# is full of messages a stricter parser will not touch. Both tools read the same
# files, so they read them the same way.


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
    held = archive.threads(root)
    known = set(held)
    # The same thread can be known under a Google id and under the mbox's own, so
    # recognise it by its opening post as well as by its identifier.
    openings = set()
    for path in held.values():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["Messages"]:
            first = data["Messages"][0]
            openings.add((first["Author"], first["Date"], data["Title"]))
    subject = re.compile(args.match, re.I) if args.match else None
    poster = re.compile(args.author, re.I) if args.author else None

    threads: dict[str, list[dict]] = {}
    for _, raw in merge_mbox.scan(args.mbox):
        # A crossposted message carries one X-Google-Thread per group it went to.
        ids = [found.decode() for found in merge_mbox.RE_THREAD.findall(raw)]
        if args.thread and args.thread not in ids:
            continue
        when = merge_mbox.posted(raw)
        # A post the archive cannot date cannot be put in order either, and a
        # thread is only worth citing if it reads in the order it was written.
        if when is None:
            said = merge_mbox.header(raw, "Message-ID") or "a post with no id"
            print(f"skipped {said}: no readable date", file=sys.stderr)
            continue
        for thread_id in [args.thread] if args.thread else ids:
            threads.setdefault(thread_id, []).append(
                {
                    "when": when,
                    "Author": merge_mbox.author(merge_mbox.header(raw, "From")),
                    "Subject": merge_mbox.header(raw, "Subject"),
                    "Message-ID": merge_mbox.header(raw, "Message-ID"),
                    "Body": merge_mbox.body(raw),
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
            archive.displayed(posts[0]["when"]),
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
                    "Date": archive.displayed(p["when"]),
                    "Body": p["Body"],
                }
                for p in posts
            ],
        }
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(archive.dump(thread), encoding="utf-8")
        else:
            archive.write_thread(thread, root)
        written += 1
        messages += len(posts)
    print(f"{written} threads, {messages} messages imported from {args.group}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
