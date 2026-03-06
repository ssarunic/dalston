"""WebSocket close codes.

Centralizes WebSocket close codes used in gateway and realtime SDK
for consistent error handling and client communication.

Standard codes (RFC 6455):
- 1000-1999: Reserved for WebSocket protocol
- 3000-3999: Reserved for libraries/frameworks
- 4000-4999: Available for application use (Dalston uses this range)
"""

# =============================================================================
# Standard WebSocket Close Codes (RFC 6455)
# =============================================================================
# These are defined by the WebSocket protocol

WS_CLOSE_NORMAL = 1000  # Normal closure
WS_CLOSE_GOING_AWAY = 1001  # Endpoint going away (e.g., server shutdown)
WS_CLOSE_PROTOCOL_ERROR = 1002  # Protocol error (invalid parameter)
WS_CLOSE_UNSUPPORTED_DATA = 1003  # Unsupported data type received
WS_CLOSE_NO_STATUS = 1005  # No status code present (reserved, not sent)
WS_CLOSE_ABNORMAL = 1006  # Abnormal closure (reserved, not sent)
WS_CLOSE_INVALID_PAYLOAD = 1007  # Invalid payload data
WS_CLOSE_POLICY_VIOLATION = 1008  # Policy violation (invalid path)
WS_CLOSE_MESSAGE_TOO_BIG = 1009  # Message too big
WS_CLOSE_EXTENSION_REQUIRED = 1010  # Extension required
WS_CLOSE_INTERNAL_ERROR = 1011  # Unexpected server error
WS_CLOSE_TRY_AGAIN_LATER = 1013  # Server at capacity, try later

# =============================================================================
# Dalston Application Close Codes (4xxx range)
# =============================================================================
# Custom codes for application-specific errors

WS_CLOSE_LAG_EXCEEDED = 4010  # Realtime lag budget exceeded
WS_CLOSE_INVALID_REQUEST = 4400  # Invalid parameters / unsupported intent
WS_CLOSE_UNAUTHORIZED = 4401  # Authentication required / invalid credentials
WS_CLOSE_FORBIDDEN = 4403  # Access denied
WS_CLOSE_NOT_FOUND = 4404  # Resource not found
WS_CLOSE_RATE_LIMITED = 4429  # Rate limit exceeded
WS_CLOSE_SERVICE_UNAVAILABLE = 4503  # Service unavailable / no capacity
