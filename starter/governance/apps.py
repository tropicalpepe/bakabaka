from django.apps import AppConfig


class GovernanceConfig(AppConfig):
    name = "governance"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.db.backends.signals import connection_created

        def enable_wal(sender, connection, **kwargs):
            if connection.vendor == "sqlite":
                connection.cursor().execute("PRAGMA journal_mode=WAL;")
                connection.cursor().execute("PRAGMA synchronous=NORMAL;")

        connection_created.connect(enable_wal)
