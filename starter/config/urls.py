from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path

from accounts.views import health, home
from projects.views import api_job_status

urlpatterns = [
    path("", home, name="home"),
    path("healthz", health, name="health"),
    path("login/", LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("projects/", include("projects.urls", namespace="projects")),
    path("research/", include("recommendations.urls", namespace="recommendations")),
    path("supervision/", include("supervision.urls", namespace="supervision")),
    path("documents/", include("documents.urls", namespace="documents")),
    path("api/jobs/<str:job_id>/status/", api_job_status, name="job_status"),
]
