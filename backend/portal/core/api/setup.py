"""The new-user setup (P20i), under /api/core/setup: the steps of the caller's run with what each
shows, and the last step's Finish. The other steps write through the routes Settings already uses
(the name and title through PATCH /core/auth/me, the signature through /core/users/{id}/signature,
an agreement through /core/agreements/{id}/accept). Names no request."""

from datetime import datetime
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from portal.core.api.auth import MeOut, me_out
from portal.core.models import User
from portal.core.rls import setup_session_auth
from portal.core.services import agreements as agreements_service
from portal.core.services import setup as setup_service
from portal.core.services.settings import get_setting, org_short_name

router = Router(tags=["setup"])


class AgreementClauseOut(Schema):
    lead: str
    text: str


class SetupAgreementOut(Schema):
    id: UUID
    kind: str
    title: str
    version: int
    clauses: list[AgreementClauseOut]
    is_placeholder: bool
    published_at: datetime
    # When the caller agreed to this version; null while they have not.
    accepted_at: datetime | None


class NewUserSetupOut(Schema):
    required: bool
    completed_at: datetime | None
    # welcome, name, signature, confidentiality, intern: this run's, in order ("done" follows).
    steps: list[str]
    agreements: list[SetupAgreementOut]
    # For the welcome's "You already have an SCLU account" and the signature's preview.
    org_name: str
    org_short_name: str


def _caller(request: HttpRequest) -> User:
    user = request.user
    if not isinstance(user, User):  # session auth has already refused anyone signed out
        raise HttpError(401, "Unauthorized")
    return user


@router.get("", auth=setup_session_auth, response=NewUserSetupOut)
def get_setup(request: HttpRequest) -> NewUserSetupOut:
    state = setup_service.state(_caller(request))
    return NewUserSetupOut(
        required=state.required,
        completed_at=state.completed_at,
        steps=list(state.steps),
        agreements=[
            SetupAgreementOut(
                id=step.agreement.pk,
                kind=step.agreement.kind,
                title=agreements_service.title_of(step.agreement.kind),
                version=step.agreement.version,
                clauses=[
                    AgreementClauseOut(lead=c.lead, text=c.text)
                    for c in agreements_service.clauses(step.agreement.body)
                ],
                is_placeholder=step.agreement.is_placeholder,
                published_at=step.agreement.published_at,
                accepted_at=step.acceptance.accepted_at if step.acceptance else None,
            )
            for step in state.agreements
        ],
        org_name=str(get_setting("org.name") or "").strip(),
        org_short_name=org_short_name(),
    )


@router.post("/finish", auth=setup_session_auth, response=MeOut)
def finish_setup(request: HttpRequest) -> MeOut:
    """The last step's button. Refused (422, with the reason) while an agreement in force is not
    accepted or no signature is stored."""
    return me_out(setup_service.finish(_caller(request)))
