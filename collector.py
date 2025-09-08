#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, csv, json, time, sqlite3, pathlib, urllib.parse, requests, logging
from datetime import datetime, timedelta, timezone
from collections import Counter

# Load .env next to this file
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

# --------- helpers ---------
def env_bool(name: str, default: bool=False) -> bool:
    return str(os.getenv(name, "true" if default else "false")).lower() in ("1","true","yes","y","on")

def env_csv(name: str, default: str=""):
    s = os.getenv(name, default)
    return [x.strip() for x in s.split(",") if x.strip()]

def _q(token: str) -> str:
    if any(ch.isspace() for ch in token) or any(ch in token for ch in ['"', "'", ':', '#', '(', ')', '+']):
        return f'"{token}"'
    return token

def _clean_repos(csv_list):
    cleaned = []
    for r in csv_list:
        r = r.strip().strip('"').strip("'")
        if not r or r.startswith("#"):  # allow comments in .env
            continue
        if re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', r):
            cleaned.append(r)
    return cleaned

def chunk_text(text: str, limit: int):
    """Split long text into chunks <= limit, preferring line breaks."""
    chunks = []
    s = text
    while s:
        if len(s) <= limit:
            chunks.append(s); break
        cut = s.rfind("\n", 0, limit)
        if cut <= 0: cut = limit
        chunks.append(s[:cut])
        s = s[cut:].lstrip()
    return chunks

# --------- required config (.env) ---------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    raise SystemExit("Missing GITHUB_TOKEN in .env")

SLACK_BOT_TOKEN   = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
if not SLACK_BOT_TOKEN and not SLACK_WEBHOOK_URL:
    raise SystemExit("Provide SLACK_BOT_TOKEN or SLACK_WEBHOOK_URL in .env")

# --------- optional config ---------
MODE                = os.getenv("MODE", "collect")
GITHUB_USERNAME     = os.getenv("GITHUB_USERNAME", "bryanwills")
USE_PROFILE_LANGS   = env_bool("USE_PROFILE_LANGS", True)
STATIC_LANGUAGES    = env_csv("STATIC_LANGUAGES", "TypeScript,Go,Python")
LABELS              = env_csv("LABELS", "bounty,💎 Bounty,reward,algora")
REPOS_LIST          = _clean_repos(env_csv("REPOS", ""))
WINDOW_MINUTES      = int(os.getenv("WINDOW_MINUTES", "12"))

BOOTSTRAP_DAYS      = int(os.getenv("BOOTSTRAP_DAYS", "7"))
ALGORA_ORGS         = env_csv("ALGORA_ORGS", "")

SLACK_CHANNEL       = os.getenv("SLACK_CHANNEL", "#bounties")
DIGEST_LOOKBACK_MIN = int(os.getenv("DIGEST_LOOKBACK_MIN", "60"))
DIGEST_MIN_COUNT    = int(os.getenv("DIGEST_MIN_COUNT", "1"))
MAX_ITEMS_IN_DIGEST = int(os.getenv("MAX_ITEMS_IN_DIGEST", "50"))
MAX_SLACK_CHARS     = int(os.getenv("MAX_SLACK_CHARS", "3500"))
POST_LONG_AS_THREAD = env_bool("POST_LONG_AS_THREAD", True)
SLACK_UNFURL        = env_bool("SLACK_UNFURL", True)  # set false to suppress rich link previews

WRITE_CSV           = env_bool("WRITE_CSV", True)
CSV_DIR             = os.getenv("CSV_DIR", "./bounty_csv")
UPLOAD_CSV_TO_SLACK = env_bool("UPLOAD_CSV_TO_SLACK", False)

DB_PATH             = os.getenv("BOUNTY_DB", os.path.join(os.path.dirname(__file__), "bounties.db"))

