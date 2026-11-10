"""
Keystroke-dynamics study: can a simple classifier tell real typing from the bot?

    python keystroke_study.py record human          # you type a test in Chrome; the log is saved
    python keystroke_study.py record bot --runs 5   # the bot types the same test (hidden window)
    python keystroke_study.py analyze               # features + classifiers -> results/report.md

Logs are JSON files in data/<label>/ (the label is "human" or "bot" unless you
pass --label). You can also open typing_test.html in any browser, type the test,
click "Download keystroke log", and put the file in data/human/.

By default the bot is recorded at the median speed and accuracy of your human
sessions, so the classifier can't just learn "the bot is faster".

Human keystroke timing is personal (it can identify you), so data/ is in
.gitignore and stays on your computer unless you choose to share it.
"""
import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "typing_test.html"
WINDOW_SIZES = [10, 25, 50, 100]          # keystrokes per sample
MIN_SAMPLES_PER_LABEL = 10                # fewer than this and a score is mostly noise


# ------------------------------------------------------------- recording ---
def page_url(seconds):
    return PAGE.as_uri() + f"?seconds={seconds}"


def save_log(log, data_dir, label, source):
    folder = data_dir / label
    folder.mkdir(parents=True, exist_ok=True)
    log["label"], log["source"] = label, source
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"{stamp}.json"
    n = 1
    while path.exists():
        n += 1
        path = folder / f"{stamp}-{n}.json"
    path.write_text(json.dumps(log), encoding="utf-8")
    downs = sum(1 for e in log["events"] if e["type"] == "down")
    print(f"Saved {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}: "
          f"{downs} key presses, {log['wpm']} WPM, {log['accuracy']}% accuracy.")
    return path


def make_browser(engine, headless):
    if engine == "selenium":
        from typing_bot import SeleniumBrowser
        return SeleniumBrowser(headless=headless)
    from typing_bot_pw import PlaywrightBrowser
    return PlaywrightBrowser(headless=headless)


def record_human(a):
    from human_typing import BrowserError
    print(f"Opening a {a.seconds}-second test in Chrome. Type it the way you normally do;")
    print("the timer starts on your first key. The log is saved when the time runs out.")
    print("(Chrome will say it's being controlled by automated software: that's this")
    print("script waiting to collect the log. Close the window to cancel.)")
    b = make_browser(a.engine, headless=False)
    try:
        b.open(page_url(a.seconds))
        while True:
            log = b.js("return window.__keylog || null;")
            if log:
                save_log(log, a.data, a.label or "human", source="human")
                break
            time.sleep(0.5)
        time.sleep(2)
    except BrowserError as e:
        print(f"Stopped: {e}")
    except KeyboardInterrupt:
        print("\nCancelled.")
    finally:
        try:
            b.close()
        except BrowserError:
            pass


def human_baseline(data_dir):
    """Median WPM and accuracy of the recorded human sessions, if there are any."""
    logs = [json.loads(f.read_text(encoding="utf-8")) for f in (data_dir / "human").glob("*.json")]
    logs = [g for g in logs if g.get("format") == "keylog-v1" and g.get("wpm")]
    if not logs:
        return None
    return statistics.median(g["wpm"] for g in logs), statistics.median(g["accuracy"] for g in logs) / 100


def record_bot(a):
    import human_typing as ht
    wpm, acc = a.wpm, a.accuracy
    if wpm is None or acc is None:
        base = human_baseline(a.data)
        if base:
            print(f"Matching your human sessions: {base[0]:.0f} WPM, {base[1]:.0%} accuracy.")
        base = base or (ht.TARGET_WPM, ht.TARGET_ACCURACY)
        wpm = wpm or base[0]
        acc = acc or base[1]
    if acc > 1:
        acc /= 100
    for i in range(a.runs):
        print(f"\nBot run {i + 1} of {a.runs} ({a.engine}, {wpm:.0f} WPM, {acc:.0%} accuracy, {a.seconds}s)")
        args = argparse.Namespace(site="local", url=page_url(a.seconds), wpm=wpm, accuracy=acc,
                                  max_seconds=a.seconds + 5, headless=True)
        captured = {}
        ht.run(make_browser(a.engine, headless=True), args,
               on_finish=lambda b: captured.update(log=b.js("return window.__keylog || null;")))
        if captured.get("log"):
            save_log(captured["log"], a.data, a.label or "bot", source=f"bot-{a.engine}")
        else:
            print("No log came back from this run.")


