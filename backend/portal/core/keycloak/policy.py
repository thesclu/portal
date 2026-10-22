"""The sign-in rules an administrator sets in Settings → System → Sign-in, and how they are
written to Keycloak (P25a; house rule: switches and rules are editable on a screen, never only in
the environment).

  auth.session_hours           A session ends after this many hours without use. The portal's
                               session idle limit and Keycloak's SSO idle timeout (default 8, as
                               SESSION_HOURS was).
  auth.session_max_hours       The longest a sign-in lasts, used or not: Keycloak's SSO maximum and
                               the age after which the password is asked again (level 1).
  auth.reverify_hours          The portal asks for the passkey again once the last passkey check is
                               this old (level 2's max age in the portal's flow; default 4).
  auth.reverify_tool_minutes   Another application (the drafting tool, chat, the Mac app) asks for
                               the passkey again when its last check is older than this (level 2's
                               max age in the tools' flow; default 30).
  auth.allow_authenticator_app Whether an authenticator app is a second step beside the passkey
                               (on by default since P25c; owner, 2026-10-20: "i want to allow both
                               pass key and auth app"). On: the OTP form is an alternative in each
                               second-step choice (both browser flows' "second step known" and the
                               forgot-password flow's), the "first app" branch someone with no
                               second step yet reaches by choosing an app is CONDITIONAL, and
                               CONFIGURE_TOTP is on. Off: all three DISABLED/off, so the second
                               step is a passkey (or a recovery code) and a first one is a passkey.

Everything else about signing in (the password policy, lockouts, the email templates, the
passkey settings) is Keycloak's own, in its admin console or the realm file.

Writing is one queued change (`KeycloakChange.POLICY`), applied by `apply()` through the admin API:
the realm's session limits, the four level-of-authentication conditions, the OTP executions, the
two "first app" branches and the CONFIGURE_TOTP required action. keycloak-config-cli applies the
realm file on Keycloak's start with the defaults below; `apply()` runs after it (`just
keycloak-up`) and every hour, so Settings wins. A change of the authenticator-app switch also
re-syncs every person (`api/settings.py`), since what each one owes Keycloak (a passkey for someone
left with an app that is no longer allowed) follows from it (`admin._baseline_actions`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from portal.core.services.settings import get_setting

SESSION_HOURS = "auth.session_hours"
SESSION_MAX_HOURS = "auth.session_max_hours"
REVERIFY_HOURS = "auth.reverify_hours"
REVERIFY_TOOL_MINUTES = "auth.reverify_tool_minutes"
ALLOW_APP = "auth.allow_authenticator_app"
KEYS = (SESSION_HOURS, SESSION_MAX_HOURS, REVERIFY_HOURS, REVERIFY_TOOL_MINUTES, ALLOW_APP)

# The realm file's names for what `apply()` touches (keycloak/realm/sclu.json).
PORTAL_FLOW = "sclu portal browser"
TOOLS_FLOW = "sclu tools browser"
LOA_PROVIDER = "conditional-level-of-authentication"
OTP_PROVIDER = "auth-otp-form"
OTP_ACTION = "CONFIGURE_TOTP"
# Where an authenticator app is one of the second steps a person already has: each browser flow's
# "second step known" and the forgot-password flow's second step.
CHOICE_FLOWS = (
    "sclu portal second step known",
    "sclu tools second step known",
    "sclu reset second step",
)
# P25c: the branch someone with no second step yet takes after choosing an authenticator app (the
# sign-in asks for APP_SCOPE; `oidc.start`), one per browser flow.
FIRST_APP_FLOWS = {PORTAL_FLOW: "sclu portal first app", TOOLS_FLOW: "sclu tools first app"}
APP_SCOPE = "sclu-app-second-step"
# Bounds an administrator may choose within (product numbers, not legal ones).
LIMITS = {
    SESSION_HOURS: (1, 168),
    SESSION_MAX_HOURS: (1, 720),
    REVERIFY_HOURS: (1, 168),
    REVERIFY_TOOL_MINUTES: (1, 1440),
}


@dataclass(frozen=True)
class Policy:
    session_hours: int
    session_max_hours: int
    reverify_hours: int
    reverify_tool_minutes: int
    allow_authenticator_app: bool

    @property
    def session_seconds(self) -> int:
        return self.session_hours * 3600

    @property
    def session_max_seconds(self) -> int:
        return self.session_max_hours * 3600

    @property
    def reverify_seconds(self) -> int:
        return self.reverify_hours * 3600

    @property
    def reverify_tool_seconds(self) -> int:
        return self.reverify_tool_minutes * 60


def _int(key: str, fallback: int) -> int:
    value = get_setting(key, fallback)
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def current() -> Policy:
    return Policy(
        session_hours=_int(SESSION_HOURS, 8),
        session_max_hours=_int(SESSION_MAX_HOURS, 24),
        reverify_hours=_int(REVERIFY_HOURS, 4),
        reverify_tool_minutes=_int(REVERIFY_TOOL_MINUTES, 30),
        allow_authenticator_app=bool(get_setting(ALLOW_APP, True)),
    )


def check(key: str, value: Any, *, others: Policy | None = None) -> str | None:
    """Why `value` cannot be `key`'s, or None when it can. The type is already checked."""
    if key not in LIMITS:
        return None
    low, high = LIMITS[key]
    if not low <= int(value) <= high:
        return f"Choose between {low} and {high}."
    now = others or current()
    if key == SESSION_HOURS and int(value) > now.session_max_hours:
        return "A session cannot be idle for longer than the longest sign-in."
    if key == SESSION_MAX_HOURS and int(value) < now.session_hours:
        return "The longest sign-in cannot be shorter than the idle limit."
    if key == REVERIFY_HOURS and int(value) > now.session_max_hours:
        return "The passkey check cannot be rarer than the longest sign-in."
    return None


