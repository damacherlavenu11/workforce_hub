# Free pilot deployment

The default `render.yaml` now uses **Render Free**, with no paid disk. Accounts and timesheets are stored in **Supabase PostgreSQL**, outside Render's temporary filesystem. A private Supabase bucket is configured for future document storage; **uploads remain disabled until a malware scanner is available**, as requested.

No cloud resources have been created. Connect your Render and Supabase accounts to deploy. Choose Free plans; this configuration does not provision paid resources or automatically upgrade them.

## Free-tier limits

As verified when preparing this configuration:

- Render Free sleeps after 15 minutes without inbound traffic, so the first visit may take longer. It has an ephemeral filesystem; the application refuses to start in free production mode without remote persistence.
- Supabase Free includes a 500 MB database and 1 GB file storage. Projects with low activity can pause after seven days and require restoration from the dashboard.
- Free hosting does not offer the uptime guarantees or capacity of a paid deployment. Keep this to a small pilot and monitor provider quotas.
- Automatic email delivery is not configured; invitations are shared manually.
- The free app image excludes ClamAV. Content checks remain implemented, but production uploads stay disabled instead of silently skipping malware scanning.

References:

- https://render.com/docs/free
- https://supabase.com/docs/guides/platform/billing-on-supabase
- https://supabase.com/docs/guides/platform/free-project-pausing

## Create the Supabase project

1. In your own Supabase account, create a project on the **Free** plan, using a strong database password. Choose a nearby region.
2. Create a **private** Storage bucket named `workforce-documents`. Do not mark it public and do not add public access policies. The backend verifies the private setting before startup.
3. Copy the IPv4-compatible **session pooler** PostgreSQL connection string (port 5432) from the Connect dialog. URL-encode the database password and retain `sslmode=require`.
4. Record the project URL and **server-only service-role key**. These are backend environment variables; never put them into `static/app.js`, Git, screenshots or chat messages.
5. The app creates tables in a private `workforce` database schema and revokes public/anonymous schema access. Do not add this schema to Supabase's exposed Data API schemas. Authentication remains in our backend; Supabase Auth signup is not used.

## Deploy to Render

Connect the GitHub repository `damacherlavenu11/workforce_hub`, then create a Blueprint from `render.yaml`. Review it to confirm the service is **Free** and there is no disk or paid database.

Set these backend environment variables in Render's dashboard:

| Variable | Value |
|---|---|
| `WORKFORCE_ENV` | `production` |
| `HOSTING_TIER` | `free` |
| `WORKFORCE_DATA` | `/tmp/workforce` (temporary staging only) |
| `PUBLIC_ORIGIN` | Exact assigned HTTPS origin, without a path |
| `DATABASE_URL` | Supabase session-pooler connection string, with SSL |
| `SUPABASE_URL` | Project HTTPS origin |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-only service-role key |
| `SUPABASE_BUCKET` | `workforce-documents` |
| `TRUST_PROXY` | `1` (Render proxy only) |

Leave `DOCUMENT_SCANNER` unset. The UI and API disable uploads. Local staging is never the authoritative database or file store in this mode.

If Render does not show the public service URL until after creation, set `PUBLIC_ORIGIN` once it is assigned and redeploy. The app intentionally refuses startup with an absent or invalid production origin.

## Create the first employer

Render Free does not provide a service shell. Create the account from your local terminal using the hosted database connection; it will appear in the hosted app:

```sh
source .venv/bin/activate
python -m pip install -r requirements.txt
# Enter the Supabase session-pooler URL in a hidden prompt, not a command argument.
read -s 'DATABASE_URL?Supabase database URL: '
export DATABASE_URL
python app.py --create-user
unset DATABASE_URL
```

The hidden prompt above is for macOS zsh. Choose role `manager` and enter a new password interactively. The command connects to the hosted database because `DATABASE_URL` is set. Without that variable it creates a local account only. Local demo credentials and data are not deployed automatically.

For forgotten passwords, use the same secure hosted connection setup and run `python app.py --reset-password`; it revokes existing sessions.

## Remote acceptance checks

1. Verify `/healthz` and employer sign-in over HTTPS.
2. Invite a test employee and activate the invitation once. Verify employee/manager access separation.
3. Save, submit, return, resubmit and approve a test week.
4. Confirm Documents shows uploads disabled, and the upload API rejects an authenticated upload with status 503.
5. Redeploy and verify employee accounts and timesheets persist in Supabase.
6. Change a password and disable a test employee; old sessions must stop working.
7. Export a database backup and test restoration in an isolated project before relying on the pilot for live data.

CI exercises local SQLite and a real PostgreSQL database, both container builds, secure Gunicorn startup and the paid scanner path. Supabase object API tests use controlled responses; they do not replace verification against your actual private bucket and credentials.

## Backups and future upgrades

The SQLite disk backup scheduler is disabled for hosted PostgreSQL/object storage. Render's temporary disk is not a backup destination. Free Supabase does not include paid database backup features: regularly export your PostgreSQL database using `pg_dump` or Supabase's documented backup tools to an encrypted, company-controlled location. The `workforce` schema contains account hashes, sessions, audit records and timesheets. Exclude/revoke restored sessions before exposing a restored service.

Documents cannot be uploaded in this pilot. If storage is enabled later, database exports alone will not back up object contents; export the private bucket too. Choose retention and backup schedules for your company before depending on this for live operations.

Keep a single company per deployment. Monitor usage, availability and provider plan settings; upgrade deliberately when needed. The previous paid configuration is preserved as `render.paid.yaml` (with `Dockerfile` and ClamAV) for future review. Applying that file would create paid services and needs a new budget approval.
