"""Who did a thing, as one value instead of two nullable columns at every call site.

`events.recorded_by_user_id` and `events.recorded_by_system` are mutually exclusive by CHECK
constraint, and the same either/or shows up on tasks, sources and audit_log. Passing an `Actor`
means a service never has to remember which of the pair to fill in, and -- the reason this type
is load-bearing rather than a convenience -- it gives the status chokepoint one thing to ask
"is a person accountable for this?" of, which is the whole of non-negotiable #4.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class Actor:
    user_id: UUID | None = None
    system: str | None = None

    def __post_init__(self) -> None:
        if (self.user_id is None) == (self.system is None):
            raise ValueError("An Actor is exactly one of a user or a named system, never both.")

    @classmethod
    def human(cls, user: Any) -> "Actor":
        """`user` is a core.User, a records.RecordsUser, or the uuid the two share."""
        if isinstance(user, UUID):
            return cls(user_id=user)
        user_id = getattr(user, "pk", None)
        if not isinstance(user_id, UUID):
            raise ValueError(f"Cannot read a user uuid from {user!r}.")
        return cls(user_id=user_id)

    @classmethod
    def for_system(cls, name: str) -> "Actor":
        """`name` names the process, e.g. "postmark_webhook", "overdue_sweep"."""
        if not name:
            raise ValueError("A system actor needs a name.")
        return cls(system=name)

    @property
    def is_human(self) -> bool:
        return self.user_id is not None

    def recorded_by(self) -> dict[str, Any]:
        if self.user_id is not None:
            return {"recorded_by_user_id": self.user_id, "recorded_by_system": None}
        return {"recorded_by_user_id": None, "recorded_by_system": self.system}
