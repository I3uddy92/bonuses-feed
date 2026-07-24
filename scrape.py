#!/usr/bin/env python3
"""Scrape bonus sections from LeekDuck event pages and write bonuses.json.

Fills a gap in the ScrapedDuck feeds (events/raids/eggs/research), which do not
include the per-event "Bonuses" section rendered as HTML on leekduck.com.
"""
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

EVENTS_URL = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/events.json"
OUTPUT_PATH = Path(__file__).parent / "bonuses.json"
LOOKAHEAD_DAYS = 14
REQUEST_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# --- German translations for recurring bonus phrasings -----------------------
# Regex is matched against the trimmed English bonus text (short, abbreviated
# output by design). Anything that doesn't match a rule is kept in English
# rather than risk a bad translation.
TRANSLATIONS = [
    (re.compile(r"^(\d+)\s*[x×]\s*XP for catching Pok[ée]mon\.?$", re.I), "{0}× Fang-XP"),
    (re.compile(r"^(\d+)\s*[x×]\s*Stardust for catching Pok[ée]mon\.?$", re.I), "{0}× Fang-Sternenstaub"),
    (re.compile(r"^(\d+)\s*[x×]\s*Candy for catching Pok[ée]mon\.?$", re.I), "{0}× Fang-Bonbons"),
    (re.compile(r"^(\d+)\s*[x×]\s*Catch Candy$", re.I), "{0}× Fang-Bonbons"),
    (re.compile(r"^(\d+)\s*[x×]\s*Catch Stardust$", re.I), "{0}× Fang-Sternenstaub"),
    (re.compile(r"^(\d+)\s*[x×]\s*XP for hatching Eggs\.?$", re.I), "{0}× Schlüpf-XP"),
    (re.compile(r"^(\d+)\s*[x×]\s*Stardust for hatching Eggs\.?$", re.I), "{0}× Schlüpf-Sternenstaub"),
    (re.compile(r"^(\d+)\s*[x×]\s*Candy for hatching Eggs\.?$", re.I), "{0}× Schlüpf-Bonbons"),
    (re.compile(r"^Eggs? require 1/(\d+) the distance to hatch\.?$", re.I), "1/{0} Ei-Distanz"),
    (re.compile(r"^(\d+)\s*[x×]\s*Daily Adventure Incense duration\.?$", re.I), "{0}× Abenteuer-Rauch-Dauer"),
    (re.compile(r"^(\d+)\s*[x×]\s*(?:duration for |Incense )?Incense duration\.?$", re.I), "{0}× Rauch-Dauer"),
    (re.compile(r"^(\d+)\s*[x×]\s*Lure Module duration\.?$", re.I), "{0}× Lockmodul-Dauer"),
    (re.compile(r"^Increased chance of encountering Shiny Pok[ée]mon\.?$", re.I), "Erhöhte Shiny-Chance"),
    (re.compile(r"^Increased Buddy [Cc]andy earn rate\.?$", re.I), "Erhöhte Buddy-Bonbons"),
    (re.compile(r"^Increased XP and Stardust from hatching Eggs\.?$", re.I), "Erhöhte Schlüpf-XP/-Staub"),
    (re.compile(r"^Open up to (\d+) Gifts per day$", re.I), "Bis {0} Geschenke/Tag öffnen"),
    (re.compile(r"^Receive up to (\d+) Gifts per day from spinning Pok[ée]Stop and Gym Photo Discs$", re.I), "Bis {0} Geschenke/Tag erhalten"),
    (re.compile(r"^Hold (?:up to )?(\d+) more Gifts? in your Item Bag$", re.I), "+{0} Taschenplätze (Geschenke)"),
    (re.compile(r"^One additional Candy for trading Pok[ée]mon\.?$", re.I), "+1 Bonbon beim Tauschen"),
    (re.compile(r"^Trainers level (\d+) and above will receive one guaranteed Candy XL when trading Pok[ée]mon\.?$", re.I),
        "Garantiertes Bonbon XL ab Level {0} (Tausch)"),
    (re.compile(r"^(\d+)\s*[x×]\s*Trainer XP for the first catch of the day\.?$", re.I), "{0}× XP (erster Fang/Tag)"),
    (re.compile(r"^(\d+)\s*[x×]\s*Catch XP$", re.I), "{0}× Fang-XP"),
    (re.compile(r"^(\d+)\s*[x×]\s*XP for spinning a Pok[ée]Stop\.?$", re.I), "{0}× PokéStop-XP"),
    (re.compile(r"^1/(\d+) Egg Hatch Distance when Eggs are placed in an Incubator during the event period\.?$", re.I),
        "1/{0} Ei-Distanz (Inkubator)"),
    (re.compile(r"^Increased chance of receiving Candy XL from catching Pok[ée]mon\.?$", re.I), "Erhöhte Bonbon-XL-Chance (Fang)"),
    (re.compile(r"^Increased limits on opening, receiving from Pok[ée]Stop and Gym Photo Discs, and storing Gifts\.?$", re.I),
        "Erhöhte Geschenke-Limits"),
    (re.compile(r"^One single-use Incubator awarded for your first Pok[ée]Stop or Gym spin of the day\.?$", re.I),
        "1 Einweg-Inkubator (erster Spin/Tag)"),
]

