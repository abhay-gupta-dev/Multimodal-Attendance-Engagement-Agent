# Multimodal Attendance & Engagement Agent

An offline-first classroom dashboard for attendance records and transparent behavioral proxies. The system keeps the initial implementation local: SQLite stores application data, the FastAPI service exposes the API, and the React dashboard consumes that API.

## Features

- Persistent SQLite student, class, session, attendance, metric, and recommendation records.
- Demo dataset with 15 students, three classes, completed sessions, charts, and rule-based suggestions.
- Student and class management, session creation, attendance uniqueness, manual correction API, and CSV export.
- One-time, expiring first-administrator bootstrap; scrypt password hashes, revocable HttpOnly sessions, login throttling, role checks, faculty-class scoping, student self-attendance access, and attendance correction audit records.
- LNCT institution profile and a protected CSV/XLSX roster preview, conflict check, transactional import, and import history.
- Student-scoped LNCT AccSoft portal handoff and CSV/XLSX attendance report preview/import with duplicate prevention and administrator review.
- Engagement indicator with missing-signal handling and explicit synthetic-data labeling.
- Opt-in webcam preview and sampled local frame processing during active sessions; browser tracks are released when stopped or the panel closes.
- Opt-in microphone capture using Web Audio RMS windows. Only derived activity percentages are sent; raw audio is not uploaded or recorded.
- OpenCV anonymous face counts and optional MediaPipe face landmarks / approximate orientation proxies when a compatible local `.task` model is configured. No identity matching or automatic attendance.
- OpenCV video processing with upload, duration, frame-count, and incremental-read limits.
- Session analytics, student attendance summaries, explainable rule prompts, and CSV attendance export.
- Responsive dark dashboard with loading and backend-error states, and lazy-loaded charts.

## Start locally

Prerequisites: Python 3.11 or 3.12 x64, Node.js 18+, and a webcam/microphone for live capture. Camera/microphone access requires a supported browser on localhost.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1
```

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. API documentation is available at http://localhost:8000/docs.

## Import the LNCT student roster

The Students page accepts `.xlsx` and UTF-8 `.csv` rosters with Enrollment, Student Name, and Section columns. Configure a private `ROSTER_IMPORT_TOKEN` in `backend/.env` (use a long random secret; do not commit or share it), enter it in the page, preview the records, review any duplicates or conflicts, then confirm. The local setup has a generated token in `backend/.env`. Preview tokens expire after one hour and confirmation requires the same file. Imports are transactional, create section cohorts, and do not create attendance.

The current data is the 2025-26, Year 2, Semester 3 B.Tech AIML source roster. Its source metadata does not verify current enrollment or teaching assignments. Imported rows are labeled `source-roster-unverified`; an institution administrator must verify them before treating them as current enrollment. The workbook supplied for this import contained 466 valid rows across Sections A-G. The pre-existing 15 synthetic demo records remain clearly marked and separate.

## LNCT AccSoft attendance reports

The **My AccSoft Attendance** student page provides a link to the official [LNCT AccSoft login](https://portal.lnct.ac.in/Accsoft2/studentlogin.aspx). LNCT's [online fee instructions](https://lnct.ac.in/pay-fees-online/) also direct Parent/Student users to AccSoft credentials. I found no official public API or vendor integration specification in the official LNCT and vendor material reviewed, so this app does not automate login, scrape the portal, or ask for/store AccSoft passwords. Portal access stays on the official site.

After downloading an attendance export while signed in to AccSoft, a student can upload their own UTF-8 CSV or XLSX from **College attendance**. Reports must include the student's enrollment/roll number, academic session, semester, and subject name or code, plus either an attendance percentage, conducted and attended counts, or a date and status. Student identity must match the linked account. The app previews validation errors and duplicates before confirmation; it stores normalized rows and their source filename/row, but not the original uploaded file bytes. Values are not recalculated. Every imported row starts as `pending_review`; only a Super Admin can review it, with a required reason. The student can view only records linked to their own account. No verified attendance is shown as a zero or synthetic percentage.

Student-uploaded report values are not independently authenticated by AccSoft. A Super Admin must check the corresponding evidence outside this app before marking a row verified. Direct live attendance retrieval remains unavailable unless LNCT or the AccSoft vendor provides an authorized integration method.

## First administrator setup

The setup page appears only when the database has no `super_admin` account. The backend accepts the one-time token only through the `X-Admin-Bootstrap` request header. It compares the token with `AUTH_BOOTSTRAP_TOKEN` using a constant-time comparison and requires the UTC ISO-8601 expiry in `AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT`. The roster import token is separate and is never accepted for administrator setup. Once the first Super Admin exists, setup returns `409` and stays disabled while that account exists. No default administrator or password is created. Passwords are stored as salted scrypt hashes; session tokens are stored only as SHA-256 digests and sent in HttpOnly, SameSite cookies.

Use PowerShell to create or renew a 24-hour token. Python's `secrets` module generates it; the token is written only to the ignored `backend/.env` and copied to the clipboard without printing it. Paste it into the administrator setup form. The same steps renew an expired token.

```powershell
Set-Location "C:\Users\disha\OneDrive\Desktop\Attendance app\backend"
$token = python -c "import secrets; print(secrets.token_urlsafe(32))"
$expiry = (Get-Date).ToUniversalTime().AddHours(24).ToString("yyyy-MM-ddTHH:mm:ssZ")
$env:LNCT_SETUP_TOKEN = $token
$env:LNCT_SETUP_EXPIRY = $expiry
python -c "import os; from pathlib import Path; p=Path('.env'); keys=('AUTH_BOOTSTRAP_TOKEN','AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT'); lines=[x for x in (p.read_text(encoding='utf-8').splitlines() if p.exists() else []) if not any(x.lstrip().startswith(k+'=') for k in keys)]; lines += ['AUTH_BOOTSTRAP_TOKEN='+os.environ['LNCT_SETUP_TOKEN'], 'AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT='+os.environ['LNCT_SETUP_EXPIRY']]; p.write_text('\n'.join(lines)+'\n', encoding='utf-8')"
Remove-Item Env:LNCT_SETUP_TOKEN
Remove-Item Env:LNCT_SETUP_EXPIRY
$token | Set-Clipboard
Remove-Variable token
Write-Host "Administrator setup token configured and copied to clipboard."
```

If `AUTH_BOOTSTRAP_TOKEN` is already present and unexpired, copy the current value without displaying it:

```powershell
Set-Location "C:\Users\disha\OneDrive\Desktop\Attendance app\backend"
$token = (Get-Content .env | Where-Object { $_ -like 'AUTH_BOOTSTRAP_TOKEN=*' } | Select-Object -First 1).Substring('AUTH_BOOTSTRAP_TOKEN='.Length)
$token | Set-Clipboard
Remove-Variable token
```

Stop Uvicorn with `Ctrl+C` in its current terminal, then restart from the `backend` directory:

```powershell
Set-Location "C:\Users\disha\OneDrive\Desktop\Attendance app\backend"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Refresh `http://localhost:5173`, paste the token, choose the first administrator login and a password of at least 12 characters, and submit. The setup key is not returned by the API and is not embedded in frontend assets. A missing token, invalid token, invalid expiry, expired token, and completed setup each have distinct errors. Do not commit the `.env` file or share setup credentials in logs/screenshots.

