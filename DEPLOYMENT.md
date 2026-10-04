# Render deployment: Workforce Hub

Status: deployment configuration prepared. No Render resources have been created by this repository. Account access, a reviewed monthly budget and remote validation are required before live employee use.

## Proposed services and costs

`render.yaml` defines one Docker web service named `workforce-hub`, one instance, and a 10 GB persistent disk. Both the frontend and API are served by this service using Gunicorn, behind Render's HTTPS proxy.

The service uses Render's `pro` compute tier (4 GB RAM) because the bundled ClamAV scanner requires substantial memory. Estimated compute is $85/month plus $2.50/month for a 10 GB disk, approximately **$87.50/month**, excluding workspace subscription changes, extra bandwidth, taxes and any future email service. Verify the current total in Render before applying the Blueprint:

- https://render.com/pricing
- https://docs.clamav.net/ (ClamAV recommends roughly 3–4 GiB RAM)

This differs from the earlier ~$7 prototype estimate: that estimate did not include a properly sized malware scanner or backup storage. Do not apply the paid Blueprint until the service budget is accepted. A smaller service with an independently hosted scanner is another option, but requires access to that scanner and a new cost estimate.

Free Render services have ephemeral filesystems and cannot preserve this application's SQLite database and documents across deployments. Never store live employee data there.

## Account setup and deploy

1. Sign in to https://dashboard.render.com and connect the GitHub repository `damacherlavenu11/workforce_hub`.
2. Create a Blueprint from `render.yaml`; review the paid service and disk before accepting.
3. Set `PUBLIC_ORIGIN` to the exact HTTPS origin Render assigns, for example `https://your-workforce-service.onrender.com` (no trailing path). If the URL is not available until service creation, set it in the service environment after creation and redeploy. The application intentionally refuses startup with an absent or invalid production origin.
4. Keep `WORKFORCE_DATA=/var/data`, `WORKFORCE_ENV=production` and `TRUST_PROXY=1`. Trust proxy headers only when the backend is reached through Render's proxy. Keep the `DOCUMENT_SCANNER` value from the Blueprint.
5. Wait for GitHub checks and the deployment to pass. Antivirus definitions update in the background; uploads fail closed until valid, fresh signatures exist. Check FreshClam logs for update failures; do not disable scanning to force uploads through.
6. In the Render service's shell, create your first employer account with `gosu workforce python app.py --create-user`, using role `manager`. Enter a new strong password interactively; do not put passwords in commands, source files, screenshots or logs.
7. Open the HTTPS URL, sign in, and create a test employee invitation. Complete the checks below before onboarding real employees.

Existing local accounts and uploads do not automatically transfer. Start with a new employer on the host, or use a separately reviewed encrypted migration. Do not upload the local database to GitHub.

## Required remote verification

- `/healthz` returns `{ "status": "ok" }`; set alerts for service outages and errors. Health checks verify the database connection, not scanner readiness.
- Employer login works over HTTPS; the session cookie has Secure, HttpOnly and SameSite=Strict attributes.
- Employee invitation activation succeeds once; the employee can only see their own records and cannot access manager endpoints.
- Save, submit, return, resubmit and approve a test week. Approved weeks cannot be edited.
- Upload and download a harmless test document, and verify a second employee cannot download it.
- Verify ClamAV rejects the standard EICAR antivirus test file, using a designated test account. Never include genuine sensitive documents in antivirus tests.
- Change a password and verify old sessions are revoked. Disable a test employee and verify access ends.
- Redeploy once and confirm test accounts, sheets and files persist.
- Verify daily backup creation; download a backup securely and restore it into an isolated test installation.

Container builds and real antivirus tests must pass on GitHub before the service is accepted. This workspace does not have Docker or a connected Render account, so these remote checks cannot be replaced by the local mocked scanner tests.

## Backups and recovery

The single Gunicorn worker runs a backup scheduler. It creates a coherent archive of SQLite and committed immutable files at startup if due, checks hourly, and retains daily archives for seven days under `/var/data/backups/`. Creation failures are logged and must alert an operator. Archives contain authentication hashes and private employee documents: restrict access and encrypt exports.

Render takes encrypted daily disk snapshots. Consistent archives can be recovered from those snapshots, but this is not an independent backup strategy. Export archives regularly to a company-controlled encrypted backup destination. Capacity must cover live uploads plus seven archive copies. Monitor disk space; increase the disk before it fills. Render snapshots alone are not a substitute for testing SQLite recovery.

Manual archive:

```sh
gosu workforce python scripts/backup.py --output /var/data/backups/manual-backup.tar.gz
```

To restore, use an isolated installation first: stop writes, preserve its current data, extract an archive you trust into an empty data directory, check `PRAGMA integrity_check` and referenced files, then start the app with `WORKFORCE_DATA` pointing there. Invalidate all restored sessions and invitations before opening a restored production service. For production recovery, stop the service and take an emergency backup first; never overwrite live data while requests are running.

Do not publicly expose the disk or archive directory. The application routes never serve these paths.

## Operating boundaries

This is a small single-company, single-instance deployment. Horizontal scaling requires moving the database to PostgreSQL and files to private object storage and adapting the persistence layer. Employer/manager roles currently have company-wide record access. Establish employee consent, authorized manager access, document retention and support procedures for your company before inviting real users.

Invitation emails are manual. Integrating automatic delivery and self-service email password recovery requires a verified sending domain and provider configuration; do not send shared plaintext passwords.
