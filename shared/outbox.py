"""
AEGIS-ECO Outbox Worker

Two components:
  - OutboxWriter: used by services INSIDE their transaction (tx.write_outbox)
    This module doesn't contain it because it's built into Transaction.
    
  - OutboxWorker: polls PENDING outbox entries from a service's database
    and publishes them through the event bus, then marks them SENT.
    This is what guarantees reliable event publication.

The separation is the whole point of the Outbox pattern:
  1. Service handler: BEGIN → domain mutation → outbox INSERT → COMMIT
  2. Outbox worker: reads PENDING → publishes to bus/Kafka → marks SENT

If the process crashes between step 1 and step 2, the outbox entry
survives in the database and gets picked up on the next poll.
"""

import json
import logging
import time
from typing import Optional

from shared.event_bus import bus

logger = logging.getLogger("AEGIS_OUTBOX")


class OutboxWorker:
    """Polls a service database's outbox table and publishes events."""

    def __init__(self, db, service_name: str = "unknown"):
        self.db = db
        self.service_name = service_name

    def poll_and_publish(self, limit: int = 50) -> int:
        """Read PENDING outbox entries, publish each, mark SENT.
        
        Returns the number of events published.
        """
        published = 0
        with self.db.begin() as tx:
            rows = tx.fetchall(
                "SELECT event_id, event_type, payload FROM outbox "
                "WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT ?",
                (limit,)
            )

        # Publish outside the SELECT transaction to avoid holding the lock
        for event_id, event_type, payload_json in rows:
            try:
                payload = json.loads(payload_json)
                res = bus.publish(event_type, payload)

                if res is not None:
                    with self.db.begin() as tx:
                        tx.execute(
                            "UPDATE outbox SET status = 'SENT' WHERE event_id = ?",
                            (event_id,)
                        )
                    published += 1
                else:
                    logger.warning(f"[OUTBOX WORKER {self.service_name}] Publish of {event_type} returned None (dropped/failed); leaving as PENDING")
            except Exception as e:
                logger.error(
                    f"[OUTBOX WORKER {self.service_name}] Failed to publish "
                    f"{event_type} ({event_id[:8]}): {e}"
                )
                # Leave as PENDING — next poll will retry
        
        if published > 0:
            logger.info(
                f"[OUTBOX WORKER {self.service_name}] Published {published} "
                f"pending events"
            )
        return published

    def run_once(self) -> int:
        """Convenience alias."""
        return self.poll_and_publish()
