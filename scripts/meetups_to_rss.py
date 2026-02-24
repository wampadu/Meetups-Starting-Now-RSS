import os
import re
import html
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
        except:
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
    except:
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
        if it.get("start_dt_utc"):
            pubdate = f"<pubDate>{it['start_dt_utc']}</pubDate>"

        rss_items.append(f"""<item>
  <title>{title}</title>
  <link>{link}</link>
  <guid isPermaLink="true">{link}</guid>
  {pubdate}
  <description>{desc}</description>
</item>""")

    body = "\n".join(rss_items)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>{esc(FEED_TITLE)}</title>
  <link>{esc(FEED_LINK)}</link>
  <description>{esc(FEED_DESCRIPTION)}</description>
  <lastBuildDate>{last_build}</lastBuildDate>
  <ttl>60</ttl>
{body}
</channel>
</rss>
"""


def scrape_meetup_cards():
    """
    Render the lazy page and extract event-like cards using evaluate(),
    similar to your previous scraper. Much more stable than brittle selectors.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 2200})

        page.goto(MEETUP_URL, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(4000)

        # Scroll to trigger lazy-load
        prev_height = 0
        stable = 0
        for _ in range(18):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1200)
            h = page.evaluate("document.body.scrollHeight")
            if h == prev_height:
                stable += 1
            else:
                stable = 0
                prev_height = h
            if stable >= 4:
                break

        raw = page.evaluate(
            """
            () => {
              const anchors = Array.from(document.querySelectorAll("a[href*='/events/']"));
              const out = [];
              const seen = new Set();

              function absUrl(href) {
                try { return new URL(href, location.origin).toString(); }
                catch(e) { return href || ""; }
              }

              for (const a of anchors) {
                const url = absUrl(a.getAttribute("href") || a.href || "");
                if (!url || seen.has(url)) continue;
                seen.add(url);

                // Try to find a card-like container near the link
                const card = a.closest("article") || a.closest("li") || a.closest("div");

                // Title: prefer h3 within card, else aria-label, else link text
                let title =
                  (card && card.querySelector("h3") && card.querySelector("h3").innerText) ||
                  a.getAttribute("aria-label") ||
                  a.innerText ||
                  "";

                title = (title || "").trim();
                if (!title || title.length < 3) continue;

                // Time: prefer <time>, use datetime attr too if exists
                const timeEl = card ? card.querySelector("time") : null;
                const whenText = (timeEl && timeEl.innerText ? timeEl.innerText : "").trim();
                const dtAttr = (timeEl && timeEl.getAttribute("datetime") ? timeEl.getAttribute("datetime") : "").trim();

                // Grab card text for attendee parsing
                const cardText = (card && card.innerText) ? card.innerText : (a.innerText || "");
                out.push({
                  title,
                  url,
                  whenText,
                  dtAttr,
                  cardText
                });
              }
              return out;
            }
            """
        )

        browser.close()
        return raw


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    raw = scrape_meetup_cards()

    # Convert + filter
    items = []
    for r in raw:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        when_text = (r.get("whenText") or "").strip()
        dt_attr = (r.get("dtAttr") or "").strip()
        card_text = r.get("cardText") or ""

        attendees = extract_attendees_from_text(card_text)

        start_dt = parse_start_dt(dt_attr, when_text)
        keep = is_within_next_hour(start_dt, when_text)
        if not keep:
            continue

        start_dt_utc = None
        if start_dt:
            start_dt_utc = rfc2822(start_dt.astimezone(timezone.utc))

        items.append({
            "title": title,
            "url": url,
            "when_text": when_text,
            "attendees": attendees,
            "start_dt": start_dt,
            "start_dt_utc": start_dt_utc,
        })

    # Sort by start_dt (unknown times last)
    far = datetime.max.replace(tzinfo=LOCAL_TZ)
    items.sort(key=lambda x: x["start_dt"] if x["start_dt"] else far)

    # Cap
    items = items[:MAX_ITEMS]

    rss = build_rss(items)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"Wrote {OUT_FILE} with {len(items)} items (next {WINDOW_MINUTES} minutes).")
    # Helpful debug in Actions logs:
    for it in items[:10]:
        print("DEBUG:", it["title"], "|", it["when_text"], "| attendees:", it["attendees"], "| start_dt:", it["start_dt"])


if __name__ == "__main__":
    main()
