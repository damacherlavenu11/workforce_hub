# Free Render + Neon pilot

The default `render.yaml` deploys one **Render Free** web service using `Dockerfile.free`. Both the frontend and backend use the same HTTPS URL. Accounts, invitations, timesheets, sessions and audit records live in **Neon PostgreSQL**. Document uploads remain disabled until malware scanning and private object storage are available. Supabase is optional for future file storage and is not required for this pilot.

No cloud resources have been created by these configuration changes.

## 1. Create the Neon database

1. Sign in to https://console.neon.tech and create a project on the Free plan.
2. Name the project `workforce-hub`. Choose a region close to your Render service.
3. Use the default database (`neondb`) and role, or create your own database named `workforce_hub`.
4. Click **Connect**. Select the desired branch, database and role.
5. For this small pilot, disable **Connection pooling** and copy the **direct PostgreSQL connection string**. This preserves the startup `search_path` option used by the current database adapter. Retain `sslmode=require` and any supplied `channel_binding` parameter.
6. Keep the connection string private. It contains the database password; do not commit it or paste it into chat.

Example shape only:

```text
postgresql://ROLE:PASSWORD@NEON_HOST/neondb?sslmode=require
```

No tables need to be created manually. The backend initializes a private `workforce` schema on first startup. The selected role must be allowed to create that schema and its tables.

## 2. Create the Render project and web service

Starting at the dashboard shown in your screenshot:

1. Click **+ New → Project**. Name it `Workforce Hub`. A project groups services; it does not run the application by itself.
2. Click **+ New → Web Service**.
3. Connect your GitHub account if needed and select `damacherlavenu11/workforce_hub`.
4. Configure the service:

| Field | Value |
|---|---|
| Name | `workforce-hub`, or a unique variant |
| Project | `Workforce Hub` |
| Branch | `main` |
| Language / Runtime | `Docker` |
| Region | Close to your Neon database |
| Root Directory | Leave empty |
| Dockerfile Path | `./Dockerfile.free` |
| Docker Build Context | `.` if shown |
| Docker Command | Leave empty; use the image's default command |
| Instance Type / Plan | **Free** |
| Health Check Path | `/healthz` |

Use `Dockerfile.free`, not `Dockerfile` (the latter includes the future paid scanner). Do not add a persistent disk or Render database. You don't need a separate Static Site for this frontend.

5. Add the environment variables below.
6. Click **Deploy Web Service** or **Create Web Service**, as shown by Render.
7. Wait for the Docker build and deployment to show **Live**. Open the assigned `https://...onrender.com` URL.

## 3. Render environment variables

| Key | Value |
|---|---|
| `WORKFORCE_ENV` | `production` |
| `HOSTING_TIER` | `free` |
| `WORKFORCE_DATA` | `/tmp/workforce` |
| `DATABASE_URL` | Full direct Neon connection string |
| `TRUST_PROXY` | `1` |

Render supplies `RENDER_EXTERNAL_URL`; the backend uses it as its HTTPS origin automatically. If you later add a custom domain, set `PUBLIC_ORIGIN` to its exact HTTPS origin with no path.

Leave `DOCUMENT_SCANNER` unset. No `SUPABASE_*` variables are required. If they were entered previously, remove them for this Neon-only deployment. Keep database credentials only in backend environment variables.

Render sets `PORT` automatically, and Gunicorn reads it. `/tmp/workforce` is temporary staging, not your database. The application refuses free production startup without a remote PostgreSQL database.

## 4. Create the first employer account

Render Free does not provide a service shell. Create the account from your local terminal using the **same Neon database URL**. It will then be available in the remote application.

In the repository folder, on macOS zsh:

```sh
source .venv/bin/activate
python -m pip install -r requirements.txt
read -s 'DATABASE_URL?Paste the direct Neon database URL: '
export DATABASE_URL
python app.py --create-user
unset DATABASE_URL
```

If `.venv` does not exist, create it first with `python3 -m venv .venv`.

The hidden prompt avoids putting the connection string into command history. Enter your name, email, role **manager**, and a new password of at least 12 characters. Without `DATABASE_URL`, the command would create a local account instead.

Sign in at the Render URL with this email/password. Existing local demo credentials are not automatically copied to Neon.

## 5. Invite and test an employee

1. Open **Employees**, enter the employee name/email and click **Create invitation**.
2. Open the generated HTTPS invitation in an incognito window.
3. Set the employee password, then sign in with their email.
4. Save and submit a week as the employee; review and approve it as the employer.
5. Confirm the employee cannot access other employees' records or manager actions.
6. Confirm Documents says uploads are disabled.
7. Redeploy once and confirm the accounts and timesheets remain in Neon.
8. Check `https://YOUR-SERVICE.onrender.com/healthz`; expected response is `{"status":"ok"}`.

## Troubleshooting

- Missing remote database: add `DATABASE_URL` and redeploy.
- Connection/authentication error: verify you selected the correct Neon branch/database/role, copied the complete direct URL, and retained SSL parameters.
- Invalid host or production origin: remove an old `PUBLIC_ORIGIN`, or set it to the exact current HTTPS URL.
- Supabase bucket verification error: remove unused `SUPABASE_*` environment variables in this Neon-only setup.
- No employer account: create it with the same Neon URL, not a local SQLite database.
- A slow first visit can be normal: Render Free sleeps after inactivity and Neon compute can also scale to zero.

## Limits, backups and future upgrades

Keep this deployment to a small single-company pilot. Free plans have usage quotas and availability limits. Select Free explicitly and do not enable automatic paid upgrades. Monitor usage in both dashboards.

The SQLite archive scheduler is disabled for hosted PostgreSQL. Regularly export the private `workforce` schema using Neon/PostgreSQL backup tools such as `pg_dump`, store exports encrypted, and test restoration into an isolated database. Exports include authentication hashes and private timesheets. Revoke restored sessions before opening a restored deployment.

A database backup will not cover future object-store file contents. Documents remain disabled now; add private storage plus scanning and a separate file backup strategy when enabling them. Invitation sharing remains manual; automatic email delivery is not integrated.

The previous paid configuration is preserved in `render.paid.yaml` for later review. Applying it would create paid services and requires a fresh budget approval.

References:

- https://neon.com/docs/get-started-with-neon/connect-neon
- https://render.com/docs/web-services
- https://render.com/docs/docker
- https://render.com/docs/environment-variables
- https://render.com/docs/free
