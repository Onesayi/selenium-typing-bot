"""
Shared core of the typing bot: the human typing model, the site profiles, and the
run logic. It doesn't know which automation library drives the browser; that's
the job of a small "browser" object from one of the front-ends:

    typing_bot.py      Selenium
    typing_bot_pw.py   Playwright

A browser object needs these methods (see either front-end for an example):

    open(url)                 load a page
    js(body, *args)           run a JavaScript function body ("return ...", arguments[0], ...)
    press(key)                send one key: a single character, or "Backspace"
    click(selector)           scroll to an element and click it (focus it if something's in the way)
    add_init_script(source)   run this script on every page loaded from now on
    close()

Any failure inside those methods must be raised as BrowserError.
"""
import argparse
import math
import random
import re
import time
from pathlib import Path


class BrowserError(Exception):
    """A browser problem, already described in one readable line."""


# ------------------------------------------------------------------ sites ---
# mode "words":   the page shows one active word at a time and you type into a box.
# mode "passage": the page shows the whole text up front; keys go to a focused area.
# mode "manual":  any site; you show the bot the text and click where you type.
SITES = {
    "local": {
        "url": Path(__file__).with_name("typing_test.html").as_uri(),
        "mode": "words",
        "input": "input#typing-input",
        "active_word": ".word.active",
        "results": [("", "#result")],
    },
    "sto": {   # speedtypingonline.com typing tutor lessons
        "url": "https://www.speedtypingonline.com/typing-tutor/learn-f-j",
        "match": r"speedtypingonline\.com/typing-tutor/",   # --url addresses this profile handles (regex)
        "mode": "passage",
        "focus": "#blockDivContainer",          # click here, then type anywhere
        "chars": ".blockLines span",            # one <span> per character
        "ready": ".nxtLetter",                  # the cursor; present once the lesson is loaded
        "results": [("Speed", "#WPM_Result"), ("Raw speed", "#rWPM_Result"),
                    ("Accuracy", "#Accur_Result"), ("Time", "#time_Result")],
        # Measured: this site's WPM reads ~1.19x the standard (chars / 5 per minute)
        # for the same typing, so the bot aims lower to make the *page's* score hit --wpm.
        "speed_bias": 1.19,
    },
    "typingdrill": {   # typingdrill.com typing practice (timed tests)
        "url": "https://typingdrill.com/typing-practice/",
        # The homepage's free test and the practice page only (not certificates or exams).
        "match": r"typingdrill\.com/?(typing-practice/?)?([?#].*)?$",
        "mode": "passage",
        "chars": "#w-word-display letter",          # one <letter> per character, spaces included
        "ready": "#w-word-display .word",
        "focus_js": "document.getElementById('w-raw-input').focus();",   # invisible input over the text
        # In a visible window you pick the test settings, then click the words to start.
        "start_click": "#w-textbox-wrapper",
        # The page keeps accepting keys after time is up, so stop when the timer hits 0.
        "done_js": "return document.getElementById('w-live-timer').innerText.trim() === '0'"
                   " || !!(document.getElementById('results-panel') || {}).offsetParent;",
        "results": [("Speed", "#w-res-wpm-num"), ("Accuracy", "#w-res-acc")],
        "speed_bias": 1.08,   # measured: the site's WPM reads ~8% above the standard formula
    },
    "manual": {    # any site: you highlight or click the text, then click where you type
        "url": None,
        "mode": "manual",
        "results": [],
    },
}

TARGET_WPM = 85            # the score you want (net: only correctly typed words count)
TARGET_ACCURACY = 0.85     # share of keystrokes that are right (what typing sites report)
NOTICE_RATE = 0.85         # chance a mistake is spotted straight away and fixed
END_OF_WORD_CHECK = 0.5    # chance a missed mistake is caught before pressing space
TEST_SECONDS = 35          # "words" mode: safety stop; the page normally ends the test itself
START_TIMEOUT = 20         # seconds to wait for the page to load

