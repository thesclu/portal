from django.apps import AppConfig


class MailDbConfig(AppConfig):
    """Holds the SQL migrations for the `mail` schema and nothing else.

    Same split as drafting_db: 0001 applies db/mail/mail-schema.sql verbatim, and the models
    that read those tables live in portal.mail, which has no migrations of its own.
    """

    name = "portal.mail_db"
    label = "mail_db"
    verbose_name = "Mail schema"
