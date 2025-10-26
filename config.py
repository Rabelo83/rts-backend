import os

# IMPORTANT:
# On Render you will set BUS_API_KEY as an environment variable.
# For local testing, this default is fine, but treat it as secret publicly.
API_KEY = os.getenv("BUS_API_KEY", "KfRiwhzgjPeFG9rviJvkpCjnr")

# Clever/BusTime usually uses one feed. RTS is using "bustime"
RTPIDATAFEED = os.getenv("RTPIDATAFEED", "bustime")

# Base API host for RTS BusTime
BASE_HOST = "https://riderts.app"
BASE_API = f"{BASE_HOST}/bustime/api/v3"
