"""Configuration is read entirely from environment variables — locally from a
gitignored .env (see .env.example), in the cloud from variables the platform
injects via Key Vault-backed secretRefs and a managed identity. Application
code is identical either way: it only ever calls os.getenv, never a vault SDK.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    database_url: str
    min_magnitude: float
    notable_min_magnitude: float
    mailer: str
    digest_to: str
    digest_from: str
    graph_tenant_id: Optional[str]
    graph_client_id: Optional[str]
    graph_client_secret: Optional[str]


def load_config() -> Config:
    return Config(
        database_url=_require("DATABASE_URL"),
        min_magnitude=float(os.getenv("MIN_MAGNITUDE", "2.5")),
        notable_min_magnitude=float(os.getenv("NOTABLE_MIN_MAGNITUDE", "4.5")),
        mailer=os.getenv("MAILER", "none"),
        digest_to=os.getenv("DIGEST_TO", ""),
        digest_from=os.getenv("DIGEST_FROM", ""),
        graph_tenant_id=os.getenv("GRAPH_TENANT_ID"),
        graph_client_id=os.getenv("GRAPH_CLIENT_ID"),
        graph_client_secret=os.getenv("GRAPH_CLIENT_SECRET"),
    )


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value
