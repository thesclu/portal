from django.apps import AppConfig


class RecordsDbConfig(AppConfig):
    """Holds the SQL migrations for the `records` schema and nothing else.

    0001 applies db/records/schema.sql verbatim. Every later migration is
    additive and reviewed by owner. The models that read these tables live in
    portal.records, which has no migrations of its own.
    """

    name = "portal.records_db"
    label = "records_db"
    verbose_name = "Records schema"
