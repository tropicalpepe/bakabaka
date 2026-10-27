"""Seed demonstration projects and state."""
import json
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

DEMO_PROJECT_ID = "11111111-1111-4111-8111-111111111111"
DEMO_PROJECT2_ID = "22222222-2222-4222-8222-222222222222"


class Command(BaseCommand):
    help = "Seed demo projects and state"

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["fresh", "prepared"], default="fresh")

    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("Demo seed requires DEMO_MODE=1")

        mode = options["mode"]
        self._seed_fresh()
        if mode == "prepared":
            self._seed_prepared()
        self.stdout.write(self.style.SUCCESS(f"Demo seed ({mode}) complete."))

    def _seed_fresh(self):
        """Create empty demo projects for Anna and Jānis if they don't exist."""
        from accounts.models import User
        from governance.services import get_active_ruleset
        from projects.models import Project

        ruleset = get_active_ruleset()

        # Load project context fixture
        ctx_path = settings.FIXTURE_DIR / "project_context.json"
        ctx = json.loads(ctx_path.read_text())

        # Anna's project
        try:
            anna = User.objects.get(username="anna")
        except User.DoesNotExist:
            self.stdout.write("Anna account missing, run seed_pilots first.")
            return

        anna_project, created = Project.objects.get_or_create(
            id=DEMO_PROJECT_ID,
            defaults={
                "owner": anna,
                "requirement_set": ruleset,
                "title": ctx["title"],
                "idea": ctx["idea"],
                "keywords": ctx["keywords"],
            },
        )
        if created:
            self.stdout.write(f"Created Anna's demo project: {anna_project.id}")
            # Ensure student profile has programme
            try:
                anna.student_profile.programme = ctx.get("programme", "BakaBaka demonstrācijas programma")
                anna.student_profile.interests = ctx.get("interests", [])
                anna.student_profile.keywords = ctx.get("keywords", [])
                anna.student_profile.save()
            except Exception as exc:
                logger.warning("Could not update Anna's profile: %s", exc)

        # Jānis's project (isolation test)
        try:
            janis = User.objects.get(username="janis")
            Project.objects.get_or_create(
                id=DEMO_PROJECT2_ID,
                defaults={
                    "owner": janis,
                    "requirement_set": ruleset,
                    "title": "Jāņa demonstrācijas projekts",
                    "idea": "Atsevišķs projekts izolācijas testēšanai.",
                    "keywords": ["izolācija", "tests"],
                },
            )
        except User.DoesNotExist:
            pass

    def _seed_prepared(self):
        """Set up Anna's project with accepted supervisor and topic draft."""
        from accounts.models import User
        from core.utils import make_uuid
        from projects.models import Project, ProjectSource, TopicDraft
        from recommendations.models import Source
        from recommendations.search import load_corpus
        from supervision.models import SupervisorAssignment

        # Load corpus first
        load_corpus()

        try:
            anna = User.objects.get(username="anna")
            ilze = User.objects.get(username="ilze")
        except User.DoesNotExist as exc:
            self.stdout.write(f"Skipping prepared seed: {exc}")
            return

        project = Project.objects.get(id=DEMO_PROJECT_ID)

        # Accepted supervisor assignment
        assignment, _ = SupervisorAssignment.objects.get_or_create(
            project_id=DEMO_PROJECT_ID,
            supervisor=ilze,
            defaults={
                "id": make_uuid(),
                "requested_by": anna,
                "state": SupervisorAssignment.STATE_ACCEPTED,
            },
        )
        if assignment.state != SupervisorAssignment.STATE_ACCEPTED:
            assignment.state = SupervisorAssignment.STATE_ACCEPTED
            assignment.save(update_fields=["state"])

        # Load synthetic topic output as a fixture generation
        topo_path = settings.FIXTURE_DIR / "topic_output.synthetic.json"
        if topo_path.exists():
            topo = json.loads(topo_path.read_text())
            from projects.models import TopicGeneration

            gen, _ = TopicGeneration.objects.get_or_create(
                project=project,
                origin="synthetic_fixture",
                defaults={
                    "context_fingerprint": "prepared-demo",
                    "model": "synthetic",
                    "proposals_json": topo["response"]["proposals"],
                    "source_ids": [sid for p in topo["response"]["proposals"] for sid in p["source_ids"]],
                    "limitations": topo["response"]["grounding_note"],
                    "prompt_version": topo.get("prompt_version", "topic-v1"),
                },
            )

            # Save draft from first proposal
            proposals = topo["response"]["proposals"]
            if proposals:
                p0 = proposals[0]
                TopicDraft.objects.update_or_create(
                    project=project,
                    defaults={
                        "title": p0["title"],
                        "hypothesis": p0["hypothesis"],
                        "rationale": p0["rationale"],
                        "source_ids": p0.get("source_ids", []),
                        "updated_by": anna,
                    },
                )

        # Save a few sources for Anna's project
        scholarly = list(Source.objects.filter(synthetic=False)[:3])
        for src in scholarly:
            ProjectSource.objects.get_or_create(
                project=project,
                source_id=src.id,
                defaults={"saved_by": anna},
            )

        self.stdout.write("Prepared demo state ready.")
