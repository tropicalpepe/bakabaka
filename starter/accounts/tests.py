from types import SimpleNamespace
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from .access import authorize_project
@override_settings(STORAGES={"default":{"BACKEND":"django.core.files.storage.FileSystemStorage"},"staticfiles":{"BACKEND":"django.contrib.staticfiles.storage.StaticFilesStorage"}}, WHITENOISE_USE_FINDERS=True)
class FoundationTests(TestCase):
    def setUp(self):
        User=get_user_model()
        self.anna=User.objects.create_user("anna",password="test-password",role="student")
        self.other=User.objects.create_user("other",password="test-password",role="student")
        self.sup=User.objects.create_user("sup",password="test-password",role="supervisor")
    def test_login_session_and_role(self):
        self.assertTrue(self.client.login(username="anna",password="test-password"))
        # Home redirects to /projects/ (follows to 200)
        self.assertEqual(self.client.get("/", follow=True).status_code,200)
        self.assertEqual(self.client.get("/healthz").json()["status"],"ok")
    def test_invalid_login(self):
        self.assertFalse(self.client.login(username="anna",password="wrong"))
        self.assertEqual(self.client.get("/").status_code,302)
    def test_foreign_project_denied(self):
        project=SimpleNamespace(owner_id=self.anna.id)
        with self.assertRaises(PermissionDenied):
            authorize_project(self.other,project,"view")
    def test_supervisor_scoped_actions(self):
        project=SimpleNamespace(owner_id=self.anna.id)
        authorize_project(self.sup,project,"comment",self.sup.id)
        with self.assertRaises(PermissionDenied):
            authorize_project(self.sup,project,"edit_context",self.sup.id)
        with self.assertRaises(PermissionDenied):
            authorize_project(self.sup,project,"view")
    def test_logout_requires_post(self):
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get("/logout/").status_code,405)

