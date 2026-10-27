from django.urls import path

from . import views

app_name = "recommendations"

urlpatterns = [
    path("<str:project_id>/", views.research, name="research"),
    path("<str:project_id>/search/", views.api_search, name="search"),
    path("<str:project_id>/queue-topics/", views.api_queue_topics, name="queue_topics"),
    path("<str:project_id>/generation/<str:generation_id>/", views.api_generation_result, name="generation_result"),
    path("<str:project_id>/supervisors/", views.api_supervisors, name="supervisors"),
]
