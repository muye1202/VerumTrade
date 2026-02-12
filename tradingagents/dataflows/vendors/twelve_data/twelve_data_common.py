import os
import requests

API_BASE_URL = "https://api.twelvedata.com"


class TwelveDataRateLimitError(Exception):
    """Exception raised when Twelve Data API rate limit is exceeded."""


def get_api_key() -> str:
    """Retrieve the API key for Twelve Data from environment variables."""
    api_key = os.getenv("TWELVE_DATA_API_KEY")
    if not api_key:
        raise ValueError("TWELVE_DATA_API_KEY environment variable is not set.")
    return api_key


def _make_api_request(endpoint: str, params: dict) -> dict:
    """Helper function to make API requests and handle Twelve Data error payloads."""
    api_params = params.copy()
    api_params["apikey"] = get_api_key()

    url = f"{API_BASE_URL}/{endpoint.lstrip('/')}"
    response = requests.get(url, params=api_params, timeout=30)
    response.raise_for_status()

    try:
        payload = response.json()
    except Exception as e:
        raise RuntimeError(
            f"Twelve Data API returned non-JSON for endpoint '{endpoint}'."
        ) from e

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Twelve Data API returned an unexpected payload type for endpoint '{endpoint}'."
        )

    if payload.get("status") == "error":
        code = str(payload.get("code", ""))
        message = str(payload.get("message", "Unknown error"))
        if code == "429":
            raise TwelveDataRateLimitError(
                f"Twelve Data rate limit exceeded: {message}"
            )
        raise RuntimeError(
            f"Twelve Data API error on endpoint '{endpoint}': {message} (code={code})"
        )

    return payload