# --------- logging ---------
LOG_DIR   = os.getenv("LOG_DIR", os.path.join(os.path.dirname(__file__), "log"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
pathlib.Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, f"{MODE}.log"),
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("bounty")

# --------- DB ---------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS pending(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, key TEXT, title TEXT, url TEXT, repo TEXT,
        labels TEXT, language TEXT,
        amount REAL, currency TEXT,
        created_at INTEGER, notified INTEGER DEFAULT 0
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_time ON pending(created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS meta(
        k TEXT PRIMARY KEY, v TEXT
    )""")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pending)")}
    if "amount"   not in cols: conn.execute("ALTER TABLE pending ADD COLUMN amount REAL")
    if "currency" not in cols: conn.execute("ALTER TABLE pending ADD COLUMN currency TEXT")
    conn.commit()
    return conn

def meta_get(conn, k, default=None):
    cur = conn.cursor()
    cur.execute("SELECT v FROM meta WHERE k=?", (k,))
    row = cur.fetchone()
    return json.loads(row[0]) if row else default

def meta_set(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, json.dumps(v)))
    conn.commit()

# --------- languages from profile ---------
def fetch_profile_languages():
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    langs = Counter(); page = 1
    while True:
        r = requests.get(f"https://api.github.com/users/{GITHUB_USERNAME}/repos?per_page=100&page={page}",
                         headers=headers, timeout=20)
        r.raise_for_status()
        repos = r.json()
        if not repos: break
        for repo in repos:
            lang = repo.get("language")
            if lang: langs[lang] += 1
        page += 1
        if page > 5: break
    top = [name for name, _ in langs.most_common(8)]
    return top or ["TypeScript","Go","Python"]

def ensure_languages(conn):
    if USE_PROFILE_LANGS:
        cached = meta_get(conn, "profile_languages")
        ts     = meta_get(conn, "profile_languages_ts")
        if cached and ts and (time.time()-ts) < 24*3600:
            return cached
        langs = fetch_profile_languages()
        meta_set(conn, "profile_languages", langs)
        meta_set(conn, "profile_languages_ts", int(time.time()))
        return langs
    return STATIC_LANGUAGES

# --------- GitHub search ---------
def github_search(languages, since_minutes):
    base = "https://api.github.com/search/issues"
    clauses = ["is:issue", "is:open"]

    label_or = " OR ".join([
        f'label:{_q(l)}' if (" " in l or l.startswith("💎") or ':' in l) else f"label:{l}"
        for l in LABELS
    ])
    clauses.append(f"({label_or})")

    if languages:
        clauses.append("(" + " OR ".join([f"language:{_q(l)}" for l in languages]) + ")")

    if REPOS_LIST:
        clauses.append("(" + " OR ".join([f"repo:{r}" for r in REPOS_LIST]) + ")")

    since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    clauses.append(f"created:>={since.strftime('%Y-%m-%dT%H:%M:%SZ')}")

    q = " ".join(clauses)
    url = f"{base}?q={urllib.parse.quote(q)}&sort=created&order=desc&per_page=100&advanced_search=true"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    r = requests.get(url, headers=headers, timeout=20)
    if r.status_code == 422:
        logger.error("[GitHub 422] Query: %s", q)
        logger.error("[GitHub 422] Response: %s", r.text[:500])
    r.raise_for_status()
    return r.json().get("items", [])

# --------- Algora (per-org API) ---------
def algora_list():
    if not ALGORA_ORGS:
        return []
    out = []
    for org in ALGORA_ORGS:
        cursor = None
        while True:
            url = f"https://console.algora.io/api/orgs/{org}/bounties?limit=100"
            if cursor: url += f"&cursor={cursor}"
            try:
                r = requests.get(url, timeout=20); r.raise_for_status()
            except Exception as e:
                logger.warning("[Algora] skip %s: %s", org, e); break
            data = r.json()
            for b in data.get("items", []):
                if b.get("status") != "active": continue
                issue = b.get("issue") or {}
                owner = b.get("repo_owner") or ""; name = b.get("repo_name") or ""
                out.append({
                    "id":       f"algora:{b.get('id')}",
                    "source":   "algora",
                    "title":    issue.get("title") or "(no title)",
                    "url":      issue.get("html_url") or f"https://algora.io/{org}/bounties",
                    "repo":     f"{owner}/{name}" if owner and name else "",
                    "labels":   [f"{b.get('currency','USD')} { (b.get('amount') or 0)/100:.2f}"],
                    "amount":   (b.get("amount") or 0)/100.0,
                    "currency": b.get("currency") or "USD",
                    "created_at": int(time.time()),
                })
            cursor = data.get("next_cursor")
            if not cursor: break
    return out

# --------- collect ---------
def upsert_pending(conn, source, key, title, url, repo, labels, language, amount=None, currency=None, created_at=None):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pending WHERE source=? AND key=?", (source, key))
    if cur.fetchone(): return False
    conn.execute("""INSERT INTO pending(source,key,title,url,repo,labels,language,amount,currency,created_at,notified)
                    VALUES(?,?,?,?,?,?,?,?,?,?,0)""",
                 (source, key, title, url, repo, json.dumps(labels or []), language or "",
                  amount, currency, created_at or int(time.time())))
    conn.commit()
    return True

def collect(since_minutes=None, include_algora=True):
    conn = db()
    languages = ensure_languages(conn)
    logger.info("[collect] languages: %s", languages)

    # GitHub
    gh_new = 0
    try:
        for it in github_search(languages, since_minutes or WINDOW_MINUTES):
            if upsert_pending(
                conn,
                source="github",
                key=f"gh:{it['id']}",
                title=it.get("title",""),
                url=it.get("html_url",""),
                repo=(it.get("repository_url","").split("repos/")[-1] if "repos/" in it.get("repository_url","") else ""),
                labels=[l["name"] for l in it.get("labels", [])],
                language=""
            ):
                gh_new += 1
    except requests.HTTPError as e:
        logger.error("[collect] GitHub error: %s", e)

    # Algora
    al_new = 0
    if include_algora:
        try:
            for b in algora_list():
                if upsert_pending(conn,
                    source=b["source"], key=b["id"], title=b["title"], url=b["url"],
                    repo=b["repo"], labels=b["labels"], language="",
                    amount=b.get("amount"), currency=b.get("currency"),
                    created_at=b.get("created_at")):
                    al_new += 1
        except Exception as e:
            logger.error("[collect] Algora error: %s", e)

    logger.info("[collect] inserted gh=%d, algora=%d", gh_new, al_new)

# --------- Slack ---------
def post_slack_bot(text: str):
    if not SLACK_BOT_TOKEN: return {"ok": False, "error": "no_bot_token"}
    url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-type": "application/json; charset=utf-8"}
    payload = {"channel": SLACK_CHANNEL, "text": text}
    if not SLACK_UNFURL:
        payload.update({"unfurl_links": False, "unfurl_media": False})
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    try: return r.json()
    except Exception: return {"ok": False, "error": f"bad_json:{r.text[:200]}", "status": r.status_code}

def post_slack_thread(ts: str, text: str):
    url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-type": "application/json; charset=utf-8"}
    payload = {"channel": SLACK_CHANNEL, "text": text, "thread_ts": ts}
    if not SLACK_UNFURL:
        payload.update({"unfurl_links": False, "unfurl_media": False})
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    try: return r.json()
    except Exception: return {"ok": False}

def post_slack_webhook(text: str):
    if not SLACK_WEBHOOK_URL: return {"ok": False, "error": "no_webhook"}
    r = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
    if r.status_code == 200 and r.text.strip() in ("ok",""): return {"ok": True}
    return {"ok": False, "error": f"webhook_status_{r.status_code}:{r.text[:120]}"}

def upload_file_to_slack(path: str, title: str):
    if not (SLACK_BOT_TOKEN and UPLOAD_CSV_TO_SLACK):
        return {"ok": False, "error": "upload_disabled"}
    url = "https://slack.com/api/files.upload"
    with open(path, "rb") as f:
        files = {"file": f}
        data  = {"channels": SLACK_CHANNEL, "filename": os.path.basename(path), "title": title}
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        r = requests.post(url, headers=headers, files=files, data=data, timeout=30)
        try: return r.json()
        except Exception: return {"ok": False, "error": f"upload_http_{r.status_code}"}

# --------- digest ---------
def make_digest_text(rows, lookback_min: int):
    count = len(rows)
    header = f"💎 Bounty digest — last {lookback_min} min · {count} item{'s' if count!=1 else ''}"
    by_source = Counter(r["source"] for r in rows)
    by_repo   = Counter(r["repo"] for r in rows if r["repo"])
    top_repos = ", ".join([f"{k} ({v})" for k,v in by_repo.most_common(5)]) if by_repo else "—"

    def bullet(r):
        amount = ""
        if r.get("amount") not in (None, ""):
            try: amount = f"${float(r['amount']):.0f}"
            except Exception: amount = ""
        elif r.get("labels"):
            for lab in r["labels"]:
                if lab.startswith("USD "):
                    try: amount = f"${float(lab.split(' ',1)[1]):.0f}"; break
                    except Exception: pass
        repo = r.get("repo",""); title = r.get("title",""); url = r.get("url","")
        tstr = datetime.fromtimestamp(r["created_at"], timezone.utc).strftime("%H:%MZ")
        amt = f"{amount} — " if amount else ""; rep = f"{repo} — " if repo else ""
        return f"• {amt}{rep}{title}\n  {url} ({tstr})"

    bullets = "\n".join(bullet(r) for r in rows)
    body = f"{header}\nSources: {dict(by_source)}\nTop repos: {top_repos}\n\n{bullets}"
    if len(body) <= MAX_SLACK_CHARS:
        return body, None
    short = f"{header}\nSources: {dict(by_source)}\nTop repos: {top_repos}\n\n(Details in thread ⤵️)"
    return short, bullets

def write_csv(rows, target_dir: str):
    pathlib.Path(target_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    path = os.path.join(target_dir, f"bounty_digest_{ts}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["created_at_utc","source","repo","title","labels","url","amount","currency"])
        for r in rows:
            t = datetime.fromtimestamp(r["created_at"], timezone.utc).isoformat()
            w.writerow([t, r["source"], r["repo"], r["title"], "|".join(r["labels"]), r["url"],
                        "" if r.get("amount") is None else r.get("amount"),
                        r.get("currency","")])
    logger.info("[csv] wrote %s", path)
    return path

def digest(lookback_override_min=None):
    conn = db()
    now = int(time.time())
    lookback = lookback_override_min or DIGEST_LOOKBACK_MIN
    since = now - (lookback * 60)
    cur = conn.cursor()
    cur.execute("""SELECT id,source,key,title,url,repo,labels,language,amount,currency,created_at
                   FROM pending
                   WHERE created_at>=? AND notified=0
                   ORDER BY created_at DESC
                   LIMIT ?""", (since, MAX_ITEMS_IN_DIGEST))
    rows_raw = cur.fetchall()
    if len(rows_raw) < DIGEST_MIN_COUNT:
        logger.info("[digest] nothing to send"); return

    rows = []
    for r in rows_raw:
        rid, source, key, title, url, repo, labels_json, language, amount, currency, ts = r
        rows.append({
            "id": rid, "source": source, "key": key, "title": title, "url": url,
            "repo": repo, "labels": json.loads(labels_json) if labels_json else [],
            "language": language, "created_at": ts,
            "amount": amount, "currency": currency
        })

    text, thread_details = make_digest_text(rows, lookback)

    # post header
    res = post_slack_bot(text)
    posted, thread_ts = False, None
    if res.get("ok"):
        posted = True
        thread_ts = res.get("ts") or (res.get("message", {}) or {}).get("ts")
        # if details are long, chunk into multiple thread replies
        if thread_details and POST_LONG_AS_THREAD and thread_ts:
            for i, chunk in enumerate(chunk_text(thread_details, MAX_SLACK_CHARS)):
                prefix = "" if i == 0 else "(cont.)\n"
                post_slack_thread(thread_ts, prefix + chunk)

    # webhook fallback (sends everything as one message)
    if not posted and SLACK_WEBHOOK_URL:
        res2 = post_slack_webhook(text if not thread_details else f"{text}\n\n{thread_details}")
        posted = res2.get("ok", False)

    # CSV
    if WRITE_CSV:
        path = write_csv(rows, CSV_DIR)
        if posted and UPLOAD_CSV_TO_SLACK:
            upload_file_to_slack(path, title="Bounty digest CSV")

    if posted:
        ids = [r["id"] for r in rows]
        conn.executemany("UPDATE pending SET notified=1 WHERE id=?", [(i,) for i in ids])
        conn.commit()
        logger.info("[digest] sent %d item(s)", len(ids))
    else:
        logger.error("[digest] failed to post; items remain un-notified")

# --------- test & bootstrap ---------
def inject_dummy():
    conn = db()
    upsert_pending(conn,
        source="test", key=f"dummy:{int(time.time())}",
        title="(TEST) Example Bounty Title",
        url="https://example.com/bounty",
        repo="owner/repo",
        labels=["USD 123.00","bounty"],
        language="Python",
        amount=123.0, currency="USD",
        created_at=int(time.time()))
    logger.info("[test] dummy row inserted")

def bootstrap():
    minutes = max(1, BOOTSTRAP_DAYS * 1440)
    logger.info("[bootstrap] collecting last %d day(s) (%d minutes)...", BOOTSTRAP_DAYS, minutes)
    collect(since_minutes=minutes, include_algora=True)
    logger.info("[bootstrap] sending digest…")
    digest(lookback_override_min=minutes)
    logger.info("[bootstrap] done")

# --------- main ---------
if __name__ == "__main__":
    try:
        if MODE == "digest":
            digest()
        elif MODE == "langs":
            conn = db()
            logger.info("[langs] profile languages: %s", ensure_languages(conn))
        elif MODE == "test_digest":
            inject_dummy()
            digest(lookback_override_min=60)
        elif MODE == "bootstrap":
            bootstrap()
        else:
            collect()
    except KeyboardInterrupt:
        pass
