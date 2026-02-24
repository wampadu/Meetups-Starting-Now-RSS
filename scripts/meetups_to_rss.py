import os
import re
import html
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser
from playwright.sync_api import sync_playwright


MEETUP_URL = "https://www.meetup.com/find/?dateRange=startingSoon&source=EVENTS&eventType=online"

OUT_DIR = "public"
OUT_FILE = os.path.join(OUT_DIR, "feed.xml")

LOCAL_TZ = ZoneInfo("America/Toronto")
WINDOW_MINUTES = 60
MAX_ITEMS = 50

FEED_TITLE = "Meetup (Online) — Starting Soon (Next Hour)"
FEED_LINK = MEETUP_URL
FEED_DESCRIPTION = "Auto-generated RSS for Meetup online events starting within the next hour."


def now_local() -> datetime:
    return datetime.now(tz=LOCAL_TZ)


def rfc2822(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")


def esc(s: str) -> str:
    return html.escape((s or "").strip())


def extract_attendees_from_text(text: str):
    """
    Best-effort attendee extraction from a card's text content.
    Examples we may see:
      - "12 attendees"
      - "12 going"
      - "12 RSVPs"
      - "Attendees 12"
    """
    if not text:
        return None
    t = " ".join(text.split())
    m = re.search(r"\b(\d{1,6})\s*(attendees|going|rsvps|people|attending)\b", t, re.I)
    if m:
        try:
            return int(m.group(1))
        except:
            return None
    m2 = re.search(r"\battendees?\s*[:\-]?\s*(\d{1,6})\b", t, re.I)
    if m2:
        try:
            return int(m2.group(1))
        except:
            return None
    return None


def parse_start_dt(dt_attr: str, when_text: str):
    """
    Try to produce a timezone-aware local datetime.

    Priority:
      1) <time datetime="..."> attribute (usually ISO)
      2) text parsing (Today/Tomorrow/clock formats)
      3) relative text "in 30 minutes" / "in 1 hour"
    """
    base = now_local()

    # 1) datetime attribute
    if dt_attr:
        try:
            dt = dateparser.parse(dt_attr)
            if dt:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=LOCAL_TZ)
                else:
                    dt = dt.astimezone(LOCAL_TZ)
                return dt
        except Exception as e:
            print(f"DEBUG: Failed to parse dt_attr '{dt_attr}': {e}")
            pass

    t = (when_text or "").strip()
    if not t:
        return None
    t_clean = re.sub(r"\s+", " ", t)

    # 3) relative
    rel = re.search(r"\bin\s+(\d{1,3})\s*(minute|minutes|hour|hours)\b", t_clean, re.I)
    if rel:
        n = int(rel.group(1))
        unit = rel.group(2).lower()
        if "hour" in unit:
            return base + timedelta(hours=n)
        return base + timedelta(minutes=n)

    # 2) Today/Tomorrow substitution
    if re.search(r"\btoday\b", t_clean, re.I):
        t_clean = re.sub(r"\btoday\b", base.strftime("%Y-%m-%d"), t_clean, flags=re.I)
    elif re.search(r"\btomorrow\b", t_clean, re.I):
        t_clean = re.sub(r"\btomorrow\b", (base + timedelta(days=1)).strftime("%Y-%m-%d"), t_clean, flags=re.I)

    # parse
    try:
        dt = dateparser.parse(t_clean)
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        else:
            dt = dt.astimezone(LOCAL_TZ)
        return dt
    except Exception as e:
        print(f"DEBUG: Failed to parse when_text '{when_text}' as '{t_clean}': {e}")
        return None


def is_within_next_hour(start_dt: datetime | None, when_text: str) -> bool:
    """
    True if start_dt is within the next WINDOW_MINUTES.
    If start_dt is None, allow obvious "starting soon"/relative text as fallback.
    """
    if start_dt:
        start = now_local()
        end = start + timedelta(minutes=WINDOW_MINUTES)
        return start <= start_dt <= end

    # fallback if parsing failed
    t = (when_text or "").lower()
    if "starting soon" in t:
        return True
    if re.search(r"\bin\s+\d+\s+minutes?\b", t):
        return True
    if re.search(r"\bin\s+1\s+hour\b", t):
        return True
    return False


def build_rss(items):
    last_build = rfc2822(datetime.now(timezone.utc))

    rss_items = []
    for it in items:
        title = esc(it.get("title", ""))
        link = esc(it.get("url", ""))
        when_text = esc(it.get("when_text", ""))
        attendees = it.get("attendees")

        desc_parts = []
        if when_text:
            desc_parts.append(f"<p><b>Time:</b> {when_text}</p>")
        if attendees is not None:
            desc_parts.append(f"<p><b>Attendees:</b> {attendees}</p>")
        desc_parts.append(f"<p><a href=\"{link}\">Open event</a></p>")

        desc = "<![CDATA[" + "".join(desc_parts) + "]]>"

        pubdate = ""
        if it.get("start_dt_

