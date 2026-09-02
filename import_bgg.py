#!/usr/bin/env python3
"""Import a VTES thread from BoardGameGeek into the archive.

Four rulings cite BoardGameGeek, and there is a reason they do: as Usenet wound
down, L. Scott Johnson took to answering rules questions in the game's Rules
forum there, and his last ruling as rules director was written on that site
rather than on the newsgroup or the V:EKN forum. A month later the seat passed
to Pascal Bertrand and the rulings moved to the forum for good, so nothing here
from after 11 June 2011 is a director's answer -- he still turns up to answer a
question, most recently in December 2025, and those threads are kept for
completeness rather than for citing.

    python3 import_bgg.py $(cat bgg-threads.txt)

Threads are named, not discovered: BoardGameGeek will not list a forum's
threads to anyone without an API key, so `bgg-threads.txt` holds the 57 numbers
an advanced search on the site turned up. Running a number again rewrites the
thread with whatever has been added to it since.

BoardGameGeek's public XML API wants a key now and its HTML sits behind a bot
check, but the JSON its own pages read is open: `/api/threads/<id>` for the
subject and `/api/articles?threadid=<id>` for the posts, both on
api.geekdo.com. A thread becomes `bgg-609699` and every post keeps the article
number BoardGameGeek gave it, because that is what the rulings cite -- and they
cite it in the path, `/thread/609699/article/6142361`, not only in a fragment.
"""

import argparse
import datetime
import html
import json
import pathlib
import re
import sys
import time
from xml.etree import ElementTree

import archive
import import_forum

GROUP = "boardgamegeek.com/vtes"
THREAD = "https://api.geekdo.com/api/threads/{topic}"
ARTICLES = "https://api.geekdo.com/api/articles?threadid={topic}&pageid={page}"
USER = "https://api.geekdo.com/api/users/{who}"
#: A page holds 25 posts and the longest thread here runs to 18, but a busier
#: one would paginate and a half-read thread is worse than none.
MAX_PAGES = 40
RE_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
RE_TAG = re.compile(r"<[^>]+>")
RE_EMOTICON = re.compile(r'<img[^>]*class="emoticon"[^>]*>', re.IGNORECASE)
RE_SRC = re.compile(r'src="[^"]*/([^"/]+)"')
RE_ALT = re.compile(r'alt="([^"]*)"')
RE_LINK = re.compile(
    r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)
#: What a typed smiley turns back into. BoardGameGeek renders one as an image
#: and drops the characters, so they are put back -- every one of these was
#: read off the plain-text copy of a post that contains it, not guessed.
EMOTICON = {
    "smile.gif": ":)",
    "wink.gif": ";)",
    "biggrin.gif": ":D",
    "sad.gif": ":(",
    "tongue.gif": ":p",
    "blush.gif": ":blush:",
    "cry.gif": ":cry:",
    "soblue.gif": ":soblue:",
    "thumbs-up.gif": ":thumbsup:",
    "thumbs-down.gif": ":thumbsdown:",
    "geekgold.gif": ":gg:",
}


def plain(fragment: str) -> str:
    """The text of one of BoardGameGeek's HTML fragments.

    A post's markup arrives escaped inside the XML, so what comes out of the
    parser is HTML source: line breaks are tags, and anything the poster typed
    as a link is a tag wrapped around the URL itself.
    """

    def smiley(match: re.Match) -> str:
        source = RE_SRC.search(match.group(0))
        alt = RE_ALT.search(match.group(0))
        name = source.group(1) if source else ""
        return EMOTICON.get(name, alt.group(1) if alt else "")

    def link(match: re.Match) -> str:
        href, shown = match.group(1), RE_TAG.sub("", match.group(2)).strip()
        # Most links are the URL itself, written out; where a poster wrote
        # words over one instead, the address has to go somewhere or it is
        # lost, and a citation is exactly the kind of link they did that to.
        return shown if shown == href else f"{shown} ({href})"

    text = RE_EMOTICON.sub(smiley, fragment)
    text = RE_LINK.sub(link, text)
    text = RE_BREAK.sub("\n", text)
    return html.unescape(RE_TAG.sub("", text))


