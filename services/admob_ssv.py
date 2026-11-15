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
FORCED_REFRESH_COOLDOWN_SECONDS = 60.0
FETCH_FAILURE_BACKOFF_SECONDS = 60.0
VERIFIER_KEYS_TIMEOUT_SECONDS = 10.0
_SIGNATURE_PARAM_NAMES = frozenset({"signature", "key_id"})

# {verifier_url: monotonic_fetched_at, {key_id: public_pem}}
_verifier_key_cache: Dict[str, Tuple[float, Dict[str, str]]] = {}
# {verifier_url: monotonic timestamp of the last network fetch attempt}
_last_fetch_attempt: Dict[str, float] = {}
# {verifier_url: monotonic timestamp of the last failed fetch}
_last_fetch_failure: Dict[str, float] = {}


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


def verifier_keys_are_fresh(url: str) -> bool:
    """Return True while the cached key set for `url` is still within its TTL.

    Callers use this to decide whether an unknown `key_id` may justify a
    refetch: a fresh set was fetched recently, so unknown keys fail closed.
    """
    cached = _verifier_key_cache.get(url)
    return (
        cached is not None
        and time.monotonic() - cached[0] < VERIFIER_KEY_CACHE_TTL_SECONDS
    )


def _may_force_refresh(url: str, now: float) -> bool:
    """Allow at most one forced refresh per cooldown window.

    Any recent network attempt (normal or forced) starts the window, so an
    unauthenticated callback can never turn one inbound request into repeated
    outbound requests, even when the cache was just reloaded.
    """
    last = _last_fetch_attempt.get(url)
    return last is None or now - last >= FORCED_REFRESH_COOLDOWN_SECONDS


async def fetch_verifier_keys(url: str, force_refresh: bool = False) -> Dict[str, str]:
    """Return AdMob verifier public keys keyed by `key_id`.

    Results are cached in memory for `VERIFIER_KEY_CACHE_TTL_SECONDS`. A
    `force_refresh` skips the TTL but is rate limited to one attempt per
    `FORCED_REFRESH_COOLDOWN_SECONDS` (measured from any fetch attempt), so
    unauthenticated callbacks cannot drive unbounded outbound requests. Failed
    fetches back off for `FETCH_FAILURE_BACKOFF_SECONDS`; a stale cached copy
    is served in the meantime when one exists.
    """
    now = time.monotonic()
    cached = _verifier_key_cache.get(url)

    if force_refresh:
        if not _may_force_refresh(url, now):
            return cached[1] if cached is not None else {}
    elif (
        cached is not None
        and now - cached[0] < VERIFIER_KEY_CACHE_TTL_SECONDS
    ):
        return cached[1]

    failure_at = _last_fetch_failure.get(url)
    if (
        failure_at is not None
        and now - failure_at < FETCH_FAILURE_BACKOFF_SECONDS
    ):
        if cached is not None:
            return cached[1]
        raise RuntimeError(
            f"AdMob verifier key fetch for {url} is backing off after a recent failure"
        )

    _last_fetch_attempt[url] = now
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
        _last_fetch_failure[url] = time.monotonic()
        logger.error("Unable to fetch AdMob verifier keys from %s: %s", url, exc)
        if cached is not None:
            return cached[1]
        raise

    _last_fetch_failure.pop(url, None)
    _verifier_key_cache[url] = (now, keys)
    return keys
