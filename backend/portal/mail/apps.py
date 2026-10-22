from django.apps import AppConfig


class MailConfig(AppConfig):
    """Messages the portal sends (BUILD_PLAN P11): composing, the undo window, the provider, the
    delivery webhook, and the seam that makes a sent message a records source and event.

    Unmanaged models over the `mail` schema. Every foreign key points mail -> records or
    mail -> drafting; nothing points back, and `records` is told about mail only through the two
    registries it offers (`filing.after_filed` is drafting's; `sweeps.on_followup_due` is ours).
    """

    name = "portal.mail"
    label = "mail"
    verbose_name = "Outbound mail"

    def ready(self) -> None:
        from portal.mail.services.followups import send_followup
        from portal.records.services import sweeps

        sweeps.on_followup_due(send_followup)