def flow(node: ElementTree.Element, delay: float) -> list[tuple[int, str]]:
    """Everything under an element, each piece at the depth it is quoted at.

    BoardGameGeek keeps a post as a run of prose and quotes, and a quote holds
    prose and quotes in turn, so this recurses and the depth accumulates on the
    way back out -- the same `> ` nesting the newsgroup wrote by hand. The
    pieces are not lines: a mention of another member interrupts a sentence
    without ending it, so what comes back is joined before it is broken up.
    """
    pieces: list[tuple[int, str]] = []
    for child in node:
        if child.tag == "safehtml":
            pieces.append((0, plain(child.text or "")))
        elif child.tag == "user":
            # A member named mid-sentence is an element of its own, holding a
            # number rather than the name that was displayed in its place.
            pieces.append((0, named(int(child.attrib["userid"]), delay)))
        elif child.tag == "quote":
            author = child.find("qauthor")
            quoted = child.find("qbody")
            inner = flow(quoted, delay) if quoted is not None else []
            name = (
                plain("".join(part.text or "" for part in author)).strip()
                if author is not None
                else ""
            )
            if name:
                inner.insert(0, (0, f"{name} wrote:\n"))
            # A quote is a block: it starts a line and it ends one.
            pieces.append((0, "\n"))
            pieces.extend((depth + 1, text) for depth, text in inner)
            pieces.append((0, "\n"))
    return pieces


def body(raw: str, delay: float = 0.0) -> str:
    """A post, written out the way the archive writes posts."""
    return import_forum.written(flow(ElementTree.fromstring(raw), delay))


def read(url: str) -> dict:
    """One of BoardGameGeek's JSON replies."""
    return json.loads(import_forum.fetch(url))


#: A post names its author by number, and the same handful of people answer
#: across every thread, so a name is asked for once and then remembered.
NAMES: dict[int, str] = {}

#: A handle is all BoardGameGeek knows a poster by, and for all but one of them
#: that is all the archive needs. These threads are here for the Rules Director's
#: answers, and a reader who came from a ruling citing one should not have to
#: know which handle was his, so his is annotated with the name the newsgroup
#: knew him by.
ANNOTATED = {"Rulemonger": "L. Scott Johnson (Rulemonger)"}


def named(who: int, delay: float) -> str:
    """What a poster is called, asked for at most once."""
    if who not in NAMES:
        time.sleep(delay)
        handle = read(USER.format(who=who))["username"]
        NAMES[who] = ANNOTATED.get(handle, handle)
    return NAMES[who]


def articles(topic: str, delay: float) -> list[dict]:
    """Every post in a thread, in the order it was written."""
    posts: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        answer = read(ARTICLES.format(topic=topic, page=page))
        posts.extend(answer["articles"])
        if len(posts) >= answer["total"] or not answer["articles"]:
            break
        time.sleep(delay)
    return posts


def build_thread(topic: str, delay: float) -> dict:
    """One archive thread out of a BoardGameGeek thread."""
    about = read(THREAD.format(topic=topic))
    time.sleep(delay)
    posts = articles(topic, delay)
    if not posts:
        raise ValueError(f"no posts found in thread {topic}")
    messages = []
    for post in posts:
        when = datetime.datetime.fromisoformat(post["postdate"])
        messages.append(
            {
                "Author": named(post["author"], delay),
                "Date": archive.displayed(when.replace(tzinfo=None)),
                "Body": body(post["bodyXml"], delay),
                "Id": post["id"],
            }
        )
    return {
        "ThreadId": f"bgg-{topic}",
        "Group": GROUP,
        "Url": about["canonical_link"],
        "Title": about["subject"].strip(),
        "Messages": messages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topics", nargs="+", help="BoardGameGeek thread numbers")
    parser.add_argument(
        "--out", type=pathlib.Path, default=pathlib.Path(__file__).parent
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, help="seconds between reads"
    )
    args = parser.parse_args()

    for index, topic in enumerate(args.topics):
        if index:
            time.sleep(args.delay)
        try:
            thread = build_thread(topic, args.delay)
        except (ValueError, KeyError) as problem:
            print(f"{topic}: {problem}", file=sys.stderr)
            return 1
        path = archive.write_thread(thread, args.out)
        print(f"{path}  {len(thread['Messages'])} posts  {thread['Title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
