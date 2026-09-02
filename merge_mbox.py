#!/usr/bin/env python3
"""Put back the posts Google's copy of the newsgroup lost.

The archive is built from Google Groups, which dropped messages here and there and
whole threads elsewhere. The Internet Archive keeps the same newsgroups as mbox
files, and those copies are more complete:

    curl -LO https://archive.org/download/usenet-rec/rec.games.trading-cards.jyhad.mbox.zip

The two copies line up exactly. Google's `X-Google-Thread` header carries the very
id its URLs are built on, written in hex where the URL writes it in base64url, so
`ffa097a0a4d22e7c` and `_6CXoKTSLnw` are one thread named twice. Within a thread the
posting times agree to the second, which is enough to tell which posts the archive
already holds and which it is missing.

    python3 merge_mbox.py ../usenet-mbox/rec.games.trading-cards.jyhad.mbox \\
        --group rec.games.trading-cards.jyhad --author 'LSJ|vtesrep@' --anchors anchors.json

By default it only reports. `--write` applies the merge.

Two rules make the result safe to cite. Merging is insert-only: a post the archive
already holds keeps its body and its position, so a citation pointing at it still
points at the same words. And because the anchors in the rulings database are
positional -- `#m3` is the fourth message -- inserting a post moves every anchor
after it, so `--anchors` writes out the map from old position to new for the
database to follow. Applying one without the other silently re-points citations.
"""

import argparse
import base64
import binascii
import collections
import datetime
import difflib
import email
import email.header
import email.policy
import email.utils
import json
import pathlib
import re
import sys
import zoneinfo

import archive

#: A crossposted message carries one of these per group it was posted to.
RE_THREAD = re.compile(rb"^X-Google-Thread: \w+,(\w+)", re.MULTILINE)
RE_HEADER = rb"^%s:[ \t]*(.*(?:\n[ \t].*)*)"
#: Google's own export sometimes replaced the Date header with a bare day.
RE_PLAIN_DATE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
#: The archive shows times the way Google Groups did, in the timezone of the
#: browser that scraped it. `derive_timezone` checks that against the mbox rather
#: than trusting it.
DISPLAY_TZ = zoneinfo.ZoneInfo("Europe/Paris")


