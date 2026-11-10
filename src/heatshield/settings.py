"""Explicit runtime configuration for the synthetic workflow application."""

import os
from dataclasses import dataclass, field
from pathlib import Path

DEMO_JWT_SECRET = "heatshield-synthetic-demo-only-signing-key-change-before-deployment"


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./data/heatshield.db"
    demo_mode: bool = True
    demo_content: bool = False
    jwt_secret: str = field(default=DEMO_JWT_SECRET, repr=False)
    token_lifetime_minutes: int = 60
    jwt_issuer: str = "heatshield-os"
    jwt_audience: str = "heatshield-web"
    frontend_dist: Path = Path("web/dist")

    def __post_init__(self):
        if not self.database_url:
            raise ValueError("HEATSHIELD_DATABASE_URL must not be empty")
        if not self.demo_mode and (
            self.jwt_secret == DEMO_JWT_SECRET or len(self.jwt_secret.encode()) < 32
        ):
            raise ValueError(
                "Non-demo mode requires HEATSHIELD_JWT_SECRET containing at least "
                "32 bytes of independently generated secret material"
            )
        if not self.jwt_secret:
            raise ValueError("HEATSHIELD_JWT_SECRET must not be empty")
        if not 1 <= self.token_lifetime_minutes <= 1440:
            raise ValueError("Token lifetime must be between 1 and 1440 minutes")
        object.__setattr__(self, "frontend_dist", Path(self.frontend_dist))

    @classmethod
    def from_env(cls) -> "Settings":
        demo_value = os.getenv("HEATSHIELD_DEMO", "true").strip().lower()
        if demo_value not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("HEATSHIELD_DEMO must be true or false")
        demo = demo_value in {"true", "1", "yes"}
        content_value = os.getenv("HEATSHIELD_DEMO_CONTENT", "true" if demo else "false")
        content_value = content_value.strip().lower()
        if content_value not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("HEATSHIELD_DEMO_CONTENT must be true or false")
        return cls(
            database_url=os.getenv("HEATSHIELD_DATABASE_URL", cls.database_url),
            demo_mode=demo,
            demo_content=demo and content_value in {"true", "1", "yes"},
            jwt_secret=os.getenv("HEATSHIELD_JWT_SECRET", DEMO_JWT_SECRET if demo else ""),
            token_lifetime_minutes=int(os.getenv("HEATSHIELD_TOKEN_LIFETIME_MINUTES", "60")),
            jwt_issuer=os.getenv("HEATSHIELD_JWT_ISSUER", "heatshield-os"),
            jwt_audience=os.getenv("HEATSHIELD_JWT_AUDIENCE", "heatshield-web"),
            frontend_dist=Path(os.getenv("HEATSHIELD_FRONTEND_DIST", "web/dist")),
        )
