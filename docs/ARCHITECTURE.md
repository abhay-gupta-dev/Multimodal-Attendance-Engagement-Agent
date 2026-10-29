# Architecture

The React/Vite frontend calls the versioned FastAPI REST API with Axios. FastAPI validates request bodies with Pydantic and uses SQLAlchemy with SQLite for persistence. The rule-based analytics service receives recorded metrics and produces an explainable indicator and optional teaching suggestions.

`backend/app/vision/analysis.py` streams a temporary video file through OpenCV, sampling about one frame per second with size, duration, and frame-count caps. Live camera frames use the same image path and are discarded after analysis. The optional MediaPipe Tasks FaceLandmarker requires both the pinned package and a configured local `.task` asset; when available it stores only aggregate landmark counts and rough orientation proxies. Otherwise the capability API reports why landmarks are unavailable. No recognition model is installed; identity matching and enrollment are unavailable.

`backend/app/audio/analysis.py` reads uploaded PCM WAV in memory. Live microphone activity uses the browser Web Audio analyzer and sends one-second RMS-derived percentages; no raw live audio is sent. Derived video/audio metrics are linked to sessions in SQLite. The API middleware restricts requests to loopback clients, and only active sessions accept live sensor data.

The indicator currently summarizes recorded session metrics and demo features. Missing signal values are omitted by the scoring helper. Media analysis is not automatically converted into an engagement score or an attendance decision.

SQLite tables are created automatically for development. A production deployment should adopt Alembic migrations before changing schemas.

