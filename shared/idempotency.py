"""
AEGIS-ECO Idempotency Module (Stub — kept for backward compatibility)

The real idempotency logic now lives inside shared/database.py:
    Transaction.try_claim_event(event_id, event_type)

This module is retained only so existing imports don't break during
the migration.  Services should use tx.try_claim_event() directly
inside their transaction blocks.
"""

import logging

logger = logging.getLogger("AEGIS_IDEMPOTENCY")


class IdempotencyHandler:
    """Legacy stub. Use tx.try_claim_event() inside a Transaction instead."""

    def __init__(self, db_conn=None):
        self.local_processed = set()

    def is_processed(self, event_id: str, service_name: str = "default") -> bool:
        return event_id in self.local_processed

    def mark_processed(self, event_id: str, event_type: str,
                       service_name: str = "default") -> bool:
        self.local_processed.add(event_id)
        return True
