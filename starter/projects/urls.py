from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("new/", views.new_project, name="new_project"),
    path("<str:project_id>/", views.project_detail, name="detail"),
    path("<str:project_id>/context/", views.edit_context, name="edit_context"),
    path("<str:project_id>/draft/", views.api_save_draft, name="save_draft"),
    path("<str:project_id>/sources/", views.api_save_source, name="save_source"),
    path("<str:project_id>/submit/", views.api_submit_topic, name="submit_topic"),
]
