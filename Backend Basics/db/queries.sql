-- Query templates for LLM schedule Q&A

-- 1) Stop lookup by name (fuzzy)
-- :stop_name_like -> "%Rosa Parks%"
SELECT stop_id, stop_name, stop_id_padded
FROM stops
WHERE stop_name LIKE :stop_name_like
ORDER BY stop_name;

-- 2) Routes serving a stop (by stop_id_padded)
-- :stop_id_padded -> "0027"
SELECT DISTINCT r.route_id, r.route_short_name, r.route_long_name
FROM routes r
JOIN trips t ON t.route_id = r.route_id
JOIN stop_times st ON st.trip_id = t.trip_id
JOIN stops s ON s.stop_id = st.stop_id
WHERE s.stop_id_padded = :stop_id_padded
ORDER BY r.route_short_name;

-- 3) Departures for a route + stop on a given date
-- :route_short_name -> "5"
-- :stop_name_like -> "%Rosa Parks%"
-- :date -> "2026-01-28"
SELECT st.departure_time, t.trip_id, t.trip_headsign
FROM stops s
JOIN stop_times st ON st.stop_id = s.stop_id
JOIN trips t ON t.trip_id = st.trip_id
JOIN routes r ON r.route_id = t.route_id
JOIN calendar c ON c.service_id = t.service_id
WHERE r.route_short_name = :route_short_name
  AND s.stop_name LIKE :stop_name_like
  AND :date BETWEEN c.start_date AND c.end_date
  AND (
    (c.monday = 1 AND strftime('%w', :date) = '1') OR
    (c.tuesday = 1 AND strftime('%w', :date) = '2') OR
    (c.wednesday = 1 AND strftime('%w', :date) = '3') OR
    (c.thursday = 1 AND strftime('%w', :date) = '4') OR
    (c.friday = 1 AND strftime('%w', :date) = '5') OR
    (c.saturday = 1 AND strftime('%w', :date) = '6') OR
    (c.sunday = 1 AND strftime('%w', :date) = '0')
  )
ORDER BY st.departure_time;

-- 4) Departures for a route + stop on a given date (with exceptions)
-- This applies calendar_dates overrides (1=add, 2=remove).
-- :route_short_name -> "5"
-- :stop_name_like -> "%Rosa Parks%"
-- :date -> "2026-01-28"
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
SELECT st.departure_time, t.trip_id, t.trip_headsign
FROM stops s
JOIN stop_times st ON st.stop_id = s.stop_id
JOIN trips t ON t.trip_id = st.trip_id
JOIN routes r ON r.route_id = t.route_id
JOIN active_services a ON a.service_id = t.service_id
WHERE r.route_short_name = :route_short_name
  AND s.stop_name LIKE :stop_name_like
ORDER BY st.departure_time;

-- 5) Next departures after a given time (with exceptions)
-- :route_short_name -> "5"
-- :stop_name_like -> "%Rosa Parks%"
-- :date -> "2026-01-28"
-- :time -> "14:30:00"
-- :limit -> 10
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
SELECT st.departure_time, t.trip_id, t.trip_headsign
FROM stops s
JOIN stop_times st ON st.stop_id = s.stop_id
JOIN trips t ON t.trip_id = st.trip_id
JOIN routes r ON r.route_id = t.route_id
JOIN active_services a ON a.service_id = t.service_id
WHERE r.route_short_name = :route_short_name
  AND s.stop_name LIKE :stop_name_like
  AND st.departure_time >= :time
ORDER BY st.departure_time
LIMIT :limit;

-- 6) Next departures from a stop (any route) after a given time (with exceptions)
-- :stop_name_like -> "%Rosa Parks%"
-- :date -> "2026-01-28"
-- :time -> "14:30:00"
-- :limit -> 10
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
SELECT st.departure_time, r.route_short_name, t.trip_id, t.trip_headsign
FROM stops s
JOIN stop_times st ON st.stop_id = s.stop_id
JOIN trips t ON t.trip_id = st.trip_id
JOIN routes r ON r.route_id = t.route_id
JOIN active_services a ON a.service_id = t.service_id
WHERE s.stop_name LIKE :stop_name_like
  AND st.departure_time >= :time
