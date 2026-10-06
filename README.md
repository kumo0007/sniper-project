# Minecraft Username Monitor

A small web app for monitoring usernames you type in yourself and changing them onto Minecraft accounts you own, through the current Microsoft and Minecraft Services APIs.

**First milestone:** the Monitor page includes a live acceptance checklist mapped to the requirements. Client test steps are in [docs/FIRST_MILESTONE.md](docs/FIRST_MILESTONE.md). The verified API path is in [docs/API_FLOW.md](docs/API_FLOW.md).

## What you can do

- Sign in to this app with an operator password. That password is not a Minecraft password.
- Connect about 10 of your own Microsoft/Minecraft accounts with Microsoft's device-code page. The app never asks for the account password.
- Add and remove target usernames, including 3-character and OG names.
- Start and stop monitoring. The monitor keeps running when the browser tab is closed.
- See account state, target state, attempts, errors, and timestamps.
- Remove an account, which deletes its stored session.

A rename is marked successful only after Minecraft's profile endpoint shows that account now has the name.

## Limits that are enforced

- One availability check per account every 16 seconds or slower. This stays under the published cap of about 20 checks per 5 minutes per account. The setting cannot go lower.
- At most 3 username-change requests per minute, and at most 3 accounts in one claim burst.
- After a burst, that name waits 30 seconds before another burst.
- Account cap defaults to 25 and cannot be set above 50. There is no proxy support.

An optional release hint uses Mojang's published note that an old Java username becomes available about 37 days after it is changed. Outside a window around that hint, checks slow down. The clock never claims a name. Only `AVAILABLE` from the official endpoint does.

## Setup

1. Install Python 3.11 or newer.
2. From this folder:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts/generate_env.py
```

`generate_env.py` writes `.env` and prints the operator password once. Keep that password. Do not commit `.env`.

3. Create an Azure app registration:

- Personal Microsoft accounts only.
- Authentication: allow public client flows. No client secret.
- Copy the Application (client) ID into `MICROSOFT_CLIENT_ID` in `.env`.

New Azure apps often get HTTP 403 from Minecraft until the app id is approved for Minecraft Services. That approval is Microsoft's process, described in [docs/API_FLOW.md](docs/API_FLOW.md). This app does not work around a 403.

4. Start it:

```bash
python -m app
```

5. Open `http://127.0.0.1:8000`, sign in with the operator password, connect an account, add a target, then press Start.

6. On Monitor, follow the **First milestone** checklist until it says ready for client sign-off. Use **Check now** on a target for one immediate official availability check.

The monitor runs inside this process. To split them, set `RUN_WORKER=0` and run `python -m app.worker` in a second terminal. A second monitor idles while the first one is heartbeating, so the rate limit is not doubled by accident.

## Try it safely

Use a disposable username and an account you can afford to rename. Each account can change its Java username once every 30 days. Do not point the first test at a valuable 3-character name.

The account must own Minecraft Java and already have a profile. Game Pass accounts sometimes need one launch of the official launcher before a profile exists.

## Production

Put the app behind HTTPS and set `SECURE_COOKIES=1`. A minimal Caddy site:

```
monitor.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

The SQLite file lives in `data/`. Back that directory up; it holds the encrypted sessions and the target list.

Docker is optional:

```bash
docker compose up --build
```

Set a real `APP_PASSWORD`, `SECRET_KEY`, and `TOKEN_ENCRYPTION_KEY` in `.env` first (`python scripts/generate_env.py`). In Docker, terminate HTTPS in front of the container and then set `SECURE_COOKIES=1`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests do not call Microsoft or Minecraft.

## Troubleshooting

- **Azure client id is missing.** Set `MICROSOFT_CLIENT_ID` and restart.
- **HTTP 403 on Minecraft login.** The Azure app is not approved for Minecraft Services yet.
- **No Xbox profile / child account / region.** Xbox returned `XErr`. The account row shows the reason. Fix that on the Microsoft account, then reconnect.
- **No Minecraft profile.** The Microsoft account has not created a Java username yet.
- **Cooldown.** That account changed its name in the last 30 days. It can still check availability. Add another account if you need a rename.
- **Rate limited.** The account backs off. Leave the gap at 16 seconds or higher.
- **Monitor is not heartbeating.** The web process was started with `RUN_WORKER=0` and the worker process is not running.
