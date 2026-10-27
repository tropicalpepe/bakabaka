"""Docker init entrypoint: migrate, load rulesets/corpus, seed pilots.

Runs once per container start (init service in compose.yaml).
Does not overwrite existing data or make paid API calls.
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.core.management import call_command

call_command("migrate", interactive=False)
call_command("load_ruleset")
call_command("seed_pilots")
call_command("load_corpus")
call_command("seed_demo", "--mode", "prepared")

print("Init complete.")
