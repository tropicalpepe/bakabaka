"""Foundation helper; final services must load and validate real project relationships."""
from django.core.exceptions import PermissionDenied
STUDENT_ACTIONS={"view","edit_context","edit_draft","save_source","submit_topic","upload","check","advice","comment","resolve"}
SUPERVISOR_ACTIONS={"view","decide_topic","comment"}
def authorize_project(actor, project, action, accepted_supervisor_id=None):
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied
    if actor.role=="student" and project.owner_id==actor.id and action in STUDENT_ACTIONS:
        return project
    if actor.role=="supervisor" and accepted_supervisor_id==actor.id and action in SUPERVISOR_ACTIONS:
        return project
    raise PermissionDenied

