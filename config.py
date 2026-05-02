import os

# IMPORTANT:
# On Render you will set BUS_API_KEY as an environment variable.
# BUS_API_KEYS can hold multiple authorized keys separated by commas. This is
# only intended for keys issued for this app/account, so the backend can fail
# over cleanly if one key reaches its provider-side daily transaction cap.
API_KEY = os.getenv("BUS_API_KEY", "KfRiwhzgjPeFG9rviJvkpCjnr")
API_KEYS = [
    key.strip()
    for key in os.getenv("BUS_API_KEYS", API_KEY).split(",")
    if key.strip()
]
if not API_KEYS:
    API_KEYS = [API_KEY]

# Clever/BusTime usually uses one feed. RTS is using "bustime"
RTPIDATAFEED = os.getenv("RTPIDATAFEED", "bustime")

# Base API host for RTS BusTime
BASE_HOST = "https://riderts.app"
BASE_API = f"{BASE_HOST}/bustime/api/v3"
