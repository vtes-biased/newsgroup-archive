#!/usr/bin/env python3
"""Copy every V:EKN forum topic a member has posted in into the archive.

Nearly a third of the sources the rulings database cites live on the V:EKN
forum -- 490 of some 1,550 references, across 457 topics -- and it loses topics:
79155, which a ruling still rests on, 404s today and survives only because the
Wayback Machine happened to keep it. Rather than wait for the next one to go,
this walks the forum and copies the topics the rules director has answered in,
written as the same JSON the newsgroup threads use.

    python3 sync_forum.py --list      # say what would be fetched, fetch nothing
    python3 sync_forum.py             # fetch what the archive has not got
    python3 sync_forum.py --refresh   # fetch every topic again, replies and all
    python3 sync_forum.py --refresh --recent 60   # only the ones still talked in

The forum publishes no listing of a member's topics -- the one on their profile
shows the six most recent -- so the search does the enumerating instead:
`searchuser=ankha` returns every post they wrote, newest first, a hundred to a
page, and each result names the topic it is in. Five dozen requests give the
whole list; the topics themselves are then fetched one at a time.

A plain run fetches a topic the archive does not have, or one where the search
shows a post of theirs the archive is missing. Everything else is left alone,
so a second run costs a minute. What it cannot see is a reply written after
that member's last post in a topic: only `--refresh` picks those up, and
`--recent` keeps that to the topics whose last post is still fresh.

A run the forum would not let finish exits non-zero, and so does one that
leaves a topic named by `--expect` missing. A search cut short, a cited topic
the forum has lost and a forum with nothing new look alike from the outside,
and the nightly sweep in `.github/workflows/sync.yml` has to tell them apart.
"""

import argparse
import datetime
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import archive
import import_forum

SITE = "https://www.vekn.net"
#: The search, with every category and no date limit: every post by one member.
SEARCH = (
    SITE + "/forum/search?searchuser={user}&searchdate=all"
    "&childforums=1&limit={size}&start={start}"
)
#: A hundred is as many as either view will give, and both honour it.
PAGE = 100
#: A topic that runs longer than this is not a topic, it is a mistake.
MAX_PAGES = 50
#: Each hit in the search results links the topic it was found in, at the post.
RE_HIT = re.compile(r'href="/forum/(?!user/)([a-z0-9-]+)/(\d+)-([^"?#]*)[^"]*#(\d+)"')
#: And names its author, whom we asked for but had better check.
RE_WHO = re.compile(r'href="/forum/user/(\d+)-([^"]*)"')
RE_POST = re.compile(r'class="kmessage"')
#: Every post carries its own number as the anchor the forum links it by.
RE_ID = re.compile(r'id="(\d+)"')
#: A link to a topic, wherever it is written. The topic is the last numbered
#: segment: in `/forum/6-rules-questions/66453-...` the 6 is a category.
RE_CITED = re.compile(r'vekn\.net(/forum[\w-]*/(?:[^\s?#"]*/)?(\d+)-[^\s?#"/]*)')


def patiently(url: str, delay: float, tries: int = 4) -> str | None:
    """Fetch a page, waiting first, and again longer if the forum stumbles.

    A 404 is an answer, not a stumble: topics go, and the caller decides what
    that means. Anything else is worth another try before giving up on it.
    """
    for attempt in range(tries):
        time.sleep(delay if attempt == 0 else delay + 5 * 3**attempt)
        try:
            return import_forum.fetch(url)
        except urllib.error.HTTPError as problem:
            if problem.code in (403, 404, 410):
                return None
            trouble = f"HTTP {problem.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as problem:
            trouble = str(problem)
        print(f"  {trouble}, retrying: {url}", file=sys.stderr)
    return None


def slug(name: str) -> str:
    """The forum's own spelling of a member's name where it links them.

    Members are named with spaces in them -- Pascal Bertrand is one -- which
    the search wants encoded and a link writes hyphenated.
    """
    return name.lower().replace(" ", "-")


def hits(page: str) -> list[dict]:
    """The posts on one page of search results.

    Kunena opens every hit with the same marker, so the page splits into one
    chunk per hit and the first topic link in a chunk is that hit's own.
    """
    found = []
    for chunk in page.split('id="kunena_search_results"')[1:]:
        hit = RE_HIT.search(chunk)
        who = RE_WHO.search(chunk)
        if hit:
            found.append(
                {
                    "topic": hit.group(2),
                    "path": f"/forum/{hit.group(1)}/{hit.group(2)}-{hit.group(3)}",
                    "post": hit.group(4),
                    "who": who.group(2) if who else "",
                }
            )
    return found


def every_post(user: str, delay: float) -> tuple[list[dict], bool]:
    """Walk the search until it runs out of posts by that member.

    Says as well whether it reached the end. A search that stops halfway has
    not told us what is new, which hardly matters to somebody watching it run
    and matters a great deal to the nightly one, where a forum that refused to
    answer looks exactly like a forum with nothing to add.
    """
    posts: list[dict] = []
    start = 0
    whole = True
    while True:
        asked = SEARCH.format(user=urllib.parse.quote(user), size=PAGE, start=start)
        page = patiently(asked, delay)
        if page is None:
            print(f"search failed at start={start}", file=sys.stderr)
            whole = False
            break
        found = hits(page)
        posts.extend(found)
        print(f"  {len(posts)} posts", end="\r", file=sys.stderr)
        if len(found) < PAGE:
            break
        start += PAGE
    strangers = {post["who"].lower() for post in posts} - {slug(user), ""}
    if strangers:
        print(f"search returned other members: {sorted(strangers)}", file=sys.stderr)
    return posts, whole


