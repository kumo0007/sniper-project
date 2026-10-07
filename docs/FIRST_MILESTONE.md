# First milestone — acceptance checklist

This matches section 14 of the project requirements. Use it to prove the core monitoring and legitimate claim workflow before any later work.

Verified API details and limits: [API_FLOW.md](API_FLOW.md).

## What this milestone must prove

1. You can open the web app.
2. You can configure your own Minecraft accounts without sending passwords to a developer.
3. You can add a target username.
4. The app can monitor that target.
5. The app can detect availability with a current, reliable Minecraft method.
6. The app can start the official Minecraft username-change operation.
7. The result is clearly shown.
8. Authentication and other errors are understandable.
9. You can run the demo yourself with your own accounts.
10. The design does not need thousands of accounts or proxies.

## Setup for the demo

1. Install Python 3.11+, then from the project folder:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts/generate_env.py
```

2. Create an Azure app registration for **personal Microsoft accounts**, enable **public client / device code** flows, and put the Application (client) ID in `.env` as `MICROSOFT_CLIENT_ID`.

3. If Minecraft login returns HTTP 403, the Azure app still needs Minecraft Services API access. That is Microsoft's approval process. This app does not bypass it.

4. Start the demo:

```bash
python -m app
```

5. Open `http://127.0.0.1:8000` and sign in with the operator password printed by `generate_env.py`.

## Client test script

Use a **disposable** username and an account you can afford to rename. Each Java account can change its name once every 30 days.

| Step | Action | What you should see |
| --- | --- | --- |
| 1 | Open the site and sign in | Monitor page loads |
| 2 | Accounts → choose device sign-in or bearer token | Microsoft device code, or paste a Minecraft Services access token; no Minecraft password field |
| 3 | Finish auth | Account status becomes Ready or Cooldown; Minecraft name appears. Bearer tokens show only a fingerprint afterward |
| 4 | Targets → add a username | Target appears with state Unavailable / Waiting |
| 5 | Press Start | Sniper status shows Running |
| 6 | Wait one check cycle (about 16+ seconds per account) | Target `last checked` updates; Activity shows `availability_check` |
| 7 | If the name is taken | State stays Unavailable; that still proves official detection |
| 8 | If the name returns AVAILABLE | State becomes Attempting, then Successful only after a profile confirm |
| 9 | Force a bad session (optional) | Reconnect / auth error text appears on the account and in Activity |
| 10 | Remove an account | Session data is deleted |

The Monitor page also shows a live **First milestone** checklist. When every item is green, the first payment milestone's technical proof is ready.

## States you should see

Targets:

- unavailable
- checking
- attempting
- successful
- failed
- authentication_error
- rate_limited
- unknown_error

Accounts:

- needs_login
- authenticating
- ready
- cooldown
- auth_error
- error
- blocked
- disabled

## Explicit non-goals for this milestone

- Automatic discovery of every upcoming drop
- NameMC as the claim API
- Proxy farms / thousands of accounts
- Perfect UI polish
- Production HTTPS domain (local demo is enough for the first proof; production notes are in the README)

## Pass / fail

**Pass:** the checklist on the Monitor page is complete, availability checks are logged from the official endpoint, and either a rename-ready account is connected or a claim attempt/result is visible.

**Fail:** the app cannot connect a real account you own, cannot check availability through Minecraft Services, or claims success without a confirming profile read.