def _loa_level(config: dict[str, Any]) -> str:
    return str((config.get("config") or {}).get("loa-condition-level", ""))


def apply(kc: Any, policy: Policy | None = None) -> list[str]:
    """Write the policy to the realm through the admin API (`kc`: a python-keycloak admin
    client). Returns what changed, for the log. Raises the client's errors."""
    from portal.core.keycloak.admin import _call

    rules = policy or current()
    changed: list[str] = []
    realm_name = kc.connection.realm_name
    realm = _call("Reading the realm", lambda: kc.get_realm(realm_name))
    wanted = {
        "ssoSessionIdleTimeout": rules.session_seconds,
        "ssoSessionMaxLifespan": rules.session_max_seconds,
    }
    if any(realm.get(k) != v for k, v in wanted.items()):
        _call("Writing session limits", lambda: kc.update_realm(realm_name, wanted))
        changed.append("session limits")
    second = {PORTAL_FLOW: rules.reverify_seconds, TOOLS_FLOW: rules.reverify_tool_seconds}
    otp = "ALTERNATIVE" if rules.allow_authenticator_app else "DISABLED"
    first_app = "CONDITIONAL" if rules.allow_authenticator_app else "DISABLED"

    def write_execution(flow: str, execution: dict[str, Any], requirement: str, what: str) -> None:
        # The whole execution, its priority included: Keycloak 26.8 resets a priority the body
        # leaves out to 0, which would move the step ahead of its condition.
        body = {**execution, "requirement": requirement}
        _call(
            f"Writing {what}",
            lambda: kc.update_authentication_flow_executions(body, flow),
        )

    for flow in (PORTAL_FLOW, TOOLS_FLOW):
        executions = _call(
            f"Reading {flow}", lambda flow=flow: kc.get_authentication_flow_executions(flow)
        )
        for execution in executions:
            provider = execution.get("providerId")
            if provider == LOA_PROVIDER and execution.get("authenticationConfig"):
                config_id = execution["authenticationConfig"]
                config = _call(
                    "Reading a level condition",
                    lambda config_id=config_id: kc.get_authenticator_config(config_id),
                )
                level = _loa_level(config)
                age = rules.session_max_seconds if level == "1" else second[flow]
                if str(config["config"].get("loa-max-age")) != str(age):
                    config["config"]["loa-max-age"] = str(age)
                    _call(
                        "Writing a level condition",
                        lambda config=config, config_id=config_id: kc.update_authenticator_config(
                            config, config_id
                        ),
                    )
                    changed.append(f"{flow} level {level}")
            elif (
                execution.get("authenticationFlow")
                and execution.get("displayName") == FIRST_APP_FLOWS[flow]
                and execution.get("requirement") != first_app
            ):
                write_execution(flow, execution, first_app, "the first-app branch")
                changed.append(f"{flow} first app {first_app.lower()}")
    # The app as one of the second steps a person already has. Read by each choice's own alias:
    # a browser flow also holds the first-app branch's OTP form, which is REQUIRED (it sets the
    # app up) and is never Settings' to change.
    for flow in CHOICE_FLOWS:
        executions = _call(
            f"Reading {flow}", lambda flow=flow: kc.get_authentication_flow_executions(flow)
        )
        for execution in executions:
            if execution.get("providerId") == OTP_PROVIDER and execution.get("requirement") != otp:
                write_execution(flow, execution, otp, "the authenticator-app step")
                changed.append(f"{flow} app codes {otp.lower()}")
    action = _call("Reading CONFIGURE_TOTP", lambda: kc.get_required_action_by_alias(OTP_ACTION))
    if action is not None and bool(action.get("enabled")) != rules.allow_authenticator_app:
        action["enabled"] = rules.allow_authenticator_app
        _call("Writing CONFIGURE_TOTP", lambda: kc.update_required_action(OTP_ACTION, action))
        changed.append(f"authenticator app {'allowed' if rules.allow_authenticator_app else 'off'}")
    return changed