def topics(posts: list[dict]) -> dict[str, dict]:
    """The posts gathered into the topics that hold them, newest topic first."""
    gathered: dict[str, dict] = {}
    for post in posts:
        topic = gathered.setdefault(
            post["topic"], {"path": post["path"], "posts": set()}
        )
        topic["posts"].add(post["post"])
    return gathered


def archived(out: pathlib.Path) -> dict[str, tuple[pathlib.Path, dict]]:
    """Every forum topic the archive already holds, by topic number."""
    known = {}
    for path in sorted(out.glob("threads/*/*_vekn-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        known[data["ThreadId"].removeprefix("vekn-")] = (path, data)
    return known


def pages(url: str, delay: float) -> list[str] | None:
    """A topic, page by page, asking for as many posts at a time as allowed."""
    collected: list[str] = []
    seen: set[str] = set()
    for page_number in range(MAX_PAGES):
        start = page_number * PAGE
        page = patiently(f"{url}?limit={PAGE}&start={start}", delay)
        if page is None:
            # Half a topic looks exactly like a whole one once it is written,
            # and the next run would see the posts it was looking for and skip
            # it forever. A topic arrives entire or it does not arrive.
            return None
        posts = set(RE_ID.findall(page))
        if not posts - seen:
            # Asked for a page past the end, Kunena hands back one we have
            # already read rather than an empty one, so a topic whose length
            # is an exact multiple of the page size would repeat until the cap
            # and be written fifty times over. A page of nothing new is the end.
            return collected
        seen |= posts
        collected.append(page)
        if len(RE_POST.findall(page)) < PAGE:
            return collected
    return collected


def last_post(thread: dict) -> datetime.datetime:
    return max(archive.read(message["Date"]) for message in thread["Messages"])


def cited(path: pathlib.Path | None) -> dict[str, dict]:
    """The topics a file links to, by number, each with the path it is read at."""
    if path is None:
        return {}
    found: dict[str, dict] = {}
    for link in RE_CITED.finditer(path.read_text(encoding="utf-8")):
        found.setdefault(link.group(2), {"path": link.group(1)})
    return found


def sync(args: argparse.Namespace) -> int:
    out: pathlib.Path = args.out
    print(f"reading every post by {args.user}", file=sys.stderr)
    found, whole = every_post(args.user, args.delay)
    wanted = topics(found)
    expected = cited(args.expect)
    known = archived(out)
    print(f"{len(wanted)} topics, {len(known)} already in the archive")
    # Forum dates carry no zone, so this is off by a few hours at most.
    fresh = None
    if args.recent is not None:
        fresh = datetime.datetime.now() - datetime.timedelta(days=args.recent)

    todo = []
    for number, topic in wanted.items():
        if number not in known:
            todo.append((number, topic, "new"))
        elif args.refresh and (fresh is None or last_post(known[number][1]) >= fresh):
            todo.append((number, topic, "refresh"))
        elif not topic["posts"] <= {
            message.get("Id") for message in known[number][1]["Messages"]
        }:
            todo.append((number, topic, "more posts"))
    for number, topic in expected.items():
        if number not in known and number not in wanted:
            todo.append((number, topic, "cited"))
    print(f"{len(todo)} to fetch")
    if args.list:
        for number, topic, why in todo:
            print(f"  {number:>6}  {why:<10}  {topic['path']}")
    else:
        fetched = gone = failed = 0
        for count, (number, topic, why) in enumerate(todo, 1):
            url = SITE + topic["path"]
            print(f"[{count}/{len(todo)}] {why}: {url}", file=sys.stderr)
            collected = pages(url, args.delay)
            if collected is None:
                if number in known:
                    print(f"  not fetched whole; keeping {known[number][0]}")
                    gone += 1
                else:
                    print(f"  not fetched, and not archived: {url}")
                    failed += 1
                continue
            try:
                thread = import_forum.build_thread(collected, url)
            except ValueError as problem:
                print(f"  {problem}", file=sys.stderr)
                failed += 1
                continue
            path = archive.write_thread(thread, out)
            known[number] = (path, thread)
            fetched += 1
        print(f"fetched {fetched}, gone {gone}, failed {failed}")
    if args.expect and not expected:
        print(f"no forum link in {args.expect}: nothing checked", file=sys.stderr)
        return 1
    status = 0 if args.list else report(expected, known)
    if not whole:
        print("the search did not finish: what is new is not known", file=sys.stderr)
        return 1
    return status


def report(expected: dict, known: dict) -> int:
    """Fail on any topic somebody cites that the archive still has not got.

    By now every one of them has been asked of the forum, so a topic still
    missing is one the forum has dropped: only a copy from elsewhere, the
    Wayback Machine's, can fill it, and nobody will go looking unless told.
    """
    missing = sorted(set(expected) - set(known), key=int)
    if expected:
        print(f"{len(expected) - len(missing)}/{len(expected)} cited topics archived")
    for number in missing:
        print(f"  missing {number}: {SITE}{expected[number]['path']}")
    return 1 if missing else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default="ankha", help="the member to follow")
    parser.add_argument(
        "--out", type=pathlib.Path, default=pathlib.Path(__file__).parent
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, help="seconds between requests"
    )
    parser.add_argument(
        "--list", action="store_true", help="say what would be fetched, fetch nothing"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="fetch known topics again too"
    )
    parser.add_argument(
        "--recent",
        type=int,
        metavar="DAYS",
        help="refresh only the topics with a post in the last DAYS days",
    )
    parser.add_argument(
        "--expect",
        type=pathlib.Path,
        help="a file whose forum links name topics the archive must hold;"
        " those it lacks are fetched, and any still missing fail the run",
    )
    args = parser.parse_args()
    if args.recent is not None and not args.refresh:
        parser.error("--recent only narrows --refresh")
    return sync(args)


if __name__ == "__main__":
    sys.exit(main())
