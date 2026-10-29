from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AttendanceRecord, ClassEnrollment, Classroom, ClassSession, EngagementMetric, Recommendation, Student
from app.services.analytics import recommendations


def seed_demo(db: Session) -> None:
    if db.scalar(select(Student.id).limit(1)) is not None:
        return
    classes = [Classroom(name=name, subject=subject) for name, subject in [("Data Structures", "Computer Science"), ("Human Computer Interaction", "Design"), ("Machine Learning Lab", "AI Engineering")]]
    db.add_all(classes)
    db.flush()
    students = [Student(student_code=f"STU-{i:03}", name=f"Demo Student {i}", class_name=classes[(i - 1) % 3].name, institution_code="DEMO", is_demo=True, enrollment_status="demo") for i in range(1, 16)]
    db.add_all(students)
    db.flush()
    for student in students:
        db.add(ClassEnrollment(class_id=classes[(student.id - 1) % 3].id, student_id=student.id))
    now = datetime.utcnow()
    for offset in range(3):
        session = ClassSession(class_id=classes[offset].id, title=f"Demo session {offset + 1}", source="demo", status="completed", is_demo=True, started_at=now - timedelta(days=offset + 1, hours=1), ended_at=now - timedelta(days=offset + 1))
        db.add(session)
        db.flush()
        indicator = 68 - offset * 8
        metric = EngagementMetric(session_id=session.id, indicator=indicator, face_visibility=indicator + 4, audio_activity=35 - offset * 5, data_quality="synthetic-demo")
        db.add(metric)
        for student in students:
            if student.class_name == classes[offset].name:
                db.add(AttendanceRecord(session_id=session.id, student_id=student.id, status="present" if student.id % 5 else "late", duration_seconds=3000 if student.id % 5 else 2400, source="demo"))
        for item in recommendations(indicator, metric.audio_activity, metric.data_quality):
            db.add(Recommendation(session_id=session.id, **item))
    db.commit()