# --------------------------------------------------------- human model ---
# QWERTY neighbours, used to make realistic "fat finger" typos.
NEIGHBOURS = {
    "q": "wa", "w": "qes", "e": "wrd", "r": "etf", "t": "ryg", "y": "tuh", "u": "yij",
    "i": "uok", "o": "ipl", "p": "ol", "a": "qsz", "s": "awdz", "d": "sefx", "f": "drgc",
    "g": "fthv", "h": "gyjb", "j": "hukn", "k": "jilm", "l": "kop", "z": "asx",
    "x": "zsdc", "c": "xdfv", "v": "cfgb", "b": "vghn", "n": "bhjm", "m": "njk",
}
# Bigrams people type fast because they're so frequent / alternate hands.
FAST_BIGRAMS = {"th", "he", "in", "er", "an", "re", "on", "at", "en", "nd",
                "ti", "es", "or", "te", "of", "ed", "is", "it", "al", "ar"}
# Kinds of slip, weighted by how often people make them:
#   substitute: hit a neighbouring key      ("thr" for "the")
#   transpose:  swap two letters            ("teh" for "the")
#   double:     press a key twice           ("thee" for "the")
#   skip:       miss a letter out           ("te" for "the")
MISTAKES = ["substitute"] * 5 + ["transpose"] * 2 + ["double"] * 2 + ["skip"]

SIGMA = 0.35               # spread of the log-normal timing


def keystroke_delay(prev: str, ch: str, base: float) -> float:
    """Seconds to wait before pressing `ch`, given the previous key."""
    # Log-normal: mostly near the mean, occasional slow outliers (like real typists).
    mu = math.log(base) - SIGMA ** 2 / 2
    d = random.lognormvariate(mu, SIGMA)

    if (prev + ch).lower() in FAST_BIGRAMS:
        d *= 0.7
    if prev == " ":
        d *= 1.3                      # small pause starting a new word
    if prev == ch:
        d *= 1.15                     # same finger twice
    if ch.isupper():
        d *= 1.4                      # reaching for shift
    if not ch.isalnum() and ch != " ":
        d *= 1.6                      # punctuation is slower
    if random.random() < 0.015:
        d += random.uniform(0.25, 0.7)  # brief hesitation / "thinking"
    return d


