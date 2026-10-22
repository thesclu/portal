from django.apps import AppConfig


class DraftingDbConfig(AppConfig):
    """Holds the SQL migrations for the `drafting` schema and nothing else.

    Same split as records_db: 0001 applies db/drafting/drafting-schema.sql
    verbatim, and the models that read those tables live in portal.drafting,
    which has no migrations of its own.
    """

    name = "portal.drafting_db"
    label = "drafting_db"
    verbose_name = "Drafting schema"
