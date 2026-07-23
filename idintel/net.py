"""HTTP with retry.

Public research APIs (NCBI especially) return transient 5xx and rate-limit
responses under normal load. Without a retry a single blip silently drops an
entire query from the morning digest, which is worse than a slow run.
"""

from __future__ import annotations

import random
import time

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = 4,
    backoff: float = 1.5,
    **kwargs,
) -> httpx.Response:
    """Issue a request, retrying transient failures with jittered backoff.

    Raises the final ``httpx`` exception, or ``HTTPStatusError`` if the last
    attempt still returned a retryable status.
    """
    last_exc: Exception | None = None

    for attempt in range(attempts):
        try:
            resp = client.request(method, url, **kwargs)
            if resp.status_code not in RETRY_STATUS:
                resp.raise_for_status()
                return resp
            last_exc = httpx.HTTPStatusError(
                f"HTTP {resp.status_code}", request=resp.request, response=resp
            )
            # Honour Retry-After when the server supplies it.
            wait = resp.headers.get("Retry-After")
            delay = float(wait) if wait and wait.isdigit() else backoff ** attempt
        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            last_exc = exc
            delay = backoff ** attempt

        if attempt < attempts - 1:
            time.sleep(delay + random.uniform(0, 0.4))

    assert last_exc is not None
    raise last_exc