class Typist:
    def __init__(self, press, wpm=TARGET_WPM, accuracy=TARGET_ACCURACY, keep_aligned=False):
        self.send = press                 # function that sends one key to the page
        self.keep_aligned = keep_aligned  # never leave extra/missing letters (passage pages)
        self.base_delay = 60.0 / (wpm * 5)   # mean seconds per keystroke
        self.target_accuracy = accuracy
        self.prev = " "
        self.next_time = time.perf_counter()

        self.word = ""     # word being typed
        self.buf = []      # what we've typed of it so far (our model of the page)
        self.flow = 1.0    # per-word rhythm: some words flow, some stutter

        # Pace controller: pauses and fixes cost time, so scale the delays until
        # the net speed (what the site scores) matches the target WPM.
        # Start faster when we'll be making (and fixing) more mistakes.
        self.scale = 1 / (1 + 3 * (1 - accuracy))
        self.t0 = None
        self.t_last = None
        self.net_chars = 0

        # Accuracy controller: nudge how often we slip until keystroke accuracy
        # (counted the way typing sites do) matches the target.
        self.err_rate = (1 - accuracy) / 1.5
        self.keys_total = 0
        self.keys_correct = 0

    # -- low-level keys -------------------------------------------------------
    def _press(self, key):
        # Schedule against a clock so the browser library's own latency doesn't slow us down.
        now = time.perf_counter()
        if self.next_time > now:
            time.sleep(self.next_time - now)
        self.send(key)
        self.t_last = time.perf_counter()
        if self.t0 is None:
            self.t0 = self.t_last
        self.next_time = max(self.next_time, self.t_last)

    def key(self, ch):
        self.next_time += keystroke_delay(self.prev, ch, self.base_delay) * self.scale * self.flow
        self._press(ch)
        pos = len(self.buf)
        expected = self.word[pos] if pos < len(self.word) else " "
        self.keys_total += 1
        self.keys_correct += ch == expected
        if ch != " ":
            self.buf.append(ch)
        self.prev = ch

    def backspace(self):
        self.next_time += random.uniform(0.07, 0.14) * self.scale   # backspace runs are quick
        self._press("Backspace")
        if self.buf:
            self.buf.pop()
        self.prev = "\b"

    def _on_track(self):
        return self.buf == list(self.word[:len(self.buf)])

    def _fix(self, reaction):
        """Pause (the 'oops' moment), then backspace until the text is right again."""
        self.next_time += random.uniform(*reaction) * self.scale
        while not self._on_track():
            self.backspace()
        return len(self.buf)

    # -- mistakes -------------------------------------------------------------
    def _next_after(self, pos):
        """The character after position `pos` in the current word (a space at the end)."""
        return self.word[pos + 1] if pos + 1 < len(self.word) else " "

    def _mistake(self, i):
        """Slip up at word[i]. Returns the index of the next letter to type."""
        w, ch = self.word, self.word[i]
        pos = len(self.buf)
        kinds, subs, can_double = MISTAKES, NEIGHBOURS.get(ch.lower(), ""), True
        if self.keep_aligned:
            # Passage pages like SpeedTypingOnline treat "typed the letter after the one
            # expected" as a skipped letter and jump the cursor ahead by two. So leave
            # out slips that type the next letter early (transpose, skip), and any wrong
            # key that happens to match it, or the bot and page drift out of step.
            kinds = [k for k in MISTAKES if k in ("substitute", "double")]
            subs = [c for c in subs if c != self._next_after(pos).lower()]
            can_double = i + 1 < len(w) and ch != self._next_after(pos + 1)
        kind = random.choice(kinds)

        if kind == "substitute" and subs:
            wrong = random.choice(subs)
            self.key(wrong.upper() if ch.isupper() else wrong)
            nxt = i + 1
        elif kind == "transpose" and i + 1 < len(w):
            self.key(w[i + 1])
            self.key(ch)
            nxt = i + 2
        elif kind == "skip" and i + 1 < len(w):
            self.key(w[i + 1])
            nxt = i + 2
        elif can_double:
            self.key(ch)
            self.key(ch)
            nxt = i + 1
        elif subs:
            wrong = random.choice(subs)
            self.key(wrong.upper() if ch.isupper() else wrong)
            nxt = i + 1
        else:
            self.key(ch)                                  # no safe slip here; type it right
            return i + 1

        # On passage pages an extra or missing letter shifts everything after it,
        # so those are always noticed straight away (and without running on).
        must_fix = self.keep_aligned and len(self.buf) != nxt
        if not must_fix and random.random() > NOTICE_RATE:
            return nxt                                    # didn't notice; carry on
        # Fingers often run on a key or two before the brain catches up.
        run_on = 0 if must_fix else random.choice([0, 0, 1, 1, 2])
        for c in w[nxt:nxt + run_on]:
            self.key(c)
        return self._fix((0.15, 0.45))

    # -- words ----------------------------------------------------------------
    def type_word(self, word, trailing=" ", final=False):
        """Type one word (with its mistakes and fixes), then `trailing` (usually a space).
        `final`: last word of a passage; pages end the lesson the moment its last letter
        is typed, so no slip there (it couldn't be fixed)."""
        self.word, self.buf = word, []
        self.flow = random.lognormvariate(0, 0.15)
        i, attempt = 0, 0
        while True:
            while i < len(word):
                last_letter = final and i == len(word) - 1
                if attempt < 2 and not last_letter and random.random() < self.err_rate:
                    i = self._mistake(i)
                else:
                    self.key(word[i])
                    i += 1
            attempt += 1
            if self.buf == list(word):
                break
            # On whole-passage pages an extra or missing letter would shift everything
            # after it out of line, so those always get fixed before moving on.
            misaligned = self.keep_aligned and len(self.buf) != len(word)
            if not misaligned and (attempt >= 3 or random.random() > END_OF_WORD_CHECK):
                break
            i = self._fix((0.25, 0.6))                  # re-read the word before moving on
        correct = self.buf == list(word)
        if trailing:
            self.key(trailing)
        if self.keep_aligned:
            # Passage sites score correct characters, not whole words.
            self.net_chars += sum(a == b for a, b in zip(self.buf, word)) + len(trailing)
        elif correct:
            self.net_chars += len(word) + len(trailing)
        self._recalibrate()

    def _recalibrate(self):
        if self.t0 is None:
            return
        elapsed = time.perf_counter() - self.t0
        ideal = self.net_chars * self.base_delay
        if elapsed > 0 and ideal > 0:
            behind = elapsed / ideal                     # >1 means slower than target
            self.scale = min(1.5, max(0.3, self.scale / behind ** 0.8))
        if self.keys_total >= 20:
            diff = self.accuracy - self.target_accuracy  # >0 means too accurate
            self.err_rate = min(0.25, max(0.01, self.err_rate * math.exp(1.5 * diff)))

    @property
    def net_wpm(self):
        if self.t0 is None or self.t_last <= self.t0:
            return 0.0
        return self.net_chars / 5 / ((self.t_last - self.t0) / 60)

    @property
    def accuracy(self):
        return self.keys_correct / self.keys_total if self.keys_total else 1.0


