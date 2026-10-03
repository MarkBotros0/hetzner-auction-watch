# Hetzner auction watch

Checks the Hetzner Server Auction every 5 minutes with GitHub Actions and
sends a phone notification (via ntfy) on every run: a high-priority alert when
a new server matches, and a low-priority status update otherwise
(`STATUS_PRIORITY` in `watch.py`). A server matches when it has:

- CPU at least ~20,000 PassMark (your i7-13700H is ~25,800); old i7/Xeon E3/E-2xxx/Ryzen 5 3600 etc. are skipped
- 64 GB+ RAM, 2+ NVMe drives of 512 GB or more
- no setup fee, total ≤ $80/month including the IPv4 address

Every run also writes a table of the top 5 compatible servers to the run's
summary page (Actions tab → click a run).

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
- If the auction feed can't be fetched, every failed run sends a "check failed" notification.
- Already-notified servers are stored in `state.json`; you get a high-priority
  alert again only if one gets $3+ cheaper. Delete its line to be alerted again.
- If a server with a CPU the script doesn't know fits the other rules, it's
  listed under "Unrecognised CPUs" in the run summary. Add it to `CPUS` in
  `watch.py` with its PassMark score to include it.
- To change the rules (budget, RAM, etc.) edit the settings at the top of `watch.py`.
- To stop: Actions tab → the workflow → **⋯ → Disable workflow**.
