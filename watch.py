#!/usr/bin/env python3
"""Hetzner Server Auction watcher.

Goal: find a clear UPGRADE over the current server (see CURRENT SERVER below).
Exposed on Vercel by app.py (GET /api/check), called every minute by cron-job.org;
`python watch.py` runs one check locally.
Fetches the public auction feed, keeps servers that fit the upgrade rules below,
returns a report of the top upgrades every time, and sends a phone
notification (ntfy.sh) only when a NEW upgrade shows
up, or the same server as the current one is listed for less than you pay.
Once a day at 12:00 (TIMEZONE) it also sends a silent note (no sound or pop-up) with the
closest match. Tapping a notification opens the auction filtered to that server.

Read-only: it only reads the public feed. It never buys, reserves, cancels or
logs in - ordering, migrating and cancelling are always done by hand.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zoneinfo

# ---------------------------------------------------------------- settings
FEED_URL = "https://www.hetzner.com/_resources/app/data/app/live_data_sb.json"
AUCTION_URL = "https://www.hetzner.com/sb/"
# Already-notified servers are kept in Upstash Redis (connected to the Vercel
# project as a Marketplace store) or, when that isn't configured, in a local file.
STATE_FILE = "state.json"
STATE_KEY = "hetzner-auction-watch:state"
REDIS_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
REDIS_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")

# ---- CURRENT SERVER (bought 2026-10-06 from the Hetzner auction)
# AMD Ryzen 9 3900 (12C/24T, PassMark ~30,500), 64 GB DDR4 ECC,
# 2× 512 GB M.2 NVMe SSD, 1 Gbit Intel I210 NIC, HEL1.
# $66.00 server + $1.90 IPv4 = $67.90/mo excl. VAT = $80.80/mo incl. 19% VAT.
# About 18% faster than the laptop (i7-13700H, PassMark ~25,800).
CURRENT_CPU = "Ryzen 9 3900"
CURRENT_SHORT = "3900"
CURRENT_PASSMARK = 30500
CURRENT_USD = 67.90        # per month excl. VAT, incl. IPv4
# Also alert when the same server (Ryzen 9 3900, not the 3900X, 64 GB+ RAM,
# 2+ NVMe SSDs of 512 GB+, no setup fee) is listed for less than CURRENT_USD.
SAME_CPU = re.compile(r"\bryzen 9 3900(?![\w])")
SAME_MIN_RAM_GB = 64
SAME_MIN_SSD_GB = 512

# ---- Upgrade rules
# Prices: the auction list shows prices excl. VAT and already including IPv4
# (the feed has them as two fields, added up below); the order page adds VAT.
# All prices shown are USD excl. VAT, with EUR in brackets.
VAT_RATE = 0.19
LAPTOP_PASSMARK = 25800    # Intel i7-13700H
MIN_PASSMARK = 42000       # >= ~40% faster than the 3900
MIN_RAM_GB = 64            # no less than the current server
MIN_NVME_COUNT = 2         # NVMe SSDs of any size - SATA SSDs and HDDs don't count
MAX_TOTAL_USD = 80.00      # server + IPv4, per month, excl. VAT
EUR_TO_USD = 1.115         # only used if the feed has no USD prices
RENOTIFY_DROP_USD = 3.00   # notify again if a known server got this much cheaper
TABLE_ROWS = 5
REPORT_HOUR = 12           # daily silent report: the first run at/after this hour (TIMEZONE)
TIMEZONE = "Africa/Cairo"  # for the daily report and showing when the next price drop happens
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

try:
    LOCAL_TZ = zoneinfo.ZoneInfo(TIMEZONE)
    LOCAL_TZ_LABEL = TIMEZONE.split("/")[-1].replace("_", " ")
except zoneinfo.ZoneInfoNotFoundError:  # e.g. Windows without the tzdata package
    LOCAL_TZ, LOCAL_TZ_LABEL = dt.timezone.utc, "UTC"

# CPUs the user explicitly does not want, however cheap (skipped silently).
EXCLUDE = re.compile(
    r"xeon e3|xeon e5|xeon e-2|xeon w-2145|i7-6700|i7-7700|i7-8700|i9-9900|ryzen 5 3600"
    r"|ryzen 7 1700x|ryzen 7 2700",
)


# ----------------------------------------------------------------- helpers
# Notifications contain emoji, which a Windows console code page can't print.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def log(msg):
    print(msg, flush=True)


def redis(*command):
    """Run one command through the Upstash Redis REST API, e.g. redis("GET", key)."""
    req = urllib.request.Request(
        REDIS_URL, data=json.dumps(command).encode(), method="POST",
        headers={"Authorization": f"Bearer {REDIS_TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)["result"]


def load_state():
    if REDIS_URL:
        raw = redis("GET", STATE_KEY)
        state = json.loads(raw) if raw else {}
    else:
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            state = {}
    state.setdefault("notified", {})
    return state


def save_state(state):
    if REDIS_URL:
        redis("SET", STATE_KEY, json.dumps(state, sort_keys=True))
        return
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def notify(title, body, click=AUCTION_URL, priority="high", buttons=()):
    """Tapping the notification opens `click`; `buttons` are (label, url) pairs (max 3)."""
    actions = "; ".join(f"view, {label.replace(',', '')}, {url}" for label, url in buttons[:3])
    if not NTFY_TOPIC:
        log(f"NTFY_TOPIC not set - would have sent:\n{title}\n{body}\n"
            f"[tap: {click}] [buttons: {actions or '-'}]")
        return
    # Title etc. go in the query string so non-ASCII characters (×, €) survive.
    params = {"title": title, "priority": priority, "tags": "computer", "click": click}
    if actions:
        params["actions"] = actions
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


def norm_cpu(name):
    """Lower-case CPU name with "PRO" removed, e.g. 'amd ryzen 7 1700x'."""
    return re.sub(r"\s+", " ", re.sub(r"\bpro\b\s*", "", name.lower()))


def cpu_info(name):
    n = norm_cpu(name)
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


def vs_current(mark):
    """Speed vs the current server, e.g. '~2.5x faster'."""
    ratio = round(mark / CURRENT_PASSMARK, 1)
    if ratio == 1:
        return "about the same"
    if ratio > 1:
        return f"~{ratio:g}x faster"
    return f"~{round(CURRENT_PASSMARK / mark, 1):g}x slower"


def price_diff(usd_total):
    """Monthly price difference vs the current server, e.g. '+$12.40'."""
    d = round(usd_total - CURRENT_USD, 2)
    return f"{'-' if d < 0 else '+'}${abs(d):.2f}"


def disks_text(drives, kind="NVMe"):
    def size(gb):
        if gb >= 1000:
            return f"{gb // 1024} TB" if gb % 1024 == 0 else f"{gb / 1000:g} TB"
        return f"{gb} GB"
    sizes = sorted(drives, reverse=True)
    if not sizes:
        return ""
    if len(set(sizes)) == 1:
        return f"{len(sizes)}× {size(sizes[0])} {kind}"
    return " + ".join(size(s) for s in sizes) + f" {kind}"


def next_drop(s, now):
    """When the auction price drops next, e.g. 'in 8h 46m (Sun 10:15 Cairo)'."""
    if s.get("Prices", {}).get("fixed"):
        return "fixed price"
    t = s.get("Timer", {}) or {}
    ts = t.get("ReduceNextTimestamp") or (now + t["ReduceNext"] if t.get("ReduceNext") else None)
    if ts is None:
        return "?"
    mins = max(int(ts - now), 0) // 60
    when = dt.datetime.fromtimestamp(ts, LOCAL_TZ).strftime("%a %H:%M")
    return f"in {mins // 60}h {mins % 60:02d}m ({when} {LOCAL_TZ_LABEL})"


# -------------------------------------------------------------------- main
def main():
    """Run one check. Returns (ok, report): ok is False when the feed couldn't be fetched."""
    now = time.time()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    state = load_state()

    try:
        servers = fetch_feed()
    except Exception as e:  # noqa: BLE001
        report = f"### Hetzner auction check - {stamp}\n\n**Check failed:** {e}\n"
        log(report)
        return False, report

    # near = known CPU that fails exactly one upgrade rule (shown with the reason
    # when there is no upgrade)
    # cheaper = the same server as the current one, listed for less
    matches, near, unknown, cheaper = [], [], [], []
    for s in servers:
        hw = s.get("Hardware", {})
        name = hw.get("CPU", {}).get("Name", "?")
        ram = hw.get("RAM", {}).get("Size", 0) or 0
        drives = hw.get("Storage", {}).get("Details", {})
        nvme = [d for d in drives.get("nvme", []) or []]
        sata = [d for d in drives.get("sata", []) or []]  # SATA SSDs (HDDs are listed separately)
        prices = s.get("Prices", {})
        setup = usd(prices.get("setup"))
        # server + IPv4 = the price on the auction list (excl. VAT)
        total_usd = usd(prices.get("monthly")) + usd(s.get("IPPrices", {}).get("monthly"))
        total_eur = eur(prices.get("monthly")) + eur(s.get("IPPrices", {}).get("monthly"))
        disks = " + ".join(t for t in (disks_text(nvme), disks_text(sata, "SATA SSD"),
                                       disks_text(drives.get("hdd") or [], "HDD")) if t) or "no disks"
        kind, mark, ct = cpu_info(name)
        row = {
            "id": str(s["Id"]),
            "cpu": name,
            "ct": ct or "?",
            "mark": mark,
            "ram": f"{ram} GB{' ECC' if hw.get('RAM', {}).get('ecc') else ''}",
            "disks": disks,
            "usd": round(total_usd, 2),
            "eur": round(total_eur, 2),
            "dc": s.get("Details", {}).get("Datacenter", {}).get("Name", "?"),
            "drop": next_drop(s, now),
            "link": f"{AUCTION_URL}?freetext={s['Id']}",  # opens the auction filtered to this server
        }
        if SAME_CPU.search(norm_cpu(name)):
            if (ram >= SAME_MIN_RAM_GB and setup == 0 and round(total_usd, 2) < CURRENT_USD
                    and len([d for d in nvme if d >= SAME_MIN_SSD_GB]) >= MIN_NVME_COUNT):
                cheaper.append(dict(row, mark=CURRENT_PASSMARK, ct="12/24"))
            continue
        if kind == "excluded":
            continue
        fails = []
        if kind == "known" and mark < MIN_PASSMARK:
            fails.append(f"CPU too slow (~{mark:,} PassMark, needs ~{MIN_PASSMARK:,})")
        if ram < MIN_RAM_GB:
            fails.append(f"only {ram} GB RAM (needs {MIN_RAM_GB} GB)")
        if len(nvme) < MIN_NVME_COUNT:
            fails.append(f"{len(nvme) or 'no'} NVMe SSD(s) (needs {MIN_NVME_COUNT}; "
                         "SATA SSDs and HDDs don't count)")
        if setup > 0:
            fails.append(f"${setup:.2f} setup fee")
        if total_usd > MAX_TOTAL_USD + 1e-9:
            fails.append(f"${total_usd - MAX_TOTAL_USD:.2f} over the ${MAX_TOTAL_USD:.0f} budget")
        row["why"] = "; ".join(fails)
        if kind == "unknown":
            if not fails:
                unknown.append(row)
        elif not fails:
            matches.append(row)
        elif len(fails) == 1:
            near.append(row)

    # As-fast-or-faster than the laptop first, then best speed per dollar.
    matches.sort(key=lambda r: (r["mark"] < LAPTOP_PASSMARK, -r["mark"] / r["usd"]))
    # Cheapest near-miss first.
    near.sort(key=lambda r: r["usd"])
    cheaper.sort(key=lambda r: r["usd"])

    notified = state["notified"]
    for r in matches + cheaper:
        prev = notified.get(r["id"])
        r["new"] = prev is None or r["usd"] <= float(prev) - RENOTIFY_DROP_USD
    new = [r for r in matches if r["new"]]
    new_cheaper = [r for r in cheaper if r["new"]]

    # ---- run summary (always)
    md = [f"### Hetzner auction check - {stamp}",
          f"{len(servers)} servers in the feed, **{len(matches)} compatible upgrade(s)**, "
          f"{len(new)} new.",
          f"Current server: {CURRENT_CPU}, ${CURRENT_USD:.2f}/mo. Prices are per month excl. VAT "
          f"incl. IPv4, as on the auction list (the order page adds {VAT_RATE:.0%} VAT).\n"]
    if matches:
        md.append(f"| Id | CPU (c/t) | vs current {CURRENT_SHORT} | vs laptop | RAM | Disks "
                  f"| $/mo excl. VAT incl. IPv4 | vs ${CURRENT_USD:.2f} now | DC | Next drop | New? |")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in matches[:TABLE_ROWS]:
            md.append(
                f"| [{r['id']}]({r['link']}) | {r['cpu']} ({r['ct']}) | {vs_current(r['mark'])} "
                f"| {vs_laptop(r['mark'])} | {r['ram']} | {r['disks']} "
                f"| ${r['usd']:.2f} (€{r['eur']:.2f}) | {price_diff(r['usd'])}/mo | {r['dc']} "
                f"| {r['drop']} | {'yes' if r['new'] else 'no'} |"
            )
        md.append(f"\n**My suggestion:** {suggestion(matches[0])}")
    else:
        md.append(f"No upgrade over your current {CURRENT_CPU} right now.")
        if near:
            c = near[0]
            md.append(f"\n**Cheapest near-miss** (failed: {c['why']}): {details(c)}")
        md.append(f"\n**My suggestion:** keep the {CURRENT_SHORT} for now.")
    if cheaper:
        md.append(f"\n**Same server as your {CURRENT_SHORT}, but cheaper than ${CURRENT_USD:.2f}/mo:**")
        for r in cheaper[:TABLE_ROWS]:
            md.append(f"- {'(new) ' if r['new'] else ''}{details(r)}")
    if unknown:
        md.append("\n**Unrecognised CPUs that pass RAM/SSD/budget (check by hand, "
                  "and add them to `CPUS` in watch.py):**")
        for r in unknown[:10]:
            md.append(f"- [{r['id']}]({r['link']}) {r['cpu']}, {r['ram']}, {r['disks']}, "
                      f"${r['usd']:.2f} (€{r['eur']:.2f}), {price_diff(r['usd'])}/mo vs now")
    report = "\n".join(md) + "\n"
    log(report)

    # ---- phone notification (only for NEW upgrades, or the same server for less)
    if new or new_cheaper:
        top = (new or new_cheaper)[0]
        title = (f"{'Upgrade' if new else f'Cheaper {CURRENT_SHORT}'}: "
                 f"{short_cpu(top['cpu'])}, "
                 f"{top['ram']}, {top['disks']} for ${top['usd']:.2f}/mo "
                 f"({price_diff(top['usd'])} vs your {CURRENT_SHORT}) (#{top['id']})")
        if new:
            r = new[0]
            advice = (f"Worth switching to #{r['id']} (best speed per dollar):\n"
                      f"💻 {short_cpu(r['cpu'])} · 🧠 {r['ram']} RAM · 💾 {r['disks']}\n"
                      f"⚡ {vs_current(r['mark']).lstrip('~')} than your {CURRENT_SHORT} · "
                      f"💵 {price_diff(r['usd'])}/mo vs now")
        else:
            advice = f"Same hardware as yours for ${CURRENT_USD - top['usd']:.2f}/mo less."
        body = ("\n\n".join(card(r) for r in (new + new_cheaper)[:5])
                + f"\n\n👉 {advice}\n{HOURLY_NOTE}")
        notify(title, body, click=top["link"], buttons=auction_buttons(new + new_cheaper))
        for r in new + new_cheaper:
            notified[r["id"]] = r["usd"]
        # keep the 200 most recent entries
        state["notified"] = dict(list(notified.items())[-200:])
        save_state(state)

    # ---- silent daily report (the first run at/after REPORT_HOUR local time)
    local_now = dt.datetime.fromtimestamp(now, LOCAL_TZ)
    today = local_now.date().isoformat()
    if local_now.hour >= REPORT_HOUR and state.get("last_report") != today:
        daily_report(matches, near)
        state["last_report"] = today
        save_state(state)

    log(f"{len(matches)} compatible upgrades, {len(new)} new, {len(cheaper)} cheaper {CURRENT_SHORT}, "
        f"{len(unknown)} unknown CPUs")
    return True, report


