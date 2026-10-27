from django.urls import path

from .views import api_job_status

urlpatterns = [
    path("", api_job_status, name="job_status"),
]
