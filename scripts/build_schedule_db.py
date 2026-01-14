import os
import re
import json
import sqlite3
from datetime import datetime
from collections import defaultdict

import pdfplumber


PDF_DEFAULT = "data/2026-Spring-RTS_Schedule.pdf"
DB_DEFAULT  = "data/schedule.db"
SCHEMA_PATH = "db/schema.sql"
META_PATH   = "data/schedule_meta.json"

TIME_TOKEN_RE = re.compile(r"^\d{1,2}(:\d{2})?$")  # "6:30" or "11"


# ---------- small helpers ----------

def norm_stop_id(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown_stop"


def parse_route_id(page_text: str) -> str | None:
    # supports both "52\nROUTE" and "\n52\nROUTE"
    m = re.search(r"^(\d{1,3})\s*\nROUTE\b", page_text, flags=re.M)
    if m:
        return m.group(1)
    m = re.search(r"\n(\d{1,3})\nROUTE\b", page_text, flags=re.M)
    if m:
        return m.group(1)
    m = re.search(r"\bROUTE\b\s*\n(\d{1,3})", page_text, flags=re.M)
    if m:
        return m.group(1)
    return None


def service_id_from_heading(heading: str) -> str:
    h = heading.upper().strip()
    if "MONDAY" in h and "FRIDAY" in h:
        return "mon_fri"
    if "MONDAY" in h and "THURSDAY" in h:
        return "mon_thu"
    if "SATURDAY" in h:
        return "sat"
    if "SUNDAY" in h:
        return "sun"
    return "unknown"


def calendar_flags(service_id: str):
    # mon,tue,wed,thu,fri,sat,sun
    if service_id == "mon_fri":
        return (1, 1, 1, 1, 1, 0, 0)
    if service_id == "mon_thu":
        return (1, 1, 1, 1, 0, 0, 0)
    if service_id == "sat":
        return (0, 0, 0, 0, 0, 1, 0)
    if service_id == "sun":
        return (0, 0, 0, 0, 0, 0, 1)
    return (0, 0, 0, 0, 0, 0, 0)


def time_to_24h_seconds(time_str: str, noon_seen: bool) -> tuple[int, str, bool]:
    """
    Convert schedule token like "6:30" or "11" into:
      (seconds since midnight, "HH:MM:SS", updated_noon_seen)

    Robust parsing:
      - Accepts "H", "H:MM", or even "H:MM:SS" (we ignore seconds).
      - If pdf parsing accidentally includes extra ":" pieces, we keep only the first two.
    """
    raw = str(time_str).strip()

    # Keep only digits and colons (defensive)
    raw = re.sub(r"[^0-9:]", "", raw)

    if not raw:
        raise ValueError(f"Empty/invalid time token: {time_str!r}")

    if ":" in raw:
        parts = raw.split(":")
        # Keep first two pieces only (H and MM)
        h = int(parts[0]) if parts[0] else 0
        m = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    else:
        h = int(raw)
        m = 0

    # Normalize minutes if something weird sneaks in
    if m >= 60:
        m = m % 60

    if h == 12:
        hour24 = 12
        noon_seen = True
    else:
        if noon_seen and 1 <= h <= 11:
            hour24 = h + 12
        else:
            hour24 = h

    secs = hour24 * 3600 + m * 60
    hhmmss = f"{hour24:02d}:{m:02d}:00"
    return secs, hhmmss, noon_seen


def group_upright_lines(words, y_tol=2.0):
    """
    Group words into lines by y (top). Returns list of (top, text, words_in_line).
    """
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        placed = False
        for line in lines:
            if abs(line["top"] - w["top"]) <= y_tol:
                line["words"].append(w)
                # keep representative top (simple average)
                line["top"] = sum(x["top"] for x in line["words"]) / len(line["words"])
                placed = True
                break
        if not placed:
            lines.append({"top": w["top"], "words": [w]})

    out = []
    for line in lines:
        ws = sorted(line["words"], key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in ws)
        out.append((line["top"], text, ws))
    out.sort(key=lambda x: x[0])
    return out


def find_service_blocks(lines):
    """
    Find day headings (MONDAY..., SATURDAY, SUNDAY).
    Returns list of blocks: [{top, heading, bottom_guess}]
    """
    candidates = []
    for top, text, ws in lines:
        t = text.upper()
        if "MONDAY" in t or "SATURDAY" in t or "SUNDAY" in t:
            heading = re.sub(r"\s+", " ", t).strip()
            candidates.append((top, heading))

    blocks = []
    for top, heading in candidates:
        if not blocks:
            blocks.append([top, heading])
            continue
        prev_top, prev_heading = blocks[-1]
        if heading == prev_heading and abs(top - prev_top) < 5:
            continue
        blocks.append([top, heading])

    out = []
    for i, (top, heading) in enumerate(blocks):
        bottom = blocks[i + 1][0] - 5 if i + 1 < len(blocks) else 10_000
        out.append({"top": top, "heading": heading, "bottom": bottom})
    return out


def find_letter_row(words, y_min, y_max):
    letters = [
        w for w in words
        if w.get("upright")
        and w["text"] in list("ABCDEFGH")
        and y_min <= w["top"] <= y_max
    ]
    if not letters:
        return None

    buckets = defaultdict(list)
    for w in letters:
        buckets[round(w["top"])].append(w)

    best_top, best_words = max(buckets.items(), key=lambda kv: len(kv[1]))
    best_words = sorted(best_words, key=lambda w: w["x0"])
    return float(best_top), best_words


def assign_to_columns(words, centers, y_min, y_max, text_filter=None):
    selected = [
        w for w in words
        if w.get("upright") and y_min <= w["top"] <= y_max
    ]
    if text_filter:
        selected = [w for w in selected if text_filter(w["text"])]

    cols = defaultdict(list)
    for w in selected:
        cx = (w["x0"] + w["x1"]) / 2
        idx = min(range(len(centers)), key=lambda i: abs(centers[i] - cx))
        cols[idx].append(w)

    out = []
    for i in range(len(centers)):
        ws = sorted(cols.get(i, []), key=lambda w: w["x0"])
        out.append(" ".join(w["text"] for w in ws).strip())
    return out


# ---------- main build ----------

def main():
    os.makedirs("data", exist_ok=True)
    os.makedirs("scripts", exist_ok=True)

    pdf_path = os.environ.get("RTS_SCHEDULE_PDF", PDF_DEFAULT)
    db_path  = os.environ.get("RTS_SCHEDULE_DB", DB_DEFAULT)

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found at {pdf_path}. Upload it to your repo.")

    meta = {
        "start_date": "2026-01-05",
        "end_date": "2026-05-03",
        "exceptions_removed": ["2026-01-19"],  # MLK Day (no service)
    }
    if os.path.exists(META_PATH):
        with open(META_PATH, "r", encoding="utf-8") as f:
            meta.update(json.load(f))

    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())

    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                 ("generated_at", datetime.utcnow().isoformat() + "Z"))
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                 ("pdf_path", pdf_path))
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                 ("start_date", meta["start_date"]))
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                 ("end_date", meta["end_date"]))

    start_date = meta["start_date"]
    end_date   = meta["end_date"]

    seen_service_ids = set()

    def ensure_calendar(service_id: str):
        if service_id in seen_service_ids:
            return
        mon, tue, wed, thu, fri, sat, sun = calendar_flags(service_id)
        conn.execute(
            """INSERT OR IGNORE INTO calendar
               (service_id, start_date, end_date, mon, tue, wed, thu, fri, sat, sun)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (service_id, start_date, end_date, mon, tue, wed, thu, fri, sat, sun)
        )
        seen_service_ids.add(service_id)

    removed_dates = meta.get("exceptions_removed", [])

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if "Complete Route" not in text:
                continue

            route_id = parse_route_id(text)
            if not route_id:
                continue

            conn.execute(
                "INSERT OR IGNORE INTO routes(route_id, route_name) VALUES (?,?)",
                (route_id, None)
            )

            words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
            upright = [w for w in words if w.get("upright")]

            lines = group_upright_lines(upright)
            blocks = find_service_blocks(lines)

            if not blocks:
                blocks = [{"top": 0, "heading": "UNKNOWN", "bottom": 10_000}]

            trip_counter = 0

            for block in blocks:
                service_id = service_id_from_heading(block["heading"])
                ensure_calendar(service_id)

                letter_row = find_letter_row(upright, block["top"], block["bottom"])
                if not letter_row:
                    continue

                letter_top, letter_words = letter_row
                centers = [(w["x0"] + w["x1"]) / 2 for w in letter_words]
                col_count = len(centers)
                if col_count < 2:
                    continue

                stop_names = assign_to_columns(
                    upright, centers,
                    y_min=letter_top + 6, y_max=letter_top + 28
                )

                if sum(1 for s in stop_names if s) < 2:
                    continue

                half = col_count // 2
                if half * 2 != col_count:
                    continue

                stops_dir0 = stop_names[:half]
                stops_dir1 = stop_names[half:]

                for s in stop_names:
                    if not s:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO stops(stop_id, stop_name) VALUES(?,?)",
                        (norm_stop_id(s), s)
                    )

                time_words = [
                    w for w in upright
                    if block["top"] <= w["top"] <= block["bottom"]
                    and TIME_TOKEN_RE.match(w["text"])
                    and w["top"] > letter_top + 20
                ]

                rows = defaultdict(list)
                for w in time_words:
                    rows[round(w["top"])].append(w)

                noon_seen_dir0 = False
                noon_seen_dir1 = False

                for row_top in sorted(rows.keys()):
                    col_times = assign_to_columns(
                        rows[row_top], centers,
                        y_min=row_top - 1, y_max=row_top + 1,
                        text_filter=lambda t: bool(TIME_TOKEN_RE.match(t))
                    )

                    if sum(1 for t in col_times if t) < col_count:
                        continue

                    times_dir0 = col_times[:half]
                    times_dir1 = col_times[half:]

                    if any(not t for t in times_dir0) or any(not t for t in times_dir1):
                        continue

                    trip_counter += 1
                    trip_id0 = f"{route_id}_{service_id}_dir0_{trip_counter:03d}"
                    trip_id1 = f"{route_id}_{service_id}_dir1_{trip_counter:03d}"

                    conn.execute(
                        "INSERT OR IGNORE INTO trips(trip_id, route_id, service_id, direction_id, headsign) VALUES (?,?,?,?,?)",
                        (trip_id0, route_id, service_id, 0, None)
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO trips(trip_id, route_id, service_id, direction_id, headsign) VALUES (?,?,?,?,?)",
                        (trip_id1, route_id, service_id, 1, None)
                    )

                    for seq, (stop_name, t) in enumerate(zip(stops_dir0, times_dir0), start=1):
                        stop_id = norm_stop_id(stop_name)
                        secs, hhmmss, noon_seen_dir0 = time_to_24h_seconds(t, noon_seen_dir0)
                        conn.execute(
                            """INSERT OR REPLACE INTO stop_times
                               (trip_id, route_id, stop_id, stop_sequence, arrival_time, departure_time, arrival_secs, departure_secs)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (trip_id0, route_id, stop_id, seq, hhmmss, hhmmss, secs, secs)
                        )

                    for seq, (stop_name, t) in enumerate(zip(stops_dir1, times_dir1), start=1):
                        stop_id = norm_stop_id(stop_name)
                        secs, hhmmss, noon_seen_dir1 = time_to_24h_seconds(t, noon_seen_dir1)
                        conn.execute(
                            """INSERT OR REPLACE INTO stop_times
                               (trip_id, route_id, stop_id, stop_sequence, arrival_time, departure_time, arrival_secs, departure_secs)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (trip_id1, route_id, stop_id, seq, hhmmss, hhmmss, secs, secs)
                        )

    for service_id in list(seen_service_ids):
        for d in removed_dates:
            conn.execute(
                "INSERT OR REPLACE INTO calendar_dates(service_id, date, exception_type) VALUES (?,?,?)",
                (service_id, d, 2)
            )

    conn.execute("DELETE FROM stop_last_departure")
    conn.execute(
        """
        INSERT INTO stop_last_departure(route_id, service_id, stop_id, last_departure_time, last_departure_secs)
        SELECT st.route_id, t.service_id, st.stop_id,
               MAX(st.departure_time) as last_departure_time,
               MAX(st.departure_secs) as last_departure_secs
        FROM stop_times st
        JOIN trips t ON t.trip_id = st.trip_id
        GROUP BY st.route_id, t.service_id, st.stop_id
        """
    )

    conn.commit()
    conn.close()
    print(f"✅ Built {db_path} from {pdf_path}")


if __name__ == "__main__":
    main()