HOURLY_NOTE = f"Hetzner bills hourly: order, migrate, then cancel the {CURRENT_SHORT}."


def details(r):
    return (f"#{r['id']} {r['cpu']} ({r['ct']}), {vs_current(r['mark'])} vs your {CURRENT_SHORT}, "
            f"{vs_laptop(r['mark'])} vs laptop; {r['ram']}; {r['disks']}; "
            f"${r['usd']:.2f}/mo (€{r['eur']:.2f}) excl. VAT, {price_diff(r['usd'])} vs now; "
            f"{r['dc']}; next drop {r['drop']}\n{r['link']}")


def card(r):
    """One server as a short block of lines for phone notifications."""
    def than(phrase, who):
        phrase = phrase.lstrip("~")
        return f"{phrase} {'as' if phrase == 'about the same' else 'than'} {who}"
    drop = r["drop"] if r["drop"] == "fixed price" else f"next drop {r['drop']}"
    lines = [f"❌ {r['why']}"] if r.get("why") else []
    return "\n".join(lines + [
        f"💻 #{r['id']} · {short_cpu(r['cpu'])} ({r['ct']})",
        f"⚡ {than(vs_current(r['mark']), f'your {CURRENT_SHORT}')} · "
        f"{than(vs_laptop(r['mark']), 'laptop')}",
        f"🧠 {r['ram']} RAM · 💾 {r['disks']}",
        f"💵 ${r['usd']:.2f}/mo (€{r['eur']:.2f}) · {price_diff(r['usd'])} vs now",
        f"📍 {r['dc']} · {drop}",
    ])


