# Instructor guide

1. Start the backend and frontend using the commands in the root README.
2. Open **Institution** to review the LNCT profile and cited sources.
3. Open **Students** to preview a `.xlsx` or `.csv` roster. Enter the local `ROSTER_IMPORT_TOKEN` from `backend/.env`, select the file, and review row counts, sections, errors, and conflicts. Confirm only after reviewing the preview. The preview expires after one hour and must be confirmed using the same file.
4. Imported students are labeled `source-roster-unverified`. The provided 2025-26 roster is source metadata; verify current enrollment with the institution before using it operationally. Imports create section cohorts but never attendance records. Demo students are excluded from the LNCT student list.
5. Use **Students** to add a student ID and name. Deactivation preserves historical records.
6. Use **Classes** to inspect rosters, create classes, and assign students.
7. Use **Sessions & attendance** to create a session and click **Start**. For live signals, click **Live inputs**, then explicitly start the camera and/or microphone. Camera preview frames are sent at about one per second; the mic sends only local RMS summaries. Stop either device or end the session to release its hardware track.
8. Alternatively, choose **Uploaded video** and use Analyze video to count anonymous detections from sampled frames. For audio sessions, upload PCM WAV to derive RMS activity. These measurements do not identify students or mark attendance.
9. Review indicators as behavioral proxies with their data-quality label. Use recommendations as optional prompts, never as automated teaching decisions. Attendance rate remains blank until recorded non-demo attendance exists.
10. Export attendance using the CSV button. MediaPipe status appears in the live inputs panel. To enable landmarks, install the pinned backend requirements, download a compatible Face Landmarker `.task` model from Google's official model page, and set `MEDIAPIPE_MODEL_PATH` in `backend/.env`.

Face identity matching and biometric enrollment are unavailable. Use manual attendance and review/correct each status; the application never uses a camera match to confirm presence.

# Student AccSoft attendance

1. Sign in to the attendance app using your account linked to your LNCT student record.
2. Open **My AccSoft Attendance**, then open AccSoft using the official portal link. Sign in on that portal only; this app has no AccSoft password field.
3. Download your attendance export from AccSoft, return to the app, select the CSV/XLSX, and choose **Preview report**.
4. Check accepted rows, duplicates, and row-level validation issues. Confirm only after checking the preview.
5. Imported records appear as **pending review** until the Super Admin reviews the supporting report evidence. The app displays only source values and does not calculate attendance percentages.

Imports must contain the signed-in student's enrollment/roll number, academic session, semester, and subject name or code. Include a source percentage, conducted and attended counts, or both a class date and status. Reports are limited to 10 MB and 50,000 data rows. A preview is valid for one hour and confirmation requires the same file. Re-importing an identical record is blocked as a duplicate.
