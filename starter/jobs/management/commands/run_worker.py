import logging
import time

from django.core.management.base import BaseCommand

from jobs import engine, registry

logger = logging.getLogger("worker")


def _load_handlers():
    """Import each module's job handler file to trigger registration."""
    for module_path in ["recommendations.jobs", "documents.jobs"]:
        try:
            __import__(module_path)
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("Handler load error for %s: %s", module_path, exc)


class Command(BaseCommand):
    help = "Run the sequential background worker"

    def handle(self, *args, **options):
        _load_handlers()
        kinds = registry.all_kinds()
        self.stdout.write(f"Worker started. Registered kinds: {kinds}")

        engine.fail_exhausted()

        while True:
            try:
                active_kinds = registry.all_kinds() or ["__none__"]
                job = engine.claim_next(active_kinds)
                if job is None:
                    time.sleep(2)
                    continue

                handler = registry.get_handler(job.kind)
                if handler is None:
                    self.stdout.write(f"No handler for {job.kind}, skipping {job.id}")
                    engine.finish(job.id, job.lease_token, error_code="no_handler")
                    continue

                self.stdout.write(f"Processing job {job.id} kind={job.kind} attempt={job.attempts}")
                try:
                    result = handler(job)
                    engine.finish(job.id, job.lease_token, result_id=str(result or "done"))
                    self.stdout.write(self.style.SUCCESS(f"Job {job.id} succeeded"))
                except Exception as exc:
                    logger.exception("Job %s failed: %s", job.id, exc)
                    engine.finish(job.id, job.lease_token, error_code=str(exc)[:100])
                    self.stdout.write(self.style.ERROR(f"Job {job.id} failed: {exc}"))

            except KeyboardInterrupt:
                self.stdout.write("Worker shutting down.")
                break
