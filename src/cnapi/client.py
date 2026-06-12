import os
import time
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from requests_oauthlib import OAuth1Session


class CNClient:
    """Thin wrapper around OAuth‑signed requests with basic 429 back‑off."""

    def __init__(self) -> None:
        # Ensure .env is loaded even if the caller forgot
        load_dotenv()

        consumer_key = os.getenv("X_API_KEY")
        consumer_secret = (
            os.getenv("X_API_KEY_SECRET")  # preferred
            or os.getenv("X_API_SECRET")   # fallback
        )
        access_token = os.getenv("X_ACCESS_TOKEN")
        access_secret = os.getenv("X_ACCESS_TOKEN_SECRET")

        missing = [
            name
            for name, val in [
                ("X_API_KEY", consumer_key),
                ("X_API_KEY_SECRET / X_API_SECRET", consumer_secret),
                ("X_ACCESS_TOKEN", access_token),
                ("X_ACCESS_TOKEN_SECRET", access_secret),
            ]
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"Missing required env vars: {', '.join(missing)}. "
                "Check your .env or GitHub Action secrets."
            )

        # Persistent signed session
        self.session = OAuth1Session(
            client_key=consumer_key,
            client_secret=consumer_secret,
            resource_owner_key=access_token,
            resource_owner_secret=access_secret,
        )

    # ── Internal helper with naive 429 back‑off ─────────────────
    def _request(
        self, method: str, url: str, retry: int = 3, **kwargs
    ) -> Dict[str, Any]:
        """
        Wrapper around session.request that:
          • Raises on any non‑429 HTTP error.
          • Prints the JSON error body for easier debugging.
          • Sleeps and retries (up to `retry` times) on 429.
        """
        for attempt in range(retry + 1):
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                # Transient network failure — most often DNS not ready right
                # after laptop wake (launchd fires missed runs on wake). This
                # killed whole scheduled runs before; wait and retry instead.
                if attempt >= retry:
                    raise
                wait = 20 * (attempt + 1)
                print(f"⚠️  network error ({type(e).__name__}); retrying in {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code != 429:
                if resp.status_code >= 400:
                    # Surface the exact error payload
                    print(f"\n🔴 {resp.status_code} {resp.reason} → {resp.text}\n")
                resp.raise_for_status()
                return resp.json() if resp.text else {}

            # 429: rate‑limited → back‑off until reset
            reset_ts = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
            time.sleep(max(reset_ts - time.time(), 1))

        # If we get here, we exceeded max retries
        resp.raise_for_status()
