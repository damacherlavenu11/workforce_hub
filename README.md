# Workforce Hub

Single-company employee and employer portal for weekly timesheet approvals, employee invitations and private documents.

## Local development

Python 3.9+ is supported locally. Deployment and CI use Python 3.12.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py --create-user
python app.py
```

Choose the `manager` role for the first employer. Open http://localhost:8000. Existing SQLite data is migrated automatically; existing password hashes are upgraded on successful login. Existing users retain their roles and passwords. Sessions created by the old prototype require signing in again.

There are no built-in production accounts. Local demo credentials and local documents are never included in Git or container builds.

## Employee workflow

Employers open **Employees**, enter a name and email, then share a 48-hour, one-use invitation. Employees choose their passwords and sign in on the same page; their role determines their workspace. Invitations can be regenerated. **Disable access** immediately revokes an employee's sessions and invitations while retaining records.

Employees save seven daily hours for Monday-starting weeks, submit them, and view approval status. Managers approve submitted weeks or return them with a correction note. Monthly summaries count the days within the selected month; approval remains weekly.

Employees can only view their own data. Managers can view all records within this single-company installation. This deployment is not a multi-company SaaS: use a separate installation for each company until tenant isolation is implemented.

For the free hosted pilot, document uploads are disabled until malware scanning is available. Existing authenticated download routes remain protected.

Both roles can change passwords from **Account settings**. For forgotten passwords, an authorized deployment operator runs:

```sh
python app.py --reset-password
```

The password prompt hides input and all sessions for that account are revoked. Email delivery and self-service forgotten-password emails are not integrated; invitations currently use copy-link/email-draft sharing.

## Technology

- Frontend: responsive HTML, CSS and JavaScript, hosted at `/`.
- Backend: Flask JSON endpoints at `/api/*`, run by Gunicorn in production.
- Data: local SQLite, or hosted PostgreSQL in a private schema for the free pilot.
- Files: local private paths or private Supabase objects, with authenticated downloads. Production uploads require malware scanning.
- Deployment: Docker, Render Blueprint and GitHub Actions checks.

The frontend and backend are both remote after deployment and share one HTTPS origin. Separate services/domains are unnecessary for this initial deployment and would require additional cookie/CORS configuration.

## Validation

```sh
python -m unittest discover -s tests -v
node --check static/app.js
```

Tests cover onboarding, employee isolation, approvals, malformed requests, login limits, secure cookies, password upgrades/resets, offboarding, scanner failures, local backup restoration, remote object authorization and private-bucket validation. CI also runs workflow tests against PostgreSQL. Scanner unit tests mock the process result; the CI container check separately exercises a real ClamAV executable using a local test signature.

## Deploy and operate

See [DEPLOYMENT.md](DEPLOYMENT.md) for the Render configuration, estimated costs, account setup, deployment validation, and backup/restore procedure. The deployment files are a candidate configuration; a successful local test is not proof that the remote service is ready. Complete the remote checks before live onboarding.

Production adds HTTPS enforcement, Secure/HttpOnly/SameSite cookies, hashed session tokens, CSRF/origin validation, database-backed request limits, audit events and security headers. Gunicorn uses one worker and four threads; scans are serialized to bound memory. Do not increase worker or instance counts without moving shared storage and scan coordination out of the process.

The default `render.yaml` selects free Render hosting with Supabase persistence. The prior paid configuration is in `render.paid.yaml`. Free hosting can sleep or pause and requires manual backup exports.

Local SQLite records and uploads live under `WORKFORCE_DATA`; local `data/` is ignored by Git. With `DATABASE_URL` configured, accounts and timesheets use PostgreSQL. With Supabase storage configured, committed documents use the private bucket; the temporary app filesystem is never the authoritative remote store. The local SQLite backup scheduler runs only for the paid/local storage mode. Monitor disk usage and logs, review access periodically, set company retention rules, and maintain an independent encrypted backup export. Antivirus reduces risk but does not guarantee every document is safe. No public upload directory is exposed.
