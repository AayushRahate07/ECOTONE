"""
AEGIS-ECO Structured Observability & Telemetry Logger
======================================================

Emits structured JSON logs containing:
  - event_id
  - saga_id
  - correlation_id
  - service
  - event_type
  - timestamp
"""

import json
import logging
import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger("AEGIS_STRUCTURED_LOG")


def log_event(service_name: str, event_type: str, payload: dict,
              event_id: Optional[str] = None, saga_id: Optional[str] = None,
              correlation_id: Optional[str] = None, level: str = "INFO"):
    """Emit a structured JSON log entry."""
    eid = event_id or payload.get("event_id") or "N/A"
    sid = saga_id or payload.get("saga_id") or payload.get("project_id") or "N/A"
    cid = correlation_id or payload.get("correlation_id") or eid

    log_entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "service": service_name,
        "event_type": event_type,
        "event_id": eid,
        "saga_id": sid,
        "correlation_id": cid,
        "payload_summary": {
            k: v for k, v in payload.items()
            if k in ("project_id", "title", "status", "reason", "grant_code",
                     "reserved_amount", "allocated_resources", "reserved_resources", "location_name")
        }
    }

    formatted = json.dumps(log_entry)
    if level == "WARNING":
        logger.warning(formatted)
    elif level == "ERROR":
        logger.error(formatted)
    else:
        logger.info(formatted)

    return log_entry
