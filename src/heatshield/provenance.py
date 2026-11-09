"""Canonical hashes bind results to all scenario and policy inputs."""

import hashlib
import json

from .models import Contract


def content_hash(model: Contract) -> str:
    encoded = json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
