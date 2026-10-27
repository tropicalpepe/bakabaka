import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts.models import StudentProfile, SupervisorProfile


class Command(BaseCommand):
    help = "Create missing synthetic pilot accounts and profiles; do not reset existing passwords."

    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("Pilot accounts require DEMO_MODE=1")

        User = get_user_model()
        records = json.loads((settings.FIXTURE_DIR / "accounts.json").read_text())
        supervisors_data = {
            row["user_id"]: row
            for row in json.loads((settings.FIXTURE_DIR / "supervisors.json").read_text())
        }

        for row in records:
            user, created = User.objects.get_or_create(
                username=row["username"],
                defaults={
                    "id": row["id"],
                    "role": row["role"],
                    "first_name": row["first_name"],
                    "last_name": row["last_name"],
                    "synthetic": True,
                },
            )
            if created:
                user.set_password(row["password"])
                user.save(update_fields=["password"])

            # Create profiles
            if row["role"] == "student":
                StudentProfile.objects.get_or_create(user=user)
            elif row["role"] == "supervisor":
                sup_data = supervisors_data.get(row["id"], {})
                profile, _ = SupervisorProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        "competencies": sup_data.get("competencies", []),
                        "keywords": sup_data.get("keywords", []),
                        "available": sup_data.get("available", True),
                        "capacity": sup_data.get("capacity", 3),
                        "institution": sup_data.get("institution", "BakaBaka demonstrācija"),
                    },
                )

        self.stdout.write(self.style.SUCCESS("Pilot accounts and profiles ready; existing accounts preserved."))
