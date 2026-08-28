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
import unicodedata

# Google Groups substitutes this private-use character for the quoted text it
# folds away behind a "show trimmed content" button. The scrape never saw the
# text behind it, so all we can do is say so.
ELIDED = ""

DATE_FORMATS = (
    "%b %d, %Y, %I:%M:%S %p",
    "%b %d, %Y, %I:%M %p",
    "%b %d, %Y",
)

#: What counts as a word for the search index: letters and digits, with
#: apostrophes and hyphens allowed inside, lowercased. Two characters is the
#: shortest thing worth filing, thirty the longest thing worth calling a word.
RE_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")
WORD_MIN, WORD_MAX = 2, 30
#: Accents are stripped before a word is filed, and stripped again from what is
#: typed, so that Rötschreck is one word rather than "r" and "tschreck" and is
#: found by the spelling most of the group used. The letters here are the ones
#: that carry no accent to peel off: they have to be spelled out by hand.
LIGATURES = str.maketrans(
    {"æ": "ae", "œ": "oe", "ß": "ss", "ø": "o", "ð": "d", "đ": "d", "ł": "l", "þ": "th"}
)
#: A prefix matches many words, and unioning all of them helps nobody: 'ra'
#: starts 906 of them. Beyond three characters 89% of prefixes in this archive
#: reach ten words or fewer, so ten is where the search stops widening.
PREFIX_CAP = 10

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


def source_name(url: str) -> str:
    """What to call the place a thread was read from."""
    if "web.archive.org" in url:
        return "the page as the Wayback Machine kept it"
    if "vekn.net" in url:
        return "the topic on the V:EKN forum"
    if "boardgamegeek.com" in url:
        return "the thread on BoardGameGeek"
    if "/c/" in url:
        return "original thread on Google Groups"
    return "source archive"


