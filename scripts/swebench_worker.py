import logging
import os
import sys
import time
from pathlib import Path

repo_dir = sys.argv[1]
problem = sys.argv[2]
instance_id = sys.argv[3] if len(sys.argv) > 3 else "unknown"

log_dir = Path("/tmp/sweb-loom/logs")
log_dir.mkdir(parents=True, exist_ok=True)
log_path = log_dir / f"{instance_id}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(log_path), mode="w"),
        logging.StreamHandler(sys.stdout),
    ],
)

logging.info("Starting instance %s in %s", instance_id, repo_dir)
start = time.time()

os.chdir(repo_dir)

os.environ["LOGURU_LEVEL"] = "INFO"
from loguru import logger  # noqa: E402

logger.remove()
logger.add(str(log_path), level="INFO", rotation="10 MB")
logger.add(sys.stderr, level="WARNING")

from loom.agent.loop import agent_loop, apply_config, load_config  # noqa: E402

try:
    apply_config(load_config(Path.cwd()))
    logging.info("Config applied, starting agent_loop...")
    history = [{"role": "user", "content": problem}]
    agent_loop(history)
except Exception as e:
    logging.error("Worker failed: %s", e, exc_info=True)

elapsed = time.time() - start
logging.info("Worker finished in %.0fs", elapsed)
print(f"[WORKER_DONE] {elapsed:.0f}s")
