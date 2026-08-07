"""
AEGIS-ECO Real Apache Kafka Event Transport & Bus Layer
=========================================================

Provides real Kafka Producer and Consumer event transport using `kafka-python-ng`.
Also integrates KafkaOutboxWorker that polls outbox tables and publishes
messages directly to Apache Kafka brokers (`localhost:9092`).

Usage:
    from shared.kafka_bus import KafkaEventBus, KafkaOutboxWorker

    bus = KafkaEventBus(bootstrap_servers="localhost:9092")
    bus.subscribe("aegis.command.reserve-funds", handle_reserve_funds)
    bus.publish("aegis.event.funds-reserved", payload)
"""

import json
import uuid
import logging
import threading
import time
from typing import Callable, Dict, List, Any, Optional

try:
    from kafka import KafkaProducer, KafkaConsumer
    from kafka.errors import KafkaError
    HAS_KAFKA = True
except ImportError:
    KafkaProducer = None
    KafkaConsumer = None
    HAS_KAFKA = False

from shared.config import KAFKA_BOOTSTRAP_SERVERS, TOPICS

logger = logging.getLogger("AEGIS_KAFKA_BUS")


class KafkaEventBus:
    """Real Apache Kafka Event Bus using kafka-python-ng."""

    def __init__(self, bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS, client_id: str = "aegis-bus"):
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self.producer = None
        self.consumers: Dict[str, List[Callable]] = {}
        self.consumer_threads: Dict[str, threading.Thread] = {}
        self._running = True

        if HAS_KAFKA:
            try:
                self.producer = KafkaProducer(
                    bootstrap_servers=self.bootstrap_servers,
                    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                    key_serializer=lambda k: k.encode('utf-8') if k else None,
                    retries=3,
                    request_timeout_ms=5000,
                    api_version=(2, 5, 0)
                )
                logger.info(f"[KAFKA] Connected producer to {bootstrap_servers}")
            except Exception as e:
                logger.warning(f"[KAFKA] Producer connection failed: {e}")
                self.producer = None

    def is_available(self) -> bool:
        """Check if Kafka cluster is reachable."""
        return self.producer is not None

    def subscribe(self, topic: str, handler: Callable, group_id: str = "aegis-group"):
        """Subscribe a handler function to a Kafka topic."""
        if topic not in self.consumers:
            self.consumers[topic] = []
            # Start consumer background thread for this topic if Kafka is connected
            if HAS_KAFKA and self.producer:
                t = threading.Thread(
                    target=self._consume_loop,
                    args=(topic, group_id),
                    daemon=True
                )
                t.start()
                self.consumer_threads[topic] = t
                
        self.consumers[topic].append(handler)
        logger.info(f"[KAFKA] Subscribed handler '{handler.__name__}' to topic '{topic}'")

    def _consume_loop(self, topic: str, group_id: str):
        """Background thread consuming messages from Kafka topic."""
        try:
            consumer = KafkaConsumer(
                topic,
                bootstrap_servers=self.bootstrap_servers,
                group_id=f"{group_id}-{topic}",
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                auto_offset_reset='earliest',
                enable_auto_commit=True,
                api_version=(2, 5, 0)
            )
            logger.info(f"[KAFKA CONSUMER] Started listening on '{topic}'")
            
            for message in consumer:
                if not self._running:
                    break
                event_data = message.value
                for handler in self.consumers.get(topic, []):
                    try:
                        handler(event_data)
                    except Exception as e:
                        logger.error(f"[KAFKA CONSUMER] Handler error on '{topic}': {e}")
        except Exception as e:
            logger.warning(f"[KAFKA CONSUMER] Consumer stopped for '{topic}': {e}")

    def publish(self, topic: str, payload: dict, key: str = None) -> Optional[dict]:
        """Publish an event to a Kafka topic."""
        event_id = payload.get("event_id") or str(uuid.uuid4())
        payload["event_id"] = event_id

        event = {
            "event_id": event_id,
            "event_type": topic,
            "timestamp": time.time(),
            "payload": payload
        }

        if self.producer:
            try:
                future = self.producer.send(topic, key=key or event_id, value=event)
                record_metadata = future.get(timeout=5)
                logger.info(
                    f"[KAFKA PUBLISH] Topic '{topic}' | Partition {record_metadata.partition} "
                    f"| Offset {record_metadata.offset} | EventID: {event_id[:8]}"
                )
                return event
            except Exception as e:
                logger.error(f"[KAFKA PUBLISH ERROR] Failed to publish to '{topic}': {e}")
                return None
        else:
            logger.warning(f"[KAFKA BUS] Producer offline; event {topic} ({event_id[:8]}) not sent")
            return None

    def close(self):
        self._running = False
        if self.producer:
            try:
                self.producer.close()
            except Exception:
                pass


class KafkaOutboxWorker:
    """Outbox worker that polls database outbox entries and publishes to Kafka."""

    def __init__(self, db, kafka_bus: KafkaEventBus, service_name: str = "unknown"):
        self.db = db
        self.kafka_bus = kafka_bus
        self.service_name = service_name

    def poll_and_publish(self, limit: int = 50) -> int:
        published = 0
        with self.db.begin() as tx:
            rows = tx.fetchall(
                "SELECT event_id, event_type, payload FROM outbox "
                "WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT %s;",
                (limit,)
            )

        for event_id, event_type, payload_data in rows:
            try:
                payload = payload_data if isinstance(payload_data, dict) else json.loads(payload_data)
                res = self.kafka_bus.publish(event_type, payload)

                if res is not None:
                    with self.db.begin() as tx:
                        tx.execute(
                            "UPDATE outbox SET status = 'SENT' WHERE event_id = %s;",
                            (event_id,)
                        )
                    published += 1
            except Exception as e:
                logger.error(f"[KAFKA OUTBOX WORKER {self.service_name}] Outbox publish error: {e}")

        return published
