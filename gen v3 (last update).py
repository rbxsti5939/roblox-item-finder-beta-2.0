"""
Roblox Item Finder BETA 1.0 - FULLY FUNCTIONAL
All 14 features working with proper UI, file dialogs, threading, and database
"""

import csv
import json
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import os
import queue
import random
import re
import sqlite3
import sys
import threading
import time
import webbrowser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests")
    input("Press Enter...")
    raise SystemExit

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
    HAVE_TK = True
except ImportError:
    HAVE_TK = False

# ---- PATHS ----
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR
if not (APP_DIR / "roblox_items.db").exists():
    for candidate in (APP_DIR / "folder", APP_DIR.parent / "folder"):
        if (candidate / "roblox_items.db").exists():
            DATA_DIR = candidate
            break
# ---- SETTINGS ----
VERSION = "BETA 1.0"
DEFAULT_RATE = 10
MIN_PRICE = 2
ID_MIN, ID_MAX = 1_000_000, 20_000_000
SPREAD = 2_000_000
PROXY_TIMEOUT = (4, 8)
PROXY_MAX_FAILS = 3
MAX_ACTIVE_PROXIES = 100
PROXY_TEST_WORKERS = 100
SPEED_ADJUST_THRESHOLD = 0.15
PER_PROXY_INTERVAL = 1.0        # seconds between requests through the SAME proxy (feature 2)
SKIP_SAVED_IDS = True           # search deeper pages instead of re-checking items you already saved
PROXY_COOLDOWN_429 = 45        # seconds a proxy rests after Roblox rate-limits it
MIN_POOL_BEFORE_REFILL = 5     # auto-refill when fewer working proxies than this
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
]

NOTE_FILE = DATA_DIR / "generated_ids.txt"
VALID_FILE = DATA_DIR / "valid_items.txt"
PROXY_FILE = DATA_DIR / "proxies.txt"
DB_FILE = DATA_DIR / "roblox_items.db"
WEBHOOK_FILE = DATA_DIR / "webhook_config.txt"
CHECKPOINT_FILE = DATA_DIR / "resume_state.json"
SEARCH_HISTORY_FILE = DATA_DIR / "search_history.json"
SETTINGS_FILE = APP_DIR / "settings.json"
SEARCH_PRESETS = {"Hats": "hat", "Hair": "hair accessory", "Heads": "head", "Dynamic heads": "dynamic head", "Classic faces": "classic face", "Classic shirts": "classic shirt", "Pants": "pants", "Accessories": "accessory", "Limited items": "limited"}

ALLOWED_TYPES = {
    "Hat", "HairAccessory", "FaceAccessory", "NeckAccessory", "ShoulderAccessory",
    "FrontAccessory", "BackAccessory", "WaistAccessory", "Face", "Head",
    "Shirt", "Pants", "T-Shirt", "TShirtAccessory", "ShirtAccessory",
    "PantsAccessory", "JacketAccessory", "SweaterAccessory", "ShortsAccessory",
    "LeftShoeAccessory", "RightShoeAccessory", "DressSkirtAccessory", "DynamicHead",
}

TYPE_PRESETS = {
    "All wearable items": ALLOWED_TYPES,
    "Accessories only": {
        "Hat", "HairAccessory", "FaceAccessory", "NeckAccessory", "ShoulderAccessory",
        "FrontAccessory", "BackAccessory", "WaistAccessory",
    },
    "Hair only": {"HairAccessory"},
    "Hats only": {"Hat"},
    "Clothing only": {
        "Shirt", "Pants", "T-Shirt", "TShirtAccessory", "ShirtAccessory",
        "PantsAccessory", "JacketAccessory", "SweaterAccessory", "ShortsAccessory",
        "DressSkirtAccessory",
    },
    "Heads and faces": {"Face", "Head", "DynamicHead", "FaceAccessory"},
    "Dynamic heads only": {"DynamicHead"},
    "Shoes only": {"LeftShoeAccessory", "RightShoeAccessory"},
}

SORT_OPTIONS = {"Order found": "found", "Price: low to high": "low", "Price: high to low": "high"}
COUNTRY_CODES = {"US": "us", "GB": "gb", "CA": "ca", "AU": "au", "DE": "de", "FR": "fr", "JP": "jp", "All": "all"}

URL = "https://economy.roblox.com/v2/assets/{}/details"
SEARCH_URL = "https://catalog.roblox.com/v1/search/items/details"
ITEM_URL = "https://www.roblox.com/catalog/{}"
PROXY_TEST_URL = URL.format(1)
PROXYSCRAPE_URL = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&protocol=http&timeout=10000&country={country}&ssl={ssl}"

ASSET_TYPES = {
    1: "Image", 3: "Audio", 4: "Mesh", 5: "Lua", 9: "Place", 10: "Model",
    13: "Decal", 21: "Badge", 24: "Animation", 34: "GamePass", 38: "Plugin",
    40: "MeshPart", 62: "Video",
    2: "T-Shirt", 8: "Hat", 11: "Shirt", 12: "Pants", 17: "Head", 18: "Face",
    19: "Gear", 41: "HairAccessory", 42: "FaceAccessory", 43: "NeckAccessory",
    44: "ShoulderAccessory", 45: "FrontAccessory", 46: "BackAccessory",
    47: "WaistAccessory", 64: "TShirtAccessory", 65: "ShirtAccessory",
    66: "PantsAccessory", 67: "JacketAccessory", 68: "SweaterAccessory",
    69: "ShortsAccessory", 70: "LeftShoeAccessory", 71: "RightShoeAccessory",
    72: "DressSkirtAccessory", 79: "DynamicHead",
}

THEME = {
    "bg": "#101923", "fg": "#d7e7f0", "field": "#182a38",
    "btn": "#176b87", "btn_active": "#2389a8", "sel_bg": "#2389a8",
    "sel_fg": "#ffffff", "border": "#305066"
}

# =============================================================== DATABASE
def load_settings():
    defaults = {
        "results_dir": str(DATA_DIR), "request_speed": DEFAULT_RATE,
        "startup_tab": "Generator", "restore_proxies": True, "proxy_interval": PER_PROXY_INTERVAL,
        "webhook_enabled": False, "webhook_limited_only": False,
        "notifications": True, "keyword_presets": {}
    }
    try:
        loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            defaults.update({key: value for key, value in loaded.items() if key in defaults})
    except (OSError, ValueError):
        pass
    if not isinstance(defaults.get("keyword_presets"), dict):
        defaults["keyword_presets"] = {}
    return defaults

def save_settings_file(settings):
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")

def set_data_directory(folder):
    global DATA_DIR, NOTE_FILE, VALID_FILE, PROXY_FILE, DB_FILE, WEBHOOK_FILE, CHECKPOINT_FILE, SEARCH_HISTORY_FILE
    DATA_DIR = Path(folder).expanduser().resolve()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    NOTE_FILE = DATA_DIR / "generated_ids.txt"
    VALID_FILE = DATA_DIR / "valid_items.txt"
    PROXY_FILE = DATA_DIR / "proxies.txt"
    DB_FILE = DATA_DIR / "roblox_items.db"
    WEBHOOK_FILE = DATA_DIR / "webhook_config.txt"
    CHECKPOINT_FILE = DATA_DIR / "resume_state.json"
    SEARCH_HISTORY_FILE = DATA_DIR / "search_history.json"
