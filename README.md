# Hetzner auction watch

Checks the Hetzner Server Auction every minute: cron-job.org calls a small
Flask app on Vercel (`app.py`, `GET /api/check` runs `watch.py`), and it sends a phone
notification (via ntfy) only when a new upgrade over the current server shows
up, or the same server as the current one is listed for less than you pay.
Once a day at 12:00 Cairo time (`REPORT_HOUR`) it also sends a **silent** note (ntfy `min` priority: no sound,
vibration or pop-up) with the closest match: the top upgrade, or else the
cheapest near-miss and the rule it failed. Each run's report is the function's
response (visible in cron-job.org's history). Tapping a notification opens the
auction filtered to that one server (`https://www.hetzner.com/sb/?freetext=<Id>`).

It is read-only: it never buys, reserves, cancels or logs in.

## Current server

Bought 2026-10-06 from the Hetzner auction: AMD Ryzen 9 3900
(12C/24T, PassMark ~30,500), 64 GB DDR4 ECC, 2× 512 GB M.2 NVMe SSD,
1 Gbit Intel I210 NIC, HEL1. $67.90/mo excl. VAT incl. IPv4 ($66.00 server + $1.90 IPv4)
= $80.80/mo incl. 19% VAT. About 18% faster than the laptop (i7-13700H, PassMark ~25,800).
It replaced a Ryzen 7 PRO 1700X (FSN1, $61.90/mo).

## What counts as an upgrade

The goal is a clear upgrade over the 3900, not just any server. A server matches when it has:

- CPU at least ~42,000 PassMark (≥ ~40% faster than the 3900); old i7/Xeon E3/E-2xxx/Xeon W-2145/Ryzen 5 3600/Ryzen 7 1700X etc. are skipped
- 64 GB+ RAM, 2+ NVMe SSDs of any size (SATA SSDs and HDDs don't count)
- no setup fee, total ≤ $80/month excl. VAT including the IPv4 address

You're also alerted when the **same server as yours** (Ryzen 9 3900, not the
3900X, 64 GB+ RAM, 2+ NVMe SSDs of 512 GB+, no setup fee) is listed for less than
$67.90/mo.

**Prices:** the auction list shows prices excl. VAT and already including
IPv4; the order page adds 19% VAT. Everything the watcher shows is USD excl.
VAT (EUR in brackets), plus the difference vs. the current $67.90 (e.g. "+$12.40/mo").

Every run also returns a table of the top 5 compatible upgrades, with speed
vs. the 3900 and the price difference. When there is no upgrade, it shows the cheapest near-miss (a server
that fails just one rule) and why it failed.

## Setup

1. **Phone alerts.** Install the free **ntfy** app (iOS / Android), tap **+**
   and subscribe to a hard-to-guess topic, for example
   `hetzner-watch-7185a65316c3`. Anyone who knows the name can read it, so keep it private.

2. **Vercel project.** On vercel.com click **Add New → Project**, import the
   `hetzner-auction-watch` GitHub repo and deploy it (Vercel detects the Flask app in `app.py`; no build settings needed).
   Every push to `main` redeploys it.

3. **State store.** In the project open **Storage → Create Database → Upstash
   for Redis** (free plan) and connect it to the project. This adds
   `KV_REST_API_URL` / `KV_REST_API_TOKEN`; already-notified servers are kept there.

4. **Environment variables** (project **Settings → Environment Variables**, Production):
   - `NTFY_TOPIC`: your ntfy topic name
   - `CRON_SECRET`: a long random string (only callers that send it can run a check)

   Then **Deployments → ⋯ → Redeploy** so the function picks them up.

5. **Run it every minute.** On cron-job.org create (or edit) a job:
   URL `https://<your-project>.vercel.app/api/check`, every minute, method GET,
   and under **Advanced → Headers** add `Authorization: Bearer <CRON_SECRET>`.
   A `200` response is a successful check; open a run's details in cron-job.org
   to read its report.

To test locally without Vercel: `python watch.py` for one check, or `python app.py`
and open http://127.0.0.1:5000/api/check (both keep state in `state.json`,
and only print the notification unless `NTFY_TOPIC` is set).

## Good to know

- If the auction feed can't be fetched, the run stays silent and returns
  `502` with "Check failed" in its report.
- Already-notified servers are stored in Redis (key `hetzner-auction-watch:state`);
  you get an alert again only if one gets $3+ cheaper. Delete the key to be
  alerted about everything again.
- The daily note is sent by the first successful run at or after 12:00 Cairo
  time, so a missed or failed run only delays it by a minute.
- If a server with a CPU the script doesn't know fits the other rules, it's
  listed under "Unrecognised CPUs" in the report. Add it to `CPUS` in
  `watch.py` with its PassMark score to include it.
- Each notification shows the new upgrade(s) or cheaper 3900 with their details. When nothing
  fits, the report shows the cheapest near-miss and the rule it failed.
- Hetzner bills hourly, so when an upgrade shows up you can order the new one,
  migrate, then cancel the old one. Price drops are shown with their exact
  time in `TIMEZONE` (Africa/Cairo); the feed doesn't say how much a drop will be.
- To change the rules (budget, RAM, etc.) edit the settings at the top of `watch.py`
  and push; Vercel redeploys automatically.
- To stop: pause the cron job on cron-job.org.
