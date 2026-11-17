"""Verify desktop account identities against a trusted Supabase project."""

import re
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException


async def verify_account(
    client: httpx.AsyncClient,
    project_url: str,
    public_key: str,
    authorization: str,
) -> dict[str, str]:
    """Verify a bearer token; project_url must come from trusted app configuration."""
    project = urlsplit(project_url)
    if (
        project.scheme != "https"
        or not project.hostname
        or not re.fullmatch(r"[a-z0-9-]+\.supabase\.co", project.hostname)
        or project.netloc != project.hostname
        or project.path not in ("", "/")
        or project.query
        or project.fragment
    ):
        raise HTTPException(status_code=503, detail="Invalid desktop account service configuration")
    if not isinstance(public_key, str) or not public_key or len(public_key) > 2048 or re.search(r"\s", public_key):
        raise HTTPException(status_code=503, detail="Invalid desktop account service configuration")
    match = re.fullmatch(r"Bearer ([^\s]+)", authorization, re.IGNORECASE)
    if not match or len(match.group(1)) > 8192:
        raise HTTPException(status_code=401, detail="Account authentication required")

    try:
        response = await client.get(
            f"https://{project.hostname}/auth/v1/user",
            headers={"apikey": public_key, "Authorization": f"Bearer {match.group(1)}"},
            follow_redirects=False,
        )
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail="Account verification is unavailable") from error
    if response.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Account authentication failed")
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Account verification is unavailable")
    try:
        data = response.json()
        identifier = data.get("id") if isinstance(data, dict) else None
        if not isinstance(identifier, str):
            raise ValueError("Missing account ID")
        account_id = str(UUID(identifier))
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=503, detail="Invalid account verification response") from error
    return {"id": account_id}
