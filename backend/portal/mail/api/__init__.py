from ninja import NinjaAPI

from portal.mail.api import messages


def register(api: NinjaAPI) -> None:
    api.add_router("/mail", messages.router)
