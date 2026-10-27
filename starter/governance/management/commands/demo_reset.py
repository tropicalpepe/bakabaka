"""Reset demo data. ONLY runs in DEMO_MODE. CLI-only, never a web endpoint."""
import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from governance.management.commands.seed_demo import DEMO_PROJECT_ID, DEMO_PROJECT2_ID

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reset demo projects and data (DEMO_MODE only)"

    def add_arguments(self, parser):
        parser.add_argument("--confirm", action="store_true", help="Required to actually reset")

    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("demo_reset requires DEMO_MODE=1")

        if not options["confirm"]:
            self.stdout.write(
                "This will delete demo projects and associated data. "
                "Run with --confirm to proceed."
            )
            return

        DEMO_PROJECT_IDS = [DEMO_PROJECT_ID, DEMO_PROJECT2_ID]

        try:
            from documents.models import (
                AdviceRun, CheckRun, DocumentVersion, Extraction,
                Finding, FindingResolution, RuleResult,
            )
            from governance.models import AuditEvent, Job
            from projects.models import (
                Project, ProjectSource, TopicDecision,
                TopicDraft, TopicGeneration, TopicSubmission,
            )
            from supervision.models import Comment, SupervisorAssignment

            for pid in DEMO_PROJECT_IDS:
                try:
                    p = Project.objects.get(id=pid)
                except Project.DoesNotExist:
                    continue

                # --- Collect IDs BEFORE deleting parents ---

                version_ids = list(
                    DocumentVersion.objects.filter(project_id=pid).values_list("id", flat=True)
                )
                run_ids = list(
                    CheckRun.objects.filter(version_id__in=version_ids).values_list("id", flat=True)
                )
                finding_ids = list(
                    Finding.objects.filter(run_id__in=run_ids).values_list("id", flat=True)
                )
                submission_ids = list(
                    TopicSubmission.objects.filter(project=p).values_list("id", flat=True)
                )

                # --- Delete in reverse dependency order ---

                # Comments (version or finding targets)
                Comment.objects.filter(version_id__in=version_ids).delete()
                Comment.objects.filter(finding_id__in=finding_ids).delete()

                # Document chain: resolutions → findings → rule results → runs → advice → extraction → versions
                FindingResolution.objects.filter(finding_id__in=finding_ids).delete()
                Finding.objects.filter(id__in=finding_ids).delete()
                RuleResult.objects.filter(run_id__in=run_ids).delete()
                CheckRun.objects.filter(id__in=run_ids).delete()
                AdviceRun.objects.filter(version_id__in=version_ids).delete()
                Extraction.objects.filter(version_id__in=version_ids).delete()

                # Delete private files
                for v in DocumentVersion.objects.filter(project_id=pid):
                    file_path = Path(settings.PRIVATE_FILES_ROOT) / v.storage_key
                    file_path.unlink(missing_ok=True)
                DocumentVersion.objects.filter(project_id=pid).delete()

                # Topic chain
                TopicDecision.objects.filter(submission_id__in=submission_ids).delete()
                TopicSubmission.objects.filter(id__in=submission_ids).delete()
                TopicGeneration.objects.filter(project=p).delete()
                TopicDraft.objects.filter(project=p).delete()

                # Project relations
                ProjectSource.objects.filter(project=p).delete()
                SupervisorAssignment.objects.filter(project_id=pid).delete()
                Job.objects.filter(project_id=pid).delete()
                AuditEvent.objects.filter(object_id=pid).delete()

                p.delete()
                self.stdout.write(f"  Deleted project {pid[:8]}…")

            # Orphan comments (pointing to versions/findings that may have been deleted
            # outside of a tracked project — clean these up too)
            Comment.objects.filter(
                version_id__isnull=False,
                version_id__in=list(
                    c.version_id for c in Comment.objects.filter(version_id__isnull=False)
                    if not DocumentVersion.objects.filter(id=c.version_id).exists()
                )
            ).delete()

            self.stdout.write(self.style.SUCCESS("Demo data reset complete."))
        except Exception as exc:
            raise CommandError(f"Reset failed: {exc}") from exc
