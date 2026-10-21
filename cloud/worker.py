"""Private Vercel queue subscriber; build discovery is declared in pyproject."""
from vercel.queue import subscribe

from cloud.runtime import Runtime, TOPIC


@subscribe(topic=TOPIC, consumer_group="paper-tracker-v1", max_concurrency=1, retry_after=300, max_attempts=8)
def update_papers(payload: dict) -> None:
    Runtime().worker(payload)
