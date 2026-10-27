from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        STUDENT = "student", "Students"
        SUPERVISOR = "supervisor", "Darba vadītājs"
        ADMIN = "admin", "Administrators"

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.STUDENT)
    synthetic = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"


class StudentProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_profile",
        primary_key=True,
    )
    programme = models.CharField(max_length=200, default="")
    interests = models.JSONField(default=list)
    keywords = models.JSONField(default=list)

    def __str__(self):
        return f"StudentProfile({self.user_id})"


class SupervisorProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="supervisor_profile",
        primary_key=True,
    )
    competencies = models.JSONField(default=list)
    keywords = models.JSONField(default=list)
    available = models.BooleanField(default=True)
    capacity = models.IntegerField(default=3)
    institution = models.CharField(max_length=200, default="BakaBaka demonstrācija")

    def __str__(self):
        return f"SupervisorProfile({self.user_id})"
