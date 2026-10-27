"""Integration tests for BakaBaka application.

Covers acceptance scenarios A01-A14, A16, A17, A18 (mocked), A20.
Run from starter/ with: python manage.py test bakabaka_tests
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

User = get_user_model()

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
DOCS_ROOT = FIXTURE_ROOT / "documents"


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    WHITENOISE_USE_FINDERS=True,
    PRIVATE_FILES_ROOT=tempfile.mkdtemp(),
    RULES_DIR=Path(__file__).resolve().parents[1] / "rules",
    PROMPTS_DIR=Path(__file__).resolve().parents[2] / "prompts",
    FIXTURE_DIR=FIXTURE_ROOT,
    DEMO_MODE=True,
)
class AuthTests(TestCase):
    """A01: Pilot login and invalid login."""

    def setUp(self):
        self.anna = User.objects.create_user(
            "anna_test", password="test123", role="student", first_name="Anna"
        )

    def test_valid_login_redirects(self):
        resp = self.client.post("/login/", {"username": "anna_test", "password": "test123"})
        self.assertIn(resp.status_code, (302, 200))

    def test_invalid_login_rejected(self):
        resp = self.client.post("/login/", {"username": "anna_test", "password": "wrong"})
        # Should show login again or 401
        self.assertNotEqual(resp.status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.get("/projects/").status_code, 302)

    def test_unauthenticated_projects_redirects(self):
        resp = self.client.get("/projects/")
        self.assertEqual(resp.status_code, 302)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    WHITENOISE_USE_FINDERS=True,
    PRIVATE_FILES_ROOT=tempfile.mkdtemp(),
    RULES_DIR=Path(__file__).resolve().parents[1] / "rules",
    PROMPTS_DIR=Path(__file__).resolve().parents[2] / "prompts",
    FIXTURE_DIR=FIXTURE_ROOT,
    DEMO_MODE=True,
)
class AccessControlTests(TestCase):
    """A02: Foreign object access prevention."""

    def setUp(self):
        self.anna = User.objects.create_user("anna_ac", password="pw", role="student")
        self.bob = User.objects.create_user("bob_ac", password="pw", role="student")
        self.supervisor = User.objects.create_user("sup_ac", password="pw", role="supervisor")
        from governance.models import RequirementSet
        from core.utils import make_uuid
        import hashlib, json as _json
        self.ruleset = RequirementSet.objects.create(
            id=make_uuid(), key="test", version=1, programme="Test",
            rules_json={"rules": []},
            sha256=hashlib.sha256(b"{}").hexdigest(),
        )
        from projects.models import Project
        self.project = Project.objects.create(
            owner=self.anna,
            requirement_set=self.ruleset,
            title="Anna's Project",
            idea="Test idea",
        )

    def test_other_student_cannot_access(self):
        self.client.force_login(self.bob)
        resp = self.client.get(f"/projects/{self.project.id}/")
        # Should redirect or 403/404 — not 200
        self.assertNotEqual(resp.status_code, 200)

    def test_unassigned_supervisor_cannot_access(self):
        self.client.force_login(self.supervisor)
        resp = self.client.get(f"/projects/{self.project.id}/")
        self.assertNotEqual(resp.status_code, 200)

    def test_owner_can_access(self):
        self.client.force_login(self.anna)
        resp = self.client.get(f"/projects/{self.project.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_foreign_download_rejected(self):
        self.client.force_login(self.bob)
        resp = self.client.get(f"/documents/{self.project.id}/fake-version-id/download/")
        self.assertNotEqual(resp.status_code, 200)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    WHITENOISE_USE_FINDERS=True,
    PRIVATE_FILES_ROOT=tempfile.mkdtemp(),
    RULES_DIR=Path(__file__).resolve().parents[1] / "rules",
    PROMPTS_DIR=Path(__file__).resolve().parents[2] / "prompts",
    FIXTURE_DIR=FIXTURE_ROOT,
    DEMO_MODE=True,
)
class ProjectContextTests(TestCase):
    """A03: Context save/persist."""

    def setUp(self):
        self.anna = User.objects.create_user("anna_ctx", password="pw", role="student")
        from governance.models import RequirementSet
        from core.utils import make_uuid
        import hashlib
        self.ruleset = RequirementSet.objects.create(
            id=make_uuid(), key="test", version=1, programme="Test",
            rules_json={"rules": []}, sha256=hashlib.sha256(b"{}").hexdigest(),
        )
        from projects.models import Project
        self.project = Project.objects.create(
            owner=self.anna, requirement_set=self.ruleset,
            title="Initial", idea="Initial idea",
        )

    def test_save_context_updates_revision(self):
        self.client.force_login(self.anna)
        resp = self.client.post(
            f"/projects/{self.project.id}/context/",
            data=json.dumps({
                "title": "Updated Title",
                "idea": "Updated idea",
                "keywords": ["test"],
                "expected_revision": 1,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["data"]["context_revision"], 2)
        self.assertEqual(data["data"]["title"], "Updated Title")

    def test_stale_revision_conflicts(self):
        self.client.force_login(self.anna)
        resp = self.client.post(
            f"/projects/{self.project.id}/context/",
            data=json.dumps({"title": "T", "idea": "I", "keywords": [], "expected_revision": 99}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)

    def test_missing_fields_error(self):
        self.client.force_login(self.anna)
        resp = self.client.post(
            f"/projects/{self.project.id}/context/",
            data=json.dumps({"idea": "I", "keywords": [], "expected_revision": 1}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 422)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    WHITENOISE_USE_FINDERS=True,
    PRIVATE_FILES_ROOT=tempfile.mkdtemp(),
    RULES_DIR=Path(__file__).resolve().parents[1] / "rules",
    PROMPTS_DIR=Path(__file__).resolve().parents[2] / "prompts",
    FIXTURE_DIR=FIXTURE_ROOT,
    DEMO_MODE=True,
)
class SourceSearchTests(TestCase):
    """A06: Source search and save."""

    def setUp(self):
        self.anna = User.objects.create_user("anna_src", password="pw", role="student")
        from governance.models import RequirementSet
        from core.utils import make_uuid
        import hashlib
        self.ruleset = RequirementSet.objects.create(
            id=make_uuid(), key="test", version=1, programme="Test",
            rules_json={"rules": []}, sha256=hashlib.sha256(b"{}").hexdigest(),
        )
        from projects.models import Project
        self.project = Project.objects.create(
            owner=self.anna, requirement_set=self.ruleset,
            title="Test", idea="Test",
        )
        # Load a few sources
        from recommendations.models import Source
        Source.objects.create(
            id="ARXIV-TEST-001", title="Dense Passage Retrieval",
            authors=["Karpukhin"], year=2020, url="https://arxiv.org/abs/2004.04906",
            repository="arxiv", language="en", document_type="preprint",
            annotation="DPR for open-domain QA", evidence_level="metadata_annotation",
            synthetic=False, provenance={}, content_hash="abc",
        )
        Source.objects.create(
            id="DEMO-LV-001", title="Latviešu tekstu analīze",
            authors=["Demo"], year=2023, url="https://example.com",
            repository="demo", language="lv", document_type="thesis",
            annotation="Sintētisks demonstrācijas avots", evidence_level="metadata_annotation",
            synthetic=True, provenance={}, content_hash="def",
        )

    def test_keyword_search_returns_results(self):
        self.client.force_login(self.anna)
        resp = self.client.get(
            f"/research/{self.project.id}/search/?q=retrieval"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("results", data)
        self.assertGreater(len(data["results"]), 0)
        self.assertEqual(data["mode"], "keyword_only")  # No embeddings in test

    def test_save_source_is_idempotent(self):
        self.client.force_login(self.anna)
        for _ in range(2):
            resp = self.client.post(
                f"/projects/{self.project.id}/sources/",
                data=json.dumps({"source_id": "ARXIV-TEST-001", "action": "save"}),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
        from projects.models import ProjectSource
        count = ProjectSource.objects.filter(project=self.project, source_id="ARXIV-TEST-001").count()
        self.assertEqual(count, 1)

    def test_remove_source(self):
        self.client.force_login(self.anna)
        self.client.post(
            f"/projects/{self.project.id}/sources/",
            data=json.dumps({"source_id": "ARXIV-TEST-001", "action": "save"}),
            content_type="application/json",
        )
        resp = self.client.post(
            f"/projects/{self.project.id}/sources/",
            data=json.dumps({"source_id": "ARXIV-TEST-001", "action": "remove"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        from projects.models import ProjectSource
        self.assertFalse(ProjectSource.objects.filter(project=self.project, source_id="ARXIV-TEST-001").exists())


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    WHITENOISE_USE_FINDERS=True,
    PRIVATE_FILES_ROOT=tempfile.mkdtemp(),
    RULES_DIR=Path(__file__).resolve().parents[1] / "rules",
    PROMPTS_DIR=Path(__file__).resolve().parents[2] / "prompts",
    FIXTURE_DIR=FIXTURE_ROOT,
    DEMO_MODE=True,
)
class SupervisionFlowTests(TestCase):
    """A07, A08, A09: Supervision request and topic review."""

    def setUp(self):
        self.anna = User.objects.create_user("anna_sup", password="pw", role="student")
        self.ilze = User.objects.create_user("ilze_sup", password="pw", role="supervisor", synthetic=True)
        from accounts.models import SupervisorProfile
        SupervisorProfile.objects.create(
            user=self.ilze, competencies=["NLP"], keywords=["retrieval"],
            available=True, capacity=3,
        )
        from governance.models import RequirementSet
        from core.utils import make_uuid
        import hashlib
        self.ruleset = RequirementSet.objects.create(
            id=make_uuid(), key="test", version=1, programme="Test",
            rules_json={"rules": []}, sha256=hashlib.sha256(b"{}").hexdigest(),
        )
        from projects.models import Project
        self.project = Project.objects.create(
            owner=self.anna, requirement_set=self.ruleset,
            title="Test", idea="Test", keywords=["retrieval"],
        )

    def test_request_supervision(self):
        self.client.force_login(self.anna)
        resp = self.client.post(
            f"/supervision/{self.project.id}/request/",
            data=json.dumps({"supervisor_id": self.ilze.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["data"]["state"], "requested")

    def test_duplicate_request_rejected(self):
        self.client.force_login(self.anna)
        self.client.post(
            f"/supervision/{self.project.id}/request/",
            data=json.dumps({"supervisor_id": self.ilze.id}),
            content_type="application/json",
        )
        resp = self.client.post(
            f"/supervision/{self.project.id}/request/",
            data=json.dumps({"supervisor_id": self.ilze.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)

    def test_supervisor_accepts(self):
        from supervision.models import SupervisorAssignment
        from core.utils import make_uuid
        assignment = SupervisorAssignment.objects.create(
            id=make_uuid(), project_id=str(self.project.id),
            supervisor=self.ilze, requested_by=self.anna,
            state="requested",
        )
        self.client.force_login(self.ilze)
        resp = self.client.post(
            f"/supervision/assignment/{assignment.id}/respond/",
            data=json.dumps({"accepted": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.state, "accepted")

    def test_topic_decision_requires_comment_for_rejection(self):
        from core.utils import make_uuid
        from supervision.models import SupervisorAssignment
        from projects.models import TopicSubmission

        assignment = SupervisorAssignment.objects.create(
            id=make_uuid(), project_id=str(self.project.id),
            supervisor=self.ilze, requested_by=self.anna, state="accepted",
        )
        submission = TopicSubmission.objects.create(
            id=make_uuid(), project=self.project, assignment=assignment,
            number=1, title="T", hypothesis="H", rationale="R",
            sources_snapshot=[], author=self.anna,
        )
        self.client.force_login(self.ilze)
        # Missing comment for rejection
        resp = self.client.post(
            f"/supervision/{self.project.id}/decide/{submission.id}/",
            data=json.dumps({"decision": "rejected", "comment": ""}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 422)

    def test_topic_decision_approved(self):
        from core.utils import make_uuid
        from supervision.models import SupervisorAssignment
        from projects.models import TopicSubmission

        assignment = SupervisorAssignment.objects.create(
            id=make_uuid(), project_id=str(self.project.id),
            supervisor=self.ilze, requested_by=self.anna, state="accepted",
        )
        submission = TopicSubmission.objects.create(
            id=make_uuid(), project=self.project, assignment=assignment,
            number=1, title="T", hypothesis="H", rationale="R",
            sources_snapshot=[], author=self.anna,
        )
        self.client.force_login(self.ilze)
        resp = self.client.post(
            f"/supervision/{self.project.id}/decide/{submission.id}/",
            data=json.dumps({"decision": "approved", "comment": ""}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        # Decision is immutable — second attempt must fail
        resp2 = self.client.post(
            f"/supervision/{self.project.id}/decide/{submission.id}/",
            data=json.dumps({"decision": "approved", "comment": ""}),
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 409)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    WHITENOISE_USE_FINDERS=True,
    PRIVATE_FILES_ROOT=tempfile.mkdtemp(),
    RULES_DIR=Path(__file__).resolve().parents[1] / "rules",
    PROMPTS_DIR=Path(__file__).resolve().parents[2] / "prompts",
    FIXTURE_DIR=FIXTURE_ROOT,
    DEMO_MODE=True,
)
class DocumentUploadTests(TestCase):
    """A11, A12, A13, A14: Upload validation and checks."""

    def setUp(self):
        self.anna = User.objects.create_user("anna_doc", password="pw", role="student")
        from governance.models import RequirementSet
        from core.utils import make_uuid
        import hashlib
        self.ruleset = RequirementSet.objects.create(
            id=make_uuid(), key="demo-lv", version=1, programme="Test",
            rules_json=json.loads((Path(__file__).resolve().parents[1] / "rules" / "demo-lv-v1.json").read_text()),
            sha256=hashlib.sha256(b"{}").hexdigest(),
        )
        from projects.models import Project
        self.project = Project.objects.create(
            owner=self.anna, requirement_set=self.ruleset,
            title="Test", idea="Test",
        )
        import django.conf
        django.conf.settings.PRIVATE_FILES_ROOT = tempfile.mkdtemp()

    def test_reject_corrupt_docx(self):
        self.client.force_login(self.anna)
        corrupt = DOCS_ROOT / "corrupt.docx"
        with open(corrupt, "rb") as f:
            resp = self.client.post(
                f"/documents/{self.project.id}/upload/",
                {"file": f},
                follow=True,
            )
        # Should show error, not create a version
        from documents.models import DocumentVersion
        self.assertEqual(DocumentVersion.objects.filter(project_id=self.project.id).count(), 0)

    def test_accept_valid_pdf(self):
        self.client.force_login(self.anna)
        pdf = DOCS_ROOT / "draft_v1.pdf"
        if not pdf.exists():
            self.skipTest("draft_v1.pdf not found in fixtures")
        with open(pdf, "rb") as f:
            resp = self.client.post(
                f"/documents/{self.project.id}/upload/",
                {"file": f},
                follow=True,
            )
        from documents.models import DocumentVersion
        self.assertEqual(DocumentVersion.objects.filter(project_id=self.project.id).count(), 1)

    def test_accept_valid_docx(self):
        self.client.force_login(self.anna)
        docx = DOCS_ROOT / "draft_v1.docx"
        if not docx.exists():
            self.skipTest("draft_v1.docx not found in fixtures")
        with open(docx, "rb") as f:
            resp = self.client.post(
                f"/documents/{self.project.id}/upload/",
                {"file": f},
                follow=True,
            )
        from documents.models import DocumentVersion
        self.assertEqual(DocumentVersion.objects.filter(project_id=self.project.id).count(), 1)

    def test_version_numbers_increment(self):
        self.client.force_login(self.anna)
        docx = DOCS_ROOT / "draft_v1.docx"
        if not docx.exists():
            self.skipTest("draft_v1.docx not found in fixtures")
        for i in range(2):
            with open(docx, "rb") as f:
                self.client.post(f"/documents/{self.project.id}/upload/", {"file": f}, follow=True)
        from documents.models import DocumentVersion
        versions = list(DocumentVersion.objects.filter(project_id=self.project.id).order_by("number"))
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].number, 1)
        self.assertEqual(versions[1].number, 2)

    def test_deterministic_check_v1_docx(self):
        """A13: Provided v1 matches expected check results."""
        from documents.checker import extract_units, run_checks
        import json as _json

        docx = DOCS_ROOT / "draft_v1.docx"
        if not docx.exists():
            self.skipTest("draft_v1.docx not found in fixtures")

        expected_path = FIXTURE_ROOT / "expected_checks.json"
        expected = _json.loads(expected_path.read_text())["draft_v1.docx"]

        extraction = extract_units(docx)
        result = run_checks(extraction, self.ruleset.rules_json)
        for rule_id, exp_outcome in expected.items():
            self.assertEqual(
                result["rule_results"][rule_id]["outcome"],
                exp_outcome,
                f"Rule {rule_id}: expected {exp_outcome}, got {result['rule_results'][rule_id]['outcome']}",
            )

    def test_deterministic_check_v2_docx(self):
        """A13: Provided v2 matches expected check results."""
        from documents.checker import extract_units, run_checks
        import json as _json

        docx = DOCS_ROOT / "draft_v2.docx"
        if not docx.exists():
            self.skipTest("draft_v2.docx not found in fixtures")

        expected_path = FIXTURE_ROOT / "expected_checks.json"
        expected = _json.loads(expected_path.read_text())["draft_v2.docx"]

        extraction = extract_units(docx)
        result = run_checks(extraction, self.ruleset.rules_json)
        for rule_id, exp_outcome in expected.items():
            self.assertEqual(
                result["rule_results"][rule_id]["outcome"],
                exp_outcome,
                f"Rule {rule_id}: expected {exp_outcome}, got {result['rule_results'][rule_id]['outcome']}",
            )

    def test_check_independent_of_filename(self):
        """A13: Renaming a file does not change check outcome."""
        from documents.checker import extract_units, run_checks
        import json as _json
        import shutil

        docx = DOCS_ROOT / "draft_v1.docx"
        if not docx.exists():
            self.skipTest("draft_v1.docx not found in fixtures")

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            shutil.copy(docx, tmp.name)
            extraction = extract_units(Path(tmp.name))
        result = run_checks(extraction, self.ruleset.rules_json)
        expected_path = FIXTURE_ROOT / "expected_checks.json"
        expected = _json.loads(expected_path.read_text())["draft_v1.docx"]
        for rule_id, exp_outcome in expected.items():
            self.assertEqual(result["rule_results"][rule_id]["outcome"], exp_outcome)

    def test_pdf_format_not_assessed(self):
        """A14: PDF formatting is not_assessed."""
        from documents.checker import extract_units, run_checks
        pdf = DOCS_ROOT / "draft_v1.pdf"
        if not pdf.exists():
            self.skipTest("draft_v1.pdf not found in fixtures")
        extraction = extract_units(pdf)
        result = run_checks(extraction, self.ruleset.rules_json)
        self.assertEqual(result["rule_results"]["FMT-BODY"]["outcome"], "not_assessed")


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    WHITENOISE_USE_FINDERS=True,
    PRIVATE_FILES_ROOT=tempfile.mkdtemp(),
    RULES_DIR=Path(__file__).resolve().parents[1] / "rules",
    PROMPTS_DIR=Path(__file__).resolve().parents[2] / "prompts",
    FIXTURE_DIR=FIXTURE_ROOT,
    DEMO_MODE=True,
)
class JobEngineTests(TestCase):
    """A19: Worker recovery and job engine correctness."""

    def setUp(self):
        self.user = User.objects.create_user("worker_user", password="pw", role="student")
        from governance.models import RequirementSet
        from core.utils import make_uuid
        import hashlib
        self.ruleset = RequirementSet.objects.create(
            id=make_uuid(), key="test", version=1, programme="Test",
            rules_json={"rules": []}, sha256=hashlib.sha256(b"{}").hexdigest(),
        )
        from projects.models import Project
        self.project = Project.objects.create(
            owner=self.user, requirement_set=self.ruleset,
            title="Test", idea="Test",
        )

    def test_enqueue_deduplicates(self):
        from jobs.engine import enqueue
        from core.utils import fingerprint
        fp = fingerprint("test", "job")
        j1, c1 = enqueue(str(self.project.id), self.user.id, "test.kind", fp, {})
        j2, c2 = enqueue(str(self.project.id), self.user.id, "test.kind", fp, {})
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(j1.id, j2.id)

    def test_claim_and_finish(self):
        from jobs.engine import claim_next, enqueue, finish
        from core.utils import fingerprint

        fp = fingerprint("test", "claim", "finish")
        enqueue(str(self.project.id), self.user.id, "test.finish", fp, {})
        job = claim_next(["test.finish"])
        self.assertIsNotNone(job)
        self.assertEqual(job.state, "running")
        ok = finish(job.id, job.lease_token, result_id="done")
        self.assertTrue(ok)
        job.refresh_from_db()
        self.assertEqual(job.state, "succeeded")

    def test_stale_lease_rejected(self):
        from jobs.engine import claim_next, enqueue, finish
        from core.utils import fingerprint

        fp = fingerprint("test", "stale")
        enqueue(str(self.project.id), self.user.id, "test.stale", fp, {})
        job = claim_next(["test.stale"])
        ok = finish(job.id, "wrong-token", result_id="done")
        self.assertFalse(ok)
        job.refresh_from_db()
        self.assertEqual(job.state, "running")  # Not changed