# ------------------------------------------------------- page JavaScript ---
# All snippets are function bodies: they use `return` and read inputs from arguments[n].

EXISTS_JS = "return !!document.querySelector(arguments[0]);"

# The active word, and whether we've already typed it (marked with a data attribute).
ACTIVE_WORD_JS = r"""
const el = document.querySelector(arguments[0]);
if (!el) return null;
const seen = el.dataset.botSeen === '1';
el.dataset.botSeen = '1';
return {text: el.innerText.trim(), seen: seen};
"""

PASSAGE_JS = r"""
return [...document.querySelectorAll(arguments[0])]
  .map(s => s.textContent.replace(/ /g, ' ')).join('');
"""

# Visible text of one element ('' while it's hidden).
VISIBLE_TEXT_JS = r"""
const el = document.querySelector(arguments[0]);
if (!el) return null;
const shown = el.checkVisibility ? el.checkVisibility() : !!el.offsetParent;
return shown ? el.innerText.trim() : '';
"""

# Turns a DOM range into the text it *looks* like: many typing sites draw each word
# (or letter) as its own element with no real spaces between them, so a gap on
# screen or a line break counts as a space too.
EXTRACT_FN = r"""
function extractRange(range) {
  const root = range.commonAncestorContainer;
  const nodes = [];
  if (root.nodeType === Node.TEXT_NODE) nodes.push(root);
  else {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) if (range.intersectsNode(walker.currentNode)) nodes.push(walker.currentNode);
  }
  let out = '', prev = null;
  for (const n of nodes) {
    const start = n === range.startContainer ? range.startOffset : 0;
    const end = n === range.endContainer ? range.endOffset : n.textContent.length;
    if (end <= start) continue;
    const el = n.parentElement, cs = el && getComputedStyle(el);
    if (!cs || cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = document.createRange(); r.setStart(n, start); r.setEnd(n, end);
    const rects = [...r.getClientRects()].filter(x => x.width > 0);
    if (!rects.length) continue;                       // collapsed/invisible whitespace
    const s = n.textContent.slice(start, end);
    const first = rects[0];
    if (prev && !/\s$/.test(out) && !/^\s/.test(s)) {
      const fs = parseFloat(cs.fontSize) || 16;
      if (first.top >= prev.bottom - 2 || first.left - prev.right > fs * 0.2) out += ' ';
    }
    out += s;
    prev = rects[rects.length - 1];
  }
  return out.replace(/\s+/g, ' ').trim();
}
"""

SELECTION_JS = EXTRACT_FN + r"""
const sel = window.getSelection();
if (!sel || !sel.rangeCount || sel.isCollapsed) return '';
return extractRange(sel.getRangeAt(0));
"""

# Remembers where you last clicked, on every page the browser loads.
TRACK_CLICKS_JS = r"""
if (!window.__botTrack) {
  window.__botTrack = true;
  addEventListener('pointerdown', e => {
    window.__botLastClick = [e.clientX + scrollX, e.clientY + scrollY];
  }, true);
}
"""

