from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("<str:project_id>/", views.index, name="index"),
    path("<str:project_id>/upload/", views.upload, name="upload"),
    path("<str:project_id>/<str:version_id>/", views.version_detail, name="version_detail"),
    path("<str:project_id>/<str:version_id>/download/", views.download, name="download"),
    path("<str:project_id>/<str:version_id>/check/", views.api_queue_check, name="queue_check"),
    path("<str:project_id>/<str:version_id>/advice/", views.api_queue_advice, name="queue_advice"),
]
