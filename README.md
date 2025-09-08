#### GitHub + Algora Bounty Digest

Polls GitHub for “bounty-ish” issues and Algora org bounties, stores them in SQLite, and posts a periodic Slack digest (+ optional CSV).

- 🔎 **Filters**: label OR-filters, optional language filters (from your GitHub profile or a static list), optional repo allow-list  
- 💬 **Slack**: single header message; auto-splits across messages when long (no threads if `POST_LONG_AS_THREAD=false`)  
- 🗃️ **Storage**: SQLite (auto-created); CSV export stamped with UTC timestamp  
- 🧪 **Modes**: `collect`, `digest`, `bootstrap`, `test_digest`, `langs`, `reset_recent`  
- 🪵 **Logging**: per-mode log files in `LOG_DIR`

---

### Prerequisites
- Python **3.10+** (tested with 3.11)
- A Slack app (bot token) or an incoming webhook URL
- A GitHub **Personal Access Token** (fine‑grained or classic)

---

### Quick Start

#### 1) Clone & create a virtualenv (uv or venv)

###### Note: **$USER configured for GNU/Linux machines, update as necessary.**

```bash
git clone https://github.com/bryanwills/repo-bounty.git
cd repo-bounty
uv venv
. .venv/bin/activate
uv pip install -r requirements.txt
```
---

#### 2) Configure .env file
```bash
cp .env.example .env
# edit .env file with proper variables
```

##### Alternative without uv (plain venv + pip3):

```bash
python3 -m venv .venv
. .venv/bin/activate
pip3 install -r requirements.txt
```

---

#### 3) First run: backfill last 7 days (modify .env to change this)
```bash
MODE=bootstrap /$HOME/repo-bounty/.venv/bin/python3 collector.py
```
#### **Minimum required in `.env`:**
#### - `GITHUB_TOKEN=` your PAT (fine‑grained: Public repos Read; Issues Read‑only; Metadata Read‑only)
#### - One Slack delivery path:
#### - `SLACK_BOT_TOKEN=` (recommended; scope `chat:write`, add `files:write` to upload CSVs), **and** invite the bot to the channel
#### - or `SLACK_WEBHOOK_URL=` as a fallback
#### - Paths in `.env.example` assume `$HOME/repo-bounty`.

---

#### 4) Running script to backfill info

#### Collect and send a single digest for the last `BOOTSTRAP_DAYS` (defaults to 7):

```bash
MODE=bootstrap "$HOME/repo-bounty/.venv/bin/python" collector.py
```

#### - Posts a Slack digest and writes a timestamped CSV (e.g., `bounty_digest_MMDDYYYY_HHMM.csv`) into `CSV_DIR`.
#### - Marks those rows as **notified** so hourly digests won’t resend them.

#### **If the backfill looks sparse**, set `USE_LANGUAGE_FILTER=false` in `.env` and run bootstrap again
#### (We search on `(created OR updated)`; disabling the language filter broadens results.)

---

#### 5) Create cronjobs if desired

#### Run collection every 10 minutes and digest hourly

```bash
*/10 * * * * MODE=collect /$HOME/repo-bounty/.venv/bin/python /home/$USER/repo-bounty/collector.py 2>>/home/$USER/repo-bounty/log/cron.err
0 * * * * MODE=digest /$HOME/repo-bounty/.venv/bin/python /home/$USER/repo-bounty/collector.py 2>>/home/$USER/repo-bounty/log/cron.err
5 2 * * * MODE=langs /$HOME/repo-bounty/.venv/bin/python /home/$USER/repo-bounty/collector.py 2>>/home/$USER/repo-bounty/log/cron.err
```

---

#### 6) Useful commands and modes
```bash
MODE=test_digest "$HOME/repo-bounty/.venv/bin/python" collector.py
```

#### **Manual collect or digest:**
```bash
MODE=collect "$HOME/repo-bounty/.venv/bin/python" collector.py
MODE=digest  "$HOME/repo-bounty/.venv/bin/python" collector.py
```

#### **Refresh profile languages cache (used when `USE_PROFILE_LANGS=true`):**
```bash
MODE=langs "$HOME/repo-bounty/.venv/bin/python" collector.py
```

#### **(Optional) Resend recent items** — if you added the `reset_recent` helper:
```bash
MODE=reset_recent RESET_MINUTES=10080 "$HOME/repo-bounty/.venv/bin/python" collector.py
MODE=digest "$HOME/repo-bounty/.venv/bin/python" collector.py
```

---

### Configuration notes

#### - **Language filtering**
####   - `USE_PROFILE_LANGS=true` learns a small set of top languages from your GitHub profile.
####   - `USE_LANGUAGE_FILTER=true|false` controls whether `language:` is used in Issue search. It can be over‑strict; set to `false` for backfills.
####   - If no items are found and the language filter is on, the script retries **without** the filter.

#### - **Labels**
####   - `LABELS=bounty,💎 Bounty,reward,algora` (OR‑combined). Tweak to your taste.

#### - **Repos**
####   - Restrict to specific repos with `REPOS=owner/repo,owner2/repo2` (optional).

#### - **Slack formatting**
####   - `MAX_SLACK_CHARS` controls splitting; with `POST_LONG_AS_THREAD=false`, the script splits into multiple top‑level posts.
####   - Set `SLACK_UNFURL=false` to suppress link previews.

#### - **CSV**
####   - File names include a UTC timestamp: `bounty_digest_MMDDYYYY_HHMM.csv`.
####   - Columns: `created_at_utc, source, repo, title, labels, url, amount, currency`.

#### - **Logging**
####   - Logs are written per‑mode into `LOG_DIR` (e.g., `collect.log`, `digest.log`, `bootstrap.log`).
####   - Set `LOG_LEVEL=DEBUG` to see the exact GitHub query URLs.

#### - **Algora**
####   - Add org slugs to `ALGORA_ORGS` (comma‑separated) to include **active** bounties from those orgs via the public API.
####   - Leave empty to skip Algora.

---

### Troubleshooting

**No Slack messages appear**
- Ensure the bot is **invited** to the target channel (`/invite @YourAppName`).
- Verify `SLACK_BOT_TOKEN` (or `SLACK_WEBHOOK_URL`) in `.env`.
- Check logs in `LOG_DIR/digest.log`.
- Test with temp fresh database ```BOUNTY_DB=/$USER/repo-bounty/bounties_test.db MODE=bootstrap python3 collector.py```

**`not_in_channel` or `channel_not_found`**
- Invite the bot to the channel, or use a **channel ID** instead of `#name`.

**Backfill returns 0 results**
- Confirm the search uses `(created OR updated)` (the script does).
- Temporarily set `USE_LANGUAGE_FILTER=false` and re‑run `MODE=bootstrap`.

**Got results previously but now “nothing to send”**
- `digest` only sends rows with `notified=0`. Use `MODE=reset_recent` (if added) to flip recent rows back to `notified=0`, then run `MODE=digest`.

**HTTP 422 from GitHub Search**
- Clean your `REPOS` list (must be `owner/repo`, no `#`, no quotes).
- Reduce query complexity if you customized heavily.

**Rate limiting**
- Lower the frequency of `collect`, or raise `WINDOW_MINUTES` to widen each poll and call the API less often.

**CSV not uploaded to Slack**
- You need `files:write` on the bot and `UPLOAD_CSV_TO_SLACK=true`.



