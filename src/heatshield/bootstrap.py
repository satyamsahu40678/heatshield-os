"""Idempotent synthetic identities; never call this for a non-demo deployment."""

from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_password, public_user
from .identity import Organization, User

DEMO_PASSWORD = "HeatShieldDemo2026!"
DEMO_ORGANIZATION_ID = "demo-organization"


@lru_cache(maxsize=1)
def _demo_password_hash() -> str:
    # Public synthetic accounts deliberately share one fixture credential.
    # Real account creation always calls hash_password with an independent salt.
    return hash_password(DEMO_PASSWORD)


def seed_demo(session: Session, *, demo_mode: bool = True) -> list[dict]:
    if not demo_mode:
        raise ValueError("Synthetic demo accounts cannot be seeded outside demo mode")
    for organization_id, name in [
        (DEMO_ORGANIZATION_ID, "HeatShield synthetic demonstration"),
        ("second-demo-organization", "Separate synthetic organization"),
    ]:
        if session.get(Organization, organization_id) is None:
            session.add(Organization(id=organization_id, name=name))
    session.flush()
    accounts = [
        ("admin@example.test", "Demo administrator", "admin", None),
        ("safety@example.test", "Amina · safety officer", "safety_officer", None),
        ("supervisor@example.test", "Ravi · supervisor", "supervisor", None),
        ("worker@example.test", "Demo worker 01", "worker", "worker-01"),
    ] + [
        (
            f"worker-{number:02}@example.test",
            f"Demo worker {number:02}",
            "worker",
            f"worker-{number:02}",
        )
        for number in range(1, 7)
    ]
    users = []
    for email, display_name, role, worker_id in accounts:
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                organization_id=DEMO_ORGANIZATION_ID,
                email=email,
                display_name=display_name,
                role=role,
                worker_id=worker_id,
                password_hash=_demo_password_hash(),
            )
            session.add(user)
        users.append(user)
    session.flush()
    return [public_user(user) for user in users]
