from django.urls import path

from . import views

app_name = "supervision"

urlpatterns = [
    path("inbox/", views.supervisor_inbox, name="inbox"),
    path("<str:project_id>/", views.index, name="index"),
    path("<str:project_id>/request/", views.api_request, name="request"),
    path("<str:project_id>/comment/", views.api_comment, name="comment"),
    path("<str:project_id>/decide/<str:submission_id>/", views.api_decide, name="decide"),
    path("<str:project_id>/claim/<str:finding_id>/", views.api_claim_fixed, name="claim_fixed"),
    path("assignment/<str:assignment_id>/respond/", views.api_respond, name="respond"),
    path("assignment/<str:assignment_id>/cancel/", views.api_cancel, name="cancel"),
]
