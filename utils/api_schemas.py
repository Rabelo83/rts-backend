"""
API Schema Definitions for RTS Backend

This module defines the structure of all API requests and responses
to maintain consistency between frontend and backend.
"""
from typing import TypedDict, Optional, List, Literal


# ============================================================
# COMMON ERROR RESPONSES
# ============================================================

class ErrorResponse(TypedDict):
    """Standard error response structure"""
    error: bool
    error_code: str
    error_message: str
    details: Optional[dict]


# Error codes
class ErrorCode:
    # Validation errors
    INVALID_STOP_ID = "INVALID_STOP_ID"
    INVALID_ROUTE_ID = "INVALID_ROUTE_ID"
    MISSING_PARAMETER = "MISSING_PARAMETER"

    # Data errors
    STOP_NOT_FOUND = "STOP_NOT_FOUND"
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
    NO_PREDICTIONS = "NO_PREDICTIONS"
    NO_SCHEDULE_DATA = "NO_SCHEDULE_DATA"

    # System errors
    API_UNAVAILABLE = "API_UNAVAILABLE"
    DATABASE_ERROR = "DATABASE_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"

    # Session errors
    SESSION_EXPIRED = "SESSION_EXPIRED"
    INVALID_SESSION = "INVALID_SESSION"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"


# ============================================================
# PREDICTION API SCHEMAS
# ============================================================

class PredictionItem(TypedDict):
    """Single bus prediction/arrival"""
    route: str
    direction: str
    destination: str
    minutes: str  # Can be "DUE" or numeric string
    vehicle_id: str
    arrival_time: str  # Formatted time string
    delayed: bool
    is_scheduled: Optional[bool]  # True if from schedule, False if real-time


class PredictionsResponse(TypedDict):
    """Response from /api/predictions endpoint"""
    predictions: List[PredictionItem]
    stop_id: str
    timestamp: str
    source: Literal["bustime", "schedule", "mixed"]
    cached: bool


# ============================================================
# ROUTE/DIRECTION/STOP API SCHEMAS
# ============================================================

class RouteItem(TypedDict):
    """Single route definition"""
    id: str
    name: str
    color: Optional[str]


class DirectionItem(TypedDict):
    """Single direction for a route"""
    id: str
    name: str
    headsign: Optional[str]


class StopItem(TypedDict):
    """Single stop definition"""
    id: str
    name: str
    lat: float
    lon: float
    routes: Optional[List[str]]  # List of route IDs serving this stop


class RoutesResponse(TypedDict):
    """Response from /api/routes endpoint"""
    routes: List[RouteItem]


class DirectionsResponse(TypedDict):
    """Response from /api/directions endpoint"""
    directions: List[DirectionItem]
    route_id: str


class StopsResponse(TypedDict):
    """Response from /api/stops endpoint"""
    stops: List[StopItem]
    route_id: str
    direction_id: Optional[str]


# ============================================================
# AGENT/CHAT API SCHEMAS
# ============================================================

class ChatButton(TypedDict):
    """Interactive button in chat response"""
    label: str
    action: str
    type: Optional[Literal["primary", "secondary", "link"]]
    disabled: Optional[bool]


class ChatMessage(TypedDict):
    """Single message in chat history"""
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: Optional[str]


class AgentMetadata(TypedDict):
    """Metadata about agent response"""
    intent: Literal["eta", "schedule", "route_info", "vehicle_location", "general", "fallback", "error"]
    route: Optional[str]
    stop_id: Optional[str]
    destination: Optional[str]
    direction: Optional[str]
    language: Literal["en", "es"]
    needs: List[str]  # Missing information: ["route", "stop", "time"]
    prefer_schedule: bool
    timeframe: Optional[str]
    confidence: Optional[float]


class AgentRequest(TypedDict):
    """Request to /api/agent endpoint"""
    message: str
    history: Optional[List[ChatMessage]]
    messages: Optional[List[ChatMessage]]  # Alias for history
    session_id: Optional[str]
    language: Optional[Literal["en", "es"]]


class AgentResponse(TypedDict):
    """Response from /api/agent endpoint"""
    answer: str
    meta: AgentMetadata
    sources: Optional[List[str]]
    buttons: Optional[List[ChatButton]]
    session_id: str
    timestamp: str
    response_time_ms: int


# ============================================================
# SESSION MANAGEMENT
# ============================================================

class SessionData(TypedDict):
    """Server-side session data"""
    session_id: str
    created_at: str
    last_activity: str
    message_count: int
    history: List[ChatMessage]
    context: dict  # User context (route, stop, intent, etc.)


# ============================================================
# HEALTH CHECK
# ============================================================

class HealthResponse(TypedDict):
    """Response from /api/health endpoint"""
    status: Literal["healthy", "degraded", "unhealthy"]
    service: str
    web_index: bool
    backend_basics: bool
    bustime_api: bool
    cache_size: int
    active_sessions: int
    timestamp: str
