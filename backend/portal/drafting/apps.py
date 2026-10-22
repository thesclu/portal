from django.apps import AppConfig


class DraftingConfig(AppConfig):
    """Letter templates, working drafts, and the fan-out that turns them into requests.

    Unmanaged models over the `drafting` schema, the service layer, the renderer,
    and the API. Every foreign key it declares points drafting -> records; nothing
    in records points back.
    """

    name = "portal.drafting"
    label = "drafting"
    verbose_name = "Letter drafting"

    def ready(self) -> None:
        # A letter reserved here can be filed from its request's own page, which is a records
        # endpoint. `records` knows nothing of `drafting`, so this app asks to be told: the
        # callback stamps the recipient `filed` inside `file_request`'s own transaction.
        from portal.drafting.services import fork
        from portal.drafting.services.filing import stamp_recipient_filed
        from portal.records.services import access
        from portal.records.services import filing as records_filing

        records_filing.after_filed(stamp_recipient_filed)
        # P21c: the drafting tool keeps its own copy of who may open each letter, and its own
        # sessions; neither `records` nor `core` knows of it, so this app asks to be told.
        access.after_access_changed(fork.on_access_changed)
        # P25b: the tool's sessions end through Keycloak's back-channel logout, not from here.
