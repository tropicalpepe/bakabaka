from django.core.management.base import BaseCommand

from recommendations.search import load_corpus


class Command(BaseCommand):
    help = "Load source corpus from fixtures/sources.json"

    def handle(self, *args, **options):
        count = load_corpus()
        self.stdout.write(self.style.SUCCESS(f"Loaded {count} new sources into corpus."))
