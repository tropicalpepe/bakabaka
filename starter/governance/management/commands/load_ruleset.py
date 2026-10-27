from django.conf import settings
from django.core.management.base import BaseCommand

from governance.services import load_ruleset_from_file


class Command(BaseCommand):
    help = "Load the demo ruleset from rules/demo-lv-v1.json"

    def handle(self, *args, **options):
        path = settings.RULES_DIR / "demo-lv-v1.json"
        rs = load_ruleset_from_file(path)
        self.stdout.write(self.style.SUCCESS(f"Ruleset {rs.key} v{rs.version} ready."))