class Thread:
    """One thread, newsgroup or forum, loaded from its JSON dump."""

    def __init__(self, path: pathlib.Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.id: str = data["ThreadId"]
        self.title: str = data["Title"].strip() or "(no subject)"
        self.source_url: str = data["Url"]
        #: Threads imported from another group say which one they came from.
        self.group: str = data.get("Group", GROUP)
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
            f"participant{'s' if people > 1 else ''} &middot; {span}"
            + (
                f" &middot; <code>{html.escape(self.group)}</code>"
                if self.group != GROUP
                else ""
            )
            + f'<br><a class="source" href="{html.escape(self.source_url)}">'
            + source_name(self.source_url)
            + "</a></p>",
        ]
        for index, message in enumerate(self.messages):
            stamp = self.dates[index]
            #: A forum post keeps the number the forum gave it, so that a link
            #: written against the forum keeps its fragment when the rest of
            #: the URL is swapped for ours: `...#117641` still lands here.
            alias = (
                f'<a class="alias" id="{html.escape(message["Id"])}"></a>\n'
                if message.get("Id")
                else ""
            )
            parts.append(
                f'<article class="msg" id="m{index}">\n{alias}'
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


def fold(text: str) -> str:
    """Lowercase and strip the accents, so a word is spelled one way only."""
    stripped = unicodedata.normalize("NFKD", text.lower().translate(LIGATURES))
    return "".join(c for c in stripped if not unicodedata.combining(c))


def thread_words(thread: "Thread") -> set[str]:
    """Every distinct word in a thread: its title, its authors, its messages.

    Quoted lines are indexed like any other. They cost 0.9% more postings than
    skipping them would, because a quoted word is nearly always somewhere else
    in the same thread anyway -- and when it is not, it is usually mail from
    another list that the archive holds nowhere but inside that quote.
    """
    text = fold(
        "\n".join(
            [thread.title]
            + [m["Author"] for m in thread.messages]
            + [m["Body"] for m in thread.messages]
        )
    )
    return {w for w in RE_WORD.findall(text) if WORD_MIN <= len(w) <= WORD_MAX}


def shard_name(term: str) -> str:
    """The file a term is filed in: its first two characters, made safe."""
    return "".join(c if c.isalnum() else "_" for c in term[:2])


def write_search_index(out: pathlib.Path, postings: dict[str, list[int]]) -> None:
    """Write the word index the search page reads, split into small files.

    Whole, the index is 17 MB, 4.7 MB of it over the wire -- too much to hand a
    phone before it can search at all. Split by the first two characters of the
    word, a query fetches one file (a few KB for most, 167 KB gzipped for
    co.txt, the worst of them) and the rest is never downloaded.

    A line is ``word threads gap,gap,gap``: how many threads contain the word,
    then their positions in threads.json as gaps from the one before, so the
    numbers stay short. The file is sorted by word, which is what makes a
    prefix a run of consecutive lines.
    """
    shards: dict[str, list[str]] = {}
    for term in sorted(postings):
        docs = postings[term]
        gaps = []
        previous = 0
        for doc in docs:
            gaps.append(str(doc - previous))
            previous = doc
        shards.setdefault(shard_name(term), []).append(
            f"{term} {len(docs)} {','.join(gaps)}"
        )
    for name, lines in shards.items():
        write(out / "find" / f"{name}.txt", "\n".join(lines) + "\n")
    print(
        f"{len(postings):,} words in {len(shards)} files, "
        f"{sum(len(d) for d in postings.values()):,} word-thread pairs -> find/"
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
        f'<p class="meta">{len(threads)} thread{"s" if len(threads) > 1 else ""}, '
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
questions one post at a time. This is a preserved copy of every thread one of
those directors took part in &mdash;
{sum(len(t) for t in by_year.values()):,} threads, {total:,} messages &mdash;
together with the topics their successors have answered on the V:EKN forum
ever since, which is where the rulings went when Usenet ended, and three
the threads L.&nbsp;Scott&nbsp;Johnson answered on BoardGameGeek in between,
where his last ruling as rules director was posted.</p>
<p>The rulings in the <a href="https://rulings.krcg.org">VTES rulings
database</a> cite these threads. Those citations point at Google Groups today;
they will point here instead, because this copy is not going anywhere.</p>
<ul class="years">
{years}
</ul>
<p class="note"><a href="search/">Search the full text</a> &middot;
<a href="about/">Where this came from</a></p>"""
    return page("rec.games.trading-cards.jyhad archive", body, depth=0)


def render_about(total_threads: int, total_messages: int) -> str:
    body = f"""<nav class="crumbs"><a href="../">Archive</a></nav>
<h1>About this archive</h1>
<p>This is a static copy of {total_threads:,} threads
({total_messages:,} messages), from three places. Most of them come from the
Usenet newsgroup <code>{GROUP}</code>, spanning 1994 to 2010: every thread in
which one of the game's rules directors posted &mdash;
<strong>Thomas&nbsp;R.&nbsp;Wylie</strong> (from December 1994),
<strong>Shawn&nbsp;F.&nbsp;Carnes</strong> (July 1996),
<strong>Jon&nbsp;Wilkie</strong> (October 1996) and
<strong>L.&nbsp;Scott&nbsp;Johnson</strong> (June 1998 onward) &mdash; which is
where the rulings of the newsgroup era were handed down. The threads they
answered in are here whole, questions and argument included, not just the
answers.</p>

<p>A few hundred of them are not from that group at all. Jyhad was released in
1994, before <code>{GROUP}</code> existed, and its first rules discussions
&mdash; including the rules team's earliest rulings lists &mdash; happened in
<code>rec.games.deckmaster</code>, the Magic newsgroup. Those threads are here
too, marked with the group they came from, so that the rulings citing them have
somewhere to point.</p>

<p>Several thousand threads are not from Usenet at all. Rulings did not stop
when the newsgroup did &mdash; they moved to the
<a href="https://www.vekn.net/forum">V:EKN forum</a>, where
<strong>Pascal&nbsp;Bertrand</strong> and then <strong>Ankha</strong> have
answered them since. Every topic either of them posted in is here too, marked
with the forum it came from, on the same rule that decided which newsgroup
threads to keep. The forum drops topics of its own accord: the
one a Baltimore Purge ruling rests on returns a 404 today and survives only
because the Wayback Machine happened to keep it, which is reason enough not to
wait and see which goes next. Two more topics are here for the other reason
&mdash; a ruling cites them, though neither director posted in them.</p>

<p>Fifty-seven threads come from a third place. As the newsgroup wound down,
<strong>L.&nbsp;Scott&nbsp;Johnson</strong> took to answering questions in the
game's Rules forum on
<a href="https://boardgamegeek.com/boardgame/2122/vampire-the-eternal-struggle/forums/66">BoardGameGeek</a>,
and his last ruling as rules director was written there rather than on Usenet
or the forum: 11&nbsp;June&nbsp;2011. Four rulings cite that thread and its
neighbours. A month later the seat passed to
<strong>Pascal&nbsp;Bertrand</strong> and the rulings moved to the V:EKN forum
for good, so the twelve threads here from after that date &mdash; he still
turns up to answer a question, most recently in December&nbsp;2025 &mdash; are
a former director's and not a sitting one's. They are kept for completeness,
not for citing. BoardGameGeek will not list a forum's threads to anyone without
an API key, so this set came from a search run on the site itself rather than
from a crawl.</p>

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
  usenet.krcg.org/t/<b>KWekwiRSa2I</b>/</pre>
<p>Within a thread, <code>#m0</code> is the first message, <code>#m1</code> the
second, and so on. The <span class="permalink">#</span> beside each author name
is that message's own link.</p>
<p>A forum topic keeps its own number, and every post keeps the number the forum
gave it, so a link into the forum keeps its fragment too and still lands on the
post it was pointing at:</p>
<pre class="rewrite">vekn.net/forum/rules-questions/<b>75512</b>-raptor-obedience<b>#80020</b>
        &darr;
    usenet.krcg.org/t/vekn-<b>75512</b>/<b>#80020</b></pre>
<p>A BoardGameGeek thread translates the same way, except that it writes the
post number into the path rather than the fragment, so that is where the number
is read from:</p>
<pre class="rewrite">boardgamegeek.com/thread/<b>609699</b>/article/<b>6142361</b>
        &darr;
   usenet.krcg.org/t/bgg-<b>609699</b>/<b>#6142361</b></pre>

<h2>Searching it</h2>
<p><a href="../search/">Search</a> covers every word of every message, and the
thread titles and author names besides. The word index is built with the site
and split into small files, one per pair of opening characters, so a search
downloads the words it needs rather than the whole archive &mdash; it works on a
phone, and it works with no server behind it.</p>

<h2>Provenance</h2>
<p>The newsgroup threads come from Google Groups' copy, filled out from
the <a href="https://archive.org/details/usenet-rec">Internet Archive</a>'s copy
where Google's had gaps; the forum topics were read from the forum itself, and
the BoardGameGeek threads from the JSON its own pages are built out of. The
JSON the pages were rendered
from is committed alongside the site generator in the
<a href="https://github.com/vtes-biased/newsgroup-archive">newsgroup-archive</a>
repository, so they can be rebuilt, re-styled, or re-purposed without going back
to any third party.</p>
<p>The posts are the property of their authors and are reproduced here for
reference and preservation.</p>"""
    return page("About — jyhad newsgroup archive", body, depth=1)


def render_search() -> str:
    """The search page: a word index fetched a shard at a time.

    It reads threads.json for the rows it shows and one file per word typed for
    the threads to show, so the first search costs a few KB rather than the
    whole index.
    """
    body = """<nav class="crumbs"><a href="../">Archive</a></nav>
<h1>Search</h1>
<p class="meta">Every word of every message, with thread titles and author
names. A word matches from the start, so <code>temptat</code> finds
<em>temptation</em> and <em>temptations</em>; type at least __MIN__ characters.
Several words narrow the search &mdash; a thread has to hold them all.</p>
<form id="f" onsubmit="return false"><input id="q" type="search"
  placeholder="temptation torpor" autofocus autocomplete="off"
  aria-label="Search the archive"></form>
<p id="status" class="meta">Loading the index&hellip;</p>
<table class="listing" id="results"></table>
<script>
(function () {
  var MIN = __MIN__, CAP = __CAP__, LIMIT = 300;
  var WORD = /[a-z0-9][a-z0-9'-]*/g, MARKS = /[\u0300-\u036f]/g;
  var LIGATURES = [[/æ/g, 'ae'], [/œ/g, 'oe'], [/ß/g, 'ss'], [/ø/g, 'o'],
                   [/đ|ð/g, 'd'], [/ł/g, 'l'], [/þ/g, 'th'], [/ħ/g, 'h']];

  // The same folding the index was built with: accents off, so that what is
  // typed is spelled the way the words in the files are.
  function fold(text) {
    var out = text.toLowerCase(), i;
    for (i = 0; i < LIGATURES.length; i++) {
      out = out.replace(LIGATURES[i][0], LIGATURES[i][1]);
    }
    return out.normalize ? out.normalize('NFKD').replace(MARKS, '') : out;
  }
  var rows = null, shards = {}, latest = 0;
  var status = document.getElementById('status'),
      out = document.getElementById('results'),
      box = document.getElementById('q');

  fetch('../threads.json').then(function (r) { return r.json(); })
    .then(function (d) { rows = d; run(); })
    .catch(function () { status.textContent =
      'Could not load the index. Browse by year instead.'; });

  // One file per two opening characters. A word whose second character is an
  // apostrophe or a hyphen is filed under "_", as the file name has it.
  function shard(word) {
    var name = word.slice(0, 2).replace(/[^a-z0-9]/g, '_');
    if (!shards[name]) {
      shards[name] = fetch('../find/' + name + '.txt').then(function (r) {
        return r.ok ? r.text() : '';
      }).then(function (text) {
        return text ? text.split('\\n') : [];
      }).catch(function () { return []; });
    }
    return shards[name];
  }

  // Every word in the file starting with this one, commonest first: the file
  // is sorted, so they are consecutive, but there can be hundreds of them.
  function lookup(lines, word) {
    var found = [], i;
    for (i = 0; i < lines.length; i++) {
      if (lines[i].lastIndexOf(word, 0) === 0) found.push(lines[i].split(' '));
    }
    found.sort(function (a, b) { return b[1] - a[1]; });
    var chosen = [], exact = null;
    for (i = 0; i < found.length; i++) if (found[i][0] === word) exact = found[i];
    if (exact) chosen.push(exact);
    for (i = 0; i < found.length && chosen.length < CAP; i++) {
      if (found[i] !== exact) chosen.push(found[i]);
    }
    var threads = {};
    for (i = 0; i < chosen.length; i++) {
      var gaps = chosen[i][2].split(','), doc = 0, j;
      for (j = 0; j < gaps.length; j++) {
        doc += +gaps[j];
        threads[doc] = true;
      }
    }
    return { threads: threads, matched: found.length, used: chosen.length };
  }

  function inTitle(title, words) {
    var found = fold(title).match(WORD) || [], i, j;
    for (i = 0; i < words.length; i++) {
      for (j = 0; j < found.length; j++) {
        if (found[j].lastIndexOf(words[i], 0) === 0) return true;
      }
    }
    return false;
  }

  function escape(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  }

  function idle() {
    out.innerHTML = '';
    status.textContent = rows ? rows.length.toLocaleString() +
      ' threads indexed. Type ' + MIN + ' characters to search.' : '';
  }

  function show(words, results) {
    var threads = results[0].threads, i, doc;
    for (i = 1; i < results.length; i++) {
      var next = {};
      for (doc in threads) if (results[i].threads[doc]) next[doc] = true;
      threads = next;
    }
    var hits = [];
    for (doc in threads) hits.push(+doc);
    hits.sort(function (a, b) { return a - b; });
    // Date order alone buries the thread that is plainly about the word under
    // every 1994 post that mentions it in passing, and the 300-row cap then
    // hides it altogether. Threads whose title holds a word come first; within
    // each half the archive's own order stands.
    var titled = [], others = [];
    for (i = 0; i < hits.length; i++) {
      (inTitle(rows[hits[i]][1], words) ? titled : others).push(hits[i]);
    }
    hits = titled.concat(others);

    var note = [];
    for (i = 0; i < results.length; i++) {
      if (results[i].matched > results[i].used) {
        note.push(results[i].used + ' commonest of ' + results[i].matched +
          ' words starting “' + words[i] + '”');
      } else if (!results[i].matched) {
        note.push('no word starts “' + words[i] + '”');
      }
    }
    status.textContent = hits.length.toLocaleString() +
      (hits.length > LIMIT ? ' threads, first ' + LIMIT + ' shown, titles first' :
       ' thread' + (hits.length === 1 ? '' : 's')) +
      (note.length ? ' · ' + note.join('; ') : '') + '.';

    var html = [];
    for (i = 0; i < hits.length && i < LIMIT; i++) {
      var t = rows[hits[i]];
      html.push('<tr><td class="d">' + t[2] + '</td><td><a href="../t/' +
        t[0] + '/">' + escape(t[1]) + '</a></td><td class="n">' + t[4] +
        '</td></tr>');
    }
    out.innerHTML = html.join('');
  }

  function run() {
    if (!rows) return;
    var typed = fold(box.value).match(WORD) || [], words = [], i;
    for (i = 0; i < typed.length; i++) {
      if (typed[i].length >= MIN) words.push(typed[i]);
    }
    if (!words.length) { idle(); return; }
    var mine = ++latest;
    Promise.all(words.map(function (word) {
      return shard(word).then(function (lines) { return lookup(lines, word); });
    })).then(function (results) {
      if (mine === latest) show(words, results);
    });
  }

  box.addEventListener('input', run);
})();
</script>"""
    body = body.replace("__MIN__", str(WORD_MIN)).replace("__CAP__", str(PREFIX_CAP))
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
    #: word -> the threads holding it, numbered in the order they are read here
    #: and renumbered below, once the order threads.json will use is known.
    postings: dict[str, list[int]] = {}

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
        for word in thread_words(thread):
            postings.setdefault(word, []).append(count - 1)
        if count % 2000 == 0:
            print(f"  {count}/{len(paths)} threads")

    for year, threads in by_year.items():
        threads.sort(key=lambda t: t.start)
        write(out / year / "index.html", render_year(year, threads))

    write(out / "index.html", render_index(by_year, total_messages))
    write(out / "about" / "index.html", render_about(len(paths), total_messages))
    write(out / "search" / "index.html", render_search())
    # threads.json is what the search page reads a hit back out of, so a word
    # is filed under a thread's position in it, not the order the files were
    # read in. Sorting is stable, so threads sharing a date keep their order.
    order = sorted(range(len(index)), key=lambda i: index[i][2])
    rank = [0] * len(order)
    for position, original in enumerate(order):
        rank[original] = position
    index = [index[i] for i in order]
    for word, docs in postings.items():
        docs[:] = sorted(rank[doc] for doc in docs)
    write(
        out / "threads.json",
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
    )
    write_search_index(out, postings)
    shutil.copyfile(here / "static" / "site.css", out / "site.css")
    write(out / ".nojekyll", "")
    write(out / "robots.txt", "User-agent: *\nAllow: /\n")
    # The custom domain lives in git, not only in the repository settings, so
    # that it survives a Pages reconfiguration and is obvious to a reader.
    cname = here / "CNAME"
    if cname.exists():
        shutil.copyfile(cname, out / "CNAME")

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