# For text that can't be highlighted: find the text under your last click, even if
# it sits beneath an invisible overlay, then grow outwards to the whole block of text.
TEXT_AT_CLICK_JS = EXTRACT_FN + r"""
const c = window.__botLastClick;
if (!c) return {error: 'noclick'};
const x = c[0] - scrollX, y = c[1] - scrollY;
const shown = el => !el.checkVisibility || el.checkVisibility({opacityProperty: true, visibilityProperty: true});
const hits = new Set();
const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
while (walker.nextNode()) {
  const n = walker.currentNode, el = n.parentElement;
  if (!el || !n.textContent.trim() || !shown(el)) continue;
  const r = document.createRange(); r.selectNodeContents(n);
  for (const b of r.getClientRects())
    if (x >= b.left - 3 && x <= b.right + 3 && y >= b.top - 3 && y <= b.bottom + 3) { hits.add(el); break; }
}
if (!hits.size) return {error: 'nothing'};
const blocks = [];
for (const el of hits) {
  for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
    const r = document.createRange(); r.selectNodeContents(e);
    const t = extractRange(r);
    if (t.split(' ').length >= 15) { blocks.push([e, t]); break; }
  }
}
if (!blocks.length) return {error: 'short'};
// Prefer the tightest block: drop any that contain another candidate.
const inner = blocks.filter(([e]) => !blocks.some(([o]) => o !== e && e.contains(o)));
inner.sort((a, b) => b[1].length - a[1].length);
return {text: inner[0][1]};
"""

CLICK_WATCH_JS = r"""
const sel = arguments[0];
if (window.__botClicked === undefined) {
  window.__botClicked = false;
  const hit = e => { if (!sel || (e.target.closest && e.target.closest(sel))) window.__botClicked = true; };
  // pointerdown too: some sites cancel it, and then the browser never sends mousedown.
  for (const type of ['pointerdown', 'mousedown', 'click']) document.addEventListener(type, hit, true);
}
return window.__botClicked;
"""


# ------------------------------------------------------------------- run ---
def wait_ready(b, selector, timeout=START_TIMEOUT):
    deadline = time.perf_counter() + timeout
    while not b.js(EXISTS_JS, selector):
        if time.perf_counter() > deadline:
            raise BrowserError("the typing test didn't appear on the page in time.")
        time.sleep(0.25)


def run_words(b, site, typist, limit=TEST_SECONDS):
    """Pages that highlight one word at a time (like typing_test.html)."""
    start = time.perf_counter()
    while time.perf_counter() - start < limit:
        try:
            active = b.js(ACTIVE_WORD_JS, site["active_word"])
        except BrowserError:
            break
        if not active or not active["text"]:
            break                                   # test finished / page changed
        if active["seen"]:
            time.sleep(0.05)                        # site hasn't advanced yet
            continue
        try:
            typist.type_word(active["text"])
        except BrowserError:
            break                                   # input got disabled when time ran out


def read_passage(b, site):
    """Read the whole text from a page that shows it up front (like SpeedTypingOnline)."""
    text = b.js(PASSAGE_JS, site["chars"])
    return text.rstrip(" ") + (" " if text.endswith(" ") else "")


def type_passage(typist, text, label="Lesson", max_seconds=None, is_done=None):
    words = text.split(" ")
    print(f"{label}: {len(text)} characters, {len([w for w in words if w])} words.")
    last_index = max(n for n, w in enumerate(words) if w)
    start = time.perf_counter()
    for n, word in enumerate(words):
        if max_seconds and time.perf_counter() - start >= max_seconds:
            print(f"Stopped after {max_seconds:g}s (--max-seconds).")
            break
        if is_done and n and is_done():
            print("The test has ended.")
            break
        if not word:
            continue
        try:
            typist.type_word(word, trailing="" if n == len(words) - 1 else " ",
                             final=n == last_index)
        except BrowserError:
            break


def wait_for_click(b, selector=None, timeout=300):
    """Block until the user clicks in the page (inside `selector`, if given)."""
    b.js("window.__botClicked = undefined;")
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if b.js(CLICK_WATCH_JS, selector):        # re-arms itself if the page reloaded
            return
        time.sleep(0.2)
    raise SystemExit(f"No click in the page after {timeout // 60} minutes; giving up.")


def track_clicks(b):
    """Remember the last click position, now and on every page loaded later."""
    try:
        b.add_init_script(TRACK_CLICKS_JS)
    except BrowserError:
        pass
    b.js(TRACK_CLICKS_JS)


CLICK_ERRORS = {
    "noclick": "Nothing is highlighted and you haven't clicked in the page yet.",
    "nothing": "Your last click wasn't on any text. Click right on a word (not the gap between words).",
    "short": "The text you clicked is too short to be the test (under 15 words). "
             "Try highlighting it instead.",
}


