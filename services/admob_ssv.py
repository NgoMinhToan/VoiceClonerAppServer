"""AdMob server-side verification (SSV) helpers.

AdMob signs the reward callback query with ECDSA-SHA256 over the raw query
string with the `signature` and `key_id` parameters removed, preserving the
order of the remaining parameters. Public verifier keys are published as JSON
by Google and cached in memory.
"""

import base64
import logging
import time
from typing import Dict, Tuple

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_public_key

logger = logging.getLogger("VoiceClonerAppServer.AdMobSSV")

VERIFIER_KEY_CACHE_TTL_SECONDS = 3600
VERIFIER_KEYS_TIMEOUT_SECONDS = 10.0
_SIGNATURE_PARAM_NAMES = frozenset({"signature", "key_id"})

# {verifier_url: (monotonic_fetched_at, {key_id: public_pem})}
_verifier_key_cache: Dict[str, Tuple[float, Dict[str, str]]] = {}


def build_signed_content(raw_query: str) -> str:
    """Rebuild the signed content from a raw query string.

    Drops `signature` and `key_id` while preserving the order and the exact
    encoding of every other parameter.
    """
    if not raw_query:
        return ""

    signed_parts = []
    for part in raw_query.split("&"):
        if not part:
            continue
        name = part.split("=", 1)[0]
        if name in _SIGNATURE_PARAM_NAMES:
            continue
        signed_parts.append(part)
    return "&".join(signed_parts)


def verify_signature(
    raw_query: str, signature: str, key_id: str, keys: Dict[str, str]
) -> bool:
    """Verify an AdMob SSV signature against the published public key."""
    pem = keys.get(str(key_id)) if keys else None
    if not signature or not key_id or not pem:
        return False

    try:
        public_key = load_pem_public_key(pem.encode("utf-8"))
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            logger.warning("AdMob verifier key %s is not an EC public key", key_id)
            return False
        public_key.verify(
            base64.b64decode(signature, validate=True),
            build_signed_content(raw_query).encode("utf-8"),
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


async def fetch_verifier_keys(url: str, force_refresh: bool = False) -> Dict[str, str]:
    """Return AdMob verifier public keys keyed by `key_id`.

    Results are cached in memory for `VERIFIER_KEY_CACHE_TTL_SECONDS`. Pass
    `force_refresh=True` when a callback references an unknown `key_id` so the
    cache is re-fetched instead of rejecting a freshly rotated key. If the
    fetch fails, a stale cached copy is served when one exists.
    """
    now = time.monotonic()
    cached = _verifier_key_cache.get(url)
    if (
        not force_refresh
        and cached is not None
        and now - cached[0] < VERIFIER_KEY_CACHE_TTL_SECONDS
    ):
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=VERIFIER_KEYS_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()

        keys = {
            str(entry["keyId"]): str(entry["pem"])
            for entry in payload.get("keys", [])
            if entry.get("keyId") is not None and entry.get("pem")
        }
    except Exception as exc:
        logger.error("Unable to fetch AdMob verifier keys from %s: %s", url, exc)
        if cached is not None:
            return cached[1]
        raise

    _verifier_key_cache[url] = (now, keys)
    return keys
