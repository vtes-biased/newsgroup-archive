#!/usr/bin/env python3
"""Render the thread archive to a static website.

Reads the JSON thread dumps in ``threads/`` and writes plain HTML in ``_site/``.
Standard library only: the point of this archive is that it can still be rebuilt
in twenty years by anyone with a Python interpreter.

    python3 build.py [--out _site] [--base-url https://example.org]
"""

import argparse
import datetime
import html
import json
import pathlib
import re
import shutil
import sys

# Google Groups substitutes this private-use character for the quoted text it
# folds away behind a "show trimmed content" button. The scrape never saw the
# text behind it, so all we can do is say so.
ELIDED = ""

DATE_FORMATS = (
    "%b %d, %Y, %I:%M:%S %p",
    "%b %d, %Y, %I:%M %p",
    "%b %d, %Y",
)

RE_URL = re.compile(r"https?://[^\s<>&\"']+")
RE_QUOTE = re.compile(r"^\s*(>+)")
# A minority of posters quoted with ":" instead of ">". Only honoured when a
# message uses it consistently, so that a stray ":D" is not read as a quote.
RE_COLON_QUOTE = re.compile(r"^(:+)( |$)")

GROUP = "rec.games.trading-cards.jyhad"


def parse_date(raw: str) -> datetime.datetime:
    """Parse the human-readable date Google Groups displays."""
    text = raw.replace(" ", " ").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unparsable date: {raw!r}")


def linkify(escaped: str) -> str:
    """Turn bare URLs into links. Operates on already-escaped text."""

    def repl(match: re.Match) -> str:
        url = match.group(0).rstrip(".,;:)")
        tail = match.group(0)[len(url) :]
        return f'<a href="{url}" rel="nofollow">{url}</a>{tail}'

    return RE_URL.sub(repl, escaped)


def render_body(body: str) -> str:
    """Render a message body, preserving its fixed-width layout.

    Quoted lines are grouped so they can be dimmed, and the elision marker is
    made explicit instead of showing up as a stray glyph.
    """
    lines = body.replace("\r\n", "\n").split("\n")
    quote_re = RE_QUOTE
    if sum(1 for line in lines if RE_COLON_QUOTE.match(line)) >= 3 and not any(
        line.startswith(">") for line in lines
    ):
        quote_re = RE_COLON_QUOTE
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_depth = -1

    def flush() -> None:
        nonlocal buffer, buffer_depth
        if not buffer:
            return
        text = linkify("\n".join(buffer))
        if buffer_depth < 0:
            chunks.append(text)
        else:
            level = min(buffer_depth, 2)
            chunks.append(f'<span class="q q{level}">{text}</span>')
        buffer = []
        buffer_depth = -1

    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip() == ELIDED:
            flush()
            # Collapse a run of markers (and the blank lines between them).
            while index + 1 < len(lines) and lines[index + 1].strip() in (ELIDED, ""):
                if lines[index + 1].strip() == "":
                    lookahead = index + 2
                    while lookahead < len(lines) and lines[lookahead].strip() == "":
                        lookahead += 1
                    if lookahead >= len(lines) or lines[lookahead].strip() != ELIDED:
                        break
                index += 1
            chunks.append(
                '<span class="elided" title="Google Groups folded this quoted '
                'text away; the archive never captured it">[ quoted text not '
                "captured ]</span>"
            )
        else:
            match = quote_re.match(line)
            depth = len(match.group(1)) if match else -1
            if depth != buffer_depth:
                flush()
                buffer_depth = depth
            buffer.append(html.escape(line))
        index += 1
    flush()
    return "\n".join(chunks)