def _parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None: parsed = parsed.astimezone()
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY, name TEXT, type TEXT, creator TEXT,
        price INTEGER, limited TEXT, url TEXT, found_at TIMESTAMP, run_id TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER, price INTEGER, checked_at TIMESTAMP, PRIMARY KEY(id, checked_at)
    )""")
    conn.commit()
    return conn

def save_to_db(h, run_id, conn):
    c = conn.cursor()
    try:
        c.execute("""INSERT OR IGNORE INTO items (id, name, type, creator, price, limited, url, found_at, run_id)
                     VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)""",
                  (h["id"], h["name"], h["type"], h["creator"], h["price"], h["limited"] or "", h["url"], run_id))
        if h["price"] is not None:
            c.execute("""INSERT OR IGNORE INTO price_history (id, price, checked_at)
                         VALUES (?, ?, datetime('now'))""", (h["id"], h["price"]))
        conn.commit()
    except:
        pass

def load_saved_ids():
    """Read prior result IDs so the checker can avoid duplicate output."""
    saved = set()
    if not VALID_FILE.exists():
        return saved
    try:
        for line in VALID_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            first = line.split(" | ", 1)[0].strip()
            if first.isdigit():
                saved.add(int(first))
    except OSError:
        pass
    return saved
_JSON_LOCK = threading.RLock()

def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with _JSON_LOCK:
        temp.write_text(payload, encoding="utf-8")
        temp.replace(path)

def read_json(path, fallback):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value
    except (OSError, ValueError, TypeError):
        return fallback

def saved_filters(filters):
    out = dict(filters)
    out["types"] = sorted(filters.get("types", ()))
    return out

def active_filters(filters):
    out = dict(filters)
    out["types"] = set(filters.get("types", ()))
    return out

def wait_if_paused(stop_event, pause_event):
    while not stop_event.is_set():
        if pause_event is None or pause_event.wait(0.2):
            return not stop_event.is_set()
    return False

# =============================================================== UTILITIES
def parse_keywords(text):
    """Split keyword input on commas, semicolons, or line breaks; retain spaces within phrases."""
    return [part.strip().strip('"').strip("'") for part in re.split(r"[,;\r\n]+", text) if part.strip().strip('"').strip("'")]
def parse_proxy_text(text):
    out, seen, bad = [], set(), 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.search(r"(https?|socks\w+)?://", line)
        if not m:
            line = "http://" + line
        if line not in seen:
            seen.add(line)
            out.append(line)
        else:
            bad += 1
    return out, bad

def parse_id_text(text):
    """Return unique positive IDs plus counts for duplicate and invalid input lines."""
    ids, seen, duplicates, invalid = [], set(), 0, 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"\d+", line)
        if not match:
            match = re.search(r"(?:/catalog/|[?&]id=)(\d+)(?:/|$)", line, re.IGNORECASE)
        if not match:
            invalid += 1
            continue
        value = int(match.group(1) if match.lastindex else match.group(0))
        if value <= 0 or value > 2**63 - 1:
            invalid += 1
        elif value in seen:
            duplicates += 1
        else:
            seen.add(value)
            ids.append(value)
    return ids, duplicates, invalid
def beep(freq=1000, ms=100):
    def play():
        try:
            import winsound
            winsound.Beep(freq, ms)
        except:
            sys.stdout.write("\a")
    threading.Thread(target=play, daemon=True).start()

# =============================================================== DISCORD WEBHOOK
DISCORD_HOOK_RE = re.compile(r"^https://(?:ptb\.|canary\.)?(?:discord|discordapp)\.com/api/webhooks/\d+/[\w-]+/?$")

def read_webhook_url():
    try:
        return WEBHOOK_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

def write_webhook_url(url):
    """The webhook URL works like a password, so it lives in its own file rather than in settings.json."""
    if url:
        WEBHOOK_FILE.write_text(url + "\n", encoding="utf-8")
    else:
        WEBHOOK_FILE.unlink(missing_ok=True)

def build_webhook_payload(info):
    price = info.get("price")
    price_text = "Off-sale" if price is None else ("Free" if price == 0 else f"{price:,} R$")
    limited = info.get("limited")
    fields = [
        {"name": "Price", "value": price_text, "inline": True},
        {"name": "Type", "value": str(info.get("type") or "?")[:100], "inline": True},
        {"name": "Creator", "value": str(info.get("creator") or "?")[:100] or "?", "inline": True},
    ]
    if limited:
        fields.append({"name": "Limited", "value": str(limited), "inline": True})
    fields.append({"name": "Asset ID", "value": str(info.get("id")), "inline": True})
    embed = {
        "title": (str(info.get("name") or "Unnamed item"))[:250],
        "url": info.get("url") or ITEM_URL.format(info.get("id")),
        "color": 0xF2C94C if limited else 0x2389A8,
        "fields": fields,
        "footer": {"text": f"Roblox Item Finder {VERSION}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    # Item names are user-generated, so never let a message ping anyone.
    return {"username": "Item Finder", "embeds": [embed], "allowed_mentions": {"parse": []}}

def post_webhook(url, payload, attempts=4):
    """POST to Discord, honouring its rate limit. Returns (ok, message)."""
    last = "Unknown error"
    for _ in range(attempts):
        try:
            r = requests.post(url, json=payload, timeout=10)
        except requests.RequestException as exc:
            last = f"Could not reach Discord ({type(exc).__name__})"
            time.sleep(2)
            continue
        if r.status_code in (200, 204):
            return True, "Sent."
        if r.status_code == 429:
            try:
                wait = float(r.json().get("retry_after", 1))
            except (ValueError, TypeError, AttributeError):
                try:
                    wait = float(r.headers.get("Retry-After", "1"))
                except ValueError:
                    wait = 1.0
            last = "Discord rate-limited the webhook"
            time.sleep(min(30.0, wait + 0.25))
            continue
        if r.status_code in (401, 403, 404):
            return False, f"Discord rejected the webhook (HTTP {r.status_code}). It may have been deleted, or the URL is wrong."
        last = f"Discord returned HTTP {r.status_code}"
        time.sleep(2)
    return False, last

class WebhookSender:
    """Sends alerts from a background thread so a slow Discord never slows down checking."""
    def __init__(self, on_error=None):
        self.q = queue.Queue(maxsize=200)
        self.on_error = on_error
        self.lock = threading.Lock()
        self.thread = None
        self.last_error = ("", 0.0)

    def send(self, url, payload):
        try:
            self.q.put_nowait((url, payload))
        except queue.Full:
            return
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()

    def _run(self):
        while True:
            try:
                url, payload = self.q.get(timeout=30)
            except queue.Empty:
                return
            ok, message = post_webhook(url, payload)
            if not ok and self.on_error:
                text, when = self.last_error
                if message != text or time.monotonic() - when > 60:
                    self.last_error = (message, time.monotonic())
                    self.on_error(f"Discord alert failed: {message}")
            time.sleep(0.5)

# =============================================================== PROXIES
class ProxyPool:
    def __init__(self):
        self.lock = threading.Lock()
        self.alive = []
        self.fails = {}
        self.health = {}
        self.cool = {}
        self.next_ok = {}
        self.interval = PER_PROXY_INTERVAL
        self.uses = {}
        self.last_used = {}
        self.i = 0

    def cooldown(self, proxy, seconds=PROXY_COOLDOWN_429):
        with self.lock:
            self.cool[proxy] = time.monotonic() + seconds

    def in_use_snapshot(self, within=10.0):
        now = time.monotonic()
        with self.lock:
            return {p for p, t in self.last_used.items() if now - t <= within}

    def add(self, proxies):
        with self.lock:
            have = set(self.alive)
            added = 0
            for p in proxies:
                if p not in have:
                    self.alive.append(p)
                    self.fails[p] = 0
                    self.health.setdefault(p, {"status": "Untested", "failures": 0, "last_checked": "Never", "latency_ms": None, "catalog": "-"})
                    have.add(p)
                    added += 1
            return added

    def replace(self, proxies):
        with self.lock:
            self.alive = list(dict.fromkeys(proxies))[:MAX_ACTIVE_PROXIES]
            self.fails = {p: 0 for p in self.alive}
            now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            for p in self.alive:
                self.health.setdefault(p, {"status": "Untested", "failures": 0, "last_checked": "Never"})
                if self.health[p].get("status") in ("Failed", "Offline"):
                    self.health[p].update(status="Untested", failures=0, last_checked=now)
            self.i = 0

    def get(self, exclude=None, need_catalog=False, throttle=True):
        """Next usable proxy. Skips proxies that are resting after an error and, when throttle=True,
        proxies that were used less than `interval` seconds ago (per-proxy rate limit)."""
        with self.lock:
            if not self.alive:
                return None
            excluded = set(exclude or ())
            now = time.monotonic()
            lists = [self.alive]
            if need_catalog:
                preferred = [p for p in self.alive if self.health.get(p, {}).get("catalog") == "OK"]
                if preferred:
                    lists = [preferred, self.alive]
            for lst in lists:
                n = len(lst)
                for k in range(n):
                    candidate = lst[(self.i + 1 + k) % n]
                    if candidate in excluded or self.cool.get(candidate, 0) > now:
                        continue
                    if throttle and self.next_ok.get(candidate, 0) > now:
                        continue
                    self.i += k + 1
                    self.uses[candidate] = self.uses.get(candidate, 0) + 1
                    self.last_used[candidate] = now
                    if throttle:
                        self.next_ok[candidate] = now + self.interval
                    return candidate
            return None

    def get_wait(self, exclude=None, need_catalog=False, timeout=12.0):
        """Like get(), but waits (up to timeout) for a proxy whose per-proxy interval has elapsed."""
        deadline = time.monotonic() + timeout
        while True:
            proxy = self.get(exclude=exclude, need_catalog=need_catalog)
            if proxy is not None or self.count() == 0 or time.monotonic() >= deadline:
                return proxy
            time.sleep(0.05)

    def ok(self, proxy):
        with self.lock:
            now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            item = self.health.setdefault(proxy, {"status": "Untested", "failures": 0, "last_checked": "Never"})
            item.update(status="Working", failures=0, last_checked=now)
            if proxy in self.fails:
                self.fails[proxy] = 0

    def mark_test(self, proxy, working, latency=None, catalog=None):
        with self.lock:
            now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            item = self.health.setdefault(proxy, {"status": "Untested", "failures": 0, "last_checked": "Never"})
            item.update(status="Working" if working else "Offline", last_checked=now)
            if latency is not None:
                item["latency_ms"] = latency
            if catalog is not None:
                item["catalog"] = catalog
            if working:
                item["failures"] = 0
            else:
                item["failures"] = int(item.get("failures", 0)) + 1

    def health_snapshot(self):
        with self.lock:
            active = set(self.alive)
            now = time.monotonic()
            return [dict(proxy=p, active=p in active, uses=self.uses.get(p, 0),
                         in_use=(now - self.last_used.get(p, -1e9)) <= 10.0, **data)
                    for p, data in sorted(self.health.items())]

    def fail(self, proxy):
        with self.lock:
            if proxy not in self.fails:
                return
            self.fails[proxy] += 1
            now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            item = self.health.setdefault(proxy, {"status": "Untested", "failures": 0, "last_checked": "Never"})
            item.update(status="Failed", failures=self.fails[proxy], last_checked=now)
            if self.fails[proxy] >= PROXY_MAX_FAILS:
                del self.fails[proxy]
                try:
                    self.alive.remove(proxy)
                except:
                    pass

    def remove_failed(self):
        with self.lock:
            removed = [p for p in self.alive if self.health.get(p, {}).get("status") in ("Failed", "Offline")]
            if removed:
                rejected = set(removed)
                self.alive = [p for p in self.alive if p not in rejected]
                for proxy in removed:
                    self.fails.pop(proxy, None)
            return removed

    def count(self):
        with self.lock:
            return len(self.alive)

    def snapshot(self):
        with self.lock:
            return list(self.alive)

def save_proxies(pool):
    try:
        PROXY_FILE.write_text("\n".join(pool.snapshot()) + "\n", encoding="utf-8")
    except:
        pass

def scrape_proxies(country, https_only, log):
    """Pull HTTP proxies from ProxyScrape plus several GitHub lists that re-check hourly."""
    urls = [PROXYSCRAPE_URL.format(country=COUNTRY_CODES.get(country, "all"), ssl="yes" if https_only else "all")] + PROXY_SOURCES
    log(f"Scraping proxies ({country}, {'HTTPS' if https_only else 'all'}) from {len(urls)} sources...")
    merged, seen = [], set()
    for url in urls:
        host = url.split("/")[2]
        try:
            r = requests.get(url, timeout=20)
            if r.status_code != 200:
                log(f"  {host}: HTTP {r.status_code}")
                continue
            proxies, _bad = parse_proxy_text(r.text)
            fresh = [p for p in proxies if p not in seen]
            seen.update(fresh); merged.extend(fresh)
            log(f"  {host}: {len(proxies)} proxies ({len(fresh)} new)")
        except Exception as exc:
            log(f"  {host}: failed ({type(exc).__name__})")
    if not merged:
        log("Proxy scrape failed: no source returned proxies.")
    else:
        random.shuffle(merged)
        log(f"Total unique proxies scraped: {len(merged)}")
    return merged

def test_proxies(proxies, log, stop, pool=None, details=None):
    """Test each proxy against BOTH Roblox hosts the app uses. A proxy is kept if the item API works;
    its catalog status (OK / Rate-limited / ...) and latency are recorded. Returns (good sorted fastest-first, stopped)."""
    def test(p):
        if stop.is_set():
            return p, False, None, "-"
        prox = {"http": p, "https": p}
        try:
            t0 = time.monotonic()
            r = requests.get(PROXY_TEST_URL, proxies=prox, timeout=PROXY_TIMEOUT)
            ms = int((time.monotonic() - t0) * 1000)
            if r.status_code not in (200, 400, 404):
                return p, False, ms, "-"
        except Exception:
            return p, False, None, "-"
        try:
            c = requests.get(SEARCH_URL, params={"Keyword": "hat", "Limit": 10}, proxies=prox, timeout=PROXY_TIMEOUT)
            catalog = "OK" if c.status_code == 200 else ("Rate-limited" if c.status_code == 429 else f"HTTP {c.status_code}")
        except Exception:
            catalog = "Failed"
        return p, True, ms, catalog

    results, done = [], 0
    ex = ThreadPoolExecutor(max_workers=PROXY_TEST_WORKERS)
    try:
        for fut in as_completed([ex.submit(test, p) for p in proxies]):
            if stop.is_set():
                break
            done += 1
            p, ok, ms, catalog = fut.result()
            if pool:
                pool.mark_test(p, ok, ms, catalog)
            if details is not None:
                details[p] = (ms, catalog)
            if ok:
                results.append((ms if ms is not None else 10**9, p, catalog))
            if done % 100 == 0:
                log(f"Tested {done}/{len(proxies)}, {len(results)} working")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    results.sort()
    good = [p for _ms, p, _c in results]
    if results:
        ms_list = [ms for ms, _p, _c in results if ms < 10**9]
        cat_ok = sum(1 for _ms, _p, c in results if c == "OK")
        fastest = f", fastest {ms_list[0]} ms, median {ms_list[len(ms_list)//2]} ms" if ms_list else ""
        log(f"Test finished: {len(good)}/{done} working; {cat_ok} can also use the catalog search{fastest}.")
    else:
        log(f"Test finished: 0/{done} working.")
    return good, stop.is_set()

def diagnose_connection(pool, log):
    """Feature 3: report the real HTTP status for your own IP and a few proxies, then say what it means."""
    def hit(label, url, params=None, proxy=None):
        kw = {"timeout": (6, 12)}
        if proxy:
            kw["proxies"] = {"http": proxy, "https": proxy}
        t0 = time.monotonic()
        try:
            r = requests.get(url, params=params, **kw)
        except Exception as exc:
            log(f"  {label}: FAILED ({type(exc).__name__})")
            return None, None
        ms = int((time.monotonic() - t0) * 1000)
        extra = []
        if r.headers.get("Retry-After"):
            extra.append(f"Retry-After {r.headers['Retry-After']}s")
        if r.headers.get("x-ratelimit-remaining"):
            extra.append(f"remaining {r.headers['x-ratelimit-remaining']}")
        log(f"  {label}: HTTP {r.status_code} in {ms} ms" + (f" ({', '.join(extra)})" if extra else ""))
        return r.status_code, r.headers.get("Retry-After")

    catalog_params = {"Keyword": "hat", "Limit": 10}
    log("Connection test - your own connection (no proxy):")
    d_item, _ = hit("item details", PROXY_TEST_URL)
    d_cat, d_retry = hit("catalog search", SEARCH_URL, catalog_params)

    sample = pool.snapshot()
    random.shuffle(sample)
    sample = sample[:3]
    p_cat_ok = p_cat_429 = p_item_ok = p_failed = 0
    if sample:
        log(f"Through {len(sample)} sampled prox{'y' if len(sample) == 1 else 'ies'}:")
    for proxy in sample:
        i_status, _ = hit(f"{proxy} item details", PROXY_TEST_URL, proxy=proxy)
        c_status, _ = hit(f"{proxy} catalog search", SEARCH_URL, catalog_params, proxy=proxy)
        p_item_ok += i_status in (200, 400, 404)
        p_cat_ok += c_status == 200
        p_cat_429 += c_status == 429
        p_failed += i_status is None and c_status is None

    log("What this means:")
    if d_cat == 200:
        log("  - Your own connection can search the catalog. Set the Generator's catalog search to 'Direct (no proxy)'.")
    elif d_cat == 429:
        wait = f" Roblox says to wait about {d_retry}s." if d_retry else " Wait a few minutes."
        log(f"  - Your own IP is currently rate-limited by Roblox.{wait} Searching direct won't work until then.")
    elif d_cat is None:
        log("  - Your direct request failed to connect. Check your internet, VPN or firewall.")
    else:
        log(f"  - Your direct catalog request returned HTTP {d_cat}; Roblox may have changed the endpoint or blocked the request.")
    if d_item == 200:
        log("  - Item lookups work from your own IP too.")
    if not sample:
        log("  - No proxies are loaded, so none were tested.")
    elif p_failed == len(sample):
        log("  - Every sampled proxy failed to connect: they are dead. Use 'Scrape fresh', then 'Test & keep working'.")
    elif p_cat_429 and not p_cat_ok:
        log("  - The sampled proxies reach Roblox but are rate-limited (429) - they are burned. Use fresher proxies or a paid rotating one.")
    elif p_cat_ok:
        log(f"  - {p_cat_ok}/{len(sample)} sampled proxies can use the catalog search; {p_item_ok}/{len(sample)} can check items.")
    else:
        log(f"  - Proxies connect but the catalog search isn't returning 200 through them; {p_item_ok}/{len(sample)} can check items.")

# =============================================================== RATE LIMITER
class RateLimiter:
    def __init__(self, per_second):
        self.per_second = per_second
        self.interval = 1.0 / per_second
        self.lock = threading.Lock()
        self.next_time = time.monotonic()

    def set_rate(self, per_second):
        with self.lock:
            self.per_second = per_second
            self.interval = 1.0 / max(1, per_second)

    def wait(self):
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_time)
            self.next_time = start + self.interval
        if start > now:
            time.sleep(start - now)

limiter = RateLimiter(DEFAULT_RATE)
_local = threading.local()

def get_session():
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session

# =============================================================== ROBLOX API
def check_id(asset_id, pool=None):
    session = get_session()
    for attempt in range(8 if pool else 6):
        limiter.wait()
        proxy = None
        kwargs = {}
        if pool:
            proxy = pool.get_wait()
            if proxy is None:
                if pool.count() > 0:      # every proxy is resting (429/5xx) or at its per-proxy limit
                    return "error", None
                return "no-proxy", None
            kwargs = {"proxies": {"http": proxy, "https": proxy}}
        
        try:
            r = session.get(URL.format(asset_id), timeout=PROXY_TIMEOUT if pool else 10, **kwargs)
        except:
            if pool and proxy:
                pool.fail(proxy)
            continue

        if r.status_code == 200:
            try:
                d = r.json()
                type_id = d.get("AssetTypeId")
                limited = "Limited U" if d.get("IsLimitedUnique") else ("Limited" if d.get("IsLimited") else "")
                if pool and proxy:
                    pool.ok(proxy)
                return "200", {
                    "id": asset_id, "name": d.get("Name", ""), "type": ASSET_TYPES.get(type_id, f"Type {type_id}"),
                    "creator": (d.get("Creator") or {}).get("Name", ""), "price": d.get("PriceInRobux"),
                    "for_sale": d.get("IsForSale", False), "limited": limited, "url": ITEM_URL.format(asset_id),
                }
            except:
                if pool and proxy:
                    pool.fail(proxy)
                continue
        
        if r.status_code == 404:
            if pool and proxy: pool.ok(proxy)
            return "404", None
        if r.status_code == 429:
            if pool and proxy:
                pool.cooldown(proxy)
                continue
            time.sleep(3)
            continue
        
        if pool and proxy and (r.status_code >= 500 or r.status_code in (403, 407)):
            pool.cooldown(proxy, 15)
            pool.fail(proxy)

    return "error", None

def catalog_retry_delay(response, retry_number):
    """Use Roblox's retry headers when present, otherwise exponential backoff."""
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after:
        try:
            return max(1.0, min(120.0, float(retry_after)))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(1.0, min(120.0, (retry_at - datetime.now(retry_at.tzinfo)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass
    reset = response.headers.get("x-ratelimit-reset", "")
    match = re.match(r"\s*(\d+(?:\.\d+)?)", reset)
    if match:
        return max(1.0, min(120.0, float(match.group(1))))
    return min(60.0, 5.0 * (2 ** max(0, retry_number - 1)))


def catalog_rate_budget(response):
    """Return remaining quota and reset seconds when Roblox supplies them."""
    remaining_match = re.match(r"\s*(\d+)", response.headers.get("x-ratelimit-remaining", ""))
    reset_match = re.match(r"\s*(\d+(?:\.\d+)?)", response.headers.get("x-ratelimit-reset", ""))
    remaining = int(remaining_match.group(1)) if remaining_match else None
    reset = float(reset_match.group(1)) if reset_match else None
    return remaining, reset


def wait_for_retry(seconds, stop, pause_event):
    deadline = time.monotonic() + seconds
    while not stop.is_set():
        if not wait_if_paused(stop, pause_event):
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        stop.wait(min(0.5, remaining))
    return False


def rotate_proxy_on_error(status, proxy_pool, proxy, run_failed_proxies, proxy_state, log, penalize=True):
    """On HTTP 429/5xx, bench the current proxy and switch to a different one. Returns the new proxy or None."""
    if penalize:
        if status == 429:
            proxy_pool.cooldown(proxy)
        else:
            proxy_pool.cooldown(proxy, 15)
            proxy_pool.fail(proxy)
    new_proxy = proxy_pool.get(exclude=run_failed_proxies | {proxy}, need_catalog=True, throttle=False)
    if new_proxy is None:
        return None
    if proxy_state is not None:
        proxy_state["proxy"] = new_proxy
    log(f"HTTP {status} via {proxy}; rotated to {new_proxy}.")
    return new_proxy


def search_keyword(keyword, want, seen, stop, log, on_id=None, pause_event=None, proxy_pool=None, proxy=None, proxy_state=None, on_pool_empty=None):
    ids, cursor = [], None
    session = requests.Session()
    retries = 0
    server_retries = 0
    rotations = 0
    warned_no_spare = False
    keyword_only = False
    run_failed_proxies = proxy_state.setdefault("failed_proxies", set()) if proxy_state is not None else set()
    request_state = proxy_state if proxy_state is not None else {}
    direct_mode = bool(proxy_state.get("direct", False)) if proxy_state is not None else False
    if proxy_state is not None:
        proxy = proxy_state.get("proxy", proxy)
    if proxy_pool and proxy is None and not direct_mode:
        proxy = proxy_pool.get(need_catalog=True, throttle=False)
        if proxy_state is not None:
            proxy_state["proxy"] = proxy
    if proxy_pool and proxy is None and not direct_mode:
        log("No active proxy is available for catalog search.")
        stop.set()
        return ids

    while (want is None or len(ids) < want) and wait_if_paused(stop, pause_event):
        # Keyword-only is always valid. Roblox rejects mismatched Category/Subcategory pairs with HTTP 400,
        # so only send them for the body-part presets, and fall back to keyword-only if Roblox refuses.
        params = {"Keyword": keyword, "SortType": 3, "Limit": 30}
        normalized_keyword = keyword.strip().casefold()
        if not keyword_only:
            if normalized_keyword in {"head", "heads"}:
                params.update(Category=4, Subcategory=15)
            elif normalized_keyword in {"dynamic head", "dynamic heads"}:
                params.update(Keyword="head", Category=4, Subcategory=66)
            elif normalized_keyword in {"classic face", "classic faces"}:
                params.update(Keyword="face", Category=4, Subcategory=10)
        if cursor:
            params["Cursor"] = cursor
        response = None
        while not stop.is_set():
            last_request = request_state.get("last_catalog_request", 0.0)
            interval = request_state.get("catalog_interval", 1.0)
            cooldown = interval - (time.monotonic() - last_request)
            cooldown = max(cooldown, request_state.get("catalog_retry_until", 0.0) - time.monotonic())
            if cooldown > 0 and not wait_for_retry(cooldown, stop, pause_event):
                break
            request_state["last_catalog_request"] = time.monotonic()
            request_options = {"proxies": {"http": proxy, "https": proxy}} if proxy else {}
            try:
                response = session.get(SEARCH_URL, params=params, timeout=(5, 12), **request_options)
            except Exception as exc:
                if not proxy_pool or not proxy:
                    log(f"Roblox catalog search failed: {exc}")
                    break
                failed_proxy = proxy
                run_failed_proxies.add(failed_proxy)
                proxy_pool.fail(failed_proxy)
                log(f"Proxy connection failed; skipping it for the rest of this run ({len(run_failed_proxies)} proxy/proxies skipped).")
                proxy = proxy_pool.get(exclude=run_failed_proxies, need_catalog=True, throttle=False)
                if proxy_state is not None:
                    proxy_state["proxy"] = proxy
                if proxy is None:
                    log(f"Catalog proxy connection/TLS failed ({exc}); no other active proxy is available. This run was stopped and saved.")
                    stop.set()
                    break
                log(f"Catalog proxy connection/TLS failed ({exc}); trying the next active proxy.")
                continue
            if proxy_pool and proxy:
                proxy_pool.ok(proxy)
            break
        if response is None:
            break

        remaining_budget, reset_seconds = catalog_rate_budget(response)
        if remaining_budget is not None and remaining_budget > 0 and reset_seconds and reset_seconds > 0:
            request_state["catalog_interval"] = max(0.25, min(8.0, reset_seconds / remaining_budget))
        if response.status_code in (429, 500, 502, 503, 504) and proxy_pool and proxy and not direct_mode and rotations < 40:
            new_proxy = rotate_proxy_on_error(response.status_code, proxy_pool, proxy, run_failed_proxies, proxy_state, log)
            if new_proxy is None and on_pool_empty:
                log(f"HTTP {response.status_code}: no spare proxy to rotate to ({proxy_pool.count()} in pool, the rest are resting). Fetching fresh proxies...")
                try:
                    on_pool_empty()
                except Exception as exc:
                    log(f"Proxy refill failed: {exc}")
                new_proxy = rotate_proxy_on_error(response.status_code, proxy_pool, proxy, run_failed_proxies, proxy_state, log, penalize=False)
            if new_proxy is None and not warned_no_spare:
                warned_no_spare = True
                log(f"HTTP {response.status_code} and there is no other working proxy to switch to. "
                    "Try the 'Direct (no proxy)' option for catalog searches, or add/scrape more proxies.")
            if new_proxy:
                rotations += 1
                proxy = new_proxy
                request_state["catalog_retry_until"] = 0.0   # fresh IP, no need to wait out the old IP's penalty
                time.sleep(0.3)
                continue
        if response.status_code == 429:
            retries += 1
            if retries > 5:
                log("Roblox catalog search stayed rate-limited after 5 retries. This run was stopped and saved; wait a few minutes, then Resume.")
                stop.set()
                break
            delay = catalog_retry_delay(response, retries)
            request_state["catalog_retry_until"] = max(request_state.get("catalog_retry_until", 0.0), time.monotonic() + delay)
            request_state["catalog_interval"] = min(8.0, max(1.0, request_state.get("catalog_interval", 1.0) * 2.0))
            log(f"Roblox catalog search rate-limited (HTTP 429). Waiting {delay:.0f}s per the response headers/backoff; retry {retries}/5…")
            if not wait_for_retry(delay, stop, pause_event):
                break
            continue

        if response.status_code in (500, 502, 503, 504):
            server_retries += 1
            if server_retries > 5:
                log(f"Roblox catalog search kept returning HTTP {response.status_code} after 5 retries; moving on from this keyword.")
                break
            delay = min(30.0, 2.0 ** server_retries)
            log(f"Roblox catalog temporarily returned HTTP {response.status_code}. Waiting {delay:.0f}s before retry {server_retries}/5…")
            if not wait_for_retry(delay, stop, pause_event):
                break
            continue
        if response.status_code == 400 and ("Category" in params or "Subcategory" in params) and not keyword_only:
            log(f"Roblox rejected the category filter ({response.text[:120]}); retrying as a plain keyword search.")
            keyword_only = True
            cursor = None
            continue
        if response.status_code != 200:
            log(f"Roblox catalog search returned HTTP {response.status_code}: {response.text[:240]}")
            break
        retries = 0
        server_retries = 0
        rotations = 0
        request_state["catalog_retry_until"] = 0.0
        try:
            data = response.json()
        except Exception as exc:
            log(f"Roblox catalog search returned invalid JSON: {exc}")
            break

        page_items = data.get("data", [])
        page_added = 0
        page_nonasset = sum(1 for it in page_items if it.get("itemType") != "Asset")
        page_dupe = sum(1 for it in page_items if it.get("itemType") == "Asset" and it.get("id") in seen)
        for item in page_items:
            if not wait_if_paused(stop, pause_event):
                break
            asset_id = item.get("id")
            if item.get("itemType") == "Asset" and isinstance(asset_id, int) and asset_id > 0 and asset_id not in seen:
                seen.add(asset_id)
                ids.append(asset_id)
                page_added += 1
                if on_id:
                    on_id(asset_id)
                if want is not None and len(ids) >= want:
                    break

        cursor = data.get("nextPageCursor")
        log(f"Catalog page returned {len(page_items)} entries; added {page_added} new asset IDs"
            + (f" ({page_nonasset} bundles/non-assets, {page_dupe} already seen/saved skipped)" if page_items else " (Roblox returned an empty page for this keyword)")
            + "; " + ("more results available" if cursor else "Roblox reports no next page"))
        if not cursor:
            break
        if not wait_for_retry(2.0, stop, pause_event):
            break

    return ids

# =============================================================== WINDOW
class App:
    """Tkinter UI. Worker threads communicate with the UI only through q."""
    def __init__(self, root):
        self.root = root
        self.th = THEME
        self.font = ("Segoe UI", 11)
        self.q = queue.Queue()
        self.pause_event = threading.Event(); self.pause_event.set()
        self.settings = load_settings()
        set_data_directory(self.settings.get("results_dir", str(DATA_DIR)))
        self.resume_state = read_json(CHECKPOINT_FILE, {})
        self.pool = ProxyPool()
        try: self.pool.interval = max(0.2, float(self.settings.get("proxy_interval", PER_PROXY_INTERVAL)))
        except (TypeError, ValueError): self.pool.interval = PER_PROXY_INTERVAL
        self.rolimons_item_cache = (0.0, {})
        self.rolimons_player_url = None
        self.rolimons_item_url = None
        self.gen_stop = threading.Event()
        self.chk_stop = threading.Event()
        self.prx_stop = threading.Event()
        self.ids, self.gen_ids = [], []
        self.db = init_db()
        self.check_running = self.gen_running = self.prx_running = False

        root.title(f"Roblox Item Finder {VERSION}")
        root.geometry("1180x700")
        root.minsize(960, 560)
        root.configure(bg=self.th["bg"])
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook.Tab", padding=(18, 10), font=("Segoe UI", 11, "bold"))
        style.configure("Treeview", rowheight=30, font=("Segoe UI", 10), background=self.th["field"], foreground=self.th["fg"], fieldbackground=self.th["field"])
        style.map("Treeview", background=[("selected", self.th["sel_bg"])], foreground=[("selected", self.th["sel_fg"])])
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), background="#263b4a", foreground=self.th["fg"], padding=(8, 7))

        header = tk.Frame(root, bg=self.th["bg"])
        header.pack(fill="x", padx=16, pady=(10, 6))
        tk.Label(header, text=f"Roblox Item Finder  /  {VERSION}", bg=self.th["bg"], fg=self.th["fg"],
                 font=("Segoe UI", 19, "bold")).pack(side="left")
        self.github = tk.Label(header, text="GitHub  ↗", bg=self.th["bg"], fg="#8dbdff",
                               font=("Segoe UI", 11, "underline"), cursor="hand2")
        self.github.pack(side="right", padx=8)
        self.github.bind("<Button-1>", lambda _event: webbrowser.open("https://github.com/rbxsti5939"))

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=4)
        self.tab_gen = tk.Frame(self.nb, bg=self.th["bg"])
        self.tab_chk = tk.Frame(self.nb, bg=self.th["bg"])
        self.tab_prx = tk.Frame(self.nb, bg=self.th["bg"])
        self.tab_stats = tk.Frame(self.nb, bg=self.th["bg"])
        self.tab_settings = tk.Frame(self.nb, bg=self.th["bg"])
        self.tab_diag = tk.Frame(self.nb, bg=self.th["bg"])
        self.tab_rolimons = tk.Frame(self.nb, bg=self.th["bg"])
        self.nb.add(self.tab_gen, text="Generator")
        self.nb.add(self.tab_chk, text="Checker")
        self.nb.add(self.tab_prx, text="Proxies")
        self.nb.add(self.tab_stats, text="Stats & Export")
        self.nb.add(self.tab_settings, text="Settings")
        self.nb.add(self.tab_diag, text="Diagnostics")
        self.nb.add(self.tab_rolimons, text="Rolimon's Tools")
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(root, textvariable=self.status_var, anchor="w", bg="#132331", fg=self.th["fg"],
                 font=("Segoe UI", 10, "bold"), padx=12, pady=7).pack(fill="x", side="bottom")

        self.build_gen()
        self.build_chk()
        self.build_prx()
        self.build_stats()
        self.build_settings()
        self.build_diagnostics()
        self.build_rolimons()
        if self.settings.get("restore_proxies", True):
            self.restore_proxies()
        startup = {"Generator": self.tab_gen, "Checker": self.tab_chk, "Proxies": self.tab_prx,
                   "Stats": self.tab_stats, "Settings": self.tab_settings, "Diagnostics": self.tab_diag, "Rolimon's": self.tab_rolimons}
        self.nb.select(startup.get(self.settings.get("startup_tab", "Generator"), self.tab_gen))
        self.reject = Counter()
        self.webhook = WebhookSender(on_error=lambda msg: self.q.put(("diagnostic", msg)))
        self._refill_lock = threading.Lock()
        self._last_refill = 0.0
        self._fail_streak = 0
        self.root.after(100, self.poll)
        self.root.after(2000, self.proxy_tick)
    def proxy_tick(self):
        """Refresh the proxy table every 2s while a run is active so 'In use' stays current."""
        try:
            if self.check_running or self.gen_running:
                self.update_proxy_health()
        except Exception:
            pass
        if self.root.winfo_exists():
            self.root.after(2000, self.proxy_tick)

    def refill_proxies(self, force=False):
        """Scrape + test fresh proxies and ADD the working ones (never wipes the pool)."""
        with self._refill_lock:
            if not force and time.monotonic() - self._last_refill < 60:
                return 0
            self._last_refill = time.monotonic()
            log = lambda m: self.q.put(("plog", m))
            log("Proxy pool low or failing; fetching and testing fresh proxies...")
            scraped = scrape_proxies("All", False, log)
            details = {}
            good, _ = test_proxies(scraped[:600], log, threading.Event(), None, details)
            good = good[:MAX_ACTIVE_PROXIES]
            added = self.pool.add(good)
            for p in good:
                self.pool.mark_test(p, True, *details.get(p, (None, None)))
            save_proxies(self.pool)
            self.q.put(("pstatus", self.pool.count(), f"loaded; auto-refill added {added}"))
            return added

    def check_with_refill(self, asset_id):
        """check_id with automatic proxy refill. Returns (code, info, give_up)."""
        pool = self.pool
        if pool.count() < MIN_POOL_BEFORE_REFILL:
            self.refill_proxies()
        code, info = check_id(asset_id, pool)
        if code in ("no-proxy", "error"):
            self._fail_streak += 1
            self.refill_proxies()
            code, info = check_id(asset_id, pool)
        if code in ("no-proxy", "error"):
            self._fail_streak += 1
        else:
            self._fail_streak = 0
        give_up = code == "no-proxy" or self._fail_streak >= 4
        return code, info, give_up

    def btn(self, parent, label, command):
        return tk.Button(parent, text=label, command=command, bg=self.th["btn"], fg=self.th["fg"],
                         activebackground=self.th["btn_active"], relief="flat", padx=15, pady=8,
                         cursor="hand2", font=("Segoe UI", 11, "bold"))
    def text_box(self, parent, height=8):
        box = tk.Text(parent, height=height, bg=self.th["field"], fg=self.th["fg"],
                      relief="flat", font=self.font, wrap="word")
        box.configure(state="disabled")
        return box

    def append_text(self, box, text):
        box.configure(state="normal")
        box.insert("end", str(text) + "\n")
        box.see("end")
        box.configure(state="disabled")

    def build_gen(self):
        tk.Label(self.tab_gen, text="Search keywords (comma, semicolon, or newline separated):",
                 bg=self.th["bg"], fg=self.th["fg"], font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(16, 5))
        entry_row = tk.Frame(self.tab_gen, bg=self.th["bg"])
        entry_row.pack(fill="x", padx=16, pady=5)
        self.g_keywords = tk.StringVar()
        tk.Entry(entry_row, textvariable=self.g_keywords, bg=self.th["field"], fg=self.th["fg"],
                 insertbackground=self.th["fg"], relief="flat", font=("Segoe UI", 12)).pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=5)
        self.btn(entry_row, "Load batch file", self.load_batch_keywords).pack(side="left")

        preset_row = tk.Frame(self.tab_gen, bg=self.th["bg"])
        preset_row.pack(fill="x", padx=16, pady=5)
        tk.Label(preset_row, text="Search preset:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left")
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(preset_row, textvariable=self.preset_var, state="readonly", width=24,
                                       values=self.preset_names())
        self.preset_box.pack(side="left", padx=8)
        if self.preset_box["values"]:
            self.preset_box.current(0)
        self.btn(preset_row, "Use preset", self.apply_preset).pack(side="left", padx=(0, 6))
        self.btn(preset_row, "Save current as preset", self.save_current_preset).pack(side="left")

        proxy_row = tk.Frame(self.tab_gen, bg=self.th["bg"]); proxy_row.pack(anchor="w", padx=16, pady=4)
        tk.Label(proxy_row, text="Generator catalog proxy:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left")
        self.g_proxy_choice = tk.StringVar(value="Direct (no proxy)")
        self.g_proxy_box = ttk.Combobox(proxy_row, textvariable=self.g_proxy_choice, state="readonly", width=48, values=("Direct (no proxy)", "Auto (proxy pool)"))
        self.g_proxy_box.pack(side="left", padx=8)
        self.btn(proxy_row, "Refresh proxies", self.refresh_generator_proxies).pack(side="left")

        limit_row = tk.Frame(self.tab_gen, bg=self.th["bg"]); limit_row.pack(anchor="w", padx=16, pady=4)
        tk.Label(limit_row, text="Max catalog results per keyword (0 = all pages):", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left")
        self.g_limit_var = tk.IntVar(value=500)
        tk.Spinbox(limit_row, from_=0, to=10000, increment=100, textvariable=self.g_limit_var, width=9, font=self.font, bg=self.th["field"], fg=self.th["fg"]).pack(side="left", padx=8)
        actions = tk.Frame(self.tab_gen, bg=self.th["bg"])
        actions.pack(anchor="w", padx=16, pady=8)
        self.g_btn_start = self.btn(actions, "Generate IDs", self.start_gen)
        self.g_btn_start.pack(side="left", padx=(0, 7))
        self.g_btn_full = self.btn(actions, "Full Search + Check", self.start_full_scan)
        self.g_btn_full.pack(side="left", padx=(0, 7))
        self.btn(actions, "Save IDs as...", self.save_gen_ids).pack(side="left", padx=(0, 7))
        self.btn(actions, "Send to Checker", self.send_gen_to_chk).pack(side="left", padx=(0, 7))
        self.g_btn_pause = self.btn(actions, "Pause", self.toggle_pause); self.g_btn_pause.pack(side="left", padx=(0, 7)); self.g_btn_pause.configure(state="disabled")
        self.g_btn_stop = self.btn(actions, "Stop", self.stop_check); self.g_btn_stop.pack(side="left", padx=(0, 7)); self.g_btn_stop.configure(state="disabled")
        self.btn(actions, "Search history…", self.show_search_history).pack(side="left", padx=(0, 7))
        self.resume_btn = self.btn(actions, "Resume saved run", self.resume_saved_run); self.resume_btn.pack(side="left")
        if not isinstance(self.resume_state, dict) or not self.resume_state.get("mode"): self.resume_btn.configure(state="disabled")
        self.g_status = tk.Label(self.tab_gen, text="Ready", bg=self.th["bg"], fg=self.th["fg"],
                                 font=("Segoe UI", 11, "bold"))
        self.g_status.pack(anchor="w", padx=16, pady=5)
        self.g_box = self.text_box(self.tab_gen, height=20)
        self.g_box.pack(fill="both", expand=True, padx=16, pady=(2, 14))

    def refresh_generator_proxies(self):
        proxies = self.pool.snapshot()
        values = ("Direct (no proxy)", "Auto (proxy pool)", *proxies)
        self.g_proxy_box.configure(values=values)
        if self.g_proxy_choice.get() not in values:
            self.g_proxy_choice.set(values[0])
        self.status_var.set(f"Generator proxy list refreshed · {len(proxies)} loaded")

    def uses_direct_catalog(self):
        return self.g_proxy_choice.get() == "Direct (no proxy)"

    def selected_catalog_proxy(self):
        choice = self.g_proxy_choice.get()
        if choice == "Direct (no proxy)":
            return None
        if choice == "Auto (proxy pool)" or choice not in self.pool.snapshot():
            return self.pool.get(need_catalog=True, throttle=False)
        return choice

    def preset_names(self):
        custom = self.settings.get("keyword_presets", {})
        return tuple(dict.fromkeys((*SEARCH_PRESETS.keys(), *custom.keys())))

    def apply_preset(self):
        name = self.preset_var.get()
        keywords = {**SEARCH_PRESETS, **self.settings.get("keyword_presets", {})}
        if name in keywords:
            self.g_keywords.set(keywords[name])
            self.status_var.set(f"Preset loaded: {name}")

    def save_current_preset(self):
        keywords = self.g_keywords.get().strip()
        if not parse_keywords(keywords):
            messagebox.showinfo("No keywords", "Enter search terms before saving a preset.")
            return
        name = simpledialog.askstring("Save search preset", "Preset name:", parent=self.root)
        if not name or not name.strip():
            return
        self.settings.setdefault("keyword_presets", {})[name.strip()] = keywords
        self.persist_settings()
        self.preset_box.configure(values=self.preset_names())
        self.preset_var.set(name.strip())
        self.status_var.set(f"Saved preset: {name.strip()}")
    def build_chk(self):
        top = tk.Frame(self.tab_chk, bg=self.th["bg"])
        top.pack(fill="x", padx=12, pady=8)
        tk.Label(top, text="Word list:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left")
        self.c_ids_name = tk.Label(top, text="No word list loaded", bg=self.th["bg"], fg=self.th["fg"], font=self.font)
        self.c_ids_name.pack(side="left", padx=8, expand=True, anchor="w")
        self.btn(top, "Load word list", self.load_wordlist).pack(side="right")

        filters = tk.Frame(self.tab_chk, bg=self.th["bg"])
        filters.pack(fill="x", padx=12, pady=4)
        tk.Label(filters, text="Speed:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left")
        self.c_speed = tk.IntVar(value=int(self.settings.get("request_speed", DEFAULT_RATE)))
        tk.Scale(filters, from_=10, to=100, orient="horizontal", length=180, variable=self.c_speed,
                 bg=self.th["bg"], fg=self.th["fg"], troughcolor=self.th["field"],
                 highlightthickness=0, bd=0).pack(side="left", padx=4)
        tk.Label(filters, text="Min price:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left", padx=(12, 2))
        self.c_min_price = tk.StringVar(value=str(MIN_PRICE))
        tk.Entry(filters, textvariable=self.c_min_price, width=6, bg=self.th["field"], fg=self.th["fg"], relief="flat").pack(side="left")
        tk.Label(filters, text="Max:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left", padx=(8, 2))
        self.c_max_price = tk.StringVar()
        tk.Entry(filters, textvariable=self.c_max_price, width=6, bg=self.th["field"], fg=self.th["fg"], relief="flat").pack(side="left")
        tk.Label(filters, text="Type:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left", padx=(8, 2))
        self.c_type = tk.StringVar(value="All wearable items")
        tk.OptionMenu(filters, self.c_type, *TYPE_PRESETS.keys()).pack(side="left")
        custom = tk.Frame(self.tab_chk, bg=self.th["bg"]); custom.pack(fill="x", padx=12, pady=4)
        tk.Label(custom, text="Creator contains:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left")
        self.c_creator = tk.StringVar(); tk.Entry(custom, textvariable=self.c_creator, width=24, bg=self.th["field"], fg=self.th["fg"], relief="flat", font=self.font).pack(side="left", padx=(6,16), ipady=3)
        tk.Label(custom, text="Sale status:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left")
        self.c_sale_status = tk.StringVar(value="Any")
        ttk.Combobox(custom, textvariable=self.c_sale_status, values=("Any", "For sale", "Off sale"), state="readonly", width=12).pack(side="left", padx=6)
        checks = tk.Frame(self.tab_chk, bg=self.th["bg"])
        checks.pack(anchor="w", padx=12, pady=3)
        self.c_limited = tk.BooleanVar(value=False)
        self.c_sound = tk.BooleanVar(value=False)
        for label, var in (("Limited only", self.c_limited), ("Beep on hit", self.c_sound)):
            tk.Checkbutton(checks, text=label, variable=var, bg=self.th["bg"], fg=self.th["fg"],
                           selectcolor=self.th["field"], activebackground=self.th["bg"], font=self.font).pack(side="left", padx=(0, 10))

        actions = tk.Frame(self.tab_chk, bg=self.th["bg"])
        actions.pack(anchor="w", padx=12, pady=4)
        self.c_btn_start = self.btn(actions, "Start", self.start_check)
        self.c_btn_start.pack(side="left", padx=(0, 6))
        self.c_btn_stop = self.btn(actions, "Stop", self.stop_check)
        self.c_btn_stop.configure(state="disabled")
        self.c_btn_stop.pack(side="left", padx=(0, 6))
        self.c_btn_pause = self.btn(actions, "Pause", self.toggle_pause); self.c_btn_pause.pack(side="left"); self.c_btn_pause.configure(state="disabled")
        self.c_status = tk.Label(self.tab_chk, text="Ready.", bg=self.th["bg"], fg=self.th["fg"], font=self.font)
        self.c_status.pack(anchor="w", padx=12, pady=3)

        table_frame = tk.Frame(self.tab_chk, bg=self.th["bg"])
        table_frame.pack(fill="both", expand=True, padx=12, pady=4)
        cols = (("id", 90), ("name", 240), ("type", 120), ("creator", 140), ("price", 70), ("limited", 80))
        self.tree = ttk.Treeview(table_frame, columns=[c for c, _ in cols], show="headings")
        for col, width in cols:
            self.tree.heading(col, text=col.title())
            self.tree.column(col, width=width, anchor="w")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())
        tk.Label(self.tab_chk, text="Log:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(anchor="w", padx=12)
        self.c_log = self.text_box(self.tab_chk, height=5)
        self.c_log.pack(fill="x", padx=12, pady=(0, 10))

    def build_prx(self):
        tk.Label(self.tab_prx, text="Proxy Management", bg=self.th["bg"], fg=self.th["fg"],
                 font=("Consolas", 12, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        self.p_status = tk.Label(self.tab_prx, text="No proxies loaded", bg=self.th["bg"], fg=self.th["fg"], font=self.font)
        self.p_status.pack(anchor="w", padx=12)
        options = tk.Frame(self.tab_prx, bg=self.th["bg"])
        options.pack(anchor="w", padx=12, pady=5)
        tk.Label(options, text="Country:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left")
        self.p_country = tk.StringVar(value="All")
        tk.OptionMenu(options, self.p_country, *COUNTRY_CODES.keys()).pack(side="left", padx=4)
        self.p_https = tk.BooleanVar(value=True)
        tk.Checkbutton(options, text="HTTPS only", variable=self.p_https, bg=self.th["bg"], fg=self.th["fg"],
                       selectcolor=self.th["field"], activebackground=self.th["bg"], font=self.font).pack(side="left", padx=8)
        actions = tk.Frame(self.tab_prx, bg=self.th["bg"])
        actions.pack(anchor="w", padx=12, pady=4)
        self.btn(actions, "Load from file", self.load_proxy_file).pack(side="left", padx=(0, 6))
        self.p_btn_scrape = self.btn(actions, "Scrape fresh", self.start_scrape)
        self.p_btn_scrape.pack(side="left", padx=(0, 6))
        self.p_btn_test = self.btn(actions, "Test & keep working", self.start_test)
        self.p_btn_test.pack(side="left", padx=(0, 6))
        self.btn(actions, "Remove failed/offline", self.remove_failed_proxies).pack(side="left", padx=(0, 6))
        self.btn(actions, "Clear all", self.clear_proxies).pack(side="left", padx=(0, 6))
        self.p_btn_conn = self.btn(actions, "Test connection", self.start_conn_test)
        self.p_btn_conn.pack(side="left")
        tk.Label(self.tab_prx, text="Paste proxies (one per line; http://ip:port is accepted):",
                 bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(anchor="w", padx=12, pady=(5, 2))
        self.p_input = tk.Text(self.tab_prx, height=5, bg=self.th["field"], fg=self.th["fg"],
                               relief="flat", font=self.font, wrap="none")
        self.p_input.pack(fill="x", padx=12)
        self.btn(self.tab_prx, "Add pasted proxies", self.add_pasted_proxies).pack(anchor="w", padx=12, pady=5)
        tk.Label(self.tab_prx, text="Proxy health · status and last check", bg=self.th["bg"], fg=self.th["fg"], font=("Segoe UI",11,"bold")).pack(anchor="w", padx=12, pady=(4,2))
        health_frame=tk.Frame(self.tab_prx,bg=self.th["bg"]); health_frame.pack(fill="both",expand=True,padx=12,pady=3)
        self.proxy_tree=ttk.Treeview(health_frame,columns=("proxy","status","latency","catalog","in_use","uses","failures","last_checked"),show="headings",height=8)
        for col,title,width in (("proxy","Proxy",260),("status","Status",80),("latency","Latency",75),("catalog","Catalog",90),("in_use","In use",55),("uses","Requests",70),("failures","Fails",45),("last_checked","Last checked",170)):
            self.proxy_tree.heading(col,text=title); self.proxy_tree.column(col,width=width,anchor="w")
        self.proxy_tree.pack(side="left",fill="both",expand=True)
        pscroll=ttk.Scrollbar(health_frame,orient="vertical",command=self.proxy_tree.yview); pscroll.pack(side="right",fill="y"); self.proxy_tree.configure(yscrollcommand=pscroll.set)
        tk.Label(self.tab_prx, text="Log:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(anchor="w", padx=12)
        self.p_log=self.text_box(self.tab_prx,height=9); self.p_log.pack(fill="x",padx=12,pady=(0,8))

    def build_stats(self):
        actions = tk.Frame(self.tab_stats, bg=self.th["bg"])
        actions.pack(anchor="w", padx=16, pady=12)
        self.btn(actions, "Export CSV", self.export_csv).pack(side="left", padx=(0, 7))
        self.btn(actions, "Export JSON", self.export_json).pack(side="left", padx=(0, 7))
        self.btn(actions, "Export search history", self.export_search_history).pack(side="left", padx=(0, 7))
        self.btn(actions, "Export proxy health", self.export_proxy_health).pack(side="left", padx=(0, 7))
        self.btn(actions, "Refresh stats", self.refresh_stats).pack(side="left")
        self.stats_box = self.text_box(self.tab_stats, height=25)
        self.stats_box.pack(fill="both", expand=True, padx=16, pady=(0, 14))

    def build_rolimons(self):
        tk.Label(self.tab_rolimons, text="Rolimon's player and item tools", bg=self.th["bg"], fg=self.th["fg"],
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=16, pady=(12, 5))
        tk.Label(self.tab_rolimons, text="Single lookups only. Data comes from Rolimon's public site/API; requests are cached and logged locally.",
                 bg=self.th["bg"], fg="#9fb7c8", font=self.font).pack(anchor="w", padx=16, pady=(0, 8))

        player = tk.LabelFrame(self.tab_rolimons, text="Player value lookup", bg=self.th["bg"], fg=self.th["fg"],
                               font=("Segoe UI", 11, "bold"), padx=10, pady=8)
        player.pack(fill="x", padx=16, pady=5)
        player_row = tk.Frame(player, bg=self.th["bg"]); player_row.pack(fill="x")
        tk.Label(player_row, text="Roblox username:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left")
        self.r_player_name = tk.StringVar()
        tk.Entry(player_row, textvariable=self.r_player_name, width=28, bg=self.th["field"], fg=self.th["fg"],
                 insertbackground=self.th["fg"], relief="flat", font=self.font).pack(side="left", padx=8, ipady=4)
        self.r_player_button = self.btn(player_row, "Look up one player", self.lookup_rolimons_player)
        self.r_player_button.pack(side="left", padx=(0, 7))
        self.r_player_link = self.btn(player_row, "Open Rolimon's profile", self.open_rolimons_player)
        self.r_player_link.pack(side="left"); self.r_player_link.configure(state="disabled")
        self.r_player_output = self.text_box(player, height=3)
        self.r_player_output.pack(fill="x", pady=(7, 0))

        item = tk.LabelFrame(self.tab_rolimons, text="Single item value logger · 30-day local chart", bg=self.th["bg"], fg=self.th["fg"],
                             font=("Segoe UI", 11, "bold"), padx=10, pady=8)
        item.pack(fill="x", padx=16, pady=5)
        item_row = tk.Frame(item, bg=self.th["bg"]); item_row.pack(fill="x")
        tk.Label(item_row, text="Rolimon's item ID:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(side="left")
        self.r_item_id = tk.StringVar()
        tk.Entry(item_row, textvariable=self.r_item_id, width=18, bg=self.th["field"], fg=self.th["fg"],
                 insertbackground=self.th["fg"], relief="flat", font=self.font).pack(side="left", padx=8, ipady=4)
        self.r_item_button = self.btn(item_row, "Check & log value", self.lookup_rolimons_item)
        self.r_item_button.pack(side="left", padx=(0, 7))
        self.r_item_link = self.btn(item_row, "Open Rolimon's item charts", self.open_rolimons_item)
        self.r_item_link.pack(side="left"); self.r_item_link.configure(state="disabled")
        self.r_item_summary = tk.StringVar(value="Enter one item ID, then check it to add a timestamped value observation.")
        tk.Label(item, textvariable=self.r_item_summary, bg=self.th["bg"], fg=self.th["fg"], font=self.font,
                 anchor="w").pack(fill="x", pady=(7, 0))
        self.r_chart = tk.Canvas(item, height=190, bg=self.th["field"], highlightthickness=0)
        self.r_chart.pack(fill="x", pady=(7, 2))
        self.r_chart.bind("<Configure>", lambda _event: self.draw_rolimons_chart())
        tk.Label(item, text="The graph uses observations this app logs from now on; Rolimon's profile holds the item's pre-existing history.",
                 bg=self.th["bg"], fg="#9fb7c8", font=("Segoe UI", 9)).pack(anchor="w")

        log_frame = tk.LabelFrame(self.tab_rolimons, text="Item observations", bg=self.th["bg"], fg=self.th["fg"],
                                  font=("Segoe UI", 11, "bold"), padx=8, pady=5)
        log_frame.pack(fill="both", expand=True, padx=16, pady=(3, 10))
        cols = ("time", "id", "name", "rap", "value")
        self.r_log_tree = ttk.Treeview(log_frame, columns=cols, show="headings", height=5)
        for col, title, width in (("time", "Observed", 165), ("id", "Item ID", 100), ("name", "Name", 400),
                                  ("rap", "RAP", 110), ("value", "Value", 110)):
            self.r_log_tree.heading(col, text=title); self.r_log_tree.column(col, width=width, anchor="w")
        self.r_log_tree.pack(fill="both", expand=True)
        self.refresh_rolimons_log()

    def rolimons_history_path(self):
        return DATA_DIR / "rolimons_item_history.json"

    def lookup_rolimons_player(self):
        username = self.r_player_name.get().strip()
        if not username or any(ch.isspace() for ch in username):
            messagebox.showinfo("Username needed", "Enter one Roblox username.")
            return
        self.r_player_button.configure(state="disabled")
        self.r_player_output.configure(state="normal"); self.r_player_output.delete("1.0", "end")
        self.r_player_output.insert("end", f"Looking up {username}…"); self.r_player_output.configure(state="disabled")
        def work():
            try:
                headers = {"User-Agent": "RobloxItemFinder/1.0", "Accept": "application/json"}
                resolved = requests.post("https://users.roblox.com/v1/usernames/users",
                                         json={"usernames": [username], "excludeBannedUsers": False},
                                         headers=headers, timeout=(5, 15))
                resolved.raise_for_status()
                matches = resolved.json().get("data", [])
                if not matches:
                    raise ValueError("Roblox did not find that username.")
                user = matches[0]; user_id = int(user["id"])
                profile = requests.get(f"https://api.rolimons.com/players/v1/playerinfo/{user_id}",
                                       headers={"User-Agent": "RobloxItemFinder/1.0", "Referer": "https://www.rolimons.com/"},
                                       timeout=(5, 15))
                # Rolimon's may deny its player-info API from some networks. Keep
                # this separate from Roblox username resolution so users see a
                # useful explanation and can still open the public profile page.
                if profile.status_code == 403:
                    self.q.put(("rplayer_blocked", {
                        "_user_id": user_id,
                        "_roblox_name": user.get("name", username),
                        "_display_name": user.get("displayName", user.get("name", username)),
                    }))
                    return
                profile.raise_for_status(); details = profile.json()
                if details.get("success") is False:
                    raise ValueError(details.get("message", "Rolimon's has no player data for this account."))
                details["_roblox_name"] = user.get("name", username)
                details["_display_name"] = user.get("displayName", user.get("name", username))
                details["_user_id"] = user_id
                self.q.put(("rplayer_done", details))
            except Exception as exc:
                self.q.put(("rplayer_error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def open_rolimons_player(self):
        if self.rolimons_player_url:
            webbrowser.open(self.rolimons_player_url)

    def lookup_rolimons_item(self):
        raw = self.r_item_id.get().strip()
        if not raw.isdigit() or int(raw) <= 0:
            messagebox.showinfo("Item ID needed", "Enter one positive numeric Rolimon's item ID.")
            return
        item_id = str(int(raw))
        self.r_item_button.configure(state="disabled")
        self.r_item_summary.set(f"Loading current Rolimon's data for item {item_id}…")
        def work():
            try:
                fetched_at, items = self.rolimons_item_cache
                if time.monotonic() - fetched_at > 90 or not items:
                    response = requests.get("https://api.rolimons.com/items/v2/itemdetails",
                                            headers={"User-Agent": "RobloxItemFinder/1.0", "Referer": "https://www.rolimons.com/"},
                                            timeout=(5, 20))
                    response.raise_for_status()
                    payload = response.json()
                    items = payload.get("items", {})
                    if not isinstance(items, dict):
                        raise ValueError("Rolimon's returned an unexpected item data format.")
                    self.rolimons_item_cache = (time.monotonic(), items)
                row = items.get(item_id)
                if row is None:
                    raise ValueError("This ID was not found in Rolimon's current valued-item data.")
                if isinstance(row, dict):
                    name = str(row.get("name", item_id)); acronym = str(row.get("acronym", ""))
                    rap = row.get("rap"); value = row.get("value")
                elif isinstance(row, (list, tuple)) and len(row) >= 4:
                    name, acronym, rap, value = str(row[0]), str(row[1]), row[2], row[3]
                else:
                    raise ValueError("Rolimon's returned incomplete item data.")
                def to_number(value):
                    try:
                        value = int(value)
                        return value if value >= 0 else None
                    except (TypeError, ValueError):
                        return None
                rap, value = to_number(rap), to_number(value)
                now = datetime.now().astimezone().isoformat(timespec="seconds")
                history = read_json(self.rolimons_history_path(), {})
                if not isinstance(history, dict): history = {}
                observations = history.setdefault(item_id, [])
                observations.append({"time": now, "name": name, "rap": rap, "value": value})
                cutoff = time.time() - 45 * 86400
                observations[:] = [point for point in observations if _parse_timestamp(point.get("time")) >= cutoff]
                write_json(self.rolimons_history_path(), history)
                result = {"id": item_id, "name": name, "acronym": acronym, "rap": rap, "value": value,
                          "time": now, "history": observations[-500:]}
                self.q.put(("ritem_done", result))
            except Exception as exc:
                self.q.put(("ritem_error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def open_rolimons_item(self):
        if self.rolimons_item_url:
            webbrowser.open(self.rolimons_item_url)

    def refresh_rolimons_log(self):
        if not hasattr(self, "r_log_tree"):
            return
        self.r_log_tree.delete(*self.r_log_tree.get_children())
        history = read_json(self.rolimons_history_path(), {})
        rows = []
        if isinstance(history, dict):
            for item_id, points in history.items():
                for point in points if isinstance(points, list) else []:
                    rows.append((point.get("time", ""), item_id, point.get("name", ""), point.get("rap"), point.get("value")))
        for observed, item_id, name, rap, value in sorted(rows, reverse=True)[:200]:
            self.r_log_tree.insert("", "end", values=(observed.replace("T", " "), item_id, name,
                                                        f"{rap:,}" if isinstance(rap, int) else "—",
                                                        f"{value:,}" if isinstance(value, int) else "—"))

    def draw_rolimons_chart(self):
        if not hasattr(self, "r_chart"):
            return
        canvas = self.r_chart; canvas.delete("all")
        item_id = self.r_item_id.get().strip()
        history = read_json(self.rolimons_history_path(), {})
        points = history.get(item_id, []) if isinstance(history, dict) else []
        cutoff = time.time() - 30 * 86400
        points = [point for point in points if _parse_timestamp(point.get("time")) >= cutoff]
        width = max(500, canvas.winfo_width()); height = max(160, canvas.winfo_height())
        left, right, top, bottom = 65, width - 20, 20, height - 35
        canvas.create_text(left, 9, text="Value (cyan) · RAP (orange) · last 30 days · local observations", anchor="w", fill="#c8dce8", font=("Segoe UI", 9))
        if not points:
            canvas.create_text(width / 2, height / 2, text="No observations yet. Check this item periodically to build its local 30-day chart.",
                               fill="#9fb7c8", font=("Segoe UI", 10))
            return
        times = [_parse_timestamp(point.get("time")) for point in points]
        all_values = [point.get(key) for point in points for key in ("value", "rap") if isinstance(point.get(key), (int, float))]
        if not all_values:
            canvas.create_text(width / 2, height / 2, text="No numeric value history is available for this item.", fill="#9fb7c8")
            return
        low, high = min(all_values), max(all_values)
        if low == high: low, high = max(0, low - 1), high + 1
        for step in range(5):
            y = top + (bottom - top) * step / 4
            amount = high - (high - low) * step / 4
            canvas.create_line(left, y, right, y, fill="#2b4555")
            canvas.create_text(left - 6, y, text=f"{amount:,.0f}", anchor="e", fill="#9fb7c8", font=("Segoe UI", 8))
        canvas.create_line(left, bottom, right, bottom, fill="#69808e")
        min_t, max_t = min(times), max(times)
        def coords(key):
            out = []
            for point, stamp in zip(points, times):
                amount = point.get(key)
                if not isinstance(amount, (int, float)): continue
                x = left + ((stamp - min_t) / (max_t - min_t) if max_t > min_t else .5) * (right - left)
                y = bottom - ((amount - low) / (high - low)) * (bottom - top)
                out.append((x, y))
            return out
        for key, color in (("value", "#21d4fd"), ("rap", "#ffad5c")):
            xy = coords(key)
            if len(xy) >= 2:
                canvas.create_line(*[coord for point in xy for coord in point], fill=color, width=2, smooth=True)
            for x, y in xy:
                canvas.create_oval(x-2, y-2, x+2, y+2, fill=color, outline=color)
        canvas.create_text(left, bottom + 14, text=datetime.fromtimestamp(min_t).strftime("%b %d"), anchor="w", fill="#9fb7c8", font=("Segoe UI", 8))
        canvas.create_text(right, bottom + 14, text=datetime.fromtimestamp(max_t).strftime("%b %d"), anchor="e", fill="#9fb7c8", font=("Segoe UI", 8))

    def build_settings(self):
        tk.Label(self.tab_settings, text="App settings", bg=self.th["bg"], fg=self.th["fg"],
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=16, pady=(16, 10))
        folder_row = tk.Frame(self.tab_settings, bg=self.th["bg"])
        folder_row.pack(fill="x", padx=16, pady=6)
        tk.Label(folder_row, text="Results and database folder", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(anchor="w")
        self.results_dir_var = tk.StringVar(value=str(DATA_DIR))
        folder_line = tk.Frame(folder_row, bg=self.th["bg"])
        folder_line.pack(fill="x", pady=4)
        tk.Entry(folder_line, textvariable=self.results_dir_var, bg=self.th["field"], fg=self.th["fg"],
                 insertbackground=self.th["fg"], relief="flat", font=self.font).pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)
        self.btn(folder_line, "Browse...", self.browse_results_folder).pack(side="left")

        options = tk.Frame(self.tab_settings, bg=self.th["bg"])
        options.pack(anchor="w", padx=16, pady=8)
        tk.Label(options, text="Default request speed (checks/sec):", bg=self.th["bg"], fg=self.th["fg"], font=self.font).grid(row=0, column=0, sticky="w", pady=7)
        self.settings_speed_var = tk.IntVar(value=int(self.settings.get("request_speed", DEFAULT_RATE)))
        tk.Spinbox(options, from_=10, to=100, textvariable=self.settings_speed_var, width=7,
                   font=self.font, bg=self.th["field"], fg=self.th["fg"]).grid(row=0, column=1, sticky="w", padx=10)
        tk.Label(options, text="Open this tab at startup:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).grid(row=1, column=0, sticky="w", pady=7)
        self.startup_tab_var = tk.StringVar(value=self.settings.get("startup_tab", "Generator"))
        ttk.Combobox(options, textvariable=self.startup_tab_var, values=("Generator", "Checker", "Proxies", "Stats", "Settings", "Diagnostics", "Rolimon's"),
                     state="readonly", width=18).grid(row=1, column=1, sticky="w", padx=10)
        self.restore_proxies_var = tk.BooleanVar(value=bool(self.settings.get("restore_proxies", True)))
        self.notifications_var = tk.BooleanVar(value=bool(self.settings.get("notifications", True)))
        tk.Checkbutton(options, text="Load saved proxies at startup", variable=self.restore_proxies_var, bg=self.th["bg"],
                       fg=self.th["fg"], selectcolor=self.th["field"], activebackground=self.th["bg"], font=self.font).grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        tk.Checkbutton(options, text="Show a popup when an item matches", variable=self.notifications_var, bg=self.th["bg"],
                       fg=self.th["fg"], selectcolor=self.th["field"], activebackground=self.th["bg"], font=self.font).grid(row=3, column=0, columnspan=2, sticky="w", pady=4)
        tk.Label(options, text="Seconds between requests on one proxy:", bg=self.th["bg"], fg=self.th["fg"], font=self.font).grid(row=4, column=0, sticky="w", pady=7)
        self.proxy_interval_var = tk.DoubleVar(value=float(self.settings.get("proxy_interval", PER_PROXY_INTERVAL)))
        tk.Spinbox(options, from_=0.2, to=10, increment=0.1, format="%.1f", textvariable=self.proxy_interval_var, width=7,
                   font=self.font, bg=self.th["field"], fg=self.th["fg"]).grid(row=4, column=1, sticky="w", padx=10)
        hook = tk.Frame(self.tab_settings, bg=self.th["bg"])
        hook.pack(fill="x", padx=16, pady=(6, 0))
        tk.Label(hook, text="Discord webhook URL (posts a message for every match):", bg=self.th["bg"], fg=self.th["fg"], font=self.font).pack(anchor="w")
        hook_line = tk.Frame(hook, bg=self.th["bg"])
        hook_line.pack(fill="x", pady=4)
        self.webhook_url_var = tk.StringVar(value=read_webhook_url())
        tk.Entry(hook_line, textvariable=self.webhook_url_var, show="\u2022", bg=self.th["field"], fg=self.th["fg"],
                 insertbackground=self.th["fg"], relief="flat", font=self.font).pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)
        self.btn(hook_line, "Send test", self.test_webhook).pack(side="left")
        self.webhook_enabled_var = tk.BooleanVar(value=bool(self.settings.get("webhook_enabled", False)))
        self.webhook_limited_var = tk.BooleanVar(value=bool(self.settings.get("webhook_limited_only", False)))
        tk.Checkbutton(hook, text="Send Discord alerts", variable=self.webhook_enabled_var, bg=self.th["bg"], fg=self.th["fg"],
                       selectcolor=self.th["field"], activebackground=self.th["bg"], font=self.font).pack(anchor="w")
        tk.Checkbutton(hook, text="Only alert for Limited items", variable=self.webhook_limited_var, bg=self.th["bg"], fg=self.th["fg"],
                       selectcolor=self.th["field"], activebackground=self.th["bg"], font=self.font).pack(anchor="w")
        tk.Label(hook, text="Create one in Discord: Channel settings > Integrations > Webhooks. Treat the URL like a password.",
                 bg=self.th["bg"], fg="#9fb7c8", font=self.font).pack(anchor="w")
        self.btn(self.tab_settings, "Save settings", self.save_settings).pack(anchor="w", padx=16, pady=10)
        tk.Label(self.tab_settings, text="Settings are saved beside the app in settings.json.", bg=self.th["bg"],
                 fg="#9fb7c8", font=self.font).pack(anchor="w", padx=16)

    def save_checkpoint(self, state):
        self.resume_state = dict(state)
        write_json(CHECKPOINT_FILE, state)
        if hasattr(self, "resume_btn"): self.resume_btn.configure(state="normal")

    def clear_checkpoint(self):
        try: CHECKPOINT_FILE.unlink(missing_ok=True)
        except OSError as exc: self.add_diagnostic(f"Could not clear resume checkpoint: {exc}")
        self.resume_state = {}
        if hasattr(self, "resume_btn"): self.resume_btn.configure(state="disabled")

    def history_add(self, entry):
        history=read_json(SEARCH_HISTORY_FILE,[])
        if not isinstance(history,list): history=[]
        history.insert(0,entry); write_json(SEARCH_HISTORY_FILE,history[:200])

    def history_update(self, run_id, **changes):
        history=read_json(SEARCH_HISTORY_FILE,[])
        if not isinstance(history,list): return
        for item in history:
            if item.get("run_id")==run_id: item.update(changes); break
        write_json(SEARCH_HISTORY_FILE,history[:200])

    def apply_saved_filters(self, filters):
        self.c_min_price.set(str(filters.get("minimum",0)))
        self.c_max_price.set("" if filters.get("maximum") is None else str(filters["maximum"]))
        self.c_type.set(filters.get("type_preset","All wearable items"))
        self.c_creator.set(filters.get("creator",""))
        self.c_sale_status.set(filters.get("sale_status","Any"))
        self.c_limited.set(bool(filters.get("limited",False)))
        self.c_sound.set(bool(filters.get("sound",False)))

    def show_search_history(self):
        history=read_json(SEARCH_HISTORY_FILE,[])
        if not isinstance(history,list) or not history:
            messagebox.showinfo("Search history","No keyword searches have been saved yet."); return
        win=tk.Toplevel(self.root); win.title("Recent searches"); win.geometry("760x440"); win.configure(bg=self.th["bg"])
        tk.Label(win,text="Select a search to load or rerun",bg=self.th["bg"],fg=self.th["fg"],font=("Segoe UI",12,"bold")).pack(anchor="w",padx=12,pady=8)
        listing=tk.Listbox(win,bg=self.th["field"],fg=self.th["fg"],selectbackground=self.th["btn"],font=self.font); listing.pack(fill="both",expand=True,padx=12,pady=6)
        for item in history: listing.insert("end",f"{item.get('time','?')} · {item.get('mode','search')} · {item.get('status','?')} · {', '.join(item.get('keywords',[]))[:95]}")
        listing.selection_set(0)
        def use(run=False):
            selected=listing.curselection()
            if not selected: return
            item=history[selected[0]]; self.g_keywords.set(", ".join(item.get("keywords",[])))
            self.g_limit_var.set(int(item.get("limit",500))); self.apply_saved_filters(item.get("filters",{})); win.destroy(); self.nb.select(self.tab_gen)
            if run:
                self.root.after(150,self.start_full_scan if item.get("mode")=="full" else self.start_gen)
            else: self.status_var.set("Search loaded from history")
        buttons=tk.Frame(win,bg=self.th["bg"]); buttons.pack(fill="x",padx=12,pady=8)
        self.btn(buttons,"Load selected",lambda:use(False)).pack(side="left",padx=(0,8)); self.btn(buttons,"Run selected",lambda:use(True)).pack(side="left")

    def add_diagnostic(self,message):
        if hasattr(self,"diag_box"):
            stamp=datetime.now().astimezone().strftime("%H:%M:%S"); self.append_text(self.diag_box,f"{stamp} · {message}")

    def run_diagnostics(self):
        self.diag_button.configure(state="disabled"); self.add_diagnostic("Checking Roblox catalog and economy API connectivity…")
        def work():
            for label,url,params in (("Catalog API",SEARCH_URL,{"Keyword":"hat","Limit":1}),("Economy API",URL.format(1),None)):
                started=time.monotonic()
                try:
                    response=requests.get(url,params=params,timeout=12); self.q.put(("diagnostic",f"{label}: HTTP {response.status_code} · {time.monotonic()-started:.2f}s"))
                except Exception as exc: self.q.put(("diagnostic",f"{label}: connection failed · {exc}"))
            self.q.put(("diagnostics_done",))
        threading.Thread(target=work,daemon=True).start()

    def build_diagnostics(self):
        tk.Label(self.tab_diag,text="Connection and save diagnostics",bg=self.th["bg"],fg=self.th["fg"],font=("Segoe UI",15,"bold")).pack(anchor="w",padx=16,pady=(14,8))
        self.diag_path=tk.Label(self.tab_diag,text=f"Results folder: {DATA_DIR}",bg=self.th["bg"],fg=self.th["fg"],font=self.font,anchor="w",wraplength=1000); self.diag_path.pack(fill="x",padx=16,pady=4)
        self.diag_button=self.btn(self.tab_diag,"Check API connections",self.run_diagnostics); self.diag_button.pack(anchor="w",padx=16,pady=8)
        tk.Label(self.tab_diag,text="Recent API and app errors",bg=self.th["bg"],fg=self.th["fg"],font=self.font).pack(anchor="w",padx=16)
        self.diag_box=self.text_box(self.tab_diag,height=24); self.diag_box.pack(fill="both",expand=True,padx=16,pady=(3,12))

    def update_proxy_health(self):
        if not hasattr(self,"proxy_tree"): return
        self.proxy_tree.delete(*self.proxy_tree.get_children())
        rows=sorted(self.pool.health_snapshot(), key=lambda it:(it.get("latency_ms") is None, it.get("latency_ms") or 0))
        for item in rows:
            status=item.get("status","Untested")
            if not item.get("active") and status=="Working": status="Removed"
            live=bool(item.get("in_use") and item.get("active"))
            ms=item.get("latency_ms")
            self.proxy_tree.insert("","end",values=(item["proxy"],status,f"{ms} ms" if ms is not None else "-",item.get("catalog","-"),"YES" if live else "",item.get("uses",0),item.get("failures",0),item.get("last_checked","Never")),tags=("inuse",) if live else ())
        self.proxy_tree.tag_configure("inuse",foreground="#6fe08f")

    def export_proxy_health(self):
        rows=self.pool.health_snapshot()
        if not rows: messagebox.showinfo("No proxies","There are no proxy records to export."); return
        path=filedialog.asksaveasfilename(defaultextension=".csv",filetypes=[("CSV","*.csv"),("Text","*.txt")])
        if not path: return
        try:
            if Path(path).suffix.lower()==".txt": Path(path).write_text("\n".join(row["proxy"] for row in rows if row["active"])+"\n",encoding="utf-8")
            else:
                with open(path,"w",newline="",encoding="utf-8") as f:
                    writer=csv.DictWriter(f,fieldnames=("proxy","status","latency_ms","catalog","failures","last_checked","active","uses","in_use"),extrasaction="ignore"); writer.writeheader(); writer.writerows(rows)
            self.status_var.set(f"Exported {len(rows)} proxy health records")
        except Exception as exc: messagebox.showerror("Proxy export failed",str(exc))

    def export_search_history(self):
        history=read_json(SEARCH_HISTORY_FILE,[])
        if not history: messagebox.showinfo("No history","There are no saved searches to export."); return
        path=filedialog.asksaveasfilename(defaultextension=".json",filetypes=[("JSON","*.json")])
        if path:
            try: Path(path).write_text(json.dumps(history,indent=2,ensure_ascii=False),encoding="utf-8"); self.status_var.set(f"Exported {len(history)} search history entries")
            except Exception as exc: messagebox.showerror("History export failed",str(exc))

    def resume_saved_run(self):
        state=self.resume_state if isinstance(self.resume_state,dict) else {}; mode=state.get("mode")
        if mode=="checker":
            self.apply_saved_filters(state.get("filters",{})); self.ids=list(state.get("remaining",[])); self.nb.select(self.tab_chk); self.start_check(resume=True)
        elif mode=="full":
            self.g_keywords.set(", ".join(state.get("keywords",[]))); self.g_limit_var.set(int(state.get("limit",0))); self.apply_saved_filters(state.get("filters",{})); self.nb.select(self.tab_gen); self.start_full_scan(resume=True)
        elif mode=="generate":
            self.g_keywords.set(", ".join(state.get("keywords",[]))); self.g_limit_var.set(int(state.get("limit",500))); self.nb.select(self.tab_gen); self.start_gen(resume=True)
        else: messagebox.showinfo("No saved run","There is no resumable run checkpoint.")

    def browse_results_folder(self):
        folder = filedialog.askdirectory(title="Choose results folder", initialdir=self.results_dir_var.get() or str(DATA_DIR))
        if folder:
            self.results_dir_var.set(folder)

    def persist_settings(self):
        try:
            save_settings_file(self.settings)
        except OSError as exc:
            messagebox.showerror("Settings error", str(exc))

    def save_settings(self):
        if self.check_running or self.gen_running or self.prx_running:
            messagebox.showinfo("Task running", "Wait for the current task to finish before changing settings.")
            return
        try:
            speed = max(10, min(100, int(self.settings_speed_var.get())))
            proxy_interval = max(0.2, min(10.0, float(self.proxy_interval_var.get())))
            hook_url = self.webhook_url_var.get().strip()
            if hook_url and not DISCORD_HOOK_RE.match(hook_url):
                raise ValueError("That doesn't look like a Discord webhook URL. It should start with https://discord.com/api/webhooks/")
            target_dir = str(Path(self.results_dir_var.get()).expanduser())
            set_data_directory(target_dir)
            write_webhook_url(hook_url)
            self.settings.update({"results_dir": str(DATA_DIR), "request_speed": speed,
                                  "startup_tab": self.startup_tab_var.get(),
                                  "restore_proxies": self.restore_proxies_var.get(),
                                  "notifications": self.notifications_var.get(),
                                  "proxy_interval": proxy_interval,
                                  "webhook_enabled": self.webhook_enabled_var.get(),
                                  "webhook_limited_only": self.webhook_limited_var.get()})
            self.pool.interval = proxy_interval
            save_settings_file(self.settings)
            try:
                self.db.close()
            except sqlite3.Error:
                pass
            self.db = init_db()
            self.c_speed.set(speed)
            self.status_var.set(f"Settings saved · results: {DATA_DIR}")
            if hasattr(self,"diag_path"): self.diag_path.config(text=f"Results folder: {DATA_DIR}")
            messagebox.showinfo("Saved", "Settings saved. The selected folder will be used for new results and the database.")
        except Exception as exc:
            messagebox.showerror("Settings error", str(exc))
    def restore_proxies(self):
        if PROXY_FILE.exists():
            try:
                proxies, bad = parse_proxy_text(PROXY_FILE.read_text(encoding="utf-8", errors="ignore"))
                self.pool.add(proxies)
                self.p_status.config(text=f"{self.pool.count():,} proxies loaded from proxies.txt ({bad} unreadable)")
                self.update_proxy_health()
                self.p_log_msg(f"Loaded {self.pool.count()} saved proxies")
            except Exception as exc:
                self.p_log_msg(f"Could not load proxies.txt: {exc}")

    def load_batch_keywords(self):
        path = filedialog.askopenfilename(title="Load keywords file", filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if path:
            try:
                words = [line.strip() for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
                self.g_keywords.set(", ".join(words))
            except Exception as exc:
                messagebox.showerror("Read error", str(exc))

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            label = "Resume"
            self.status_var.set("Paused · current task is saved and can be resumed")
        else:
            self.pause_event.set()
            label = "Pause"
            self.status_var.set("Task resumed")
        for button in (getattr(self,"g_btn_pause",None),getattr(self,"c_btn_pause",None)):
            if button is not None:
                button.configure(text=label)

    def start_gen(self, resume=False):
        state = read_json(CHECKPOINT_FILE,{}) if resume else {}
        if resume and state.get("mode") != "generate":
            messagebox.showinfo("Resume","No saved keyword generation run was found."); return
        keywords = list(state.get("keywords",[])) if resume else parse_keywords(self.g_keywords.get())
        if not keywords:
            messagebox.showerror("Missing keywords","Enter one or more keywords first."); return
        if self.gen_running or self.check_running: return
        if not self.pool.count() and not self.uses_direct_catalog():
            messagebox.showerror("No active proxies", "Choose Direct (no proxy) or load at least one proxy on the Proxies tab.")
            return
        try: limit_value = int(state.get("limit",self.g_limit_var.get()) if resume else self.g_limit_var.get())
        except (ValueError,tk.TclError):
            messagebox.showerror("Invalid limit","Use a whole number from 0 to 10,000."); return
        if limit_value < 0 or limit_value > 10000:
            messagebox.showerror("Invalid limit","Use a whole number from 0 to 10,000."); return
        limit = limit_value or None
        run_id = state.get("run_id") or str(time.time_ns())
        filters = state.get("filters") or {}
        if not resume:
            try: filters=saved_filters(self.filters_from_ui())
            except ValueError: filters={}
            state={"mode":"generate","run_id":run_id,"keywords":keywords,"limit":limit_value,
                   "ids":[],"seen_ids":[],"keyword_counts":{},"checked":0,"filters":filters}
            self.history_add({"run_id":run_id,"time":datetime.now().astimezone().isoformat(timespec="seconds"),
                              "mode":"generate","keywords":keywords,"limit":limit_value,"filters":filters,
                              "status":"Running","results":0})
        write_json(CHECKPOINT_FILE,state)
        self.resume_btn.configure(state="disabled")
        self.gen_stop.clear(); self.chk_stop.clear(); self.pause_event.set(); self.gen_running=True
        self.g_btn_start.configure(state="disabled"); self.g_btn_full.configure(state="disabled")
        self.g_btn_pause.configure(state="normal",text="Pause"); self.g_btn_stop.configure(state="normal")
        self.g_status.config(text=f"Searching {len(keywords)} keyword(s)…")
        self.status_var.set(f"Keyword search running · limit {limit_value or 'all'} per keyword")
        self.g_box.configure(state="normal"); self.g_box.delete("1.0","end"); self.g_box.configure(state="disabled")
        def work():
            seen=set(state.get("seen_ids",[])); ids=list(state.get("ids",[])); counts=dict(state.get("keyword_counts",{}))
            if SKIP_SAVED_IDS:
                prior=load_saved_ids(); seen|=prior
                if prior: self.q.put(("glog",f"Skipping {len(prior):,} items already in valid_items.txt so the search reaches new results."))
            catalog_state={"proxy":self.selected_catalog_proxy(), "direct":self.uses_direct_catalog()}
            route = "directly without a proxy" if catalog_state["direct"] else "through the selected proxy"
            self.q.put(("glog", f"Catalog searches are routed {route} for this run."))
            try:
                for word in keywords:
                    if self.gen_stop.is_set(): break
                    remaining=None if limit is None else max(0,limit-int(counts.get(word,0)))
                    if remaining == 0: continue
                    self.q.put(("glog",f'Searching "{word}"…'))
                    def collect(asset_id):
                        ids.append(asset_id); counts[word]=int(counts.get(word,0))+1
                        if len(ids)%10==0:
                            write_json(CHECKPOINT_FILE,{"mode":"generate","run_id":run_id,"keywords":keywords,"limit":limit_value,
                                "ids":ids,"seen_ids":list(seen),"keyword_counts":counts,"checked":0,"filters":filters})
                    found=search_keyword(word,remaining,seen,self.gen_stop,lambda msg:self.q.put(("glog",msg)),on_id=collect,pause_event=self.pause_event,proxy_pool=self.pool,proxy_state=catalog_state,on_pool_empty=self.refill_proxies)
                    self.q.put(("glog",f'"{word}": found {len(found)} new IDs'))
                stopped=self.gen_stop.is_set()
                if stopped:
                    write_json(CHECKPOINT_FILE,{"mode":"generate","run_id":run_id,"keywords":keywords,"limit":limit_value,
                        "ids":ids,"seen_ids":list(seen),"keyword_counts":counts,"checked":0,"filters":filters})
                self.history_update(run_id,status="Stopped" if stopped else "Complete",results=len(ids),completed_at=datetime.now().astimezone().isoformat(timespec="seconds"))
                self.q.put(("gdone",ids,stopped,run_id))
            except Exception as exc:
                write_json(CHECKPOINT_FILE,{"mode":"generate","run_id":run_id,"keywords":keywords,"limit":limit_value,
                    "ids":ids,"seen_ids":list(seen),"keyword_counts":counts,"checked":0,"filters":filters})
                self.history_update(run_id,status=f"Error: {exc}",results=len(ids))
                self.q.put(("gerror",str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def send_gen_to_chk(self):
        if not self.gen_ids:
            messagebox.showinfo("No IDs", "Generate IDs first.")
            return
        self.ids = list(self.gen_ids)
        self.c_ids_name.config(text=f"{len(self.ids):,} IDs loaded from generator")
        self.nb.select(self.tab_chk)

    def save_gen_ids(self):
        if not self.gen_ids:
            messagebox.showinfo("No IDs", "Generate IDs first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if path:
            Path(path).write_text("\n".join(map(str, self.gen_ids)) + "\n", encoding="utf-8")
            messagebox.showinfo("Saved", f"Saved {len(self.gen_ids)} IDs.")

    def load_wordlist(self):
        path = filedialog.askopenfilename(title="Load item IDs", filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if not path:
            return
        try:
            self.ids, duplicates, invalid = parse_id_text(Path(path).read_text(encoding="utf-8", errors="ignore"))
            self.c_ids_name.config(text=f"{len(self.ids):,} valid unique IDs loaded")
            note = f"Loaded {Path(path).name}: {len(self.ids)} usable; removed {duplicates} duplicates; skipped {invalid} invalid lines."
            self.c_log_msg(note)
            self.status_var.set(note)
        except Exception as exc:
            messagebox.showerror("Read error", str(exc))
    def filters_from_ui(self):
        try:
            minimum = int(self.c_min_price.get() or 0)
            maximum = int(self.c_max_price.get()) if self.c_max_price.get().strip() else None
        except ValueError as exc:
            raise ValueError("Prices must be whole numbers.") from exc
        if minimum < 0 or (maximum is not None and maximum < 0):
            raise ValueError("Prices cannot be negative.")
        if maximum is not None and maximum < minimum and self.c_sale_status.get() != "Off sale":
            raise ValueError("Maximum price must be greater than or equal to minimum price.")
        return {"minimum": minimum, "maximum": maximum,
                "types": set(TYPE_PRESETS[self.c_type.get()]), "type_preset": self.c_type.get(),
                "creator": self.c_creator.get().strip(), "sale_status": self.c_sale_status.get(),
                "limited": self.c_limited.get(), "sound": self.c_sound.get(),
                "notify": bool(self.settings.get("notifications", True))}

    @staticmethod
    def filter_reason(info, filters):
        """None if the item passes; otherwise a short reason it was rejected."""
        if info.get("type") not in filters["types"]:
            return "type not selected"
        creator = filters.get("creator", "").casefold()
        if creator and creator not in str(info.get("creator", "")).casefold():
            return "creator mismatch"
        price = info.get("price")
        sale_status = filters.get("sale_status", "For sale" if filters.get("for_sale") else "Any")
        if sale_status == "For sale" and not info.get("for_sale", price is not None):
            return "not for sale"
        if sale_status == "Off sale" and info.get("for_sale", price is not None):
            return "is for sale"
        if price is None:
            if sale_status != "Off sale" and filters["minimum"] > 0:
                return "no price (off-sale) with min price set"
        else:
            if price < filters["minimum"]:
                return "price below min"
            if filters["maximum"] is not None and price > filters["maximum"]:
                return "price above max"
        if filters["limited"] and not info.get("limited"):
            return "not limited"
        return None

    @staticmethod
    def passes_filters(info, filters):
        return App.filter_reason(info, filters) is None

    def reject_summary(self):
        top = self.reject.most_common(3)
        return " · skipped: " + ", ".join(f"{k} {v}" for k, v in top) if top else ""

    def record_match(self, info, saved, conn, filters):
        asset_id = int(info["id"])
        if asset_id in saved:
            self.reject["already saved"] += 1
            return False
        reason = self.filter_reason(info, filters)
        if reason:
            self.reject[reason] += 1
            return False
        saved.add(asset_id)
        try:
            with VALID_FILE.open("a", encoding="utf-8") as output:
                price = info["price"] if info["price"] is not None else "off-sale"
                output.write(f"{info['id']} | {info['name']} | {info['type']} | {info['creator']} | {price} R$ | {info['limited'] or '-'} | {info['url']}\n")
            save_to_db(info, "run", conn)
        except Exception as exc:
            self.q.put(("clog", f"Could not save item {asset_id}: {exc}"))
        self.q.put(("cresult", info))
        if filters["sound"]:
            self.q.put(("beep", bool(info.get("limited"))))
        if filters["notify"]:
            self.q.put(("notify", info))
        if self.settings.get("webhook_enabled") and (info.get("limited") or not self.settings.get("webhook_limited_only")):
            hook_url = read_webhook_url()
            if DISCORD_HOOK_RE.match(hook_url):
                self.webhook.send(hook_url, build_webhook_payload(info))
        return True

    def start_check(self, resume=False):
        state=read_json(CHECKPOINT_FILE,{}) if resume else {}
        if resume and state.get("mode")!="checker":
            messagebox.showinfo("Resume","No saved checker run was found."); return
        ids=list(state.get("remaining",[])) if resume else list(self.ids)
        if not ids:
            messagebox.showerror("No IDs","Load a word list or send generated IDs to the Checker first."); return
        if not self.pool.count():
            messagebox.showerror("No active proxies", "Load or scrape at least one proxy on the Proxies tab before checking IDs.")
            return
        try: filters=active_filters(state.get("filters",{})) if resume else self.filters_from_ui()
        except ValueError as exc: messagebox.showerror("Invalid filter",str(exc)); return
        if self.check_running or self.gen_running: return
        run_id=state.get("run_id",str(time.time_ns()))
        checked=int(state.get("checked",0)); found=int(state.get("found",0))
        if not resume:
            state={"mode":"checker","run_id":run_id,"all_ids":ids,"remaining":ids,"filters":saved_filters(filters),"checked":0,"found":0}
        write_json(CHECKPOINT_FILE,state)
        self.resume_btn.configure(state="disabled")
        self.chk_stop.clear(); self.gen_stop.clear(); self.pause_event.set(); self.check_running=True
        self.c_btn_start.configure(state="disabled"); self.c_btn_stop.configure(state="normal"); self.c_btn_pause.configure(state="normal",text="Pause")
        self.g_btn_pause.configure(state="normal",text="Pause"); self.g_btn_stop.configure(state="normal")
        self.c_status.config(text=f"Checking {len(ids):,} remaining IDs…"); self.status_var.set(f"Checker running · {checked} already checked")
        self.tree.delete(*self.tree.get_children()); self.c_log.configure(state="normal"); self.c_log.delete("1.0","end"); self.c_log.configure(state="disabled")
        limiter.set_rate(self.c_speed.get()); self.reject.clear()
        def work():
            nonlocal checked,found
            remaining=list(ids); conn=init_db(); started=time.monotonic(); next_index=0
            try:
                saved=load_saved_ids(); pool=self.pool
                for index,asset_id in enumerate(ids):
                    next_index=index
                    if not wait_if_paused(self.chk_stop,self.pause_event): break
                    _code,info,give_up=self.check_with_refill(asset_id)
                    if give_up:
                        self.q.put(("diagnostic",f"Stopped at ID {asset_id}: no working proxy could reach Roblox. Refresh proxies, then Resume - no IDs were skipped."))
                        next_index=index; remaining=ids[index:]; self.chk_stop.set(); break
                    if _code=="error": self.reject["request failed"]+=1; continue
                    if _code=="404": self.reject["not found (404)"]+=1
                    checked+=1
                    if info and self.record_match(info,saved,conn,filters): found+=1
                    remaining=ids[index+1:]; next_index=index+1
                    if checked%10==0:
                        elapsed=max(time.monotonic()-started,.001)
                        self.q.put(("cprogress",f"Checked {checked}/{len(state.get('all_ids',ids))} · {found} matches · {checked/elapsed:.1f}/sec"+self.reject_summary()))
                        snapshot={**state,"remaining":remaining,"checked":checked,"found":found}
                        write_json(CHECKPOINT_FILE,snapshot)
                stopped=self.chk_stop.is_set()
                if stopped:
                    remaining=ids[next_index:]
                    write_json(CHECKPOINT_FILE,{**state,"remaining":remaining,"checked":checked,"found":found})
                self.q.put(("cdone",checked,found,stopped,remaining,run_id))
            except Exception as exc:
                write_json(CHECKPOINT_FILE,{**state,"remaining":ids[next_index:],"checked":checked,"found":found})
                self.q.put(("cerror",str(exc)))
            finally: conn.close()
        threading.Thread(target=work,daemon=True).start()

    def start_full_scan(self, resume=False):
        state=read_json(CHECKPOINT_FILE,{}) if resume else {}
        if resume and state.get("mode")!="full":
            messagebox.showinfo("Resume","No saved full search run was found."); return
        keywords=list(state.get("keywords",[])) if resume else parse_keywords(self.g_keywords.get())
        if not keywords:
            messagebox.showerror("Missing keywords","Enter or select search keywords first."); return
        if not self.pool.count():
            messagebox.showerror("No active proxies", "Load or scrape at least one proxy on the Proxies tab before searching and checking.")
            return
        try:
            limit_value=int(state.get("limit",self.g_limit_var.get()) if resume else self.g_limit_var.get())
            filters=active_filters(state.get("filters",{})) if resume else self.filters_from_ui()
        except (ValueError,tk.TclError) as exc:
            messagebox.showerror("Invalid search settings",str(exc)); return
        if limit_value<0 or limit_value>10000:
            messagebox.showerror("Invalid limit","Use a whole number from 0 to 10,000."); return
        limit=limit_value or None
        if self.gen_running or self.check_running: return
        run_id=state.get("run_id",str(time.time_ns()))
        if not resume:
            state={"mode":"full","run_id":run_id,"keywords":keywords,"limit":limit_value,"filters":saved_filters(filters),
                   "ids":[],"seen_ids":[],"pending_ids":[],"keyword_counts":{},"checked":0,"found":0}
            self.history_add({"run_id":run_id,"time":datetime.now().astimezone().isoformat(timespec="seconds"),"mode":"full",
                              "keywords":keywords,"limit":limit_value,"filters":saved_filters(filters),"status":"Running","results":0})
        write_json(CHECKPOINT_FILE,state)
        self.resume_btn.configure(state="disabled")
        self.gen_stop.clear(); self.chk_stop.clear(); self.pause_event.set(); self.gen_running=self.check_running=True
        self.g_btn_start.configure(state="disabled"); self.g_btn_full.configure(state="disabled")
        self.g_btn_pause.configure(state="normal",text="Pause"); self.g_btn_stop.configure(state="normal")
        self.c_btn_start.configure(state="disabled"); self.c_btn_stop.configure(state="normal"); self.c_btn_pause.configure(state="normal",text="Pause")
        self.g_status.config(text=f"Searching and checking · limit {limit_value or 'all'} per keyword")
        self.c_status.config(text="Search + check running…"); self.status_var.set("Search + Check active · pause, resume or stop from here")
        self.tree.delete(*self.tree.get_children()); self.g_box.configure(state="normal"); self.g_box.delete("1.0","end"); self.g_box.configure(state="disabled")
        self.c_log.configure(state="normal"); self.c_log.delete("1.0","end"); self.c_log.configure(state="disabled"); limiter.set_rate(self.c_speed.get()); self.reject.clear()
        def work():
            seen=set(state.get("seen_ids",[])); all_ids=list(state.get("ids",[])); pending=list(state.get("pending_ids",[]))
            if SKIP_SAVED_IDS:
                prior=load_saved_ids(); seen|=prior
                if prior: self.q.put(("glog",f"Skipping {len(prior):,} items already in valid_items.txt so the search reaches new results."))
            counts=dict(state.get("keyword_counts",{})); checked=int(state.get("checked",0)); found=int(state.get("found",0)); started=time.monotonic()
            conn=init_db(); saved=load_saved_ids(); pool=self.pool
            catalog_state={"proxy":self.selected_catalog_proxy(), "direct":self.uses_direct_catalog()}
            route = "directly without a proxy" if catalog_state["direct"] else "through the selected proxy"
            self.q.put(("glog", f"Catalog searches are routed {route} for this run."))
            def save_progress():
                write_json(CHECKPOINT_FILE,{"mode":"full","run_id":run_id,"keywords":keywords,"limit":limit_value,"filters":saved_filters(filters),
                    "ids":all_ids,"seen_ids":list(seen),"pending_ids":pending,"keyword_counts":counts,"checked":checked,"found":found})
            def check_pending(asset_id,keyword):
                nonlocal checked,found
                if not wait_if_paused(self.chk_stop,self.pause_event): return False
                code,info,give_up=self.check_with_refill(asset_id)
                if give_up:
                    self.q.put(("diagnostic",f"Stopped at ID {asset_id}: no working proxy could reach Roblox. Refresh proxies, then Resume."))
                    self.chk_stop.set(); return False
                if code=="error": self.reject["request failed"]+=1; return True
                if code=="404": self.reject["not found (404)"]+=1
                checked+=1
                if info and self.record_match(info,saved,conn,filters): found+=1
                if asset_id in pending: pending.remove(asset_id)
                if checked%10==0:
                    elapsed=max(time.monotonic()-started,.001); self.q.put(("full_progress",keyword,checked,found,checked/elapsed)); save_progress()
                return True
            try:
                for asset_id in list(pending):
                    if not check_pending(asset_id,"Resuming saved results"): break
                for keyword in keywords:
                    if self.chk_stop.is_set(): break
                    remaining=None if limit is None else max(0,limit-int(counts.get(keyword,0)))
                    if remaining==0: continue
                    self.q.put(("glog",f'Searching "{keyword}"…'))
                    def on_id(asset_id):
                        all_ids.append(asset_id); pending.append(asset_id); counts[keyword]=int(counts.get(keyword,0))+1
                        check_pending(asset_id,keyword)
                    found_ids=search_keyword(keyword,remaining,seen,self.chk_stop,lambda msg:self.q.put(("glog",msg)),on_id=on_id,pause_event=self.pause_event,proxy_pool=self.pool,proxy_state=catalog_state,on_pool_empty=self.refill_proxies)
                    self.q.put(("glog",f'"{keyword}": found {len(found_ids)} new catalog IDs'))
                stopped=self.chk_stop.is_set()
                if stopped: save_progress()
                else: CHECKPOINT_FILE.unlink(missing_ok=True)
                self.history_update(run_id,status="Stopped" if stopped else "Complete",results=len(all_ids),checked=checked,matches=found,completed_at=datetime.now().astimezone().isoformat(timespec="seconds"))
                self.q.put(("full_done",all_ids,checked,found,stopped,run_id,dict(state,ids=all_ids,seen_ids=list(seen),pending_ids=pending,keyword_counts=counts,checked=checked,found=found)))
            except Exception as exc:
                save_progress(); self.history_update(run_id,status=f"Error: {exc}",results=len(all_ids)); self.q.put(("cerror",str(exc)))
            finally: conn.close()
        threading.Thread(target=work,daemon=True).start()

    def stop_check(self):
        self.chk_stop.set(); self.gen_stop.set(); self.pause_event.set()
        self.c_status.config(text="Stopping after the current request…")
        self.status_var.set("Stopping · progress is being saved")

    def open_selected(self):
        selection = self.tree.selection()
        if selection:
            webbrowser.open(ITEM_URL.format(selection[0]))

    def load_proxy_file(self):
        path = filedialog.askopenfilename(title="Load proxies", filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if not path:
            return
        try:
            proxies, bad = parse_proxy_text(Path(path).read_text(encoding="utf-8", errors="ignore"))
            added = self.pool.add(proxies)
            save_proxies(self.pool)
            self.p_status.config(text=f"{self.pool.count():,} proxies loaded ({bad} unreadable)")
            self.update_proxy_health()
            self.p_log_msg(f"Added {added} proxies from {Path(path).name} ({bad} unreadable)")
        except Exception as exc:
            messagebox.showerror("Proxy read error", str(exc))

    def add_pasted_proxies(self):
        raw = self.p_input.get("1.0", "end").strip()
        if not raw:
            messagebox.showinfo("No proxies", "Paste proxy addresses, one per line.")
            return
        proxies, bad = parse_proxy_text(raw)
        added = self.pool.add(proxies)
        save_proxies(self.pool)
        self.p_status.config(text=f"{self.pool.count():,} proxies loaded ({bad} unreadable)")
        self.update_proxy_health()
        self.p_log_msg(f"Added {added} pasted proxies ({bad} unreadable)")
        self.p_input.delete("1.0", "end")

    def start_scrape(self):
        if self.prx_running:
            return
        country, https_only = self.p_country.get(), self.p_https.get()
        self.prx_running = True
        self.p_btn_scrape.configure(state="disabled")
        def work():
            try:
                proxies = scrape_proxies(country, https_only, lambda message: self.q.put(("plog", message)))
                added = self.pool.add(proxies)
                self.q.put(("pscrape_results", proxies))
                save_proxies(self.pool)
                self.q.put(("pstatus", self.pool.count(), f"loaded; added {added}"))
            except Exception as exc:
                self.q.put(("plog", f"Proxy scrape failed: {exc}"))
            finally:
                self.q.put(("pscrape_done",))
        threading.Thread(target=work, daemon=True).start()

    def start_test(self):
        if self.prx_running:
            return
        proxies = self.pool.snapshot()
        if not proxies:
            messagebox.showinfo("No proxies", "Paste proxies, load a file, or scrape them first.")
            return
        self.prx_stop.clear()
        self.prx_running = True
        self.p_btn_test.configure(state="disabled")
        def work():
            try:
                good, _tested = test_proxies(proxies, lambda message: self.q.put(("plog", message)), self.prx_stop, self.pool)
                self.pool.replace(good)
                save_proxies(self.pool)
                self.q.put(("pstatus", len(good), "working after test"))
            except Exception as exc:
                self.q.put(("plog", f"Proxy test failed: {exc}"))
            finally:
                self.q.put(("ptest_done",))
        threading.Thread(target=work, daemon=True).start()

    def test_webhook(self):
        url = self.webhook_url_var.get().strip()
        if not DISCORD_HOOK_RE.match(url):
            messagebox.showerror("Discord webhook", "Paste a Discord webhook URL first (https://discord.com/api/webhooks/...).")
            return
        def work():
            ok, message = post_webhook(url, {"username": "Item Finder", "content": "Webhook connected. Item matches will appear in this channel.",
                                             "allowed_mentions": {"parse": []}}, attempts=2)
            self.q.put(("wh_test", ok, "Test message sent. Check your Discord channel." if ok else message))
        threading.Thread(target=work, daemon=True).start()

    def start_conn_test(self):
        if getattr(self, "_conn_testing", False):
            return
        self._conn_testing = True
        self.p_btn_conn.configure(state="disabled")
        def work():
            try:
                diagnose_connection(self.pool, lambda message: self.q.put(("plog", message)))
            except Exception as exc:
                self.q.put(("plog", f"Connection test failed: {exc}"))
            finally:
                self.q.put(("conn_done",))
        threading.Thread(target=work, daemon=True).start()

    def remove_failed_proxies(self):
        removed = self.pool.remove_failed()
        save_proxies(self.pool)
        self.update_proxy_health()
        if hasattr(self, "g_proxy_box"):
            self.refresh_generator_proxies()
        self.p_status.config(text=f"{self.pool.count():,} proxies remain; removed {len(removed)} failed/offline")
        self.p_log_msg(f"Removed {len(removed)} failed/offline proxies; active checks use the updated pool automatically")

    def clear_proxies(self):
        self.pool.replace([])
        save_proxies(self.pool)
        self.update_proxy_health()
        self.p_status.config(text="No proxies loaded")
        self.p_log_msg("Proxy list cleared")

    def p_log_msg(self, message):
        self.append_text(self.p_log, message)

    def c_log_msg(self, message):
        self.append_text(self.c_log, message)

    def export_rows(self):
        rows = []
        if VALID_FILE.exists():
            for line in VALID_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.split(" | ")
                if len(parts) >= 6:
                    rows.append({"ID": parts[0], "Name": parts[1], "Type": parts[2], "Creator": parts[3], "Price": parts[4], "Limited": parts[5]})
        return rows

    def export_csv(self):
        rows = self.export_rows()
        if not rows:
            messagebox.showinfo("No results", "There are no results to export yet.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            try:
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
                messagebox.showinfo("Export complete", f"Exported {len(rows)} items.")
            except Exception as exc:
                messagebox.showerror("Export failed", str(exc))

    def export_json(self):
        rows = self.export_rows()
        if not rows:
            messagebox.showinfo("No results", "There are no results to export yet.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(rows, f, indent=2)
                messagebox.showinfo("Export complete", f"Exported {len(rows)} items.")
            except Exception as exc:
                messagebox.showerror("Export failed", str(exc))

    def refresh_stats(self):
        rows = self.export_rows()
        counts, prices = Counter(row["Type"] for row in rows), []
        for row in rows:
            try:
                prices.append(int(row["Price"].replace(" R$", "")))
            except (ValueError, AttributeError):
                pass
        report = [f"Total items: {len(rows)}", "", "By type:"]
        report.extend(f"  {kind}: {count}" for kind, count in counts.most_common())
        if prices:
            report.extend(["", f"Price range: {min(prices)} - {max(prices)} R$", f"Average price: {sum(prices) / len(prices):.0f} R$"])
        self.stats_box.configure(state="normal")
        self.stats_box.delete("1.0", "end")
        self.stats_box.insert("end", "\n".join(report) if rows else "No results yet")
        self.stats_box.configure(state="disabled")

    def show_toast(self, info):
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg="#162a38")
        name = str(info.get("name") or "Catalog item")
        price = info.get("price")
        message = f"Match: {name} · {price} R$" if price is not None else f"Match: {name} · off-sale"
        tk.Label(toast, text=message, bg="#162a38", fg="#e8f4ff", font=("Segoe UI", 11, "bold"),
                 padx=16, pady=12, wraplength=360, justify="left").pack()
        toast.update_idletasks()
        x = self.root.winfo_rootx() + max(0, self.root.winfo_width() - toast.winfo_width() - 24)
        y = self.root.winfo_rooty() + max(0, self.root.winfo_height() - toast.winfo_height() - 54)
        toast.geometry(f"+{x}+{y}")
        toast.after(4500, toast.destroy)

    def poll(self):
        try:
            while True:
                message=self.q.get_nowait(); kind=message[0]
                if kind in ("glog","clog","plog"):
                    text=message[1]
                    target={"glog":self.g_box,"clog":self.c_log,"plog":self.p_log}[kind]
                    self.append_text(target,text)
                    lowered=str(text).casefold()
                    if any(word in lowered for word in ("failed","error","timeout","http ")):
                        self.add_diagnostic(text)
                    if kind=="glog": self.status_var.set(text)
                elif kind=="gdone":
                    ids,stopped,run_id=message[1],message[2],message[3]
                    self.gen_ids=ids; self.gen_running=False
                    self.g_btn_start.configure(state="normal"); self.g_btn_full.configure(state="normal")
                    self.g_btn_pause.configure(state="disabled",text="Pause"); self.g_btn_stop.configure(state="disabled")
                    suffix=" · stopped" if stopped else ""
                    self.g_status.config(text=f"Generated {len(ids):,} IDs{suffix}")
                    self.status_var.set(f"Keyword search {('stopped' if stopped else 'finished')} · {len(ids):,} unique IDs")
                    if ids: self.append_text(self.g_box,"IDs: "+", ".join(map(str,ids[:1000])))
                    elif not stopped: self.append_text(self.g_box,"No IDs returned. Check the Diagnostics tab for API errors.")
                    if stopped:
                        self.resume_state=read_json(CHECKPOINT_FILE,{})
                        self.resume_btn.configure(state="normal")
                    else: self.clear_checkpoint()
                elif kind=="gerror":
                    self.gen_running=False; self.g_btn_start.configure(state="normal"); self.g_btn_full.configure(state="normal")
                    self.g_btn_pause.configure(state="disabled",text="Pause"); self.g_btn_stop.configure(state="disabled")
                    self.g_status.config(text="Search failed"); self.status_var.set(f"Search failed · {message[1]}")
                    self.append_text(self.g_box,f"Search error: {message[1]}"); self.add_diagnostic(f"Keyword search error: {message[1]}")
                    saved=read_json(CHECKPOINT_FILE,{})
                    if saved.get("mode"): self.resume_state=saved; self.resume_btn.configure(state="normal")
                elif kind=="full_progress":
                    _,keyword,checked,found,speed=message
                    text=f"{keyword} · checked {checked} · {found} matches · {speed:.1f}/sec"+self.reject_summary()
                    self.c_status.config(text=text); self.status_var.set("Search + Check running · "+text)
                elif kind=="full_done":
                    _,ids,checked,found,stopped,run_id,saved_state=message
                    self.gen_ids=ids; self.gen_running=self.check_running=False
                    self.g_btn_start.configure(state="normal"); self.g_btn_full.configure(state="normal")
                    self.g_btn_pause.configure(state="disabled",text="Pause"); self.g_btn_stop.configure(state="disabled")
                    self.c_btn_start.configure(state="normal"); self.c_btn_stop.configure(state="disabled"); self.c_btn_pause.configure(state="disabled",text="Pause")
                    suffix=" · stopped" if stopped else ""
                    self.g_status.config(text=f"Search + Check finished · {len(ids)} IDs{suffix}")
                    self.c_status.config(text=f"Done · checked {checked} · {found} matches{suffix}")
                    self.c_ids_name.config(text=f"{len(ids):,} IDs collected by Search + Check")
                    self.status_var.set(f"Search + Check {'stopped' if stopped else 'finished'} · {checked} checked · {found} matches{suffix}")
                    self.append_text(self.g_box,f"Finished: collected {len(ids)} IDs, checked {checked}, found {found} matches.")
                    if stopped:
                        self.resume_state=saved_state; self.resume_btn.configure(state="normal")
                    else: self.clear_checkpoint()
                elif kind=="cresult":
                    info=message[1]
                    self.tree.insert("","end",iid=str(info["id"]),values=(info["id"],info["name"],info["type"],info["creator"],info["price"] if info["price"] is not None else "off-sale",info["limited"] or "-"))
                elif kind=="cprogress":
                    self.c_status.config(text=message[1]); self.status_var.set("Checker running · "+message[1])
                elif kind=="cdone":
                    _,checked,found,stopped,remaining,run_id=message
                    self.check_running=False; self.c_btn_start.configure(state="normal"); self.c_btn_stop.configure(state="disabled")
                    self.c_btn_pause.configure(state="disabled",text="Pause"); self.g_btn_pause.configure(state="disabled",text="Pause"); self.g_btn_stop.configure(state="disabled")
                    suffix=" · stopped" if stopped else ""
                    self.c_status.config(text=f"Done · checked {checked} · {found} matches{suffix}")
                    self.status_var.set(f"Checker {'stopped' if stopped else 'finished'} · {checked} checked · {found} matches{suffix}")
                    if self.reject: self.c_log_msg("Not counted as matches" + self.reject_summary().replace(" · skipped:", ":") + f" (total rejected {sum(self.reject.values())})")
                    if stopped:
                        saved=read_json(CHECKPOINT_FILE,{})
                        saved.update(mode="checker",run_id=run_id,remaining=remaining,checked=checked,found=found)
                        write_json(CHECKPOINT_FILE,saved); self.resume_state=saved; self.resume_btn.configure(state="normal")
                    else: self.clear_checkpoint()
                elif kind=="cerror":
                    self.check_running=self.gen_running=False
                    self.c_btn_start.configure(state="normal"); self.c_btn_stop.configure(state="disabled"); self.c_btn_pause.configure(state="disabled",text="Pause")
                    self.g_btn_start.configure(state="normal"); self.g_btn_full.configure(state="normal"); self.g_btn_pause.configure(state="disabled",text="Pause"); self.g_btn_stop.configure(state="disabled")
                    self.c_status.config(text="Checker error · see log"); self.status_var.set(f"Checker error · {message[1]}")
                    self.c_log_msg(f"Checker error: {message[1]}"); self.add_diagnostic(f"Checker error: {message[1]}")
                    saved=read_json(CHECKPOINT_FILE,{})
                    if saved.get("mode"): self.resume_state=saved; self.resume_btn.configure(state="normal")
                elif kind=="pscrape_results":
                    proxies=message[1]
                    if proxies:
                        self.p_input.delete("1.0","end"); self.p_input.insert("1.0","\n".join(proxies[:500]))
                        if len(proxies)>500: self.p_log_msg(f"Showing first 500 of {len(proxies)} scraped proxies; all are in the active pool.")
                elif kind=="pstatus":
                    self.p_status.config(text=f"{message[1]:,} proxies {message[2]}"); self.status_var.set(f"Proxy pool · {message[1]} proxies {message[2]}"); self.update_proxy_health()
                elif kind=="conn_done":
                    self._conn_testing=False; self.p_btn_conn.configure(state="normal")
                elif kind in ("pscrape_done","ptest_done"):
                    self.prx_running=False; self.p_btn_scrape.configure(state="normal"); self.p_btn_test.configure(state="normal"); self.update_proxy_health()
                elif kind == "rplayer_done":
                    data = message[1]; self.r_player_button.configure(state="normal")
                    user_id = data.get("_user_id"); name = data.get("_roblox_name", "")
                    self.rolimons_player_url = f"https://www.rolimons.com/player/{user_id}"
                    self.r_player_link.configure(state="normal")
                    value = data.get("value"); rap = data.get("rap")
                    lines = [f"{data.get('_display_name', name)} (@{name}) · Roblox ID {user_id}",
                             f"Rolimon's value: {value:,}   RAP: {rap:,}" if isinstance(value, int) and isinstance(rap, int) else f"Rolimon's value: {value}   RAP: {rap}",
                             f"Premium: {'Yes' if data.get('premium') else 'No'} · Rank: {data.get('rank') or '—'} · Last online: {data.get('last_online') or 'not available'}"]
                    self.r_player_output.configure(state="normal"); self.r_player_output.delete("1.0", "end"); self.r_player_output.insert("end", "\n".join(lines)); self.r_player_output.configure(state="disabled")
                    self.status_var.set(f"Rolimon's player lookup complete · {name}")
                elif kind == "rplayer_blocked":
                    details = message[1]
                    self.r_player_button.configure(state="normal")
                    self.rolimons_player_url = f"https://www.rolimons.com/player/{details['_user_id']}"
                    self.r_player_link.configure(state="normal")
                    self.r_player_output.configure(state="normal")
                    self.r_player_output.delete("1.0", "end")
                    self.r_player_output.insert(
                        "end",
                        f"Roblox username resolved: {details['_roblox_name']} (ID {details['_user_id']}).\n\n"
                        "Rolimon's denied its player-info API request (HTTP 403) from this connection. "
                        "This does not mean the username is invalid. Use the button below to open "
                        "the player's Rolimon's page; some profiles may not be tracked there."
                    )
                    self.r_player_output.configure(state="disabled")
                    self.add_diagnostic("Rolimon's player API returned HTTP 403; direct profile link enabled.")
                elif kind == "rplayer_error":
                    self.r_player_button.configure(state="normal")
                    self.r_player_output.configure(state="normal"); self.r_player_output.delete("1.0", "end"); self.r_player_output.insert("end", "Lookup failed: " + message[1]); self.r_player_output.configure(state="disabled")
                    self.add_diagnostic("Rolimon's player lookup failed: " + message[1])
                elif kind == "ritem_done":
                    data = message[1]; self.r_item_button.configure(state="normal")
                    item_id = data["id"]; self.rolimons_item_url = f"https://www.rolimons.com/item/{item_id}"
                    self.r_item_link.configure(state="normal")
                    self.r_item_summary.set(f"{data['name']} ({data['acronym'] or 'no acronym'}) · ID {item_id} · RAP {data['rap']:,}" if data.get("rap") is not None else f"{data['name']} · ID {item_id} · RAP unavailable")
                    suffix = f" · Value {data['value']:,}" if data.get("value") is not None else " · Value unavailable"
                    self.r_item_summary.set(self.r_item_summary.get() + suffix + f" · logged {data['time'].replace('T', ' ')}")
                    self.draw_rolimons_chart(); self.refresh_rolimons_log()
                    self.status_var.set(f"Rolimon's item logged · {data['name']} · value {data.get('value') if data.get('value') is not None else 'unavailable'}")
                elif kind == "ritem_error":
                    self.r_item_button.configure(state="normal"); self.r_item_summary.set("Lookup failed: " + message[1])
                    self.add_diagnostic("Rolimon's item lookup failed: " + message[1])
                elif kind=="wh_test": (messagebox.showinfo if message[1] else messagebox.showerror)("Discord webhook", message[2])
                elif kind=="beep": beep(1500 if message[1] else 1000,100)
                elif kind=="notify": self.show_toast(message[1])
                elif kind=="diagnostic": self.add_diagnostic(message[1])
                elif kind=="diagnostics_done": self.diag_button.configure(state="normal")
        except queue.Empty:
            pass
        except Exception as exc:
            try: self.status_var.set(f"UI update error · {exc}"); self.add_diagnostic(f"UI update error: {exc}")
            except Exception: pass
        if self.root.winfo_exists(): self.root.after(100,self.poll)

    def on_close(self):
        self.gen_stop.set()
        self.chk_stop.set()
        self.prx_stop.set()
        self.pause_event.set()
        try:
            self.db.close()
        except sqlite3.Error:
            pass
        self.root.destroy()


def run_window():
    root = tk.Tk()
    App(root)
    root.mainloop()

if __name__ == "__main__":
    try:
        if HAVE_TK:
            run_window()
        else:
            print("tkinter not installed")
    except:
        import traceback
        traceback.print_exc()
        input("Press Enter...")