# -------------------------------------------------------------- features ---
def keystrokes(events):
    """Pair each key press with its release. Returns [{t, up, key, ok}], in press order."""
    presses, waiting = [], {}
    for e in events:
        ident = e.get("code") or e["key"]
        if e["type"] == "down":
            if e.get("repeat"):
                continue                               # auto-repeat from holding a key down
            presses.append({"t": e["t"], "up": None, "key": e["key"], "ok": e.get("ok")})
            waiting[ident] = len(presses) - 1
        else:
            i = waiting.pop(ident, None)
            if i is not None:
                presses[i]["up"] = e["t"]
    return presses


def _mean(x):
    return float(np.mean(x)) if len(x) else np.nan


def _skew(x):
    x = np.asarray(x, float)
    if len(x) < 3 or x.std() == 0:
        return np.nan
    return float(np.mean(((x - x.mean()) / x.std()) ** 3))


FEATURES = {
    # name: plain-English description (for the report)
    "flight_mean": "average time between key presses (ms)",
    "flight_median": "median time between key presses (ms)",
    "flight_std": "spread of the time between presses (ms)",
    "flight_cv": "spread relative to average (coefficient of variation)",
    "flight_iqr": "middle-50% range of the time between presses (ms)",
    "flight_p10": "fastest 10% of gaps between presses (ms)",
    "flight_p90": "slowest 10% of gaps between presses (ms)",
    "flight_skew": "lopsidedness of the (log) gap distribution",
    "flight_autocorr": "how much one gap predicts the next (lag-1 correlation)",
    "dwell_mean": "average time a key is held down (ms)",
    "dwell_std": "spread of key hold times (ms)",
    "dwell_cv": "hold-time spread relative to average",
    "overlap_rate": "share of presses made before the previous key was released",
    "backspace_rate": "share of presses that are Backspace",
    "error_rate": "share of typed characters that were wrong",
    "fix_latency": "time from a wrong key to the Backspace that fixes it (ms)",
    "space_pause_ratio": "gap before starting a word vs gap inside a word",
}


def window_features(ks):
    t = np.array([k["t"] for k in ks], float)
    flight = np.diff(t)
    flight = flight[flight > 0]
    flight_c = np.clip(flight, 0, 2000)                  # long pauses would swamp the averages
    dwell = np.array([k["up"] - k["t"] for k in ks if k["up"] is not None and k["up"] >= k["t"]], float)
    f = dict.fromkeys(FEATURES, np.nan)
    if len(flight_c):
        f["flight_mean"] = _mean(flight_c)
        f["flight_median"] = float(np.median(flight_c))
        f["flight_std"] = float(flight_c.std())
        f["flight_cv"] = f["flight_std"] / f["flight_mean"] if f["flight_mean"] else np.nan
        q10, q25, q75, q90 = np.percentile(flight_c, [10, 25, 75, 90])
        f["flight_iqr"], f["flight_p10"], f["flight_p90"] = q75 - q25, q10, q90
        lf = np.log(np.clip(flight, 1, None))
        f["flight_skew"] = _skew(lf)
        if len(lf) > 4 and lf[:-1].std() > 0 and lf[1:].std() > 0:
            f["flight_autocorr"] = float(np.corrcoef(lf[:-1], lf[1:])[0, 1])
    if len(dwell):
        f["dwell_mean"], f["dwell_std"] = _mean(dwell), float(dwell.std())
        f["dwell_cv"] = f["dwell_std"] / f["dwell_mean"] if f["dwell_mean"] > 0 else np.nan
    pairs = [(a, b) for a, b in zip(ks, ks[1:]) if a["up"] is not None]
    if pairs:
        f["overlap_rate"] = sum(b["t"] < a["up"] for a, b in pairs) / len(pairs)
    f["backspace_rate"] = sum(k["key"] == "Backspace" for k in ks) / len(ks)
    typed = [k for k in ks if k["ok"] is not None]
    if typed:
        f["error_rate"] = sum(not k["ok"] for k in typed) / len(typed)
    fixes = []
    for i, k in enumerate(ks):
        if k["ok"] is False:
            j = next((j for j in range(i + 1, len(ks)) if ks[j]["key"] == "Backspace"), None)
            if j is not None:
                fixes.append(ks[j]["t"] - k["t"])
    f["fix_latency"] = _mean(fixes)
    start_gaps = [b["t"] - a["t"] for a, b in zip(ks, ks[1:]) if a["key"] == " " and b["key"].isalnum()]
    inner_gaps = [b["t"] - a["t"] for a, b in zip(ks, ks[1:])
                  if len(a["key"]) == 1 and a["key"].isalnum() and len(b["key"]) == 1 and b["key"].isalnum()]
    if start_gaps and inner_gaps and np.median(inner_gaps) > 0:
        f["space_pause_ratio"] = float(np.median(start_gaps) / np.median(inner_gaps))
    return f