def read_test_text(b):
    """Step 1 of manual mode: get the text from a highlight, or from a click on it."""
    while True:
        input("        Press Enter here when you've done that... ")
        text = b.js(SELECTION_JS)
        if text:
            return text
        found = b.js(TEXT_AT_CLICK_JS) or {}
        if found.get("text"):
            text = found["text"]
            preview = text if len(text) <= 70 else text[:67] + "..."
            print(f"        Found {len(text.split())} words by your click: {preview!r}")
            answer = input("        Is that the text to type? [Enter = yes, n = try again] ")
            if not answer.strip().lower().startswith("n"):
                return text
            print("        OK. Click a word in a different place, or highlight the text instead.")
            continue
        print("        " + CLICK_ERRORS.get(found.get("error"), "Couldn't read any text.")
              + "\n        (Text inside a frame or drawn on a canvas can't be read.)")


def manual_setup(b):
    """Any site: you show the bot the text and click where you type; it does the rest."""
    track_clicks(b)
    print("\nStep 1: In the Chrome window, get the test ready to start (close pop-ups,")
    print("        pick a test, etc.). Then show the bot the text you'd have to type:")
    print("          - highlight ALL of it by dragging over it with the mouse, or")
    print("          - if it won't highlight, just click once on any word of it.")
    text = read_test_text(b)
    preview = text if len(text) <= 70 else text[:67] + "..."
    print(f"        Got {len(text)} characters: {preview!r}")

    print("\nStep 2: Click where you'd start typing (the typing box or the text area).")
    print("        The bot starts typing 2 seconds after your click. Don't touch the")
    print("        mouse or keyboard while it types (Ctrl+C here stops it).")
    wait_for_click(b)
    time.sleep(2)
    return text


def read_results(b, site, wait_s=8):
    """Read the score once it has settled (some sites animate it counting up)."""
    if not site["results"]:
        return []
    deadline = time.perf_counter() + wait_s
    last, stable_since = None, None
    while True:
        lines = []
        for label, sel in site["results"]:
            txt = b.js(VISIBLE_TEXT_JS, sel)
            if txt and txt != "0":
                lines.append(f"{label}: {txt}" if label else txt)
        now = time.perf_counter()
        if lines != last:
            last, stable_since = lines, now
        elif lines and now - stable_since >= 1.0:
            return lines
        if now > deadline:
            return lines
        time.sleep(0.25)


NET_ERRORS = {
    "ERR_NAME_NOT_RESOLVED": "no website exists at that address. Check the spelling.",
    "ERR_INTERNET_DISCONNECTED": "you're not connected to the internet.",
    "ERR_CONNECTION_REFUSED": "the site refused the connection. Check the address.",
    "ERR_CONNECTION_TIMED_OUT": "the site didn't respond. Try again later.",
    "ERR_TIMED_OUT": "the site didn't respond. Try again later.",
    "ERR_CERT_": "the site's security certificate isn't valid.",
    "ERR_ADDRESS_UNREACHABLE": "the site can't be reached from this network.",
}


def describe(msg):
    """One readable line for a browser error message, instead of a traceback."""
    msg = (msg or "").strip()
    for code, text in NET_ERRORS.items():
        if code in msg:
            return text
    if "invalid argument" in msg or "Cannot navigate to invalid URL" in msg:
        return "that doesn't look like a web address."
    if any(s in msg for s in ("no such window", "invalid session id", "not reachable",
                              "has been closed", "Target closed")):
        return "the Chrome window was closed."
    if "Timeout" in msg and "exceeded" in msg:
        return "the page took too long to load."
    first = msg.splitlines()[0] if msg else "unknown error"
    return first.replace("Message: ", "").replace("unknown error: ", "")