def page(title: str, body: str, *, depth: int, description: str = "") -> str:
    """Wrap page content in the site chrome. ``depth`` is the URL nesting."""
    root = "../" * depth or "./"
    meta = (
        f'<meta name="description" content="{html.escape(description)}">\n'
        if description
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
{meta}<link rel="stylesheet" href="{root}site.css">
</head>
<body>
<header class="site">
<a class="wordmark" href="{root}">rec.games.trading-cards.jyhad</a>
<nav><a href="{root}search/">Search</a> <a href="{root}about/">About</a></nav>
</header>
<main>
{body}
</main>
<footer class="site">
<p>An archive of the Usenet newsgroup <code>{GROUP}</code>, kept so that the
rulings citing it keep working.</p>
</footer>
</body>
</html>
"""


class Thread:
    """One newsgroup thread, loaded from its JSON dump."""

    def __init__(self, path: pathlib.Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.id: str = data["ThreadId"]
        self.title: str = data["Title"].strip() or "(no subject)"
        self.source_url: str = data["Url"]
        self.messages: list[dict] = data["Messages"]
        self.dates = [parse_date(m["Date"]) for m in self.messages]
        self.start = self.dates[0]
        self.end = max(self.dates)
        self.authors = list(dict.fromkeys(m["Author"] for m in self.messages))

    @property
    def url(self) -> str:
        return f"t/{self.id}/"

    def render(self) -> str:
        year = self.start.strftime("%Y")
        span = self.start.strftime("%d %B %Y")
        if self.end.date() != self.start.date():
            span += f" &ndash; {self.end.strftime('%d %B %Y')}"
        count = len(self.messages)
        people = len(self.authors)
        parts = [
            f'<nav class="crumbs"><a href="../../">Archive</a> / '
            f'<a href="../../{year}/">{year}</a></nav>',
            f"<h1>{html.escape(self.title)}</h1>",
            '<p class="meta">'
            f"{count} message{'s' if count > 1 else ''} from {people} "
            f"participant{'s' if people > 1 else ''} &middot; {span}<br>"
            f'<a class="source" href="{html.escape(self.source_url)}">'
            "original thread on Google Groups</a></p>",
        ]
        for index, message in enumerate(self.messages):
            stamp = self.dates[index]
            parts.append(
                f'<article class="msg" id="m{index}">\n'
                f'<h2 class="who">{html.escape(message["Author"])}'
                f'<a class="permalink" href="#m{index}" '
                f'aria-label="permalink to message {index + 1}">#</a></h2>\n'
                f'<p class="when"><time datetime="{stamp.isoformat()}">'
                f'{stamp.strftime("%d %B %Y, %H:%M")}</time></p>\n'
                f'<div class="body">{render_body(message["Body"])}</div>\n'
                "</article>"
            )
        return page(
            self.title,
            "\n".join(parts),
            depth=2,
            description=f"{self.title} — {span}, {count} messages.",
        )


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def render_year(year: str, threads: list[Thread]) -> str:
    rows = "\n".join(
        f"<tr><td class='d'>{t.start.strftime('%d %b')}</td>"
        f"<td><a href='../{t.url}'>{html.escape(t.title)}</a></td>"
        f"<td class='n'>{len(t.messages)}</td></tr>"
        for t in threads
    )
    body = (
        f'<nav class="crumbs"><a href="../">Archive</a></nav>\n'
        f"<h1>{year}</h1>\n"
        f'<p class="meta">{len(threads)} threads, '
        f"{sum(len(t.messages) for t in threads)} messages.</p>\n"
        f'<table class="listing">{rows}</table>'
    )
    return page(f"{year} — jyhad newsgroup archive", body, depth=1)


def render_index(by_year: dict[str, list[Thread]], total: int) -> str:
    years = "\n".join(
        f"<li><a href='{year}/'>{year}</a> "
        f"<span class='n'>{len(threads)}</span></li>"
        for year, threads in sorted(by_year.items())
    )
    body = f"""<h1>rec.games.trading-cards.jyhad</h1>
<p class="lede">The Usenet group where Vampire: The Eternal Struggle was played
out in public from 1994 to 2010, and where its rules directors answered
questions one post at a time. This is a preserved copy of the threads
L.&nbsp;Scott Johnson took part in &mdash;
{sum(len(t) for t in by_year.values()):,} threads, {total:,} messages.</p>
<p>The rulings in the <a href="https://rulings.krcg.org">VTES rulings
database</a> cite these threads. Those citations point at Google Groups today;
they will point here instead, because this copy is not going anywhere.</p>
<ul class="years">
{years}
</ul>
<p class="note"><a href="search/">Search titles and authors</a> &middot;
<a href="about/">Where this came from</a></p>"""
    return page("rec.games.trading-cards.jyhad archive", body, depth=0)


def render_about(total_threads: int, total_messages: int) -> str:
    body = f"""<nav class="crumbs"><a href="../">Archive</a></nav>
<h1>About this archive</h1>
<p>This is a static copy of {total_threads:,} threads
({total_messages:,} messages) from the Usenet newsgroup
<code>{GROUP}</code>, spanning 1994 to 2010. It is the subset of the group in
which <strong>L.&nbsp;Scott Johnson</strong> &mdash; VTES rules director from
June 1998 &mdash; posted, plus a handful of older threads cited by the rulings
database for rulings by Thomas&nbsp;R.&nbsp;Wylie and Shawn&nbsp;F.&nbsp;Carnes.</p>

<h2>Why it exists</h2>
<p>The <a href="https://github.com/vtes-biased/vtes-rulings">VTES rulings
database</a> is a curated list of rulings, each one sourced. Several hundred of
those sources were links into Google Groups. Google Groups stopped accepting new
Usenet posts in 2024 and its archive has been progressively harder to reach; a
rulings database whose citations rot is a rulings database nobody can check. So
the cited threads live here instead, as plain HTML that any web server can hand
out and any browser from any decade can read.</p>

<h2>What you are reading</h2>
<p>Each page is one thread, in posting order. Message text is shown as it was
posted, hard wraps and all, because Usenet was a fixed-width medium and its
tables and diagrams only line up that way. Two things are not original:</p>
<ul>
<li>Author e-mail addresses were already partly obscured by Google Groups
(<code>someone...@example.com</code>); that is how they reached us.</li>
<li>Where you see <span class="elided">[ quoted text not captured ]</span>,
Google Groups had folded the quoted passage behind a "show trimmed content"
control and the scrape never saw it. The text is not lost from the archive
&mdash; it is in whichever message was being quoted.</li>
</ul>

<h2>Linking to it</h2>
<p>Every thread keeps the identifier Google Groups gave it, so a link can be
translated mechanically:</p>
<pre class="rewrite">groups.google.com/g/{GROUP}/c/<b>KWekwiRSa2I</b>
        &darr;
    <i>this site</i>/t/<b>KWekwiRSa2I</b>/</pre>
<p>Within a thread, <code>#m0</code> is the first message, <code>#m1</code> the
second, and so on. The <span class="permalink">#</span> beside each author name
is that message's own link.</p>

<h2>Provenance</h2>
<p>The threads were scraped from Google Groups' copy of the newsgroup. The JSON
they were rendered from is committed alongside the site generator in the
<a href="https://github.com/vtes-biased/newsgroup-archive">newsgroup-archive</a>
repository, so the pages can be rebuilt, re-styled, or re-purposed without
going back to any third party.</p>
<p>The posts are the property of their authors and are reproduced here for
reference and preservation.</p>"""
    return page("About — jyhad newsgroup archive", body, depth=1)


def render_search() -> str:
    body = """<nav class="crumbs"><a href="../">Archive</a></nav>
<h1>Search</h1>
<p class="meta">Thread titles and author names. Message text is not indexed
&mdash; there is too much of it to search in your browser.</p>
<form id="f" onsubmit="return false"><input id="q" type="search"
  placeholder="Camarilla Exemplary" autofocus autocomplete="off"
  aria-label="Search thread titles and authors"></form>
<p id="status" class="meta">Loading the index&hellip;</p>
<table class="listing" id="results"></table>
<script>
(function () {
  var data = null, status = document.getElementById('status'),
      out = document.getElementById('results'), box = document.getElementById('q');
  fetch('../threads.json').then(function (r) { return r.json(); })
    .then(function (d) { data = d; status.textContent =
      d.length + ' threads indexed.'; run(); })
    .catch(function () { status.textContent =
      'Could not load the index. Browse by year instead.'; });
  function run() {
    var q = box.value.trim().toLowerCase();
    out.innerHTML = '';
    if (!data || q.length < 2) {
      status.textContent = data ? data.length + ' threads indexed.' : '';
      return;
    }
    var hits = [], i;
    for (i = 0; i < data.length && hits.length < 300; i++) {
      if (data[i][1].toLowerCase().indexOf(q) >= 0 ||
          data[i][3].toLowerCase().indexOf(q) >= 0) hits.push(data[i]);
    }
    status.textContent = hits.length + (hits.length === 300 ? '+' : '') +
      ' matching thread' + (hits.length === 1 ? '' : 's') + '.';
    var rows = hits.map(function (t) {
      return '<tr><td class="d">' + t[2] + '</td><td><a href="../t/' + t[0] +
        '/">' + t[1].replace(/&/g, '&amp;').replace(/</g, '&lt;') +
        '</a></td><td class="n">' + t[4] + '</td></tr>';
    });
    out.innerHTML = rows.join('');
  }
  box.addEventListener('input', run);
})();
</script>"""
    return page("Search — jyhad newsgroup archive", body, depth=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="_site", type=pathlib.Path)
    parser.add_argument(
        "--base-url", default="", help="absolute site URL, for the sitemap"
    )
    parser.add_argument("--limit", type=int, help="build only N threads (for testing)")
    args = parser.parse_args()

    here = pathlib.Path(__file__).parent
    out = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    paths = sorted((here / "threads").glob("*/*.json"))
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print("no threads found in threads/", file=sys.stderr)
        return 1

    by_year: dict[str, list[Thread]] = {}
    index: list[list] = []
    seen: set[str] = set()
    total_messages = 0

    for count, path in enumerate(paths, 1):
        thread = Thread(path)
        if thread.id in seen:
            print(f"duplicate thread id {thread.id} ({path})", file=sys.stderr)
            return 1
        seen.add(thread.id)
        write(out / "t" / thread.id / "index.html", thread.render())
        by_year.setdefault(thread.start.strftime("%Y"), []).append(thread)
        total_messages += len(thread.messages)
        index.append(
            [
                thread.id,
                thread.title,
                thread.start.strftime("%Y-%m-%d"),
                " ".join(thread.authors),
                len(thread.messages),
            ]
        )
        if count % 2000 == 0:
            print(f"  {count}/{len(paths)} threads")

    for year, threads in by_year.items():
        threads.sort(key=lambda t: t.start)
        write(out / year / "index.html", render_year(year, threads))

    write(out / "index.html", render_index(by_year, total_messages))
    write(out / "about" / "index.html", render_about(len(paths), total_messages))
    write(out / "search" / "index.html", render_search())
    index.sort(key=lambda row: row[2])
    write(
        out / "threads.json",
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
    )
    shutil.copyfile(here / "static" / "site.css", out / "site.css")
    write(out / ".nojekyll", "")
    write(out / "robots.txt", "User-agent: *\nAllow: /\n")

    if args.base_url:
        base = args.base_url.rstrip("/")
        urls = ["", "about/", "search/"] + [f"{y}/" for y in sorted(by_year)]
        urls += [f"t/{tid}/" for tid in sorted(seen)]
        write(
            out / "sitemap.xml",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join(f"<url><loc>{base}/{u}</loc></url>\n" for u in urls)
            + "</urlset>\n",
        )

    print(f"{len(paths)} threads, {total_messages} messages -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