ORDER BY st.departure_time
LIMIT :limit;

-- 7) First and last departures for a route + stop on a given date (with exceptions)
-- :route_short_name -> "5"
-- :stop_name_like -> "%Rosa Parks%"
-- :date -> "2026-01-28"
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
),
departures AS (
  SELECT st.departure_time
  FROM stops s
  JOIN stop_times st ON st.stop_id = s.stop_id
  JOIN trips t ON t.trip_id = st.trip_id
  JOIN routes r ON r.route_id = t.route_id
  JOIN active_services a ON a.service_id = t.service_id
  WHERE r.route_short_name = :route_short_name
    AND s.stop_name LIKE :stop_name_like
)
SELECT
  MIN(departure_time) AS first_departure,
  MAX(departure_time) AS last_departure
FROM departures;

-- 8) Resolve a fuzzy name to an entity (stops/routes/headsigns)
-- :normalized_like -> "%rosa parks%"
SELECT entity_type, entity_id, display_name
FROM fuzzy_lookup
WHERE normalized LIKE :normalized_like
ORDER BY entity_type, display_name
LIMIT 25;

-- 9) Route-scoped stop lookup by name (only stops served by a route)
-- :route_short_name -> "12"
-- :stop_name_like -> "%Dollar General%"
SELECT DISTINCT s.stop_id, s.stop_id_padded, s.stop_name
FROM stops s
JOIN stop_times st ON st.stop_id = s.stop_id
JOIN trips t ON t.trip_id = st.trip_id
JOIN routes r ON r.route_id = t.route_id
WHERE r.route_short_name = :route_short_name
  AND s.stop_name LIKE :stop_name_like
ORDER BY s.stop_name;

-- 10) Next departures for a route + stop with explicit direction or headsign filter
-- Use one of: :direction_id (0/1) OR :headsign_like ("%Oaks Mall%")
-- :route_short_name -> "5"
-- :stop_id_padded -> "0001"
-- :date -> "2026-01-28"
-- :time -> "14:30:00"
-- :limit -> 10
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
SELECT st.departure_time, t.trip_headsign, t.direction_id
FROM stops s
JOIN stop_times st ON st.stop_id = s.stop_id
JOIN trips t ON t.trip_id = st.trip_id
JOIN routes r ON r.route_id = t.route_id
JOIN active_services a ON a.service_id = t.service_id
WHERE r.route_short_name = :route_short_name
  AND s.stop_id_padded = :stop_id_padded
  AND st.departure_time >= :time
  AND (
    (:direction_id IS NOT NULL AND t.direction_id = :direction_id)
    OR (:headsign_like IS NOT NULL AND t.trip_headsign LIKE :headsign_like)
  )
ORDER BY st.departure_time
LIMIT :limit;

-- 11) Next departures for a route + stop with no direction provided
-- Returns the next departure per headsign so the UI/LLM can show both directions.
-- :route_short_name -> "38"
-- :stop_id_padded -> "0018"
-- :date -> "2026-01-28"
-- :time -> "07:30:00"
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
),
ranked AS (
  SELECT st.departure_time, t.trip_headsign,
         ROW_NUMBER() OVER (PARTITION BY t.trip_headsign ORDER BY st.departure_time) AS rn
  FROM stops s
  JOIN stop_times st ON st.stop_id = s.stop_id
  JOIN trips t ON t.trip_id = st.trip_id
  JOIN routes r ON r.route_id = t.route_id
  JOIN active_services a ON a.service_id = t.service_id
  WHERE r.route_short_name = :route_short_name
    AND s.stop_id_padded = :stop_id_padded
    AND st.departure_time >= :time
)
SELECT departure_time, trip_headsign
FROM ranked
WHERE rn = 1
ORDER BY departure_time;

-- 12) Fastest 1-transfer search (implemented in Python for performance/clarity)
-- See db/transfer_search.py for a reusable helper.
