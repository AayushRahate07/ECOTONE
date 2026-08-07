import os
import json
import uuid

# Environment and Database Connection Strings
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", 5432))
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgrespassword")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

# Kafka Topics
TOPICS = {
    "PROJECT_CREATED": "aegis.project.created",
    "COMMAND_RESERVE_FUNDS": "aegis.command.reserve-funds",
    "FUNDS_RESERVED": "aegis.event.funds-reserved",
    "FUNDS_INSUFFICIENT": "aegis.event.funds-insufficient",
    "COMMAND_RELEASE_FUNDS": "aegis.command.release-funds",
    "FUNDS_RELEASED": "aegis.event.funds-released",
    
    "COMMAND_RESERVE_RESOURCES": "aegis.command.reserve-resources",
    "RESOURCES_RESERVED": "aegis.event.resources-reserved",
    "RESOURCE_UNAVAILABLE": "aegis.event.resource-unavailable",
    "COMMAND_RELEASE_RESOURCES": "aegis.command.release-resources",
    "RESOURCES_RELEASED": "aegis.event.resources-released",
    
    "COMMAND_ASSIGN_TEAM": "aegis.command.assign-team",
    "TEAM_ASSIGNED": "aegis.event.team-assigned",
    "COMMAND_UNASSIGN_TEAM": "aegis.command.unassign-team",
    "TEAM_UNASSIGNED": "aegis.event.team-unassigned",
    
    "COMMAND_REQUEST_PERMIT": "aegis.command.request-permit",
    "PERMIT_APPROVED": "aegis.event.permit-approved",
    "PERMIT_REJECTED": "aegis.event.permit-rejected",
    
    "PROJECT_ACTIVATED": "aegis.project.activated",
    "PROJECT_CANCELLED": "aegis.project.cancelled",
}

def get_db_url(db_name: str) -> str:
    return f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{db_name}"
