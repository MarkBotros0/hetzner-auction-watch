# Hetzner auction watch

Checks the Hetzner Server Auction every 5 minutes with GitHub Actions and
sends a phone notification (via ntfy) only when a new upgrade over the current
server shows up. Runs without an upgrade stay silent; their result is still in
the run summary.

It is read-only: it never buys, reserves, cancels or logs in.

## Current server

Bought 2026-10-05 from the Hetzner auction: AMD Ryzen 7 PRO 1700X
(8C/16T, PassMark ~14,500), 64 GB DDR4 non-ECC, 2× 480 GB SATA Datacenter SSD,
no Intel NIC, FSN1. $61.90/mo excl. VAT incl. IPv4 ($60.00 server + $1.90 IPv4)
= $73.66/mo incl. 19% VAT. About 45% slower than the laptop (i7-13700H, PassMark ~25,800).

## What counts as an upgrade

The goal is a clear upgrade over the 1700X, not just any server. A server matches when it has:

- CPU at least ~20,000 PassMark (≥ ~35-40% faster than the 1700X); old i7/Xeon E3/E-2xxx/Xeon W-2145/Ryzen 5 3600/Ryzen 7 1700X etc. are skipped
- 32 GB+ RAM, 2+ SSDs of any size (NVMe or SATA SSD; HDDs never count)
- no setup fee, total ≤ $80/month excl. VAT including the IPv4 address

**Prices:** the auction list shows prices excl. VAT and already including
IPv4; the order page adds 19% VAT. Everything the watcher shows is USD excl.
VAT (EUR in brackets), plus the difference vs. the current $61.90 (e.g. "+$12.40/mo").

Every run also writes a table of the top 5 compatible upgrades to the run's
summary page (Actions tab → click a run), with speed vs. the 1700X and the price
difference. When there is no upgrade, it shows the cheapest near-miss (a server
that fails just one rule) and why it failed.

## Setup (about 10 minutes)

1. **Phone alerts.** Install the free **ntfy** app (iOS / Android), tap **+**
   and subscribe to a hard-to-guess topic, for example
   `hetzner-watch-7185a65316c3`. Anyone who knows the name can read it, so keep it private.

2. **Create the repository.** On github.com click **New repository**, name it
   `hetzner-auction-watch`, choose **Public** and create it.
   Public repos get unlimited free Actions minutes. If you pick **Private**,
   change the cron line in `.github/workflows/hetzner-watch.yml` to
   `*/30 * * * *`, or you'll go over the 2,000 free minutes a month.

3. **Upload the files.** In the new repo click **uploading an existing file**
   and drag in everything from this folder **including the `.github` folder**
   (on macOS press Cmd+Shift+. in Finder to show hidden folders), then
   **Commit changes**.
   If the `.github` folder doesn't upload, click **Add file → Create new file**,
   type `.github/workflows/hetzner-watch.yml` as the name, paste the file's
   contents and commit.

4. **Add the topic as a secret.** Repo **Settings → Secrets and variables →
   Actions → New repository secret**: name `NTFY_TOPIC`, value your topic name.

5. **Test it.** Open the **Actions** tab (enable workflows if GitHub asks),
   pick **Hetzner auction watch → Run workflow**. After ~30 seconds open the
   run to see the table. From now on it runs on its own every 10 minutes.

## Good to know

- GitHub sometimes starts scheduled runs a few minutes late at busy times.
- If the auction feed can't be fetched, the run stays silent and shows
  "Check failed" in its summary.
- Already-notified servers are stored in `state.json`; you get an alert again
  only if one gets $3+ cheaper. Delete its line to be alerted again.
- If a server with a CPU the script doesn't know fits the other rules, it's
  listed under "Unrecognised CPUs" in the run summary. Add it to `CPUS` in
  `watch.py` with its PassMark score to include it.
- Each notification shows the new upgrade(s) with their details. When nothing
  fits, the run summary shows the cheapest near-miss and the rule it failed.
- Hetzner bills hourly, so when an upgrade shows up you can order the new one,
  migrate, then cancel the 1700X. Price drops are shown with their exact
  time in `TIMEZONE` (Africa/Cairo); the feed doesn't say how much a drop will be.
- To change the rules (budget, RAM, etc.) edit the settings at the top of `watch.py`.
- To stop: Actions tab → the workflow → **⋯ → Disable workflow**.
