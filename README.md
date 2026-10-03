# Workforce Hub

Employee and manager portal for private documents and weekly timesheet approvals. Suggested repository name: `workforce-hub`.

## Start locally

Requires Python 3.9 or newer; no dependencies to install.

```sh
python3 app.py --create-user
```

Create the first employer/manager account using this command. Then sign in and use **Employees** to onboard employees. The employer is represented by the manager role in this single-company MVP. Employees do not sign up publicly or receive shared passwords. Passwords require at least 12 characters; users cannot grant themselves manager access.

```sh
python3 app.py
```

Open http://localhost:8000. Use `--port 8001` to change the port.

## Sign-in troubleshooting

There are no default accounts or passwords. Create the first manager with `python3 app.py --create-user`, choosing the `manager` role, then start or restart `python3 app.py`. Invited employees must set their password through their invitation before signing in. Emails ignore capitalization and surrounding spaces; passwords are case-sensitive and preserve spaces. Use the **eye icon** inside the password field to check what you typed. Sign-in and activation failures appear as red toast notifications.

## Employee onboarding

1. The employer signs in and opens **Employees**.
2. Enter the employee's name and email, then click **Create invitation**.
3. Copy the invitation link, or use **Open email draft** and send it from your email client. The app does not send email automatically; no email delivery service is configured.
4. The employee opens the link, chooses and confirms their password, then signs in with their email.

Invitations expire after 48 hours and can only be used once. Pending accounts cannot sign in. Use **New invitation** for pending or expired invitations; this invalidates the old link. Only token hashes are stored in SQLite. Employers cannot invite another employer through the employee onboarding screen.

The app currently runs on localhost, so invitation links are only usable on the machine running it. A deployed HTTPS URL is needed for employees on other machines. Automatic email delivery can be connected to a transactional email provider in a later step.

## Workflows

- Employees enter seven daily hours for a Monday-starting week, save drafts, and submit for approval.
- Submitted and approved weeks are locked. Managers review daily hours, approve submissions, or return them with a correction note. Returned weeks can be edited and resubmitted.
- Both roles upload and download their documents. Managers can view all employees' documents and timesheets in this single-company MVP.
- Month filters include weeks overlapping a month, and monthly recorded hours count only days inside that month. Approval is per week in this version; independent monthly approval is a future extension.

SQLite records and uploaded files are stored under `data/` and excluded from Git. Set `WORKFORCE_DATA` to change the storage directory. Back up the database and uploads together.

## Validation

```sh
python3 -m unittest discover -s tests -v
```

## Architecture and next steps

The Python HTTP server serves a responsive vanilla JavaScript interface and JSON endpoints. Authentication uses salted PBKDF2 password hashes, expiring server-side sessions, HttpOnly/SameSite cookies and CSRF tokens. Files have randomized storage names and authenticated downloads. Employees can access only their own records.

This is a local development MVP, bound to localhost. Before internet deployment, replace the development server with a production service, enforce HTTPS and Secure cookies, add login rate limiting or managed identity, define company/team boundaries, add audit logs, malware scanning, encrypted backups and document retention policies. File extensions are restricted, but file contents are not scanned. Managers currently have access to every employee in this installation. Do not use real sensitive employee documents until production security and access rules are in place.

Possible production stack: React/Next.js UI, a Python or Node API, PostgreSQL, private S3-compatible document storage, and managed authentication. The current workflow provides a runnable starting point for those decisions.
