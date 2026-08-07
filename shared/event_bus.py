"""
AEGIS-ECO Event Bus with Fault Injection

In-process event bus that simulates Kafka topic semantics.
Supports fault injection hooks for chaos testing:

  bus.set_fault("DROP_PUBLISH", "aegis.event.funds-reserved")
    → The next publish to that topic is silently dropped
    
  bus.set_fault("FAIL_HANDLER", "aegis.command.release-funds")
    → The next handler invocation for that topic raises an exception

  bus.set_fault("DUPLICATE_DELIVERY", "aegis.event.funds-reserved")
    → The next publish delivers the event twice to subscribers
"""

import json
import logging
import uuid
import datetime
from typing import Callable, Dict, List, Any, Optional, Set

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AEGIS_EVENT_BUS")


class EventBus:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EventBus, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.subscribers: Dict[str, List[Callable]] = {}
        self.event_log: List[Dict[str, Any]] = []
        self._faults: Dict[str, Set[str]] = {}
        # Track which fault types are one-shot (consumed after firing)
        self._initialized = True

    def reset(self):
        """Clear all subscribers and state. Used in tests."""
        self.subscribers.clear()
        self.event_log.clear()
        self._faults.clear()

    # ---- fault injection ------------------------------------------------

    def set_fault(self, fault_type: str, topic: str, persistent: bool = False):
        """Arm a fault for operations on `topic`.
        
        fault_type:
          - "DROP_PUBLISH": silently drop the publish (event never delivered)
          - "FAIL_HANDLER": raise RuntimeError in handler call
          - "DUPLICATE_DELIVERY": deliver the event twice to subscribers
        """
        if fault_type not in self._faults:
            self._faults[fault_type] = {}
        self._faults[fault_type][topic] = persistent
        logger.warning(f"[FAULT INJECTED] {fault_type} armed on topic: {topic} (persistent={persistent})")

    def clear_fault(self, fault_type: str, topic: str):
        if fault_type in self._faults and topic in self._faults[fault_type]:
            del self._faults[fault_type][topic]
            if not self._faults[fault_type]:
                del self._faults[fault_type]
            logger.warning(f"[FAULT CLEARED] {fault_type} cleared on topic: {topic}")

    def _consume_fault(self, fault_type: str, topic: str) -> bool:
        if fault_type in self._faults and topic in self._faults[fault_type]:
            persistent = self._faults[fault_type][topic]
            if not persistent:
                del self._faults[fault_type][topic]
                if not self._faults[fault_type]:
                    del self._faults[fault_type]
            return True
        return False

    # ---- pub/sub --------------------------------------------------------

    def subscribe(self, event_type: str, handler: Callable):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)
        logger.info(f"Subscribed {handler.__name__} to event: {event_type}")

    def publish(self, event_type: str, payload: Dict[str, Any],
                correlation_id: str = None, saga_id: str = None) -> Optional[Dict[str, Any]]:
        event_id = payload.get("event_id") or str(uuid.uuid4())
        correlation_id = (correlation_id or payload.get("correlation_id")
                          or str(uuid.uuid4()))
        saga_id = (saga_id or payload.get("saga_id")
                   or payload.get("project_id") or str(uuid.uuid4()))

        event = {
            "event_id": event_id,
            "event_type": event_type,
            "correlation_id": correlation_id,
            "saga_id": saga_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "payload": payload,
        }

        self.event_log.append(event)

        # --- fault: DROP_PUBLISH ---
        if self._consume_fault("DROP_PUBLISH", event_type):
            logger.warning(
                f"[FAULT FIRED] DROP_PUBLISH on [{event_type}] — event silently dropped"
            )
            return None  # event logged but never delivered

        logger.info(
            f"[EVENT] [{event_type}] | SagaID: {saga_id[:8]}... "
            f"| EventID: {event_id[:8]}..."
        )

        # Determine delivery count
        deliveries = 1
        if self._consume_fault("DUPLICATE_DELIVERY", event_type):
            deliveries = 2
            logger.warning(
                f"[FAULT FIRED] DUPLICATE_DELIVERY on [{event_type}] — "
                f"delivering twice"
            )

        # Dispatch to subscribers
        if event_type in self.subscribers:
            for _ in range(deliveries):
                for handler in self.subscribers[event_type]:
                    # --- fault: FAIL_HANDLER ---
                    if self._consume_fault("FAIL_HANDLER", event_type):
                        logger.warning(
                            f"[FAULT FIRED] FAIL_HANDLER on [{event_type}] "
                            f"for {handler.__name__}"
                        )
                        raise RuntimeError(
                            f"Injected fault: handler {handler.__name__} "
                            f"failed for {event_type}"
                        )
                    try:
                        handler(event)
                    except RuntimeError:
                        raise  # re-raise injected faults
                    except Exception as e:
                        logger.error(
                            f"Error in handler {handler.__name__} "
                            f"for {event_type}: {e}"
                        )

        return event


bus = EventBus()
