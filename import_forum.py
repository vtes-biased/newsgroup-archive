#!/usr/bin/env python3
"""Import a topic from the VEKN forum into the archive.

The rulings database cites the VEKN forum as well as the newsgroup, and the
forum drops topics: the Baltimore Purge ruling of 29 May 2021 was answered in
topic 79155, and that topic now 404s. The Wayback Machine still has it. Rather
than cite the Wayback Machine -- a third party, which is what this archive
exists to stop doing -- the topic is copied here in the same JSON the newsgroup
threads use, and the ruling can cite a copy we keep.

    python3 import_forum.py https://web.archive.org/web/20240807100721/\\
        https://www.vekn.net/forum/technical-matters/79155-baltimore-purge

The argument is a URL to fetch or an HTML file already saved. A topic keeps the
number the forum gave it, prefixed so that it can never be mistaken for one of
Google's: topic 79155 becomes thread `vekn-79155`, and `#m0` is still the first
post. Kunena paginates long topics; only what is on the page given is read, so
a topic that runs to several pages has to be given page by page (there is no
such topic in the archive yet).
"""

import argparse
import datetime
import html.parser
import pathlib
import re
import sys
import urllib.parse
import urllib.request

import merge_mbox

GROUP = "vekn.net/forum"
#: Tags that never hold anything, so they never go on the stack.
VOID = {"br", "img", "hr", "input", "meta", "link", "source", "col", "area"}
#: "29 May 2021 19:34", the way the forum writes a post's date.
RE_DATE = re.compile(r"^(\d{1,2}) ([A-Za-z]{3}) (\d{4}) (\d{2}):(\d{2})$")
#: Kunena renders one form of quote header as bare text: "Tzimiakira post=102347".
RE_QUOTED = re.compile(r"^\s*(.*?)\s+post=\d+\s*")
RE_TOPIC = re.compile(r"/(\d+)-[^/]*$")
#: What the Wayback Machine does to every link on a page it keeps.
RE_WAYBACK = re.compile(r"https?://web\.archive\.org/web/\d+(?:im_|js_|cs_)?/")


