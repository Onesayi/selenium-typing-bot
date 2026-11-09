# Typing Bot

A Selenium script that does typing tests the way a person would: uneven rhythm,
realistic slips (wrong neighbouring key, swapped or doubled letters), and most
mistakes noticed and fixed with Backspace. You set the speed and accuracy it aims for.

For practice and curiosity only. Don't submit its scores to public leaderboards
or use it for anything that certifies typing speed, such as job application tests.

## What you need

- Windows with Google Chrome
- Python 3 (from python.org, with "Add python.exe to PATH" ticked during setup)
- Selenium: `python -m pip install selenium` (the launcher installs it if missing)

## Step by step: typingdrill.com

1. **Open the project folder** (where `typing_bot.py` is) in File Explorer.
2. **Start the bot.** Either:
   - double-click **`Typing Bot.bat`** and answer the questions:
     - **Address:** `https://typingdrill.com/typing-practice/`
     - **Speed:** press Enter for 85, or type a number
     - **Accuracy:** press Enter for 85, or type a number
     - **Time limit:** just press Enter (the bot stops when the site's timer runs out)
   - **or** open PowerShell in that folder and run:
     `python typing_bot.py --site typingdrill --wpm 85 --accuracy 85`
3. **Wait for Chrome.** A Chrome window opens on the practice page. The terminal says
   "Recognised this site" (if you gave the address) and shows instructions.
4. **Choose your test** in Chrome: length (15s, 30s, 1m, …) and mode (Words,
   Sentences, …). Clicking these buttons doesn't start the bot.
5. **Click on the words.** The bot starts typing about 2 seconds later.
6. **Hands off.** Don't touch the mouse or keyboard while it types.
   **Ctrl+C** in the terminal stops it early.
7. **See the result.** When the timer hits 0 the bot stops, prints the site's score
   in the terminal, and leaves Chrome open for 15 seconds.

Tests of 1 minute or less work best. The page loads about 150 words, so on 5- or
10-minute tests the bot will probably run out of text early.

## Any typing site: manual mode

**Easiest:** double-click **`Typing Bot.bat`** and answer the questions.

**Or from a terminal** (PowerShell or Command Prompt) in this folder:

```
python typing_bot.py --url https://some-typing-site.com
```

Then:

1. A Chrome window opens on that site. Get the test ready, then show the bot the
   text you'd have to type, either way:
   - **highlight all of it** by dragging over it with the mouse, or
   - **if it won't highlight, click once on any word of it.** The bot finds the
     whole block of text around your click (even under an invisible layer), shows
     you the start of it, and asks you to confirm.

   Then press **Enter** in the terminal.
2. **Click where you'd start typing** (the typing box or the text area). The bot
   starts 2 seconds later. Leave the mouse and keyboard alone while it types.
   **Ctrl+C** in the terminal stops it.

It reads the text the way it looks on screen, so sites that draw each word or
letter as a separate box still work.

### Options

| Option | What it does | Default |
|---|---|---|
| `--wpm 70` | Target speed | 85 |
| `--accuracy 90` | Target accuracy, in % (or 0.9) | 85 |
| `--max-seconds 60` | Stop after this long. Use it for timed tests, because the highlighted text may be longer than the test | until the text runs out |

## Built-in sites (fully automatic)

| Command | Site |
|---|---|
| `python typing_bot.py` | `typing_test.html` in this folder (a practice test) |
| `python typing_bot.py --site sto` | SpeedTypingOnline lesson 1 (F and J) |
| `python typing_bot.py --site sto --url <lesson address>` | Another SpeedTypingOnline lesson |
| `python typing_bot.py --site typingdrill` | typingdrill.com typing practice. Pick your test length on the site, then click the words to start |
| `python typing_bot.py --url https://typingdrill.com/` | The free typing test on typingdrill.com's homepage (same steps) |

Giving one of these sites' addresses with `--url` (or in `Typing Bot.bat`) picks its
profile automatically. Add `--headless` to run these without showing a browser window.

## Limits of manual mode

- **Text that appears as you go.** Some tests only show the next few lines and add
  more while you type. The bot only knows what you highlighted, so it stops when that
  runs out. Use a built-in profile, or add one, for that kind of site.
- **Text inside a frame or drawn on a canvas** can't be read, by highlighting or by
  clicking. The bot tells you if nothing was picked up.
- **Clicking picks a whole block of at least 15 words.** If what it finds includes
  extra text (a heading, a button label), answer `n` and highlight the text instead.
- **Cautious mistakes.** On unknown sites it only makes wrong-key and double-key
  slips, and fixes them straight away. Some sites move the cursor in unexpected
  ways when you type the next letter early, so it avoids swap and skip slips there.
- **Detection.** Sites can tell that the browser is automated (Selenium sets
  `navigator.webdriver`), and the bot doesn't hide that.

## Adding a site to run hands-free

Add an entry to `SITES` at the top of `typing_bot.py`. Open the site in Chrome,
press **F12**, and use the element picker to find:

- **Whole text shown up front** (`"mode": "passage"`): a CSS selector matching one
  element per character (`"chars"`), the area to click before typing (`"focus"`),
  and something that exists once the test has loaded (`"ready"`).
- **One highlighted word at a time** (`"mode": "words"`): the active word
  (`"active_word"`) and the input box (`"input"`).
- **Optional:** `"results"` lists the selectors of the score elements, so the bot
  can print them at the end.

Copy the `"sto"` or `"local"` entry as a starting point, then run it with
`--site <your name>`.

## License

MIT. See [LICENSE](LICENSE).
