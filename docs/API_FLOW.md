# Verified Minecraft authentication and username flow

This note was checked against the public Minecraft/Microsoft documentation before the claim worker was written. It describes the only network path this project uses. If Minecraft changes an endpoint, update this file and the client together.

Sources:

- [Microsoft Authentication Scheme (wiki.vg)](https://wiki.vg/Microsoft_Authentication_Scheme)
- [Microsoft authentication (Minecraft Wiki)](https://minecraft.wiki/w/Microsoft_authentication)
- [Mojang API (wiki.vg)](https://wiki.vg/Mojang_API)
- [Mojang API (Minecraft Wiki)](https://minecraft.wiki/w/Mojang_API)
- [Microsoft identity platform device code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)

NameMC is not part of this path. It has no role in authentication or claiming.

## What the operator proves

The operator signs in to their own Microsoft account with the device-code flow, **or** pastes a Minecraft Services bearer token obtained through that same official flow elsewhere. The app never asks for a Minecraft or Microsoft password. With device code, a refresh token is stored encrypted so the session can be renewed. The Minecraft bearer token is used only for that same account's profile.

## Bearer token option (added credential entry)

Supported token type: the Minecraft Services `access_token` returned by

`POST https://api.minecraftservices.com/authentication/login_with_xbox`

That token is a JWT. Clients send it as:

`Authorization: Bearer <access_token>`

It is the same credential the official profile, availability, and username-change endpoints already require. Arbitrary strings, Microsoft Graph tokens, Xbox user tokens alone, NameMC cookies, and launcher refresh tokens that have not been exchanged for a Minecraft Services access token are **not** accepted.

| Property | Verified behavior |
| --- | --- |
| Issuer / use | Minecraft Services after Xbox login |
| Typical lifetime | About 24 hours (`expires_in` ≈ 86400). JWT `exp` is read when present |
| Scope | Profile read, name availability, username change for that account |
| Refresh | This token itself has no refresh grant. Device-code accounts renew via the Microsoft refresh token. Bearer-token accounts must paste a new Minecraft access token when it expires or Minecraft returns 401 |
| Validation | Before Ready: `GET /minecraft/profile` then `GET /minecraft/profile/namechange` |
| Storage | Encrypted at rest. UI shows only a short fingerprint hint after submit. Never logged |

This option does not bypass Minecraft authentication, rate limits, or rename cooldowns. It only lets the client supply a credential they already obtained through the supported flow, without typing a password into this app.

## 1. Azure application

The operator creates their own app registration and puts its client id in `MICROSOFT_CLIENT_ID`.

- Account type: personal Microsoft accounts.
- Allow public client flows (device code). No client secret.
- Tenant: `consumers`. `common` and organization tenants fail for `XboxLive.signin`.

New Azure apps are not automatically allowed to call Minecraft Services. If `login_with_xbox` returns HTTP 403, the app id has to be approved through Minecraft's API access process. Until that approval exists, login cannot finish. This project does not work around that 403.

## 2. Device code

`POST https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode`

Form body:

- `client_id`: the operator's Azure client id
- `scope`: `XboxLive.signin offline_access`

The response gives `user_code`, `verification_uri` (normally `https://www.microsoft.com/link`), `device_code`, `interval`, and `expires_in`. The UI shows the user code and link. The device code stays encrypted on the server.

The app then polls, waiting at least the returned `interval` each time:

`POST https://login.microsoftonline.com/consumers/oauth2/v2.0/token`

- `grant_type`: `urn:ietf:params:oauth:grant-type:device_code`
- `client_id`
- `device_code`

`authorization_pending` means keep waiting. `slow_down` means wait longer. `authorization_declined` and `expired_token` stop the attempt. The app never polls faster than Microsoft asks.

## 3. Refresh

`POST https://login.microsoftonline.com/consumers/oauth2/v2.0/token`

- `grant_type`: `refresh_token`
- `refresh_token`
- `client_id`
- `scope`: `XboxLive.signin offline_access`

`invalid_grant` means the operator must sign in again. The stored Minecraft access token lasts about 24 hours; the app refreshes shortly before it expires.

## 4. Xbox Live, then Minecraft

`POST https://user.auth.xboxlive.com/user/authenticate`

```json
{
  "Properties": {
    "AuthMethod": "RPS",
    "SiteName": "user.auth.xboxlive.com",
    "RpsTicket": "d=<microsoft access token>"
  },
  "RelyingParty": "http://auth.xboxlive.com",
  "TokenType": "JWT"
}
```

The `d=` prefix is required for this v2 token. The response `Token` is the Xbox Live user token. `DisplayClaims.xui[0].uhs` is the user hash.

`POST https://xsts.auth.xboxlive.com/xsts/authorize`

```json
{
  "Properties": {
    "SandboxId": "RETAIL",
    "UserTokens": ["<xbox live token>"]
  },
  "RelyingParty": "rp://api.minecraftservices.com/",
  "TokenType": "JWT"
}
```

Some accounts fail here with an `XErr` code (no Xbox profile, region block, child account, adult verification). Those are shown as account errors. They are not retried in a loop.

`POST https://api.minecraftservices.com/authentication/login_with_xbox`

```json
{
  "identityToken": "XBL3.0 x=<user hash>;<xsts token>"
}
```

The response `access_token` is the Minecraft bearer token (`expires_in` is typically 86400). HTTP 403 here usually means the Azure app is not approved for Minecraft Services.

## 5. Profile and rename eligibility

`GET https://api.minecraftservices.com/minecraft/profile`

Header: `Authorization: Bearer <minecraft access token>`

HTTP 404 means this Microsoft account has no Minecraft Java profile yet. The account must own Minecraft and have created a username once. Game Pass profiles sometimes need one launcher login before a profile exists.

`GET https://api.minecraftservices.com/minecraft/profile/namechange`

```json
{
  "createdAt": "2019-03-02T05:44:42Z",
  "changedAt": "2020-12-02T03:11:01Z",
  "nameChangeAllowed": false
}
```

A Java username can be changed once every 30 days. `nameChangeAllowed` must be true before this app sends a rename. Accounts on cooldown can still check availability; they are not asked to rename.

## 6. Availability (the only claim signal)

`GET https://api.minecraftservices.com/minecraft/profile/name/<name>/available`

Authenticated. Response:

```json
{ "status": "AVAILABLE" }
```

`DUPLICATE` means the name is taken. `NOT_ALLOWED` means it fails the name rules. Anything else is an error, not a claim signal.

Documented limit: about **20 requests per 5 minutes per account** (Minecraft Wiki). This app's hard floor is one availability check per account every **16 seconds**, which stays under that cap (about 19 requests per 5 minutes). The floor is enforced in code. Configuration cannot lower it. HTTP 429 backs that account off and is not bypassed.

The unauthenticated profile lookup (`api.mojang.com` or `api.minecraftservices.com/minecraft/profile/lookup/name/`) can say a name has no current owner. That is not the same as "available to claim" (invalid names and names still inside the release delay both look empty). This app does not claim from that lookup.

## 7. Change name

`PUT https://api.minecraftservices.com/minecraft/profile/name/<name>`

Header: `Authorization: Bearer <minecraft access token>`

Documented results:

| HTTP | Meaning |
| --- | --- |
| 200 | The profile object, including the new name |
| 400 | Invalid name (not 3–16 characters of `A–Z`, `a–z`, `0–9`, `_`) |
| 401 | Bearer token rejected |
| 403 | Not allowed. `details.status` of `DUPLICATE` means the name is taken or not released |
| 429 | Too many rename requests |
| 5xx | Service failure |

Wiki.vg also notes a rename endpoint limit on the order of 20 requests a minute, and that repeating 429s can suspend the account's online services. This app allows at most **3 rename requests per minute** across all accounts, at most **3 accounts** in one claim burst, and a **30 second** pause before another burst for the same name. A 429 stops the burst.

A 200 from the rename call is not treated as success by itself. The app then calls `GET /minecraft/profile` and only marks the target successful when that profile's `name` matches. If the confirm request fails, the state stays unknown and a later cycle confirms before sending another rename.

## 8. The 37-day release note

Mojang's published Java username rule is that a previous name becomes available about **37 days** after the owner changes away from it, and each account can change once every **30 days**. The operator can store an optional release hint after checking NameMC or similar themselves.

The hint only chooses a slow poll (default 10 minutes) versus the full safe rate inside a window around that time. The app does not claim because a clock expired. It claims only after the authenticated availability endpoint returns `AVAILABLE` and a following profile read confirms the new name.

## 9. Behaviors this project will not implement

- Password login, or any path that takes the operator's Microsoft password
- NameMC (or any unofficial site) as the claim or authentication API
- Proxies, account farms, or more than the configured account cap (default 25, hard ceiling 50)
- Poll intervals, parallelism, or retry loops that exceed the limits above
- Pretending to be the official Minecraft launcher
- A claim result that was not confirmed from the account profile
