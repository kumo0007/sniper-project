"""Microsoft device login and the Xbox/Minecraft token exchange.

The request shapes match docs/API_FLOW.md. Tokens are returned to the caller
and are never written to logs here.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.minecraft.errors import AuthDeclined, AuthExpired, AuthPending, AuthSlowDown, MinecraftError
from app.services.sanitize import safe_api_message

DEVICE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBOX_USER_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MINECRAFT_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
SCOPE = "XboxLive.signin offline_access"

XBOX_ERRORS = {
    2148916233: "This Microsoft account has no Xbox profile. Sign in at xbox.com once, then connect again.",
    2148916235: "Xbox Live is not available for this account's region.",
    2148916236: "This account needs adult verification with Xbox before it can be used.",
    2148916237: "This account needs adult verification with Xbox before it can be used.",
    2148916238: "This is a child account and must belong to a Microsoft family.",
}


@dataclass
class DeviceCode:
    user_code: str
    device_code: str
    verification_uri: str
    message: str
    interval: int
    expires_in: int


@dataclass
class MsaTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int


@dataclass
class MinecraftTokens:
    access_token: str
    expires_in: int


def _json_body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def request_device_code(http: httpx.AsyncClient, client_id: str) -> DeviceCode:
    response = await http.post(
        DEVICE_URL,
        data={"client_id": client_id, "scope": SCOPE},
        headers={"Accept": "application/json"},
    )
    body = _json_body(response)
    if response.status_code != 200 or "device_code" not in body:
        raise MinecraftError(
            safe_api_message(body) or "Microsoft did not start a device login.",
            kind="auth_error",
            http_status=response.status_code,
        )
    return DeviceCode(
        user_code=str(body.get("user_code") or ""),
        device_code=str(body["device_code"]),
        verification_uri=str(body.get("verification_uri") or "https://www.microsoft.com/link"),
        message=str(body.get("message") or ""),
        interval=max(5, int(body.get("interval") or 5)),
        expires_in=int(body.get("expires_in") or 900),
    )


async def poll_device_token(http: httpx.AsyncClient, client_id: str, device_code: str) -> MsaTokens:
    response = await http.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
        },
        headers={"Accept": "application/json"},
    )
    body = _json_body(response)
    if response.status_code == 200 and body.get("access_token"):
        return MsaTokens(
            access_token=str(body["access_token"]),
            refresh_token=str(body["refresh_token"]) if body.get("refresh_token") else None,
            expires_in=int(body.get("expires_in") or 3600),
        )
    error = str(body.get("error") or "")
    if error == "authorization_pending":
        raise AuthPending()
    if error == "slow_down":
        raise AuthSlowDown()
    if error == "authorization_declined":
        raise AuthDeclined()
    if error in {"expired_token", "bad_verification_code"}:
        raise AuthExpired()
    raise MinecraftError(
        safe_api_message(body) or "Microsoft sign-in failed.",
        kind="auth_error",
        http_status=response.status_code,
    )


async def refresh_msa(http: httpx.AsyncClient, client_id: str, refresh_token: str) -> MsaTokens:
    response = await http.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": SCOPE,
        },
        headers={"Accept": "application/json"},
    )
    body = _json_body(response)
    if response.status_code == 200 and body.get("access_token"):
        return MsaTokens(
            access_token=str(body["access_token"]),
            refresh_token=str(body.get("refresh_token") or refresh_token),
            expires_in=int(body.get("expires_in") or 3600),
        )
    error = str(body.get("error") or "")
    kind = "auth_error" if error in {"invalid_grant", "interaction_required"} else "error"
    raise MinecraftError(
        safe_api_message(body) or "The Microsoft session could not be refreshed. Connect the account again.",
        kind=kind,
        http_status=response.status_code,
    )


def _xbox_error(body: dict, status: int) -> MinecraftError:
    code = body.get("XErr")
    try:
        code_int = int(code) if code is not None else None
    except (TypeError, ValueError):
        code_int = None
    if code_int in XBOX_ERRORS:
        message = XBOX_ERRORS[code_int]
    else:
        message = safe_api_message(body) or "Xbox authentication failed."
    return MinecraftError(message, kind="auth_error", http_status=status)


async def _xbox_token(http: httpx.AsyncClient, url: str, payload: dict) -> tuple[str, str]:
    response = await http.post(url, json=payload, headers={"Accept": "application/json"})
    body = _json_body(response)
    if response.status_code != 200 or "Token" not in body:
        raise _xbox_error(body, response.status_code)
    try:
        user_hash = body["DisplayClaims"]["xui"][0]["uhs"]
    except (KeyError, IndexError, TypeError) as exc:
        raise MinecraftError("Xbox did not return a user hash.", kind="auth_error") from exc
    return str(body["Token"]), str(user_hash)


async def exchange_minecraft(http: httpx.AsyncClient, msa_access_token: str) -> MinecraftTokens:
    xbl_token, _user_hash = await _xbox_token(
        http,
        XBOX_USER_URL,
        {
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={msa_access_token}",
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        },
    )
    xsts_token, user_hash = await _xbox_token(
        http,
        XSTS_URL,
        {
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        },
    )
    response = await http.post(
        MINECRAFT_LOGIN_URL,
        json={"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"},
        headers={"Accept": "application/json"},
    )
    body = _json_body(response)
    if response.status_code == 403:
        raise MinecraftError(
            "Minecraft rejected this Azure application (HTTP 403). "
            "The app registration needs Minecraft Services API access. See docs/API_FLOW.md.",
            kind="auth_error",
            http_status=403,
        )
    if response.status_code != 200 or not body.get("access_token"):
        raise MinecraftError(
            safe_api_message(body) or "Minecraft did not accept the Xbox login.",
            kind="auth_error",
            http_status=response.status_code,
        )
    return MinecraftTokens(
        access_token=str(body["access_token"]),
        expires_in=int(body.get("expires_in") or 86400),
    )
