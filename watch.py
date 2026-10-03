#!/usr/bin/env python3
"""Hetzner Server Auction watcher.

Fetches the public auction feed, keeps servers that fit the rules below,
writes a table of the top options to the GitHub Actions run summary every
time, and sends a phone notification (ntfy.sh) every run: a high-priority
alert for NEW matches, a low-priority status update otherwise.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- settings
FEED_URL = "https://www.hetzner.com/_resources/app/data/app/live_data_sb.json"
STATE_FILE = "state.json"

LAPTOP_PASSMARK = 25800    # Intel i7-13700H
MIN_PASSMARK = 20000       # at most ~20-25% slower than the laptop
MIN_RAM_GB = 64
MIN_NVME_COUNT = 2
MIN_NVME_GB = 512
MAX_TOTAL_USD = 80.00      # server + IPv4, per month, excl. VAT
EUR_TO_USD = 1.115         # only used if the feed has no USD prices
RENOTIFY_DROP_USD = 3.00   # notify again if a known server got this much cheaper
TABLE_ROWS = 5
STATUS_PRIORITY = "low"    # ntfy priority of the every-run status ping ("min" = silent, "default" = sound)
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

# Approximate PassMark CPU Marks (multi-thread) and cores/threads.
# Keys are matched as whole words against the lower-cased CPU name, with "PRO" removed.
CPUS = {
    # AMD Ryzen 5
    "ryzen 5 5600": (21500, "6/12"), "ryzen 5 5600x": (21900, "6/12"),
    "ryzen 5 7600": (26800, "6/12"), "ryzen 5 7600x": (28700, "6/12"),
    "ryzen 5 9600": (29000, "6/12"), "ryzen 5 9600x": (30000, "6/12"),
    # AMD Ryzen 7
    "ryzen 7 3700x": (22700, "8/16"), "ryzen 7 3800x": (23700, "8/16"),
    "ryzen 7 5700g": (24500, "8/16"), "ryzen 7 5700x": (26600, "8/16"),
    "ryzen 7 5800x": (28000, "8/16"), "ryzen 7 5800x3d": (27900, "8/16"),
    "ryzen 7 7700": (34500, "8/16"), "ryzen 7 7700x": (36000, "8/16"),
    "ryzen 7 8700ge": (28500, "8/16"), "ryzen 7 8700g": (32000, "8/16"),
    "ryzen 7 9700x": (36500, "8/16"),
    # AMD Ryzen 9
    "ryzen 9 3900": (30500, "12/24"), "ryzen 9 3900x": (32800, "12/24"),
    "ryzen 9 3950x": (39000, "16/32"), "ryzen 9 5900": (36000, "12/24"),
    "ryzen 9 5900x": (39200, "12/24"), "ryzen 9 5950x": (46000, "16/32"),
    "ryzen 9 7900": (48500, "12/24"), "ryzen 9 7900x": (51500, "12/24"),
    "ryzen 9 7900x3d": (50000, "12/24"), "ryzen 9 7950x": (62800, "16/32"),
    "ryzen 9 7950x3d": (62000, "16/32"), "ryzen 9 9900x": (54000, "12/24"),
    "ryzen 9 9950x": (66000, "16/32"),
    # Intel Core
    "i5-12500": (20200, "6/12"), "i5-12600": (21000, "6/12"),
    "i5-12600k": (27600, "10/16"), "i5-13500": (31500, "14/20"),
    "i5-13600": (33000, "14/20"), "i5-13600k": (38000, "14/20"),
    "i5-14500": (33500, "14/20"), "i5-14600": (35000, "14/20"),
    "i7-12700": (31000, "12/20"), "i7-12700k": (34500, "12/20"),
    "i7-13700": (39000, "16/24"), "i7-13700k": (46500, "16/24"),
    "i7-14700": (44000, "20/28"), "i7-14700k": (53000, "20/28"),
    "i9-10900": (20000, "10/20"), "i9-10900k": (23000, "10/20"),
    "i9-11900": (21000, "8/16"), "i9-11900k": (25000, "8/16"),
    "i9-12900": (36500, "16/24"), "i9-12900k": (41500, "16/24"),
    "i9-13900": (47000, "24/32"), "i9-13900k": (59000, "24/32"),
    "i9-14900": (48500, "24/32"), "i9-14900k": (60000, "24/32"),
    # AMD EPYC
    "epyc 7281": (20000, "16/32"), "epyc 7351p": (21500, "16/32"),
    "epyc 7313p": (39000, "16/32"), "epyc 7401p": (29000, "24/48"),
    "epyc 7443p": (58000, "24/48"), "epyc 7502p": (51000, "32/64"),
    "epyc 7543p": (62000, "32/64"), "epyc 9254": (60000, "24/48"),
    "epyc 9354p": (75000, "32/64"), "epyc 4464p": (40000, "12/24"),
    "epyc 4564p": (55000, "16/32"),
    # Intel Xeon (only the fast ones; E3/E5/E-2xxx are excluded below)
    "xeon w-2295": (30000, "18/36"), "xeon gold 5412u": (42000, "24/48"),
}

# CPUs the user explicitly does not want, however cheap (skipped silently).
EXCLUDE = re.compile(
    r"xeon e3|xeon e5|xeon e-2|i7-6700|i7-7700|i7-8700|i9-9900|ryzen 5 3600|ryzen 7 2700",
)


# ----------------------------------------------------------------- helpers
def log(msg):
    print(msg, flush=True)


def load_state():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state.setdefault("notified", {})
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def notify(title, body, click=None, priority="high"):
    if not NTFY_TOPIC:
        log("NTFY_TOPIC not set - would have sent:\n" + title + "\n" + body)
        return
    # Title etc. go in the query string so non-ASCII characters (×, €) survive.
    params = {"title": title, "priority": priority, "tags": "computer"}
    if click:
        params["click"] = click
    req = urllib.request.Request(
        f"{NTFY_SERVER.rstrip('/')}/{NTFY_TOPIC}?" + urllib.parse.urlencode(params),
        data=body.encode("utf-8"),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        log(f"ntfy: HTTP {r.status}")


def fetch_feed():
    if os.environ.get("FEED_FILE"):  # for local testing
        with open(os.environ["FEED_FILE"]) as f:
            return json.load(f)["server"]
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                FEED_URL, headers={"User-Agent": "hetzner-auction-watch/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)["server"]
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"could not fetch feed: {last_err}")


def usd(price_obj):
    """Return a USD amount from {'EUR': x, 'USD': y}, converting if needed."""
    if not price_obj:
        return 0.0
    if price_obj.get("USD") is not None:
        return float(price_obj["USD"])
    return round(float(price_obj.get("EUR", 0)) * EUR_TO_USD, 2)


def eur(price_obj):
    return float((price_obj or {}).get("EUR", 0) or 0)


def cpu_info(name):
    n = re.sub(r"\bpro\b\s*", "", name.lower())
    n = re.sub(r"\s+", " ", n)
    if EXCLUDE.search(n):
        return "excluded", None, None
    best = None
    for key, (mark, ct) in CPUS.items():
        if re.search(r"(?<![\w-])" + re.escape(key) + r"(?![\w])", n):
            if best is None or len(key) > len(best[0]):
                best = (key, mark, ct)
    if best is None:
        return "unknown", None, None
    return "known", best[1], best[2]


def vs_laptop(mark):
    pct = round((mark / LAPTOP_PASSMARK - 1) * 100 / 5) * 5
    if abs(pct) < 5:
        return "about the same"
    return f"~{abs(pct)}% {'faster' if pct > 0 else 'slower'}"


def disks_text(nvme):
    def size(gb):
        if gb >= 1000:
            return f"{gb // 1024} TB" if gb % 1024 == 0 else f"{gb / 1000:g} TB"
        return f"{gb} GB"
    sizes = sorted(nvme, reverse=True)
    if len(set(sizes)) == 1:
        return f"{len(sizes)}× {size(sizes[0])} NVMe"
    return " + ".join(size(s) for s in sizes) + " NVMe"


def next_drop(s, now):
    if s.get("Prices", {}).get("fixed"):
        return "fixed"
    t = s.get("Timer", {}) or {}
    ts = t.get("ReduceNextTimestamp")
    secs = (ts - now) if ts else t.get("ReduceNext")
    if secs is None:
        return "?"
    return f"{max(secs, 0) / 3600:.0f} h"


# -------------------------------------------------------------------- main
def main():
    now = time.time()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    state = load_state()

    try:
        servers = fetch_feed()
    except Exception as e:  # noqa: BLE001
        log(str(e))
        notify("Hetzner watch: check failed", f"{stamp}: {e}", priority="default")
        summary(f"### Hetzner auction check - {stamp}\n\n**Check failed:** {e}\n")
        return

    matches, unknown = [], []
    for s in servers:
        hw = s.get("Hardware", {})
        name = hw.get("CPU", {}).get("Name", "?")
        ram = hw.get("RAM", {}).get("Size", 0) or 0
        nvme = [d for d in hw.get("Storage", {}).get("Details", {}).get("nvme", []) or []]
        big_nvme = [d for d in nvme if d >= MIN_NVME_GB]
        prices = s.get("Prices", {})
        setup = usd(prices.get("setup"))
        total_usd = usd(prices.get("monthly")) + usd(s.get("IPPrices", {}).get("monthly"))
        total_eur = eur(prices.get("monthly")) + eur(s.get("IPPrices", {}).get("monthly"))
        if ram < MIN_RAM_GB or len(big_nvme) < MIN_NVME_COUNT or setup > 0:
            continue
        if total_usd > MAX_TOTAL_USD + 1e-9:
            continue
        kind, mark, ct = cpu_info(name)
        row = {
            "id": str(s["Id"]),
            "cpu": name,
            "ct": ct or "?",
            "mark": mark,
            "ram": f"{ram} GB{' ECC' if hw.get('RAM', {}).get('ecc') else ''}",
            "disks": disks_text(big_nvme),
            "usd": round(total_usd, 2),
            "eur": round(total_eur, 2),
            "dc": s.get("Details", {}).get("Datacenter", {}).get("Name", "?"),
            "drop": next_drop(s, now),
            "link": f"https://www.hetzner.com/sb/#search={s['Id']}",
        }
        if kind == "known" and mark >= MIN_PASSMARK:
            matches.append(row)
        elif kind == "unknown":
            unknown.append(row)

    # As-fast-or-faster first, then best speed per dollar.
    matches.sort(key=lambda r: (r["mark"] < LAPTOP_PASSMARK, -r["mark"] / r["usd"]))

    notified = state["notified"]
    for r in matches:
        prev = notified.get(r["id"])
        r["new"] = prev is None or r["usd"] <= float(prev) - RENOTIFY_DROP_USD
    new = [r for r in matches if r["new"]]

    # ---- run summary (always)
    md = [f"### Hetzner auction check - {stamp}",
          f"{len(servers)} servers in the feed, **{len(matches)} compatible option(s)**, "
          f"{len(new)} new.\n"]
    if matches:
        md.append("| Id | CPU (c/t) | vs laptop | RAM | NVMe | $/mo incl. IPv4 | DC | Next drop | New? |")
        md.append("|---|---|---|---|---|---|---|---|---|")
        for r in matches[:TABLE_ROWS]:
            md.append(
                f"| [{r['id']}]({r['link']}) | {r['cpu']} ({r['ct']}) | {vs_laptop(r['mark'])} "
                f"| {r['ram']} | {r['disks']} | ${r['usd']:.2f} (€{r['eur']:.2f}) | {r['dc']} "
                f"| {r['drop']} | {'yes' if r['new'] else 'no'} |"
            )
        md.append(f"\n**My suggestion:** {suggestion(matches[0])}")
    else:
        md.append("No compatible options right now.")
    if unknown:
        md.append("\n**Unrecognised CPUs that pass RAM/NVMe/budget (check by hand, "
                  "and add them to `CPUS` in watch.py):**")
        for r in unknown[:10]:
            md.append(f"- [{r['id']}]({r['link']}) {r['cpu']}, {r['ram']}, {r['disks']}, "
                      f"${r['usd']:.2f} (€{r['eur']:.2f})")
    summary("\n".join(md) + "\n")

    # ---- phone notification (every run: an alert for new matches, a quiet status otherwise)
    if new:
        top = new[0]
        title = (f"Match: {top['cpu'].replace('AMD ', '').replace('Intel Core ', '')}, "
                 f"{top['ram']}, {top['disks']} for ${top['usd']:.2f}/mo (#{top['id']})")
        lines = []
        for r in new[:5]:
            lines.append(
                f"#{r['id']} {r['cpu']} ({r['ct']}), {vs_laptop(r['mark'])} vs laptop; "
                f"{r['ram']}; {r['disks']}; ${r['usd']:.2f}/mo (€{r['eur']:.2f}); {r['dc']}; "
                f"next drop {r['drop']}\n{r['link']}"
            )
        body = ("\n\n".join(lines)
                + f"\n\nMy suggestion: {suggestion(new[0])}"
                + "\nHetzner bills hourly with no minimum term, so you can test and cancel cheaply.")
        notify(title, body, click=top["link"])
        for r in new:
            notified[r["id"]] = r["usd"]
        # keep the 200 most recent entries
        state["notified"] = dict(list(notified.items())[-200:])
    else:
        title = (f"Hetzner check: {len(matches)} compatible, none new" if matches
                 else "Hetzner check: no compatible options")
        lines = [f"{stamp}: {len(servers)} servers in the feed."]
        for r in matches[:3]:
            lines.append(f"#{r['id']} {r['cpu']}, {r['ram']}, {r['disks']}, "
                         f"${r['usd']:.2f}/mo, next drop {r['drop']}")
        notify(title, "\n".join(lines),
               click=matches[0]["link"] if matches else None, priority=STATUS_PRIORITY)

    # Touch the state roughly monthly so the repo stays "active" and GitHub
    # does not auto-disable the schedule after 60 days without activity.
    today = dt.date.today()
    last = state.get("keepalive")
    if not last or (today - dt.date.fromisoformat(last)).days >= 30:
        state["keepalive"] = today.isoformat()

    save_state(state)
    log(f"{len(matches)} compatible, {len(new)} new, {len(unknown)} unknown CPUs")


def suggestion(r):
    speed = vs_laptop(r["mark"])
    speed_txt = "as fast as your laptop" if speed == "about the same" else f"{speed} than your laptop"
    return (f"#{r['id']} ({r['cpu']}, {r['ram']}, {r['disks']}) at ${r['usd']:.2f}/mo - "
            f"{speed_txt}, and the best speed per dollar among the "
            f"{'as-fast-or-faster' if r['mark'] >= LAPTOP_PASSMARK else 'available'} options.")


def summary(text):
    log(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(text)


if __name__ == "__main__":
    sys.exit(main())