# ------------------------------------------------------------------- CLI ---
def parse_args(prog):
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Human-like typing bot",
        epilog=f"Any site: python {prog} --url <address>  (you highlight or click the text, "
               "then click where you type)")
    parser.add_argument("--site", choices=SITES,
                        help="a built-in site profile; default is 'manual' if --url is given, "
                             "otherwise 'local'")
    parser.add_argument("--url", help="page to open (for --site sto: a different lesson)")
    parser.add_argument("--wpm", type=float, default=TARGET_WPM, help="target speed (default %(default)g)")
    parser.add_argument("--accuracy", type=float, default=TARGET_ACCURACY,
                        help="target keystroke accuracy, 0-1 or a percentage (default %(default)g)")
    parser.add_argument("--max-seconds", type=float,
                        help="stop typing after this long (for timed tests, e.g. 60)")
    parser.add_argument("--headless", action="store_true", help="no browser window (not for manual)")
    args = parser.parse_args()
    if args.accuracy > 1:
        args.accuracy /= 100                  # allow --accuracy 85

    site_name = args.site
    if not site_name and args.url:
        # A known site's address picks its profile automatically; anything else is manual.
        bare = args.url.split("://")[-1].removeprefix("www.")
        site_name = next((n for n, s in SITES.items() if s.get("match") and re.match(s["match"], bare)),
                         "manual")
        if site_name != "manual":
            print(f"Recognised this site; using the '{site_name}' profile.")
    args.site = site_name or "local"
    site = SITES[args.site]
    args.url = args.url or site["url"]
    if site["mode"] == "manual":
        if args.headless:
            parser.error("manual mode needs a visible browser; drop --headless")
        if not args.url:
            args.url = input("Typing test address (URL): ").strip()
        if "://" not in args.url:
            args.url = "https://" + args.url
    return args


def run(b, args, on_finish=None):
    """Do one typing test in browser `b`, as described by the parsed `args`.
    `on_finish(b)`, if given, runs after the results are read and before the browser closes."""
    site = SITES[args.site]
    try:
        try:
            b.open(args.url)
        except BrowserError as e:
            raise SystemExit(f"Couldn't open {args.url}: {e}")
        text = None
        if site["mode"] == "manual":
            text = manual_setup(b)
        else:
            wait_ready(b, site.get("ready") or site.get("active_word"))
            if site.get("start_click") and not args.headless:
                print("\nIn the Chrome window, choose your test settings (length, mode, etc.),")
                print("then click on the words. The bot starts typing 2 seconds after that click.")
                print("Don't touch the mouse or keyboard while it types (Ctrl+C here stops it).")
                wait_for_click(b, site["start_click"])
                time.sleep(0.5)
            if site.get("input"):
                b.click(site["input"])
            elif site.get("focus"):
                b.click(site["focus"])
            if site.get("focus_js"):
                b.js(site["focus_js"])
            if site["mode"] == "passage":
                text = read_passage(b, site)        # read after any settings change
            time.sleep(random.uniform(0.8, 1.5))   # humans don't start instantly

        bias = site.get("speed_bias", 1.0)
        # Unknown sites get the cautious mistake model too (see Typist._mistake).
        typist = Typist(b.press, wpm=args.wpm / bias, accuracy=args.accuracy,
                        keep_aligned=site["mode"] != "words")
        start = time.perf_counter()
        if site["mode"] == "words":
            run_words(b, site, typist, limit=args.max_seconds or TEST_SECONDS)
        else:
            is_done = (lambda: b.js(site["done_js"])) if site.get("done_js") else None
            type_passage(typist, text, "Lesson" if site["mode"] == "passage" else "Text",
                         max_seconds=args.max_seconds, is_done=is_done)

        elapsed = time.perf_counter() - start
        print(f"Typed for {elapsed:.1f}s. Target {args.wpm:g} WPM / {args.accuracy:.0%} accuracy; "
              f"bot measured {typist.net_wpm * bias:.0f} WPM / {typist.accuracy:.1%}"
              f"{' (site-adjusted)' if bias != 1.0 else ''}.")
        results = read_results(b, site)
        if results:
            print("Page says:\n" + "\n".join(results))
        if on_finish:
            on_finish(b)
        if site["mode"] == "manual":
            input("Check the result in Chrome, then press Enter here to close it... ")
        elif not args.headless:
            print("Leaving the browser open for 15s so you can see it.")
            time.sleep(15)
    except KeyboardInterrupt:
        print("\nStopped.")
    except BrowserError as e:
        print(f"\nStopped: {e}")
    finally:
        try:
            b.close()
        except BrowserError:
            pass                                  # the browser is already gone
