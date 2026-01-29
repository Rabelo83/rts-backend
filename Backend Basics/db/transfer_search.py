import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "rts_gtfs.sqlite"


def search_fastest_one_transfer(
    date, time, from_stop_id_padded, to_stop_id_padded, limit=3
):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT stop_id, stop_name FROM stops WHERE stop_id_padded = ?;",
        (from_stop_id_padded,),
    )
    from_row = cur.fetchone()
    if not from_row:
        raise SystemExit("From stop not found")
    from_stop_id, from_name = from_row["stop_id"], from_row["stop_name"]

    cur.execute(
        "SELECT stop_id, stop_name FROM stops WHERE stop_id_padded = ?;",
        (to_stop_id_padded,),
    )
    to_row = cur.fetchone()
    if not to_row:
        raise SystemExit("To stop not found")
    to_stop_id, to_name = to_row["stop_id"], to_row["stop_name"]

    active_services_cte = """
    WITH base_services AS (
      SELECT c.service_id
      FROM calendar c
      WHERE :date BETWEEN c.start_date AND c.end_date
        AND (
          (c.monday = 1 AND strftime('%w', :date) = '1') OR
          (c.tuesday = 1 AND strftime('%w', :date) = '2') OR
          (c.wednesday = 1 AND strftime('%w', :date) = '3') OR
          (c.thursday = 1 AND strftime('%w', :date) = '4') OR
          (c.friday = 1 AND strftime('%w', :date) = '5') OR
          (c.saturday = 1 AND strftime('%w', :date) = '6') OR
          (c.sunday = 1 AND strftime('%w', :date) = '0')
        )
    ),
    exception_add AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date AND exception_type = 1
    ),
    exception_remove AS (
      SELECT service_id
      FROM calendar_dates
      WHERE date = :date AND exception_type = 2
    ),
    active_services AS (
      SELECT service_id FROM base_services
      UNION
      SELECT service_id FROM exception_add
      EXCEPT
      SELECT service_id FROM exception_remove
    )
    """

    sql_transfer_candidates = active_services_cte + """
    SELECT DISTINCT st_from.stop_id
    FROM stop_times st_from
    JOIN stop_times st_to ON st_to.trip_id = st_from.trip_id
    JOIN trips t ON t.trip_id = st_from.trip_id
    JOIN active_services a ON a.service_id = t.service_id
    WHERE st_to.stop_id = :to_stop_id
      AND st_from.stop_sequence < st_to.stop_sequence;
    """

    transfer_stop_ids = set(
        r[0]
        for r in conn.execute(
            sql_transfer_candidates, {"date": date, "to_stop_id": to_stop_id}
        ).fetchall()
    )

    sql_first_leg = active_services_cte + """
    SELECT st.trip_id, st.departure_time, st.stop_sequence,
           r.route_short_name, t.trip_headsign
    FROM stop_times st
    JOIN trips t ON t.trip_id = st.trip_id
    JOIN routes r ON r.route_id = t.route_id
    JOIN active_services a ON a.service_id = t.service_id
    WHERE st.stop_id = :from_stop_id
      AND st.departure_time >= :time
    ORDER BY st.departure_time
    LIMIT 120;
    """

    first_legs = conn.execute(
        sql_first_leg,
        {"date": date, "from_stop_id": from_stop_id, "time": time},
    ).fetchall()

    placeholders = ",".join("?" for _ in transfer_stop_ids) or "''"
    stop_name_map = {}
    if transfer_stop_ids:
        for r in conn.execute(
            f"SELECT stop_id, stop_name FROM stops WHERE stop_id IN ({placeholders});",
            list(transfer_stop_ids),
        ).fetchall():
            stop_name_map[r[0]] = r[1]

    sql_second_leg = active_services_cte + """
    SELECT st_from.departure_time AS depart_time,
           st_to.arrival_time AS arrive_time,
           t.trip_id, r.route_short_name, t.trip_headsign
    FROM stop_times st_from
    JOIN stop_times st_to ON st_to.trip_id = st_from.trip_id
    JOIN trips t ON t.trip_id = st_from.trip_id
    JOIN routes r ON r.route_id = t.route_id
    JOIN active_services a ON a.service_id = t.service_id
    WHERE st_from.stop_id = :transfer_stop_id
      AND st_to.stop_id = :to_stop_id
      AND st_from.stop_sequence < st_to.stop_sequence
      AND st_from.departure_time >= :min_depart
    ORDER BY st_to.arrival_time
    LIMIT 1;
    """

    itineraries = []
    for leg in first_legs:
        trip_id = leg["trip_id"]
        depart_time = leg["departure_time"]
        seq = leg["stop_sequence"]
        downstream = conn.execute(
            """
            SELECT stop_id, arrival_time, stop_sequence
            FROM stop_times
            WHERE trip_id = ? AND stop_sequence > ?
            ORDER BY stop_sequence;
            """,
            (trip_id, seq),
        ).fetchall()

        for ds in downstream:
            transfer_stop_id = ds["stop_id"]
            if transfer_stop_id not in transfer_stop_ids:
                continue
            arrive_time = ds["arrival_time"]
            row = conn.execute(
                sql_second_leg,
                {
                    "date": date,
                    "transfer_stop_id": transfer_stop_id,
                    "to_stop_id": to_stop_id,
                    "min_depart": arrive_time,
                },
            ).fetchone()
            if not row:
                continue
            itineraries.append(
                {
                    "first_route": leg["route_short_name"],
                    "first_headsign": leg["trip_headsign"],
                    "first_depart": depart_time,
                    "transfer_stop_id": transfer_stop_id,
                    "transfer_stop_name": stop_name_map.get(
                        transfer_stop_id, transfer_stop_id
                    ),
                    "transfer_arrive": arrive_time,
                    "second_route": row["route_short_name"],
                    "second_headsign": row["trip_headsign"],
                    "second_depart": row["depart_time"],
                    "final_arrive": row["arrive_time"],
                }
            )

    conn.close()

    seen = set()
    unique = []
    for it in sorted(itineraries, key=lambda x: x["final_arrive"]):
        key = (it["first_depart"], it["transfer_stop_id"], it["second_depart"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    return {
        "from_name": from_name,
        "to_name": to_name,
        "options": unique[:limit],
    }


def main():
    # Example usage
    result = search_fastest_one_transfer(
        date="2026-01-31",
        time="14:50:00",
        from_stop_id_padded="0473",
        to_stop_id_padded="1492",
        limit=3,
    )
    print(f"From: {result['from_name']} -> {result['to_name']}")
    for i, it in enumerate(result["options"], 1):
        print(f"Option {i}: {it}")


if __name__ == "__main__":
    main()
