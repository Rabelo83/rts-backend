import re
from datetime import datetime, timedelta

def time_to_secs(dt: datetime) -> int:
    return dt.hour * 3600 + dt.minute * 60 + dt.second

def parse_target_datetime(message: str, when_text: str | None, tz):
    """
    Very simple parser:
    - Supports 'tomorrow', 'today'
    - Supports '10am', '10:30am', 'around 10', 'around 10am'
    If nothing found -> now()
    """
    text = (when_text or message or "").lower()
    now = datetime.now(tz)

    day = now.date()
    if "tomorrow" in text or "mañana" in text:
        day = (now + timedelta(days=1)).date()
    elif "today" in text or "hoy" in text:
        day = now.date()

    hour = None
    minute = 0

    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if m:
        h = int(m.group(1))
        minute = int(m.group(2) or "0")
        ap = m.group(3)
        if ap == "pm" and h != 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        hour = h
    else:
        # "around 10" without am/pm → assume AM for morning times
        m2 = re.search(r"\baround\s*(\d{1,2})\b", text)
        if m2:
            hour = int(m2.group(1))
        else:
            # "10am tomorrow" might be missing "around"
            m3 = re.search(r"\b(\d{1,2})\s*(:\d{2})?\b", text)
            # too risky; only use if message includes 'am/pm' or 'tomorrow/today'
            if m3 and ("tomorrow" in text or "mañana" in text or "today" in text or "hoy" in text):
                hour = int(m3.group(1))

    if hour is None:
        return now

    return datetime(day.year, day.month, day.day, hour, minute, 0, tzinfo=tz)
