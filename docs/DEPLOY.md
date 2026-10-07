# Deploy online

This app needs a **long-running process** and a **persistent disk** for SQLite. Do not use Vercel for the sniper itself.

Supported options:

1. **Cloudflare quick tunnel** (fast demo URL pointing at your local machine)
2. **Railway** (recommended durable host)
3. **Render** (Docker/Python web service + disk)

## Environment variables (all hosts)

| Variable | Required | Notes |
| --- | --- | --- |
| `APP_PASSWORD` | yes | Operator UI password, 10+ characters |
| `SECRET_KEY` | yes | Session signing secret, 16+ characters |
| `TOKEN_ENCRYPTION_KEY` | yes | Fernet key from `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `MICROSOFT_CLIENT_ID` | for accounts | Azure app id |
| `HOST` | yes online | `0.0.0.0` |
| `PORT` | often injected | Platform sets this |
| `RUN_WORKER` | yes | `1` |
| `SECURE_COOKIES` | yes on HTTPS | `1` |
| `DATABASE_URL` | recommended | Point at a mounted volume path |
| `MAX_ACCOUNTS` | optional | Default `25` |

Never commit `.env`.

## Railway

```bash
npx @railway/cli login
npx @railway/cli init
npx @railway/cli up
npx @railway/cli variables set APP_PASSWORD=... SECRET_KEY=... TOKEN_ENCRYPTION_KEY=... HOST=0.0.0.0 RUN_WORKER=1 SECURE_COOKIES=1
npx @railway/cli domain
```

Add a volume mounted at `/data` and set:

```text
DATABASE_URL=sqlite:////data/sniper.db
```

## Render

1. Push this repo to GitHub.
2. In Render: New → Blueprint → select the repo (`render.yaml`).
3. Set `TOKEN_ENCRYPTION_KEY` and `MICROSOFT_CLIENT_ID` in the dashboard.
4. Copy the generated `APP_PASSWORD` from Render env vars and sign in.

The blueprint attaches a 1 GB disk at `/var/data`.

## Cloudflare quick tunnel (temporary demo)

With the app already running locally on port 8000:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

The printed `https://*.trycloudflare.com` URL reaches your machine. It stops when the tunnel or PC stops. Use Railway/Render for a durable demo.