def short_cpu(name):
    return name.replace("AMD ", "").replace("Intel Core ", "")


def daily_report(matches, near):
    """Silent (min priority: no sound or pop-up) note with the closest match right now."""
    if matches:
        r = matches[0]
        label = "top upgrade"
        n = len(matches)
        body = f"{n} upgrade{'s' if n > 1 else ''} right now. Best:\n{card(r)}"
    elif near:
        r = near[0]
        label = "closest"
        body = f"No upgrade yet. Closest:\n{card(r)}"
    else:
        notify("Daily Report: nothing close to an upgrade",
               f"Nothing comes close to an upgrade over your {CURRENT_CPU}.", priority="min")
        return
    title = (f"Daily Report: {label} #{r['id']} {short_cpu(r['cpu'])} "
             f"${r['usd']:.2f}/mo ({price_diff(r['usd'])})")
    notify(title, body, click=r["link"], priority="min")


def auction_buttons(rows):
    """Notification buttons: the top two servers, then the full auction list."""
    return [(f"#{r['id']} ${r['usd']:.0f}/mo", r["link"]) for r in rows[:2]] + [
        ("All auctions", AUCTION_URL)]


def suggestion(r):
    d = round(r["usd"] - CURRENT_USD, 2)
    cost = (f"for {price_diff(r['usd'])}/mo" if d > 0 else
            "at the same price" if d == 0 else f"while saving ${-d:.2f}/mo")
    return (f"Yes, switching from your {CURRENT_SHORT} is worth it now: #{r['id']} "
            f"({r['cpu']}, {r['ram']}, {r['disks']}) at ${r['usd']:.2f}/mo (€{r['eur']:.2f}) "
            f"is {vs_current(r['mark'])} {cost}, and the best speed per dollar among the "
            f"{'laptop-speed-or-faster' if r['mark'] >= LAPTOP_PASSMARK else 'available'} upgrades.")


if __name__ == "__main__":
    sys.exit(0 if main()[0] else 1)
