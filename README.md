# newsgroup-archive

A preserved copy of the Usenet newsgroup **`rec.games.trading-cards.jyhad`**
and of the **V:EKN forum** that succeeded it, published as a static website so
that the [VTES rulings database](https://github.com/vtes-biased/vtes-rulings)
can cite it instead of Google Groups and instead of a forum that loses topics.

**[Browse the archive →](https://usenet.krcg.org/)**

## What is in here

`threads/<year>/<date>_<time>_<ThreadId>.json` — 14,327 threads, 175,925
messages, 1994 to today. Two sources, one rule: every discussion in which one
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

A thread from anywhere but the newsgroup carries a `Group` saying where it came
from. `import_mbox.py`, `import_forum.py` and `sync_forum.py` are what put them
here.

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

## Building the site

```sh
python3 build.py                 # writes _site/
python3 -m http.server -d _site  # look at it
```

Options: `--out DIR`, `--base-url URL` (emits `sitemap.xml`), `--limit N` (build
the first N threads only, for a fast look at a style change).

The site is published to GitHub Pages by `.github/workflows/pages.yml` on every
push to `main`. Generated HTML is not committed.

## Searching it

`/search/` covers every word of every message, with no server behind it. The
build writes a word index to `_site/find/`: one file per pair of opening
characters, each line `word threads gap,gap,gap` — how many threads hold the
word, then their positions in `threads.json`, written as gaps so the numbers
stay short.

Whole, the index is 17 MB (6.5 MB over the wire), which is more than a phone
should have to fetch to search at all. Split this way, a query downloads only
the files its words fall in: a few KB for most of them, 211 KB for `co.txt`,
the worst of the 1,332. Words are matched by prefix, which a sorted file gives
for nothing — the matches are consecutive lines. A prefix that starts more than
ten words is cut to the ten commonest, because `ra` starts 906 of them and
unioning all 906 helps nobody. Accents come off on both sides, so Rötschreck and
Rotschreck are one word; the group spelled it both ways.

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

`--user` takes any member, and it took two to cover the rulings: Ankha, and
Pascal Bertrand, the rules director before him. Nor does the search see quite
everything — it returned 5,609 of the 6,947 posts Ankha's profile counts, and
two cited topics had to be fetched by name with `import_forum.py`. Run with
`--expect`, naming a file of topic numbers, and it says at the end which of
them the archive still has not got.

## Provenance and rights

The newsgroup threads were scraped from Google Groups' copy; the forum topics
were read from the forum itself. Posts are the property of their authors and are reproduced here for reference and
preservation. Author e-mail addresses arrived already partly obscured by Google
(`someone...@example.com`).

Where a message shows *[ quoted text not captured ]*, Google Groups had folded
that quoted passage behind a "show trimmed content" control and the scrape never
saw it. The passage is not lost from the archive — it is in whichever message
was being quoted.