def load_sessions(data_dir, labels=None):
    sessions = []
    for path in sorted(data_dir.glob("*/*.json")):
        label = path.parent.name
        if labels and label not in labels:
            continue
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"Skipping {path.name}: not a readable JSON file.")
            continue
        if log.get("format") != "keylog-v1":
            continue
        sessions.append({"label": label, "file": path, "log": log, "ks": keystrokes(log["events"])})
    return sessions


def build_samples(sessions, window):
    X, y, groups = [], [], []
    for gi, s in enumerate(sessions):
        ks = s["ks"]
        for start in range(0, len(ks) - window + 1, window):
            f = window_features(ks[start:start + window])
            X.append([f[name] for name in FEATURES])
            y.append(s["label"])
            groups.append(gi)
    return np.array(X, float), np.array(y), np.array(groups)


# -------------------------------------------------------------- analysis ---
def evaluate(X, y, groups):
    """Cross-validated scores where no session appears in both training and test data."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import balanced_accuracy_score, roc_auc_score
    from sklearn.model_selection import StratifiedGroupKFold, cross_val_predict
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    labels = sorted(set(y))
    per_class = min(len(set(groups[y == c])) for c in labels)
    cv = StratifiedGroupKFold(n_splits=min(5, per_class), shuffle=True, random_state=0)
    models = {
        "Logistic regression": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                             LogisticRegression(max_iter=2000)),
        "Random forest": make_pipeline(SimpleImputer(strategy="median"),
                                       RandomForestClassifier(n_estimators=300, random_state=0)),
    }
    out = {}
    for name, model in models.items():
        proba = cross_val_predict(model, X, y, groups=groups, cv=cv, method="predict_proba")
        pred = np.array(labels)[proba.argmax(1)]
        res = {"balanced_accuracy": balanced_accuracy_score(y, pred)}
        if len(labels) == 2:
            res["auc"] = roc_auc_score(y == labels[1], proba[:, 1])
        out[name] = res
    forest = models["Random forest"].fit(X, y)
    importances = forest[-1].feature_importances_
    return out, importances


def plot_distributions(sessions, results_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = sorted({s["label"] for s in sessions})
    charts = []
    for name, title, xlabel, get in [
        ("flight_times.png", "Time between key presses", "milliseconds (log scale)",
         lambda ks: np.diff([k["t"] for k in ks])),
        ("hold_times.png", "How long each key is held down", "milliseconds (log scale)",
         lambda ks: [k["up"] - k["t"] for k in ks if k["up"] is not None]),
    ]:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        bins = np.logspace(-1, np.log10(3000), 70)
        for label in labels:
            vals = np.concatenate([np.asarray(get(s["ks"]), float) for s in sessions if s["label"] == label])
            vals = vals[vals > 0]
            if len(vals):
                ax.hist(np.clip(vals, bins[0], bins[-1]), bins=bins, alpha=0.55, density=True,
                        label=f"{label} ({len(vals)} keys)")
        ax.set_xscale("log")
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("share of keys")
        ax.legend()
        fig.tight_layout()
        fig.savefig(results_dir / name, dpi=120)
        plt.close(fig)
        charts.append(name)
    return charts


def analyze(a):
    sessions = load_sessions(a.data, set(a.labels.split(",")) if a.labels else None)
    by_label = {}
    for s in sessions:
        by_label.setdefault(s["label"], []).append(s)
    if len(by_label) < 2:
        sys.exit(f"Need sessions from at least two labels in {a.data} (e.g. human and bot); "
                 f"found: {', '.join(by_label) or 'none'}.")
    short = [lab for lab, ss in by_label.items() if len(ss) < 2]
    if short:
        sys.exit(f"Need at least 2 sessions per label to test on unseen sessions; "
                 f"record more for: {', '.join(short)}.")
    a.results.mkdir(parents=True, exist_ok=True)

    lines = ["# Keystroke-dynamics study", "",
             f"Generated {datetime.now():%Y-%m-%d %H:%M} from `{a.data.name}/`.", "", "## Data", "",
             "| Label | Sessions | Key presses | Median WPM | Median accuracy |", "|---|---|---|---|---|"]
    for lab, ss in sorted(by_label.items()):
        lines.append(f"| {lab} | {len(ss)} | {sum(len(s['ks']) for s in ss)} | "
                     f"{statistics.median(s['log']['wpm'] for s in ss):.0f} | "
                     f"{statistics.median(s['log']['accuracy'] for s in ss):.1f}% |")

    print("\nHow well can each classifier tell the labels apart?")
    print("(cross-validated: every test session is one the model never saw in training)\n")
    lines += ["", "## How well the classifiers tell them apart", "",
              "Each sample is a run of consecutive key presses. Scores are cross-validated so "
              "that every test session was unseen in training. Balanced accuracy: 50% is "
              "guessing, 100% is perfect.", "",
              "| Keys per sample | Samples | Logistic regression | Random forest | Random forest AUC |",
              "|---|---|---|---|---|"]
    rows = []
    for w in WINDOW_SIZES:
        X, y, groups = build_samples(sessions, w)
        if len(set(y)) < 2 or min((y == c).sum() for c in set(y)) < 2:
            continue
        scores, importances = evaluate(X, y, groups)
        lr, rf = scores["Logistic regression"], scores["Random forest"]
        auc = f"{rf['auc']:.3f}" if "auc" in rf else "n/a"
        reliable = min((y == c).sum() for c in set(y)) >= MIN_SAMPLES_PER_LABEL
        flag = "" if reliable else " (too few samples: unreliable)"
        print(f"  {w:>3} keys per sample ({len(y):>4} samples): logistic regression "
              f"{lr['balanced_accuracy']:.1%}, random forest {rf['balanced_accuracy']:.1%}, AUC {auc}{flag}")
        lines.append(f"| {w} | {len(y)}{flag} | {lr['balanced_accuracy']:.1%} | "
                     f"{rf['balanced_accuracy']:.1%} | {auc} |")
        rows.append((reliable, w, X, y, importances))
    if not rows:
        sys.exit("Not enough key presses to build samples. Record longer or more sessions.")
    # Explain features using the longest samples that still give enough data per label.
    reliable_rows = [r for r in rows if r[0]]
    best = reliable_rows[-1][1:] if reliable_rows else rows[0][1:]

    w, X, y, importances = best
    order = np.argsort(importances)[::-1]
    labels = sorted(set(y))
    lines += ["", f"## What gives each label away ({w} keys per sample)", "",
              "Average value of each feature per label, most useful to the random forest first.", "",
              "| Feature | What it measures | " + " | ".join(labels) + " | Importance |",
              "|---|---|" + "---|" * len(labels) + "---|"]
    print(f"\nMost telling features ({w} keys per sample):")
    for i in order:
        name = list(FEATURES)[i]
        means = [np.nanmean(X[y == lab, i]) if np.isfinite(X[y == lab, i]).any() else np.nan for lab in labels]
        cells = " | ".join("n/a" if np.isnan(m) else f"{m:.3g}" for m in means)
        lines.append(f"| `{name}` | {FEATURES[name]} | {cells} | {importances[i]:.3f} |")
    for i in order[:5]:
        name = list(FEATURES)[i]
        means = ", ".join(f"{lab} {np.nanmean(X[y == lab, i]):.3g}" for lab in labels
                          if np.isfinite(X[y == lab, i]).any())
        print(f"  {name:<18} {means}   ({FEATURES[name]})")

    charts = plot_distributions(sessions, a.results)
    lines += ["", "## Timing distributions", ""] + [f"![{c}]({c})" for c in charts]
    lines += ["", "## Caveats", "",
              "- Small study: few sessions, usually one typist, one page and a fixed word list.",
              "- Human sessions recorded through `record human` run in a Chrome window controlled "
              "by automation software. That doesn't change your key timing, but it is not a "
              "completely normal browser session.",
              "- High scores here show these particular features separate these particular "
              "sessions; they don't say how a real anti-cheat system works."]
    report = a.results / "report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport: {report}\nCharts: {', '.join(str(a.results / c) for c in charts)}")


# ------------------------------------------------------------------- CLI ---
def main():
    p = argparse.ArgumentParser(description="Keystroke-dynamics study: human typing vs the bot")
    p.add_argument("--data", type=Path, default=ROOT / "data", help="folder for the keystroke logs")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("record", help="record typing sessions")
    r.add_argument("who", choices=["human", "bot"])
    r.add_argument("--seconds", type=int, default=60, help="test length (default 60)")
    r.add_argument("--label", help="folder name to save under (default: human or bot)")
    r.add_argument("--engine", choices=["playwright", "selenium"], default="playwright",
                   help="browser library to use (default playwright)")
    r.add_argument("--runs", type=int, default=5, help="bot only: how many sessions (default 5)")
    r.add_argument("--wpm", type=float, help="bot only: speed (default: match your human sessions)")
    r.add_argument("--accuracy", type=float, help="bot only: accuracy (default: match your human sessions)")

    an = sub.add_parser("analyze", help="compare the recorded sessions")
    an.add_argument("--labels", help="comma-separated labels to compare (default: all folders in data/)")
    an.add_argument("--results", type=Path, default=ROOT / "results", help="where to write the report")

    a = p.parse_args()
    if a.command == "record":
        (record_human if a.who == "human" else record_bot)(a)
    else:
        analyze(a)


if __name__ == "__main__":
    main()