def thread_ident(thread_id: str) -> str:
    """The mbox writes a thread id in hex, a Google URL in base64url.

    Both spell the same 64-bit number, so a thread the archive already holds under
    its Google name is recognised rather than imported a second time.
    """
    raw = binascii.unhexlify(thread_id.zfill(16))
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def header(raw: bytes, name: str) -> str:
    """One header, unfolded and decoded, without parsing the whole message.

    Usenet is full of messages no strict parser will touch, and a merge that skips
    them loses exactly the posts it is here to recover.
    """
    found = re.search(RE_HEADER % name.encode(), raw, re.MULTILINE | re.IGNORECASE)
    if not found:
        return ""
    folded = found.group(1).decode("utf-8", "replace").replace("\n", " ")
    try:
        return str(email.header.make_header(email.header.decode_header(folded)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return folded.strip()


def posted(raw: bytes) -> datetime.datetime | None:
    """When a post was made, in UTC.

    Most messages keep their original Date header. Where Google's export replaced it
    with a bare day, the arrival time it stamped is the better record.
    """
    date = header(raw, "Date")
    try:
        stamp = email.utils.parsedate_to_datetime(date)
        # A `-0000` offset means "UTC, and the sender's own zone is unknown", which
        # the parser reports as a date carrying no timezone at all. Reading that as
        # local time moves the post by however far this machine is from Greenwich.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=datetime.timezone.utc)
        return stamp.astimezone(datetime.timezone.utc)
    except (TypeError, ValueError):
        pass
    arrival = header(raw, "X-Google-ArrivalTime")
    if arrival:
        try:
            stamp = datetime.datetime.strptime(arrival[:19], "%Y-%m-%d %H:%M:%S")
            return stamp.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            pass
    plain = RE_PLAIN_DATE.match(date)
    if plain:
        return datetime.datetime(
            *map(int, plain.groups()), tzinfo=datetime.timezone.utc
        )
    return None


def author(raw: str) -> str:
    """`aahz@hal.COM (Tom Wylie)` and `"A B" <a@b>` both carry a display name."""
    name, address = email.utils.parseaddr(raw)
    parenthesised = re.search(r"\(([^)]+)\)", raw)
    return name or (parenthesised.group(1) if parenthesised else address) or raw


def decoded(payload: bytes, charset: str | None) -> str:
    """Text out of bytes, believing the post about its encoding where it says.

    Where it says nothing -- most of Usenet before about 2003 -- the bytes are
    tried as UTF-8 first, which fails on anything that is not UTF-8, and read as
    Latin-1 when it does. Decoding those bytes as UTF-8 anyway would turn the
    accent in a name into a replacement character and lose it for good, and a
    body that is noise cannot be compared with the archive's copy of the post.
    """
    if charset:
        try:
            return payload.decode(charset, "replace")
        except LookupError:
            pass
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.decode("latin-1")


def body(raw: bytes) -> str:
    """The post itself, decoded and stripped of its headers.

    Quoted-printable and base64 posts read as noise otherwise, and a body that is
    noise cannot be compared with the archive's copy of the same post.
    """
    try:
        message = email.message_from_bytes(raw, policy=email.policy.compat32)
        while message.is_multipart():
            message = message.get_payload(0)
        payload = message.get_payload(decode=True)
        if payload is None:
            raise ValueError
        return decoded(payload, message.get_content_charset()).strip("\n")
    except Exception:  # noqa: BLE001 - see above: anything at all, or lose the post
        # Headers end at the first blank line; a message with no body has none.
        split = raw.split(b"\n\n", 1)
        return decoded(split[1] if len(split) > 1 else b"", None).strip("\n")


def scan(path: pathlib.Path):
    """Every message in the mbox, as (offset, length, headers), streamed.

    The file runs to hundreds of megabytes, so it is read once and only the posts
    that turn out to be missing are read a second time for their bodies.
    """
    start, buffer = 0, []
    offset = 0
    with path.open("rb") as stream:
        for line in stream:
            if line.startswith(b"From ") and buffer:
                yield start, b"".join(buffer)
                start, buffer = offset, []
            buffer.append(line)
            offset += len(line)
    if buffer:
        yield start, b"".join(buffer)


def index(path: pathlib.Path, cache: pathlib.Path | None):
    """The mbox reduced to what the merge needs to decide, cached between runs."""
    if cache and cache.is_file():
        return json.loads(cache.read_text())
    rows, seen = [], set()
    for offset, raw in scan(path):
        message_id = header(raw, "Message-ID")
        # The same post reaches the mbox more than once, through different feeds.
        if message_id and message_id in seen:
            continue
        seen.add(message_id)
        when = posted(raw)
        rows.append(
            {
                "offset": offset,
                "length": len(raw),
                "id": message_id,
                "threads": sorted({t.decode() for t in RE_THREAD.findall(raw)}),
                "when": when.isoformat() if when else None,
                "author": author(header(raw, "From")),
                "subject": header(raw, "Subject"),
            }
        )
    if cache:
        cache.write_text(json.dumps(rows))
    return rows


def instants(text: str) -> list[datetime.datetime]:
    """Read back a date the archive displays, as the UTC instants it can mean.

    The hour the clocks go back happens twice, and the archive writes it down only
    once, so a date landing in it is genuinely two instants and has to be offered
    to the matcher as both.
    """
    try:
        naive = datetime.datetime.strptime(
            # Google writes the space before AM/PM as a narrow no-break space.
            text.replace("\u202f", " ").strip(),
            "%b %d, %Y, %I:%M:%S %p",
        )
    except ValueError:
        return []
    stamps = [
        naive.replace(tzinfo=DISPLAY_TZ, fold=fold).astimezone(datetime.timezone.utc)
        for fold in (0, 1)
    ]
    return stamps[:1] if stamps[0] == stamps[1] else stamps


def signature(text: str) -> str:
    """A post reduced to its own words, for recognising a second copy of it.

    Quoted lines go: the archive elides the text a post was replying to where the
    mbox keeps it in full, so two copies of one post read very differently until
    what it quoted is taken out of both.
    """
    kept = [
        line
        for line in text.splitlines()
        if not line.lstrip().startswith((">", ":", "|"))
    ]
    return " ".join(re.sub(r"[^a-z0-9]+", " ", "\n".join(kept).lower()).split())


def derive_timezone(threads, mbox_by_thread) -> collections.Counter:
    """Check the archive's displayed times really are Europe/Paris.

    Two copies of one post agree on the minute and the second whatever timezone
    each is written in, so the difference between them is the offset, and the
    offsets should all be whole hours of one zone. If they are not, matching on
    time is the wrong tool and the run should stop rather than guess.
    """
    offsets = collections.Counter()
    for ident, path in threads.items():
        rows = mbox_by_thread.get(ident)
        if not rows:
            continue
        stamps = collections.defaultdict(list)
        for row in rows:
            if row["when"]:
                stamp = datetime.datetime.fromisoformat(row["when"])
                stamps[(stamp.minute, stamp.second)].append(stamp)
        data = json.loads(path.read_text(encoding="utf-8"))
        for message in data["Messages"]:
            when = next(iter(instants(message["Date"])), None)
            if when is None:
                continue
            for other in stamps.get((when.minute, when.second), []):
                offsets[round((when - other).total_seconds())] += 1
                break
    return offsets


def same_author(one: str, other: str) -> bool:
    """Whether two spellings of a poster are the same poster.

    Google hid part of every address it published -- `legb...@mailandnews.com` for
    `legbiter@mailandnews.com` -- so the archive and the mbox agree about who wrote
    a post only at the two ends of the name.
    """
    one, other = one.lower().strip(), other.lower().strip()
    if one.startswith(other) or other.startswith(one):
        return True
    for shown, full in ((one, other), (other, one)):
        head, hidden, tail = shown.partition("...")
        if hidden and full.startswith(head) and full.endswith(tail):
            return True
    return False


def align(messages: list[dict], rows: list[dict]) -> dict[int, int]:
    """Which mbox message each archived message is, matched on posting time.

    Both copies stamp a post to the second, so the time is an identifier. Where a
    thread holds two posts in the same second the author breaks the tie, and a post
    whose date the mbox lost matches nothing and is simply left alone.
    """
    pool = collections.defaultdict(list)
    for position, row in enumerate(rows):
        if row["when"]:
            pool[datetime.datetime.fromisoformat(row["when"])].append(position)
    matched, taken = {}, set()
    for index_, message in enumerate(messages):
        candidates = [
            p for when in instants(message["Date"]) for p in pool.get(when, [])
        ]
        candidates = [p for p in candidates if p not in taken]
        if not candidates:
            continue
        best = next(
            (
                p
                for p in candidates
                if same_author(message["Author"], rows[p]["author"])
            ),
            candidates[0],
        )
        matched[index_] = best
        taken.add(best)
    return matched


#: How alike two posts must read before they are taken for one post twice over.
#: The threshold is high on purpose: short replies -- "Correct.", "Yes." -- resemble
#: each other closely without being the same post, and wrongly dropping a real post
#: costs more than wrongly keeping a duplicate out.
SAME_POST = 0.98


def already_present(messages, rows, matched, bodies) -> set[int]:
    """Which unmatched mbox posts the archive holds anyway, under another time.

    The two copies mostly agree on when a post was made, but not always: Google
    sometimes shows a time minutes away from the one in the headers. Matching on
    time alone would then read one post as two and file the second as a recovery,
    which moves every anchor below it for nothing. The words settle it.
    """
    seen = [(signature(m["Body"]), m["Author"]) for m in messages]
    duplicates = set()
    for position in set(bodies) - set(matched.values()):
        mine = signature(bodies[position])
        if not mine:
            continue
        name = rows[position]["author"]
        for other, who in seen:
            if not same_author(who, name):
                continue
            ratio = difflib.SequenceMatcher(None, mine[:600], other[:600]).ratio()
            if ratio >= SAME_POST:
                duplicates.add(position)
                break
    return duplicates


def merge(messages: list[dict], rows: list[dict], bodies: dict[int, str]):
    """The thread with its missing posts put back, and the map of moved anchors.

    Insert-only: the posts the archive already holds keep their order and their
    bodies, whatever the mbox says, because those are the words the rulings were
    read from. A recovered post slots in by time between the two it belongs
    between, and every anchor after it moves down by one.
    """
    matched = align(messages, rows)
    placed = sorted(matched.items(), key=lambda pair: pair[0])
    # Where a recovered post belongs is decided by the archived posts around it:
    # its time against theirs, so an mbox ordering quirk cannot reorder the thread.
    fences = [(rows[position]["when"], index_) for index_, position in placed]
    used = set(matched.values()) | already_present(messages, rows, matched, bodies)
    additions = collections.defaultdict(list)
    for position, row in enumerate(rows):
        if position in used or not row["when"]:
            continue
        after = len(messages)
        for when, index_ in fences:
            if when and row["when"] < when:
                after = index_
                break
        additions[after].append(
            {
                "Author": row["author"],
                "Date": archive.displayed(
                    datetime.datetime.fromisoformat(row["when"]).astimezone(DISPLAY_TZ)
                ),
                "Body": bodies.get(position, ""),
                "Recovered": True,
            }
        )
    merged, anchors, shift = [], {}, 0
    for index_ in range(len(messages) + 1):
        for extra in additions.get(index_, []):
            merged.append(extra)
            shift += 1
        if index_ < len(messages):
            anchors[index_] = index_ + shift
            merged.append(messages[index_])
    return merged, anchors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mbox", type=pathlib.Path)
    parser.add_argument("--group", required=True)
    parser.add_argument(
        "--author", help="only import absent threads in which someone matching posted"
    )
    parser.add_argument("--anchors", type=pathlib.Path, help="write the moved anchors")
    parser.add_argument("--cache", type=pathlib.Path, help="reuse the scan of the mbox")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    root = pathlib.Path(__file__).parent
    rows = index(args.mbox, args.cache)
    print(f"{len(rows)} messages in {args.mbox.name}")

    by_thread = collections.defaultdict(list)
    for row in rows:
        for thread_id in row["threads"]:
            try:
                by_thread[thread_ident(thread_id)].append(row)
            except (binascii.Error, ValueError):
                continue
    for thread in by_thread.values():
        thread.sort(key=lambda row: (row["when"] or "", row["id"]))
    print(f"{len(by_thread)} threads in the mbox")

    threads = archive.threads(root)
    known = set(threads) & set(by_thread)
    print(
        f"{len(known)} of them already in the archive, {len(by_thread) - len(known)} not"
    )

    offsets = derive_timezone({k: threads[k] for k in known}, by_thread)
    total = sum(offsets.values())
    off = [o for o, n in offsets.items() if n > total * 0.001]
    print(f"{total} posts matched on time, offsets {sorted(offsets.most_common()[:4])}")
    if not total or any(o % 3600 for o in off):
        print("the archive's times do not line up with the mbox's; not merging")
        return 1

    poster = re.compile(args.author, re.IGNORECASE) if args.author else None
    return apply_(args, root, threads, by_thread, known, poster)


def read_bodies(path: pathlib.Path, wanted: dict) -> dict:
    """Fetch the bodies of the posts being recovered, by their place in the file."""
    out = {}
    with path.open("rb") as stream:
        for key, row in sorted(wanted.items(), key=lambda kv: kv[1]["offset"]):
            stream.seek(row["offset"])
            out[key] = body(stream.read(row["length"]))
    return out


def apply_(args, root, threads, by_thread, known, poster):
    added, anchors_out = [], {}
    wanted = {}
    for ident in sorted(known):
        data = json.loads(threads[ident].read_text(encoding="utf-8"))
        matched = align(data["Messages"], by_thread[ident])
        for position, row in enumerate(by_thread[ident]):
            if position not in set(matched.values()) and row["when"]:
                wanted[(ident, position)] = row
    for ident in sorted(set(by_thread) - known):
        rows = by_thread[ident]
        if poster and not any(poster.search(row["author"]) for row in rows):
            continue
        added.append(ident)
        for position, row in enumerate(rows):
            if row["when"]:
                wanted[(ident, position)] = row

    print(f"{len(wanted)} posts to read back from the mbox")
    bodies = read_bodies(args.mbox, wanted)

    moved = written = recovered = renamed = 0
    for ident in sorted(known):
        path = threads[ident]
        data = json.loads(path.read_text(encoding="utf-8"))
        thread_bodies = {p: b for (i, p), b in bodies.items() if i == ident}
        merged, anchors = merge(data["Messages"], by_thread[ident], thread_bodies)
        if len(merged) == len(data["Messages"]):
            continue
        for old, new in anchors.items():
            assert merged[new] is data["Messages"][old], f"{ident} moved a message"
        recovered += len(merged) - len(data["Messages"])
        written += 1
        moved += sum(1 for old, new in anchors.items() if old != new)
        anchors_out[ident] = {str(o): n for o, n in anchors.items() if o != n}
        data["Messages"] = merged
        # A recovered post can be older than the one that opened the thread
        # until now, and a thread's file is named for the moment it opened.
        # Leaving the name alone would have it say a time no post in it carries.
        wants = archive.path_for(data, root)
        if wants != path:
            renamed += 1
            print(f"  {path.name} -> {wants.name}")
        if args.write:
            archive.write_thread(data, root)
            if wants != path:
                path.unlink()
    print(
        f"{written} threads grew by {recovered} posts, moving {moved} anchors"
        + (f" and renaming {renamed} files" if renamed else "")
    )

    imported = 0
    for ident in added:
        rows = by_thread[ident]
        rows = [r for r in rows if r["when"]]
        if not rows:
            continue
        title = re.sub(
            r"^(Re|Fwd):\s*", "", rows[0]["subject"], flags=re.IGNORECASE
        ).strip()
        thread = {
            "ThreadId": ident,
            "Group": args.group,
            # The thread keeps the identifier Google gave it, so a surviving link
            # still resolves here, but Google no longer holds the thread itself.
            "Url": "https://archive.org/details/usenet-rec",
            "Title": title or "(no subject)",
            "Messages": [
                {
                    "Author": row["author"],
                    "Date": archive.displayed(
                        datetime.datetime.fromisoformat(row["when"]).astimezone(
                            DISPLAY_TZ
                        )
                    ),
                    "Body": bodies.get((ident, position), ""),
                }
                for position, row in enumerate(rows)
            ],
        }
        imported += 1
        if args.write:
            archive.write_thread(thread, root)
    print(f"{imported} threads imported whole")

    if args.anchors:
        args.anchors.write_text(json.dumps(anchors_out, indent=1, sort_keys=True))
        print(f"anchors moved in {len(anchors_out)} threads -> {args.anchors}")
    if not args.write:
        print("nothing written, pass --write to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