_PREFIX_RE = re.compile(r"^GO Pass(?: (Deluxe)):\s*", re.I)


def strip_prefix(text_en, deluxe):
    """Detects a literal "GO Pass Deluxe:" prefix some pages bake into the item
    text itself (rather than a separate "Upgrade to Deluxe" paragraph) and folds
    it into the deluxe flag instead of keeping it as English filler text."""
    m = _PREFIX_RE.match(text_en.strip())
    if not m:
        return text_en.strip(), deluxe
    return text_en[m.end():].strip(), (deluxe or bool(m.group(1)))


def translate(text_en):
    stripped = text_en.strip()
    for pattern, template in TRANSLATIONS:
        m = pattern.match(stripped)
        if m:
            return template.format(*m.groups())
    return stripped


# --- gift-bonus consolidation ------------------------------------------------
# The "Major Milestone Bonuses" section on GO Pass pages lists gift-related
# perks (open/receive/hold limits) as three separate items; combine them into
# one short line per (rank, deluxe) tier instead.
_GIFT_OPEN_RE = re.compile(r"^Open up to (\d+) Gifts per day$", re.I)
_GIFT_RECEIVE_RE = re.compile(r"^Receive up to (\d+) Gifts per day from spinning Pok[ée]Stop and Gym Photo Discs$", re.I)
_GIFT_HOLD_RE = re.compile(r"^Hold (?:up to )?(\d+) more Gifts? in your Item Bag$", re.I)


def combine_gift_bonuses(bonuses):
    groups = defaultdict(list)
    for i, b in enumerate(bonuses):
        groups[(b["rank"], b["deluxe"])].append(i)

    remove = set()
    additions = []
    for idxs in groups.values():
        found = {}
        for i in idxs:
            text, _ = strip_prefix(bonuses[i]["text_en"], False)
            if _GIFT_OPEN_RE.match(text):
                found["open"] = (i, _GIFT_OPEN_RE.match(text).group(1))
            elif _GIFT_HOLD_RE.match(text):
                found["hold"] = (i, _GIFT_HOLD_RE.match(text).group(1))
            elif _GIFT_RECEIVE_RE.match(text):
                found["receive"] = (i, None)
        if len(found) >= 2:
            first_idx = min(v[0] for v in found.values())
            parts = []
            if "open" in found:
                parts.append(f"{found['open'][1]}/Tag öffnen")
            if "hold" in found:
                parts.append(f"+{found['hold'][1]} Taschenplätze")
            additions.append({**bonuses[first_idx], "text_en": "Geschenke: " + ", ".join(parts)})
            remove.update(v[0] for v in found.values())

    if not remove:
        return bonuses
    kept = [b for i, b in enumerate(bonuses) if i not in remove]
    kept.extend(additions)
    return kept


