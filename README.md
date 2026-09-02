# newsgroup-archive

A preserved copy of the Usenet newsgroup **`rec.games.trading-cards.jyhad`**
and of the **V:EKN forum** that succeeded it, published as a static website so
that the [VTES rulings database](https://github.com/vtes-biased/vtes-rulings)
can cite it instead of Google Groups and instead of a forum that loses topics.

**[Browse the archive →](https://usenet.krcg.org/)**

## What is in here

`threads/<year>/<date>_<time>_<ThreadId>.json` — 14,384 threads, 176,226
messages, 1994 to today. Three sources, one rule: every discussion in which one
of the game's rules directors took part.

- **The newsgroup**, 1994 to 2010, where Thomas R. Wylie (from December 1994),
  Shawn F. Carnes (July 1996), Jon Wilkie (October 1996) and L. Scott Johnson
  (June 1998 onward) answered. Several hundred of these threads are from
  `rec.games.deckmaster`, where Jyhad was discussed before it had a group of
  its own.
- **The V:EKN forum**, from 2010 on: 3,605 topics, every one that Ankha or
  Pascal Bertrand before him posted in, close to half of them rules questions.
  The forum drops topics of its own accord — the one the Baltimore Purge ruling
  rests on returns a 404 today, and survives only because the Wayback Machine
  kept it. Two topics are here for the other reason: a ruling cites them, even
  though neither director posted in them.
- **BoardGameGeek**, 2009 to 2025: 57 threads, every one in the game's Rules
  forum there that L. Scott Johnson took part in. As Usenet wound down he
  answered questions on this third site instead, and his last ruling as rules
  director was posted there, on 11 June 2011; four rulings cite it and its
  neighbours. A month later the seat passed to Pascal Bertrand and the rulings
  moved to the V:EKN forum, so nothing here from after that is a director's
  answer — 45 of the 57 threads are from his tenure, and the other 12 are kept
  for completeness, not for citation.

A thread from anywhere but the newsgroup carries a `Group` saying where it came
from. `import_mbox.py`, `import_forum.py`, `sync_forum.py` and `import_bgg.py` are
what put them here.

Each file is one thread:

```json
{
  "ThreadId": "KWekwiRSa2I",
  "Url": "https://groups.google.com/g/rec.games.trading-cards.jyhad/c/KWekwiRSa2I",
  "Title": "Camarilla Exemplary Question",
  "Messages": [{"Author": "...", "Date": "Jul 6, 1997, 9:00:00 AM", "Body": "..."}]
}
```

The JSON is the archive. The website is a rendering of it, rebuilt on every
push; nothing about the content depends on the generator surviving.

## Design principle

Same as the rulings database it serves: **the data is a pile of plain text
files, usable without any tooling**. `build.py` is standard library only — no
dependencies, no lockfile, no build system to rot. If it ever stops running,
the threads are still readable with `cat`.

## URLs

Thread pages keep the identifier Google Groups gave them, so any surviving link
into the old archive translates mechanically:

```
groups.google.com/g/rec.games.trading-cards.jyhad/c/KWekwiRSa2I
    → usenet.krcg.org/t/KWekwiRSa2I/
```

Within a thread, `#m0` is the first message, `#m1` the second, and so on. These
anchors are positional and therefore stable as long as the JSON is not
reordered — treat the message order in a thread file as part of the contract.

Google's per-message ids (the `/m/<id>` part of an old URL) are **not** recorded
anywhere in the scrape and cannot be recovered offline, so they do not map to
anchors. A citation that used one resolves to the thread, and the right message
has to be identified by author and date.

A forum topic keeps its number the same way, prefixed so it can never be taken
for one of Google's, and every forum message keeps the number the forum gave it
in an `Id` — which the page carries as a second anchor. A link into the forum
therefore keeps its fragment too:

```
vekn.net/forum/rules-questions/75512-raptor-obedience#80020
    → usenet.krcg.org/t/vekn-75512/#80020
```

A BoardGameGeek thread translates the same way, except that it writes the post
number into the path rather than the fragment:

```
boardgamegeek.com/thread/609699/article/6142361
    → usenet.krcg.org/t/bgg-609699/#6142361
```

Two things to know when rewriting forum links in bulk: match on the topic
number and the post number alone, ignoring the category segment
(`/6-rules-questions/` is a category, not a topic) and the whole query string
(`?limit=10&start=20` appears in the wild).

## Building the site

```sh
python3 build.py                 # writes _site/
python3 -m http.server -d _site  # look at it
```

`site.css` and `search.js`, in `static/`, are copied to the site as they
stand; `search.js` is what `/search/` runs. Everything else is written by
`build.py`, including a `404.html` — the links on it are absolute, because the
server hands it out under whatever path was asked for.

Options: `--out DIR`, `--base-url URL` (emits `sitemap.xml`), `--limit N` (build
the first N threads only, for a fast look at a style change).

The site is published to GitHub Pages by `.github/workflows/pages.yml` on every
push to `main`, and by the nightly forum sweep, which pushes with a token that
starts no workflow of its own and so asks for the build directly. Generated
HTML is not committed.

## Searching it

`/search/` covers every word of every message, with no server behind it. The
build writes a word index to `_site/find/`: one file per pair of opening
characters, each line `word threads gap,gap,gap` — how many threads hold the
word, then their positions in `threads.json`, written as gaps so the numbers
stay short.

Whole, the index is 17 MB (6.5 MB over the wire), which is more than a phone
should have to fetch to search at all. Split this way, a query downloads only
the files its words fall in: a few KB for most of them, 216 KB for `co.txt`,
the worst of the 1,332. Words are matched by prefix, which a sorted file gives
for nothing — the matches are consecutive lines. A prefix that starts more than
ten words is cut to the ten commonest, because `ra` starts 906 of them and
unioning all 906 helps nobody. Accents come off on both sides, so Rötschreck and
Rotschreck are one word; the group spelled it both ways.

Quotes ask for a phrase, `"the vampire is burned"`, which the index alone
cannot answer: it holds no word positions, and adding them would multiply its
size. The words are looked up as they stand instead — no prefix widening, which
would only add threads the wording throws away again — and Enter then fetches
the candidate pages, strips the markup and keeps the threads whose words fall
in that order. Sixty pages is as far as it goes, and the status line says so
when it stops there. Line wrapping does not hide a phrase: the check reads a
page as the words it says, so a sentence broken across two lines of a Usenet
post still matches.

## Keeping the forum copy current

```sh
python3 sync_forum.py --list      # say what would be fetched, fetch nothing
python3 sync_forum.py             # fetch what the archive has not got
python3 sync_forum.py --user "Pascal Bertrand"   # or any other member
python3 sync_forum.py --refresh   # fetch every topic again, replies and all
```

The forum publishes no listing of a member's topics — the one on their profile
shows the six most recent — so its search does the enumerating instead:
`searchuser=ankha` returns every post they wrote, newest first, a hundred to a
page, and each result names the topic it is in. Sixty requests give the whole
list; the topics are then fetched one a second. The first run took two hours
for 2,861 topics, a run that finds nothing new takes a minute.

What a plain run cannot see is a reply written after that member's last post in
a topic; `--refresh` is for that.

`.github/workflows/sync.yml` does all this every night at 4:17 UTC, for both
members, and commits whatever it found; the site rebuilds behind it. Most
nights it finds nothing and commits nothing. The Actions tab runs it on demand
and offers a `refresh` box for the slow kind of run.

Two things about the nightly one. A sweep whose search the forum would not let
finish fails rather than reporting a quiet night, because from the outside
those look the same and only one of them means the archive is current. And
GitHub switches a scheduled workflow off after sixty days with no commit to the
repository — so a quiet spell long enough would stop the sweep that would have
ended it. The Actions tab hands it back with a button.

`--user` takes any member, and it took two to cover the rulings: Ankha, and
Pascal Bertrand, the rules director before him. Nor does the search see quite
everything — it returned 5,609 of the 6,947 posts Ankha's profile counts, and
two cited topics had to be fetched by name with `import_forum.py`. Run with
`--expect`, naming a file of topic numbers, and it says at the end which of
them the archive still has not got.

## The BoardGameGeek threads

```sh
python3 import_bgg.py $(cat bgg-threads.txt)   # all 57, fetched whole
python3 import_bgg.py 662413                   # or any one of them
```

Threads are named, not discovered, and re-running a number rewrites the thread
with whatever has been added to it since. There is no sync script because there
is nothing to enumerate: BoardGameGeek's public XML API wants a key now and its
HTML sits behind a bot check, so `bgg-threads.txt` came from an advanced search
run on the site itself, for the threads L. Scott Johnson posted in. What is
open is the JSON BoardGameGeek's own pages read, and that is what the importer
uses — `api.geekdo.com/api/threads/<id>` for the subject,
`/api/articles?threadid=<id>` for the posts.

## Provenance and rights

The newsgroup threads were scraped from Google Groups' copy; the forum topics
were read from the forum itself, and the BoardGameGeek threads from the JSON
its own pages are built out of. Posts are the property of their authors and are reproduced here for reference and
preservation. Author e-mail addresses arrived already partly obscured by Google
(`someone...@example.com`).

Where a message shows *[ quoted text not captured ]*, Google Groups had folded
that quoted passage behind a "show trimmed content" control and the scrape never
saw it. The passage is not lost from the archive — it is in whichever message
was being quoted.
