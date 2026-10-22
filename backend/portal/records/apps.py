from django.apps import AppConfig


class RecordsConfig(AppConfig):
    """The records-request ledger.

    Unmanaged models over the `records` schema, the service layer, the API, and inbound mail.
    """

    name = "portal.records"
    label = "records"
    verbose_name = "Records requests"