class Topic(html.parser.HTMLParser):
    """Pull the posts out of a Kunena topic page.

    Kunena marks up what we need and nothing stands in for it: `kdate` is a
    post's time, `mykmsg-header` names its author, `kmsg` is what was written
    and `ksig` the signature the forum staples underneath. Everything else on
    the page is furniture.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.posts: list[dict] = []
        self.stack: list[tuple[str, list[str]]] = []
        self.dates: list[str] = []
        self.author = ""
        self.chunks: list[tuple[int, str]] = []
        self.quote_header = False
        self.reading_author = False

    # -- where we are on the page ----------------------------------------

    def inside(self, name: str) -> bool:
        return any(name in classes or tag == name for tag, classes in self.stack)

    @property
    def quote_depth(self) -> int:
        return sum(1 for tag, _ in self.stack if tag == "blockquote")

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = values.get("class", "").split()
        if tag not in VOID:
            self.stack.append((tag, classes))
        if tag in ("br", "div", "p", "blockquote") and self.in_body:
            self.emit("\n")
        elif tag == "a" and self.inside("mykmsg-header"):
            # The header names the topic as well as the poster; only the poster
            # is a forum user, and only a forum user's link carries kwho-.
            self.reading_author = any(c.startswith("kwho-") for c in classes)
        if "kmsgtext-quote" in classes:
            self.quote_header = True

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag in ("div", "p", "blockquote") and self.in_body:
            # A block ends a line as surely as it starts one: without this, a
            # reply written under a quote runs on from the last quoted line.
            self.emit("\n")
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                closing = self.stack[index:]
                del self.stack[index:]
                if any("kmessage" in classes for _, classes in closing):
                    self.close_post()
                break
        if tag == "a":
            self.reading_author = False

    @property
    def in_body(self) -> bool:
        return self.inside("kmsg") or self.inside("ksig")

    # -- what it says ------------------------------------------------------

    def emit(self, text: str) -> None:
        self.chunks.append((self.quote_depth, text))

    def handle_data(self, data):
        if self.inside("script") or self.inside("style"):
            return
        if self.inside("kdate"):
            self.dates.append(data.strip())
        elif self.reading_author:
            self.author += data.strip()
        elif self.inside("title") and not self.title:
            self.title = data.split(" - V:EKN forum")[0].strip()
        elif self.in_body:
            if self.quote_header:
                self.quote_header = False
                match = RE_QUOTED.match(data)
                if match:
                    self.emit(f"\n{match.group(1)} wrote:\n")
                    data = data[match.end() :]
            self.emit(re.sub(r"\s+", " ", RE_WAYBACK.sub("", data)))

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and self.in_body:
            self.emit("\n")

    # -- one post at a time ------------------------------------------------

    def close_post(self) -> None:
        """A post has closed: keep what it held.

        The body and the signature are two boxes inside it, so the post is what
        closes, not either box -- the signature belongs to the post the way a
        Usenet sig does, even though the forum staples it on rather than the
        poster typing it.
        """
        rows: list[tuple[int, str]] = []
        depth, current = 0, ""
        for chunk_depth, text in self.chunks:
            for index, piece in enumerate(text.split("\n")):
                if index:
                    rows.append((depth, current))
                    depth, current = chunk_depth, ""
                if piece.strip() and not current.strip():
                    # A line is quoted as deeply as the text that opens it.
                    depth = chunk_depth
                current += piece
        rows.append((depth, current))
        lines: list[str] = []
        for row_depth, text in rows:
            prefix = "> " * row_depth
            text = text.strip()
            if not text and lines and not lines[-1].strip(">").strip():
                continue  # the markup is full of blank lines; one is plenty
            lines.append(prefix + text if text else prefix.rstrip())
        body = "\n".join(lines).strip()
        if self.author and self.dates:
            self.posts.append(
                {"Author": self.author, "Date": self.dates[0], "Body": body}
            )
        self.author, self.dates, self.chunks = "", [], []


def posted(raw: str) -> datetime.datetime:
    """Read the date the forum prints over a post."""
    match = RE_DATE.match(raw)
    if not match:
        raise ValueError(f"unreadable forum date: {raw!r}")
    day, month, year, hour, minute = match.groups()
    return datetime.datetime.strptime(
        f"{day} {month} {year} {hour}:{minute}", "%d %b %Y %H:%M"
    )


def displayed(when: datetime.datetime) -> str:
    """Write a date the way the archive displays one.

    The forum shows no seconds, which the archive already has a format for, so
    the date is written without rather than padded with a second nobody
    recorded. The time is the forum's own, unconverted: which timezone it
    displays in is not on the page, and a reference dates the ruling anyway,
    not the post.
    """
    marker = "AM" if when.hour < 12 else "PM"
    return f"{when:%b} {when.day}, {when.year}, {when.hour % 12 or 12}:{when:%M}\u202f{marker}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="topic URL to fetch, or a saved HTML file")
    parser.add_argument(
        "--url", help="the URL to record, if the page was read from a file"
    )
    parser.add_argument(
        "--out", type=pathlib.Path, default=pathlib.Path(__file__).parent
    )
    args = parser.parse_args()

    if args.source.startswith(("http://", "https://")):
        request = urllib.request.Request(
            args.source, headers={"User-Agent": "newsgroup-archive/import_forum"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            page = response.read().decode("utf-8", "replace")
        source = args.url or args.source
    else:
        page = pathlib.Path(args.source).read_text(encoding="utf-8")
        if not args.url:
            print("--url is needed when the page comes from a file", file=sys.stderr)
            return 1
        source = args.url

    topic = Topic()
    topic.feed(page)
    if not topic.posts:
        print(f"no posts found in {args.source}", file=sys.stderr)
        return 1

    match = RE_TOPIC.search(urllib.parse.urlparse(source).path)
    if not match:
        print(f"no topic number in {source}", file=sys.stderr)
        return 1
    ident = f"vekn-{match.group(1)}"

    start = posted(topic.posts[0]["Date"])
    for post in topic.posts:
        post["Date"] = displayed(posted(post["Date"]))
    thread = {
        "ThreadId": ident,
        "Group": GROUP,
        "Url": source,
        "Title": topic.title,
        "Messages": topic.posts,
    }
    path = (
        args.out
        / "threads"
        / start.strftime("%Y")
        / f"{start.strftime('%Y%m%d_%H%M')}_{ident}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(merge_mbox.dump(thread), encoding="utf-8")
    print(f"{path}  {len(topic.posts)} posts  {topic.title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
