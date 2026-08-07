"""
AEGIS-ECO Analytics Service (V1)

Read-model projection service.
Consumes domain events from all service outbox workers,
maintains in-memory read models (simulating Redis cache),
and exposes dashboard summary data.

This service does NOT mutate authoritative state.
PostgreSQL/SQLite in each service is the source of truth.
"""

import logging
import datetime
from typing import Dict, List, Any
from shared.event_bus import bus
from shared.config import TOPICS

logger = logging.getLogger("ANALYTICS_SERVICE")


class AnalyticsService:
    def __init__(self):
        self.read_models: Dict[str, dict] = {}
        self.metrics = {
            "total_projects": 0,
            "active_projects": 0,
            "pending_projects": 0,
            "cancelled_projects": 0,
            "compensating_projects": 0,
            "reserved_funds": 0.0,
            "event_count": 0,
        }
        self.live_stream: List[dict] = []
        self._register_subscribers()

    def _register_subscribers(self):
        for topic in TOPICS.values():
            bus.subscribe(topic, self.project_event)

    def project_event(self, event: dict):
        self.metrics["event_count"] += 1
        event_type = event["event_type"]
        payload = event.get("payload", {})
        project_id = payload.get("project_id")

        entry = {
            "event_id": event.get("event_id"),
            "event_type": event_type,
            "saga_id": event.get("saga_id"),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "payload_summary": {
                k: v for k, v in payload.items()
                if k in ("project_id", "title", "status", "reason",
                         "grant_code", "reserved_amount")
            },
        }
        self.live_stream.append(entry)
        if len(self.live_stream) > 200:
            self.live_stream.pop(0)

        if not project_id:
            return

        if project_id not in self.read_models:
            self.read_models[project_id] = {
                "project_id": project_id,
                "title": payload.get("title", "Project " + project_id[:8]),
                "status": "INITIATED",
                "last_event": event_type,
                "updated_at": entry["timestamp"],
            }
            self.metrics["total_projects"] += 1

        model = self.read_models[project_id]
        model["last_event"] = event_type
        model["updated_at"] = entry["timestamp"]

        if event_type == TOPICS["PROJECT_ACTIVATED"]:
            model["status"] = "ACTIVE"
            self.metrics["active_projects"] += 1
        elif event_type == TOPICS["PROJECT_CANCELLED"]:
            model["status"] = "CANCELLED"
            self.metrics["cancelled_projects"] += 1
            # Decrement compensating if it was tracked
            if model.get("was_compensating"):
                self.metrics["compensating_projects"] = max(
                    0, self.metrics["compensating_projects"] - 1
                )
        elif event_type == TOPICS["PERMIT_REJECTED"]:
            model["status"] = "COMPENSATING"
            model["was_compensating"] = True
            self.metrics["compensating_projects"] += 1
        elif event_type == TOPICS["FUNDS_RESERVED"]:
            self.metrics["reserved_funds"] += payload.get("reserved_amount", 0.0)
        elif event_type == TOPICS["FUNDS_RELEASED"]:
            released = payload.get("released_amount", 0.0)
            self.metrics["reserved_funds"] = max(
                0.0, self.metrics["reserved_funds"] - released
            )

        # Recompute pending
        self.metrics["pending_projects"] = max(0,
            self.metrics["total_projects"]
            - self.metrics["active_projects"]
            - self.metrics["cancelled_projects"]
            - self.metrics["compensating_projects"]
        )

    def get_dashboard_summary(self) -> dict:
        return {
            "metrics": self.metrics,
            "read_models": list(self.read_models.values()),
            "recent_events": self.live_stream[-15:],
        }

    def reset(self):
        """Reset all state. Used in tests."""
        self.read_models.clear()
        self.live_stream.clear()
        self.metrics = {
            "total_projects": 0,
            "active_projects": 0,
            "pending_projects": 0,
            "cancelled_projects": 0,
            "compensating_projects": 0,
            "reserved_funds": 0.0,
            "event_count": 0,
        }


analytics_service = AnalyticsService()