## Database migrations

SQLite remains the configured local database and is not replaced during setup. New access-control tables are additive. Alembic is configured for deployments that set `DATABASE_URL`; run `python -m alembic upgrade head` from `backend`. To use PostgreSQL, install the pinned `psycopg` dependency and set a private `DATABASE_URL=postgresql+psycopg://...` in `backend/.env` before starting the service. The access-control revision adds new tables; it does not copy existing SQLite rows to PostgreSQL. Back up `backend/data/attendance.db` before any migration or database-engine change.

## Testing and build

```powershell
cd backend
$env:PYTHONPATH = "."
pytest
cd ..\frontend
npm run build
```

Copy `backend/.env.example` to `backend/.env` to configure local settings. Keep Uvicorn bound to `127.0.0.1`; the API rejects non-loopback requests. Set `DEMO_MODE=false` to start with an empty database. The default database is `backend/data/attendance.db`; it is created automatically and is ignored by Git. Demo attendance and metrics remain tagged as synthetic; dashboard trends separate synthetic and recorded series.

Video uploads accept MP4, AVI, MOV, MKV, or WebM extensions, but decoding depends on the local OpenCV codecs. The service streams the upload to a temporary file, samples frames incrementally, and deletes the file afterward. Defaults limit uploads to 100 MB, video duration to 30 minutes, and decoded frames to 60,000. Configure these with `UPLOAD_MAX_MB`, `MAX_VIDEO_DURATION_SECONDS`, and `MAX_VIDEO_FRAMES`. If a file fails to open, convert it to H.264 MP4, for example: `ffmpeg -i input.mov -c:v libx264 -pix_fmt yuv420p output.mp4`.

MediaPipe 0.10.21 is pinned for its verified Python Tasks FaceLandmarker API and Python 3.11 Windows x64 wheel. Install its model separately from the [official Face Landmarker model section](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker). Set `MEDIAPIPE_MODEL_PATH` to the local `.task` file. Without the package or model asset, the API reports landmarks unavailable and continues anonymous OpenCV counting. Orientation values are approximate geometric proxies, not attention measures.

## Privacy and limitations

The behavioral indicator is an approximate summary of observable inputs, not a measurement of emotion, attention, comprehension, intelligence, or learning ability. Silence, movement, or stillness are not interpreted as proof of disengagement. Demo values are synthetic and never represent camera observations. Attendance rate remains blank until recorded non-demo attendance exists.

Raw face images and raw audio are not stored. A compatible face embedding/recognition model and secure biometric-template store are not available in this build, so biometric enrollment and identity matching are explicitly unavailable. Attendance remains manual, instructor reviewed, and correctable. Staff-facing API routes require a provisioned account; students can see only their linked profile and attendance. The backend is still local-loopback only and the login throttle is process-local. See [docs/PRIVACY.md](docs/PRIVACY.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

