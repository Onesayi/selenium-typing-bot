"""
Human-like typing bot for browser typing tests: Playwright version.
(typing_bot.py is the same bot on Selenium; the shared logic is in human_typing.py.)

    pip install playwright      # uses your installed Google Chrome; no extra download

    python typing_bot_pw.py --url <any typing site>   # you highlight or click the text, it types
    python typing_bot_pw.py                    # bundled typing_test.html
    python typing_bot_pw.py --site sto         # SpeedTypingOnline lesson 1 (F and J)
    python typing_bot_pw.py --site typingdrill # typingdrill.com practice
    python typing_bot_pw.py --wpm 60 --accuracy 90 --headless

Compared with Selenium, Playwright talks to Chrome directly (no driver program),
so each key goes out in a few milliseconds instead of ~20, and keys go straight
to the focused element.
"""
import functools

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from human_typing import BrowserError, describe, parse_args, run

# Runs one of the shared JavaScript snippets (a function body that uses `return`
# and arguments[n]) the same way Selenium's execute_script does. The function is
# built here rather than with `new Function` in the page, which strict sites block.
def _as_function(body):
    return "(args) => (function () {\n" + body + "\n}).apply(null, args)"


def _wrap(method):
    """Turn Playwright errors into BrowserError with a readable message."""
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except PlaywrightError as e:
            raise BrowserError(describe(e.message)) from None
    return wrapper


class PlaywrightBrowser:
    def __init__(self, headless=False):
        self._pw = sync_playwright().start()
        try:
            # channel="chrome" uses the Google Chrome you already have installed.
            self._browser = self._pw.chromium.launch(
                channel="chrome", headless=headless, args=["--window-size=1400,1000"])
        except PlaywrightError:
            try:   # no Chrome: fall back to Playwright's own Chromium, if it's been installed
                self._browser = self._pw.chromium.launch(headless=headless)
            except PlaywrightError:
                self._pw.stop()
                raise SystemExit("Couldn't start Chrome. Install Google Chrome, or run:\n"
                                 "    python -m playwright install chromium")
        # With a window, let the page fill it; headless gets a fixed desktop-sized page.
        self._context = (self._browser.new_context(viewport={"width": 1400, "height": 900})
                         if headless else self._browser.new_context(no_viewport=True))
        self.page = self._context.new_page()

    @_wrap
    def open(self, url):
        self.page.goto(url, wait_until="domcontentloaded", timeout=45_000)

    @_wrap
    def js(self, body, *args):
        return self.page.evaluate(_as_function(body), list(args))

    @_wrap
    def press(self, key):
        if key == "Backspace":
            self.page.keyboard.press("Backspace")
        else:
            self.page.keyboard.type(key)

    @_wrap
    def click(self, selector):
        el = self.page.locator(selector).first
        el.scroll_into_view_if_needed(timeout=5_000)
        try:
            el.click(timeout=3_000)
        except PlaywrightError:                     # something (a banner) was in the way
            el.evaluate("el => el.focus()")

    @_wrap
    def add_init_script(self, source):
        self._context.add_init_script(source)

    def close(self):
        for step in (self._context.close, self._browser.close, self._pw.stop):
            try:
                step()
            except Exception:
                pass                                # already closed


def main():
    args = parse_args("typing_bot_pw.py")
    run(PlaywrightBrowser(headless=args.headless), args)


if __name__ == "__main__":
    main()
