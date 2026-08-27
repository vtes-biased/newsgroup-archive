# newsgroup-archive

A preserved copy of the Usenet newsgroup **`rec.games.trading-cards.jyhad`**,
published as a static website so that the
[VTES rulings database](https://github.com/vtes-biased/vtes-rulings) can cite it
instead of Google Groups.

**[Browse the archive →](https://usenet.krcg.org/)**

## What is in here

`threads/<year>/<date>_<time>_<ThreadId>.json` — 10,712 threads, 132,079
messages, 1994 to 2010. This is every thread in which one of the game's rules
directors posted: Thomas R. Wylie (from December 1994), Shawn F. Carnes (July
1996), Jon Wilkie (October 1996) and L. Scott Johnson (June 1998 onward).

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

## Building the site

```sh
python3 build.py                 # writes _site/
python3 -m http.server -d _site  # look at it
```

Options: `--out DIR`, `--base-url URL` (emits `sitemap.xml`), `--limit N` (build
the first N threads only, for a fast look at a style change).

The site is published to GitHub Pages by `.github/workflows/pages.yml` on every
push to `main`. Generated HTML is not committed.

## Provenance and rights

The threads were scraped from Google Groups' copy of the newsgroup. Posts are
the property of their authors and are reproduced here for reference and
preservation. Author e-mail addresses arrived already partly obscured by Google
(`someone...@example.com`).

Where a message shows *[ quoted text not captured ]*, Google Groups had folded
that quoted passage behind a "show trimmed content" control and the scrape never
saw it. The passage is not lost from the archive — it is in whichever message
was being quoted.
