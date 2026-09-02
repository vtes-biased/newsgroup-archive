// Search: a word index fetched a shard at a time.
//
// The page reads threads.json for the rows it shows and one file per word
// typed for the threads to show, so the first search costs a few KB rather
// than the whole index. Words in quotes are asked of the threads themselves:
// the index files hold no word positions, so the wording is settled by reading
// the candidate pages, which is why that step waits for Enter.
//
// The shortest and longest word the index holds, and how far a prefix is
// widened, are decided in build.py and arrive on the form as data attributes,
// so that this file and the index it reads cannot drift apart.
(function () {
  var form = document.getElementById('f');
  var MIN = +form.dataset.min, MAX = +form.dataset.max,
      CAP = +form.dataset.cap;
  var LIMIT = 300, READ = 60, AT_ONCE = 6;
  var WORD = /[a-z0-9][a-z0-9'-]*/g, MARKS = /[\u0300-\u036f]/g;
  var QUOTES = /[\u201c\u201d\u00ab\u00bb]/g, APOS = /[\u2018\u2019\u02bc]/g;
  var TAGS = /<[^>]*>/g, META = /<p class="meta">[\s\S]*?<\/p>/;
  var LIGATURES = [[/æ/g, 'ae'], [/œ/g, 'oe'], [/ß/g, 'ss'], [/ø/g, 'o'],
                   [/đ|ð/g, 'd'], [/ł/g, 'l'], [/þ/g, 'th'], [/ħ/g, 'h']];

  // The same folding the index was built with: accents off, so that what is
  // typed is spelled the way the words in the files are. A curly apostrophe
  // becomes a straight one first, which the index has and which counts as a
  // letter here, so that don’t is the word don't and not don and t.
  function fold(text) {
    var out = text.toLowerCase().replace(APOS, "'"), i;
    for (i = 0; i < LIGATURES.length; i++) {
      out = out.replace(LIGATURES[i][0], LIGATURES[i][1]);
    }
    return out.normalize ? out.normalize('NFKD').replace(MARKS, '') : out;
  }
  // threads.json, a row per thread: id, title, date, message count,
  // numbered in the order the index files count them.
  var rows = null, shards = {}, pages = {}, cached = 0, latest = 0;
  var status = document.getElementById('status'),
      out = document.getElementById('results'),
      box = document.getElementById('q');

  fetch('../threads.json').then(function (r) { return r.json(); })
    .then(function (d) { rows = d; run(false); })
    .catch(function () { status.textContent =
      'Could not load the index. Browse by year instead.'; });

  // What was typed, cut into terms: the words between one pair of quotes are
  // one term, everything else is a term of its own. A quote that has not been
  // closed yet runs to the end, so the search keeps up while a phrase is still
  // being typed. Only the double marks open a term: the single ones are the
  // apostrophe far more often than they are quotes, in a box this size.
  function parse(text) {
    var parts = fold(text).replace(QUOTES, '"').split('"'), terms = [], i, j;
    for (i = 0; i < parts.length; i++) {
      var found = parts[i].match(WORD) || [];
      if (i % 2) {
        if (found.length) terms.push({ words: found, quoted: true });
      } else {
        for (j = 0; j < found.length; j++) {
          if (found[j].length >= MIN) {
            terms.push({ words: [found[j]], quoted: false });
          }
        }
      }
    }
    return terms;
  }

  // One file per two opening characters. A word whose second character is an
  // apostrophe or a hyphen is filed under "_", as the file name has it.
  function shard(word) {
    var name = word.slice(0, 2).replace(/[^a-z0-9]/g, '_');
    if (!shards[name]) {
      shards[name] = fetch('../find/' + name + '.txt').then(function (r) {
        return r.ok ? r.text() : '';
      }).then(function (text) {
        return text ? text.split('\n') : [];
      }).catch(function () { return []; });
    }
    return shards[name];
  }

  // Every word in the file starting with this one, commonest first: the file
  // is sorted, so they are consecutive, but there can be hundreds of them. A
  // quoted word is taken as it stands instead: widening it would only add
  // threads the wording throws away again, and slowly, one page fetch each.
  function lookup(lines, word, exact) {
    var found = [], i;
    for (i = 0; i < lines.length; i++) {
      if (lines[i].lastIndexOf(word, 0) === 0) found.push(lines[i].split(' '));
    }
    found.sort(function (a, b) { return b[1] - a[1]; });
    var chosen = [], whole = null;
    for (i = 0; i < found.length; i++) if (found[i][0] === word) whole = found[i];
    if (whole) chosen.push(whole);
    if (!exact) {
      for (i = 0; i < found.length && chosen.length < CAP; i++) {
        if (found[i] !== whole) chosen.push(found[i]);
      }
    }
    var threads = {};
    for (i = 0; i < chosen.length; i++) {
      var gaps = chosen[i][2].split(','), doc = 0, j;
      for (j = 0; j < gaps.length; j++) {
        doc += +gaps[j];
        threads[doc] = true;
      }
    }
    return { threads: threads, matched: exact ? chosen.length : found.length,
             used: chosen.length, exact: exact };
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

  // The threads every term is in, in the order they will be shown. Date order
  // alone buries the thread that is plainly about the word under every 1994
  // post that mentions it in passing, and the 300-row cap then hides it
  // altogether. Threads whose title holds a word come first; within each half
  // the archive's own order stands.
  function narrow(words, results) {
    var threads = results[0].threads, i, doc;
    for (i = 1; i < results.length; i++) {
      var next = {};
      for (doc in threads) if (results[i].threads[doc]) next[doc] = true;
      threads = next;
    }
    var hits = [];
    for (doc in threads) hits.push(+doc);
    hits.sort(function (a, b) { return a - b; });
    var titled = [], others = [];
    for (i = 0; i < hits.length; i++) {
      (inTitle(rows[hits[i]][1], words) ? titled : others).push(hits[i]);
    }
    return titled.concat(others);
  }

  // A thread page read back as the words it says, in the order it says them:
  // its title and its messages, which is what the index holds too, and not the
  // header, the crumbs, the line counting the messages or the footer. Every
  // page carries that furniture, and a phrase found in it would be found in
  // all of them. Tags come out next, so that the line ending one element and
  // the line starting the next are not run into one word; what is left goes
  // through the parser for its entities alone, no markup being in it any more.
  function wordsIn(html) {
    var from = html.indexOf('<h1'), to = html.indexOf('</main>');
    if (from < 0) return [];
    var said = html.slice(from, to < 0 ? html.length : to).replace(META, ' ');
    var doc = new DOMParser().parseFromString(said.replace(TAGS, ' '), 'text/html');
    return fold(doc.documentElement.textContent || '').match(WORD) || [];
  }

  function holds(phrase, said) {
    var i, j;
    for (i = 0; i + phrase.length <= said.length; i++) {
      for (j = 0; j < phrase.length; j++) if (said[i + j] !== phrase[j]) break;
      if (j === phrase.length) return true;
    }
    return false;
  }

  // A thread's words are worth keeping while a query is being refined, but not
  // for ever: a long session of phrase searches would otherwise end up holding
  // the archive in memory. Past three hundred threads the lot is dropped.
  function page(id) {
    if (!pages[id]) {
      if (++cached > 300) { pages = {}; cached = 1; }
      pages[id] = fetch('../t/' + id + '/').then(function (r) {
        return r.ok ? r.text() : '';
      }).then(wordsIn).catch(function () { return []; });
    }
    return pages[id];
  }

  // Read the candidate threads and keep the ones that say the phrases. Their
  // pages are a few KB each, but there is no promising how many candidates a
  // common phrase leaves, so READ of them is the most this fetches and the
  // status line says when it stopped there rather than pretending it did not.
  function verify(hits, phrases, mine) {
    var read = Math.min(hits.length, READ), kept = new Array(read);
    var at = 0, running = 0;
    return new Promise(function (resolve) {
      function step() {
        if (mine !== latest) return;
        while (running < AT_ONCE && at < read) {
          (function (slot) {
            running++;
            page(rows[hits[slot]][0]).then(function (said) {
              var i, ok = said.length > 0;
              for (i = 0; ok && i < phrases.length; i++) {
                ok = holds(phrases[i], said);
              }
              kept[slot] = ok ? hits[slot] : -1;
              running--;
              step();
            });
          })(at++);
        }
        if (!running && at >= read) {
          resolve({ hits: kept.filter(function (doc) { return doc >= 0; }),
                    read: read, of: hits.length });
        }
      }
      step();
    });
  }

  function escape(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  }

  function idle() {
    out.innerHTML = '';
    status.textContent = rows ? rows.length.toLocaleString() +
      ' threads indexed. Type ' + MIN + ' characters to search.' : '';
  }

  function count(n, noun) {
    return n.toLocaleString() + ' ' + noun + (n === 1 ? '' : 's');
  }

  function notes(queries, results) {
    var note = [], i;
    for (i = 0; i < results.length; i++) {
      if (!results[i].matched) {
        note.push(results[i].exact ? 'no thread holds \u201c' + queries[i] + '\u201d' :
          'no word starts \u201c' + queries[i] + '\u201d');
      } else if (results[i].matched > results[i].used) {
        note.push(results[i].used + ' commonest of ' + results[i].matched +
          ' words starting \u201c' + queries[i] + '\u201d');
      }
    }
    return note.length ? ' \u00b7 ' + note.join('; ') : '';
  }

  function show(hits, what, tail) {
    var html = [], i;
    status.textContent = count(hits.length, 'thread') + what +
      (hits.length > LIMIT ? ', first ' + LIMIT + ' shown, titles first' : '') +
      tail + '.';
    for (i = 0; i < hits.length && i < LIMIT; i++) {
      var t = rows[hits[i]];
      html.push('<tr><td class="d">' + t[2] + '</td><td><a href="../t/' +
        t[0] + '/">' + escape(t[1]) + '</a></td><td class="n">' + t[3] +
        '</td></tr>');
    }
    out.innerHTML = html.join('');
  }

  function run(checking) {
    if (!rows) return;
    var terms = parse(box.value), queries = [], exact = [], phrases = [], i, j;
    for (i = 0; i < terms.length; i++) {
      if (terms[i].words.length > 1) phrases.push(terms[i].words);
      for (j = 0; j < terms[i].words.length; j++) {
        var word = terms[i].words[j];
        // The index files hold no word shorter than MIN or longer than MAX, so
        // asking them for one answers nothing. A quoted word like that is left
        // to the reading of the pages, which sees every word; a loose one is
        // asked for anyway, so that a search for it says no rather than
        // quietly searching for something else.
        if (word.length < MIN) continue;
        if (terms[i].quoted && word.length > MAX) continue;
        queries.push(word);
        exact.push(terms[i].quoted);
      }
    }
    if (!queries.length) { idle(); return; }
    var mine = ++latest;
    Promise.all(queries.map(function (word, at) {
      return shard(word).then(function (lines) {
        return lookup(lines, word, exact[at]);
      });
    })).then(function (results) {
      if (mine !== latest) return;
      var hits = narrow(queries, results), tail = notes(queries, results);
      if (!phrases.length) { show(hits, '', tail); return; }
      if (!checking) {
        show(hits, ' holding every word', tail +
          ' \u00b7 press Enter for the ones with the wording');
        return;
      }
      status.textContent = 'Reading ' + count(Math.min(hits.length, READ),
        'thread') + '\u2026';
      verify(hits, phrases, mine).then(function (checked) {
        if (mine !== latest) return;
        show(checked.hits, ' with the wording', tail + (checked.of > checked.read ?
          ' \u00b7 the first ' + checked.read + ' of ' +
          checked.of.toLocaleString() + ' threads holding every word were read,' +
          ' so another word in the query would reach further' : ''));
      });
    });
  }

  box.addEventListener('input', function () { run(false); });
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    run(true);
  });
})();
