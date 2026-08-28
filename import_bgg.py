#!/usr/bin/env python3
"""Import a VTES thread from BoardGameGeek into the archive.

Four rulings cite BoardGameGeek: L. Scott Johnson answered questions in the
game's Rules forum there in 2011, alongside the newsgroup he was winding down
and the V:EKN forum that replaced it. Three topics, nine to eleven posts each --
the smallest of the archive's three sources, and a closed one, since nobody has
taken a rules question there since. So this is a one-shot import rather than a
sync: name the threads and it fetches them.

    python3 import_bgg.py 609699 648695 662413

BoardGameGeek's public XML API now wants a key and its HTML sits behind a bot
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

import import_forum

GROUP = "boardgamegeek.com/vtes"
THREAD = "https://api.geekdo.com/api/threads/{topic}"
ARTICLES = "https://api.geekdo.com/api/articles?threadid={topic}&pageid={page}"
#: A page holds 25 posts and no thread here fills two, but a long one would.
MAX_PAGES = 40
RE_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
RE_TAG = re.compile(r"<[^>]+>")
RE_EMOTICON = re.compile(r'<img[^>]*class="emoticon"[^>]*>', re.IGNORECASE)
RE_SRC = re.compile(r'src="[^"]*/([^"/]+)"')
#: What a typed smiley turns into on the way through BoardGameGeek's renderer.
#: One emoticon appears in the three threads; anything else falls back to its
#: alt text, and to nothing when that is empty, rather than to a guess.
EMOTICON = {"smile.gif": ":)"}


def plain(fragment: str) -> str:
    """The text of one of BoardGameGeek's HTML fragments.

    A post's markup arrives escaped inside the XML, so what comes out of the
    parser is HTML source: line breaks are tags, and anything the poster typed
    as a link is a tag wrapped around the URL itself.
    """

    def smiley(match: re.Match) -> str:
        source = RE_SRC.search(match.group(0))
        alt = re.search(r'alt="([^"]*)"', match.group(0))
        name = source.group(1) if source else ""
        return EMOTICON.get(name, alt.group(1) if alt else "")

    text = RE_EMOTICON.sub(smiley, fragment)
    text = RE_BREAK.sub("\n", text)
    return html.unescape(RE_TAG.sub("", text))


def flow(node: ElementTree.Element) -> list[tuple[int, str]]:
    """Every line under an element, each with the depth it is quoted at.

    BoardGameGeek keeps a post as a run of prose and quotes, and a quote holds
    prose and quotes in turn, so this recurses and the depth accumulates on the
    way back out -- the same `> ` nesting the newsgroup wrote by hand.
    """
    rows: list[tuple[int, str]] = []
    for child in node:
        if child.tag == "safehtml":
            rows.extend((0, line) for line in plain(child.text or "").split("\n"))
        elif child.tag == "quote":
            author = child.find("qauthor")
            body = child.find("qbody")
            inner = flow(body) if body is not None else []
            name = (
                plain("".join(part.text or "" for part in author)).strip()
                if author is not None
                else ""
            )
            if name:
                inner.insert(0, (0, f"{name} wrote:"))
            rows.extend((depth + 1, line) for depth, line in inner)
    return rows


def body(raw: str) -> str:
    """A post, written out the way the archive writes posts."""
    lines: list[str] = []
    for depth, text in flow(ElementTree.fromstring(raw)):
        text = text.strip()
        prefix = "> " * depth
        if not text and lines and not lines[-1].strip(">").strip():
            continue  # one blank line between paragraphs is plenty
        lines.append(prefix + text if text else prefix.rstrip())
    return "\n".join(lines).strip()


def displayed(when: datetime.datetime) -> str:
    """Write a date the way the archive displays one.

    BoardGameGeek stamps a post to the second and in UTC, and says so; the
    archive keeps both rather than converting to a timezone nobody recorded the
    post in.
    """
    marker = "AM" if when.hour < 12 else "PM"
    clock = f"{when.hour % 12 or 12}:{when:%M:%S}"
    return f"{when:%b} {when.day}, {when.year}, {clock}\u202f{marker}"


def read(url: str) -> dict:
    """One of BoardGameGeek's JSON replies."""
    return json.loads(import_forum.fetch(url))


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
    authors: dict[int, str] = {}
    messages = []
    for post in posts:
        who = post["author"]
        if who not in authors:
            time.sleep(delay)
            authors[who] = read(f"https://api.geekdo.com/api/users/{who}")["username"]
        when = datetime.datetime.fromisoformat(post["postdate"])
        messages.append(
            {
                "Author": authors[who],
                "Date": displayed(when.replace(tzinfo=None)),
                "Body": body(post["bodyXml"]),
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
        path = import_forum.write_thread(thread, args.out)
        print(f"{path}  {len(thread['Messages'])} posts  {thread['Title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
