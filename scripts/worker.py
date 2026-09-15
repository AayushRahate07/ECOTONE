import time
import logging
import sys
import os

# Add parent directory to path so we can import services
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.project_service import project_outbox
from services.resource_service import resource_outbox
from services.funding_service import funding_outbox
from services.team_service import team_outbox
from services.regulatory_service import regulatory_outbox

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AEGIS_WORKER")

def run_worker():
    logger.info("[WORKER] Starting background outbox processor...")
    while True:
        try:
            p = project_outbox.process_pending()
            r = resource_outbox.process_pending()
            f = funding_outbox.process_pending()
            t = team_outbox.process_pending()
            reg = regulatory_outbox.process_pending()
            
            if not any([p, r, f, t, reg]):
                time.sleep(1)
            else:
                time.sleep(0.1)
        except Exception as e:
            logger.error(f"Error in worker loop: {e}")
            time.sleep(2)

if __name__ == "__main__":
    run_worker()