# --- HTTP / feed helpers -----------------------------------------------------

def fetch_json(url):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_html(url):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


def parse_iso(s):
    return datetime.fromisoformat(s.rstrip("Z"))


def relevant_events(events):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    horizon = now + timedelta(days=LOOKAHEAD_DAYS)
    out = []
    for e in events:
        try:
            start = parse_iso(e["start"])
            end = parse_iso(e["end"])
        except (KeyError, ValueError):
            continue
        if end > now and start <= horizon:
            out.append(e)
    return out


# --- date-range parsing ("July 21 at 10:00 a.m. – July 23 at 10:00 a.m. local time") ---

_MONTHDAY_RE = (
    r"([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?\s+at\s+"
    r"([\d:.]+\s*(?:a\.?m\.?|p\.?m\.?|noon|midnight))"
)
_RANGE_RE = re.compile(
    _MONTHDAY_RE + r"\s*[–—-]\s*" + _MONTHDAY_RE + r"\s+local time\.?$",
    re.I,
)


def _parse_clock(mon, day, year, timestr):
    timestr = timestr.strip().lower()
    if timestr == "noon":
        timestr = "12:00 pm"
    elif timestr == "midnight":
        timestr = "12:00 am"
    timestr = timestr.replace(".", "").upper()
    dt_str = f"{mon} {day} {year} {timestr}"
    for fmt in ("%B %d %Y %I:%M %p", "%b %d %Y %I:%M %p"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised date/time: {dt_str!r}")


def try_parse_date_range(text, default_year):
    text = text.replace("\xa0", " ").strip()
    m = _RANGE_RE.match(text)
    if not m:
        return None
    mon1, day1, yr1, time1, mon2, day2, yr2, time2 = m.groups()
    y1 = int(yr1) if yr1 else default_year
    y2 = int(yr2) if yr2 else default_year
    start = _parse_clock(mon1, day1, y1, time1)
    end = _parse_clock(mon2, day2, y2, time2)
    if end < start:
        end = end.replace(year=end.year + 1)
    return start, end


# --- HTML section parsing ----------------------------------------------------

def _iter_section(h2):
    """Yield direct children of h2's parent, starting after h2, until the next h2."""
    parent = h2.parent
    started = False
    for child in parent.children:
        if getattr(child, "name", None) is None:
            continue
        if child is h2:
            started = True
            continue
        if not started:
            continue
        if child.name == "h2":
            break
        yield child


def _item_texts_and_images(bonus_list_div, page_url):
    items = bonus_list_div.find_all("div", class_="bonus-item") or [bonus_list_div]
    out = []
    for it in items:
        text_div = it.find("div", class_="bonus-text")
        text = (text_div or it).get_text(" ", strip=True)
        if not text:
            continue
        img = it.find("img")
        image = urljoin(page_url, img["src"]) if img and img.get("src") else None
        out.append((text, image))
    return out


def parse_standard_bonuses(soup, event):
    h2 = soup.find("h2", id="bonuses")
    if h2 is None:
        h2 = soup.find(lambda t: t.name == "h2" and t.get_text(strip=True).lower() == "bonuses")
    if h2 is None:
        return []

    default_year = parse_iso(event["start"]).year
    event_start = parse_iso(event["start"])
    event_end = parse_iso(event["end"])

    results = []
    pending_range = None
    for child in _iter_section(h2):
        if child.name == "p":
            rng = try_parse_date_range(child.get_text(" ", strip=True), default_year)
            if rng:
                pending_range = rng
            continue
        if child.name == "div" and "bonus-list" in (child.get("class") or []):
            start, end = pending_range if pending_range else (event_start, event_end)
            for text, image in _item_texts_and_images(child, event["link"]):
                results.append({
                    "text_en": text,
                    "image": image,
                    "start": start,
                    "end": end,
                    "rank": None,
                    "deluxe": False,
                })
            pending_range = None
    return results


def parse_milestone_bonuses(soup, event):
    h2 = soup.find("h2", id="major-milestone-bonuses")
    if h2 is None:
        h2 = soup.find(lambda t: t.name == "h2" and "major milestone" in t.get_text(strip=True).lower())
    if h2 is None:
        return []

    event_start = parse_iso(event["start"])
    event_end = parse_iso(event["end"])

    results = []
    current_rank = None
    deluxe_mode = False
    for child in _iter_section(h2):
        if child.name == "h3":
            m = re.search(r"Rank\s+(\d+)", child.get_text(" ", strip=True), re.I)
            if m:
                current_rank = m.group(1)
            continue
        if child.name == "p":
            if "deluxe" in child.get_text(" ", strip=True).lower():
                deluxe_mode = True
            continue
        if child.name == "div" and "bonus-list" in (child.get("class") or []):
            for text, image in _item_texts_and_images(child, event["link"]):
                results.append({
                    "text_en": text,
                    "image": image,
                    "start": event_start,
                    "end": event_end,
                    "rank": current_rank,
                    "deluxe": deluxe_mode,
                })
    return results


def mark_rotating(bonuses):
    groups = defaultdict(list)
    for b in bonuses:
        key = b["image"] or re.sub(r"\d+", "#", b["text_en"])
        groups[key].append(b)
    for group in groups.values():
        windows = {(b["start"], b["end"]) for b in group}
        rotating = len(group) > 1 and len(windows) > 1
        for b in group:
            b["rotating"] = rotating
    return bonuses


# --- per-event scraping -------------------------------------------------------

def scrape_event(event):
    html = fetch_html(event["link"])
    soup = BeautifulSoup(html, "html.parser")

    bonuses = parse_standard_bonuses(soup, event) + parse_milestone_bonuses(soup, event)
    if not bonuses:
        return []
    bonuses = combine_gift_bonuses(bonuses)
    mark_rotating(bonuses)

    slug = event["link"].rstrip("/").rsplit("/", 1)[-1]
    out = []
    for b in bonuses:
        base_text, deluxe = strip_prefix(b["text_en"], b["deluxe"])
        text = translate(base_text).rstrip("*").strip()
        suffix_parts = []
        if b["rank"]:
            suffix_parts.append(f"ab Rang {b['rank']}")
        if deluxe:
            suffix_parts.append("Deluxe")
        if suffix_parts:
            text += " (" + ", ".join(suffix_parts) + ")"
        out.append({
            "text": text,
            "image": b["image"],
            "eventName": event["name"],
            "eventSlug": slug,
            "start": b["start"].strftime("%Y-%m-%dT%H:%M:%S"),
            "end": b["end"].strftime("%Y-%m-%dT%H:%M:%S"),
            "rotating": b["rotating"],
        })
    return out


def main():
    events = fetch_json(EVENTS_URL)
    candidates = relevant_events(events)
    print(f"{len(candidates)} relevant event(s) out of {len(events)}", file=sys.stderr)

    all_bonuses = []
    for event in candidates:
        try:
            bonuses = scrape_event(event)
        except Exception as exc:  # noqa: BLE001 - one bad event must not abort the run
            print(f"WARN: skipping {event.get('name')!r} ({event.get('link')}): {exc}", file=sys.stderr)
            continue
        if bonuses:
            print(f"  {event['name']}: {len(bonuses)} bonus(es)", file=sys.stderr)
        all_bonuses.extend(bonuses)

    if not all_bonuses:
        print("No bonuses scraped this run; leaving existing bonuses.json untouched.", file=sys.stderr)
        return

    OUTPUT_PATH.write_text(
        json.dumps(all_bonuses, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(all_bonuses)} bonus(es) to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
