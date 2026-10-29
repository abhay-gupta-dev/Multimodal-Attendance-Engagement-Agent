# Privacy

Processing is designed to remain on the instructor's machine. The API is restricted to loopback clients and should be run bound to `127.0.0.1`. The database contains student codes, names, optional email/class labels, academic section and source-roster metadata, attendance, derived metrics, and recommendations. Uploaded video is streamed to a temporary local file for processing and deleted immediately afterward. Camera preview frames are sent only during an instructor-started active session, processed, and discarded. Microphone access is opt-in; Web Audio calculates RMS locally and sends only one-second activity percentages and timestamps. Raw audio is never uploaded or recorded.

Roster preview, confirmation, and history routes require the local `ROSTER_IMPORT_TOKEN` from `backend/.env`. The token is held in the page's memory and sent only to the local API. Import history records source filename and aggregate counts, not row-level student data. XLSX and CSV imports are validated, previewed, and committed transactionally; no attendance is created. The supplied roster's current enrollment status has not been institutionally verified. Existing demo rows are labeled and excluded from the LNCT roster totals. The token is a single-operator import safeguard, not full user authentication or role-based access control.

Face detection uses a local OpenCV Haar cascade and cannot identify a student. Optional MediaPipe landmarks require the pinned package and a separately supplied local model asset. Only aggregated landmark counts and approximate orientation proxies are stored, not landmark coordinates. These features do not measure attention or comprehension. Face recognition, biometric enrollment, and biometric template storage are unavailable; no face images or templates are enrolled. Manual attendance is the available alternative.

Indicators are behavioral proxies with missing-data labels. They do not establish emotion, attention, comprehension, academic ability, or identity. Instructors must review records and may manually correct attendance. This project does not claim legal compliance; institutions should obtain appropriate legal and ethics review.

# AccSoft report imports

The AccSoft handoff opens the official LNCT portal in a separate browser tab. This application does not collect AccSoft login credentials and does not claim to retrieve data from the portal. Students upload only their own CSV/XLSX attendance export. The API matches the enrollment ID and optional name to the signed-in student's linked LNCT record.

The original file bytes are parsed in memory and discarded after the request. Normalized attendance values, the submitted row payload, source filename and source row number, import timestamps, and review notes are retained in SQLite. Imported rows are private to the linked student and the Super Admin review queue. New imports are pending review; only a Super Admin can verify or reject them, with an audit event and a required reason. Report contents are student-provided and are not portal-authenticated by this app; reviewers must check their evidence outside the system before verifying. Attendance values are displayed as supplied by the report and are not inferred.

Official portal: https://portal.lnct.ac.in/Accsoft2/studentlogin.aspx

LNCT AccSoft access instructions: https://lnct.ac.in/pay-fees-online/
