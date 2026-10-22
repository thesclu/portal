"""P20i: version 1 of each agreement the new-user setup asks for, seeded from the design.

owner, 2026-10-17 (DECISIONS 37e): "just use the current one. ill feed you stuff after". The
wording is the clause text v6 draws on screens 010 (confidentiality, every person) and 011
(working as an intern, the non-attorney roles), copied verbatim and nothing else: not the
"Legal basis (draft for attorney review)" list of the design's ARCHITECTURE.md, and no text of
the portal's own (DECISIONS 33b). Both rows are marked `is_placeholder`; an administrator replaces
them by publishing version 2 from Settings → Organisation → Agreements.

Stored as one clause per paragraph with its lead in `**`, the shape
`core.services.agreements.clauses` reads. The reverse removes the two rows only while nobody has
accepted either.
"""

from django.db import migrations

CONFIDENTIALITY = [
    (
        "What’s confidential.",
        "Anything you learn through your work with SCLU that isn’t public, including internal notes, drafts, strategy, legal analysis, and the identities of people who contact us.",
    ),
    (
        "Keep it inside SCLU.",
        "Don’t share confidential information with anyone outside SCLU, during your internship or after it ends.",
    ),
    (
        "Privileged notes.",
        "Notes marked privileged record an attorney’s analysis. Don’t copy, forward, or discuss them outside SCLU. Disclosure can waive protection SCLU cannot get back.",
    ),
    (
        "Personal information in records.",
        "Agency productions often include names, addresses, and other personal details about people outside SCLU. Don’t post, publish, or share produced records until an attorney has reviewed them.",
    ),
    (
        "AI tools.",
        "Use only the assistant built into the portal. Don’t enter SCLU information into ChatGPT or any other outside AI tool.",
    ),
    (
        "Accounts and devices.",
        "Use your SCLU account. Don’t move SCLU files to personal email, cloud drives, or devices, and don’t share your login.",
    ),
    (
        "Report problems right away.",
        "Tell your supervising attorney if something is sent to the wrong person, a device is lost, or confidential information may have gotten out.",
    ),
    (
        "When your internship ends.",
        "Return or delete SCLU materials. This agreement still applies.",
    ),
]

INTERN = [
    (
        "You don’t give legal advice.",
        "Don’t tell anyone how the law applies to them or that SCLU represents them. Send those questions to your supervising attorney.",
    ),
    ("Don’t present yourself as an attorney.", "Sign your work as Legal Intern."),
    (
        "Standard requests are yours to send.",
        "Appeals, letters arguing a legal position, and anything that mentions legal action go to an attorney first. The portal routes them for review.",
    ),
    (
        "Check what the assistant drafts.",
        "It can help you write. An attorney makes the legal calls and reviews any legal analysis before it goes out.",
    ),
    (
        "Speak for SCLU only when asked.",
        "Send press and social media questions about our work to your supervising attorney.",
    ),
    (
        "Your work is logged.",
        "Requests, letters, and edits are recorded in each request’s history.",
    ),
]


def _body(clauses: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"**{lead}** {rest}" for lead, rest in clauses)


def seed(apps, schema_editor) -> None:
    Agreement = apps.get_model("core", "Agreement")
    for kind, clauses in (("confidentiality", CONFIDENTIALITY), ("intern", INTERN)):
        Agreement.objects.get_or_create(
            kind=kind,
            version=1,
            defaults={"body": _body(clauses), "is_placeholder": True},
        )


def unseed(apps, schema_editor) -> None:
    Agreement = apps.get_model("core", "Agreement")
    AgreementAcceptance = apps.get_model("core", "AgreementAcceptance")
    seeded = Agreement.objects.filter(version=1, is_placeholder=True)
    if AgreementAcceptance.objects.filter(agreement__in=seeded).exists():
        raise RuntimeError(
            "Someone has accepted a seeded agreement; its version 1 cannot be removed."
        )
    seeded.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0026_p20i_setup_agreements"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
