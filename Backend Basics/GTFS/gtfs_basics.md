# GTFS Basics (Local Notes)

## What GTFS is
GTFS (General Transit Feed Specification) is a set of text files (usually CSV) that describe a transit system’s schedules and geography. A GTFS feed is a folder of files, zipped for sharing.

## Core files and what they do
- agency.txt: who runs the service.
- stops.txt: stop locations and IDs.
- routes.txt: route names, numbers, colors.
- trips.txt: individual vehicle trips along a route.
- stop_times.txt: times each trip serves each stop.
- calendar.txt: weekly service schedule.
- calendar_dates.txt: exceptions (holidays, extra service, etc.).

## Common optional files
- shapes.txt: the path a vehicle takes.
- frequencies.txt: headway-based service.
- transfers.txt: rules for transfers between stops/routes.
- fare_attributes.txt and fare_rules.txt: fares and where they apply.

## How the pieces connect
- routes.txt + trips.txt + stop_times.txt create the timetable.
- stops.txt provides stop locations referenced by stop_times.txt.
- calendar.txt/calendar_dates.txt define when trips run.
- shapes.txt gives the map line a trip follows.

## Quick Q&A
Q: What file defines when a trip runs?
A: calendar.txt and calendar_dates.txt.

Q: Where are stop coordinates stored?
A: stops.txt.

Q: How do you know which stops a trip serves?
A: stop_times.txt links trips to stop IDs and times.

Q: What links a trip to a route?
A: trips.txt (route_id).

Q: What is the minimum GTFS needed for schedules?
A: agency.txt, stops.txt, routes.txt, trips.txt, stop_times.txt, and either calendar.txt or calendar_dates.txt.

Q: What makes GTFS usable for maps?
A: stops.txt plus optional shapes.txt for route geometry.

## Notes for this project
Stop IDs are padded to 4 digits in GTFS (e.g., 27 -> 0027). Use stops_id_name_padded.csv for matching.

These notes are local and intended for quick reference. If you add a GTFS feed folder later, place it alongside this file.
