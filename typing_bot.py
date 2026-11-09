"""
Human-like typing bot for browser typing tests: Selenium version.
(typing_bot_pw.py is the same bot on Playwright; the shared logic is in human_typing.py.)

    pip install selenium        # Selenium 4.6+ downloads chromedriver for you

    python typing_bot.py --url <any typing site>   # you highlight or click the text, it types
    python typing_bot.py                       # bundled typing_test.html
    python typing_bot.py --site sto            # SpeedTypingOnline lesson 1 (F and J)
    python typing_bot.py --site typingdrill    # typingdrill.com practice
    python typing_bot.py --wpm 60 --accuracy 90 --headless

For a site you use a lot you can add an entry to SITES in human_typing.py so it
runs hands-free. See README.md.
"""
import functools

from selenium import webdriver
from selenium.common.exceptions import (ElementClickInterceptedException,
                                        ElementNotInteractableException, WebDriverException)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from human_typing import BrowserError, describe, parse_args, run


def _wrap(method):
    """Turn Selenium errors into BrowserError with a readable message."""
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except WebDriverException as e:
            raise BrowserError(describe(e.msg or str(e))) from None
    return wrapper


class SeleniumBrowser:
    def __init__(self, headless=False):
        options = webdriver.ChromeOptions()
        options.add_argument("--window-size=1400,1000")
        if headless:
            options.add_argument("--headless=new")
        try:
            self.driver = webdriver.Chrome(options=options)
        except WebDriverException as e:
            raise SystemExit(f"Couldn't start Chrome: {describe(e.msg or str(e))}\n"
                             "Check that Google Chrome is installed, then try again.")

    @_wrap
    def open(self, url):
        self.driver.get(url)

    @_wrap
    def js(self, body, *args):
        return self.driver.execute_script(body, *args)

    @_wrap
    def press(self, key):
        ActionChains(self.driver).send_keys(Keys.BACKSPACE if key == "Backspace" else key).perform()

    @_wrap
    def click(self, selector):
        el = self.driver.find_element(By.CSS_SELECTOR, selector)
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        try:
            el.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            self.driver.execute_script("arguments[0].focus();", el)   # something was in the way

    @_wrap
    def add_init_script(self, source):
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": source})

    @_wrap
    def close(self):
        self.driver.quit()


def main():
    args = parse_args("typing_bot.py")
    run(SeleniumBrowser(headless=args.headless), args)


if __name__ == "__main__":
    main()
