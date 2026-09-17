from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.academic.views import (
    AcademicYearViewSet,
    AssessmentViewSet,
    BroadcastPolicyView,
    BroadcastViewSet,
    ClassEnrollmentViewSet,
    ClassGroupViewSet,
    ClassSubjectViewSet,
    ExamAttemptViewSet,
    ExpectedPeriodsView,
    ExamQuestionViewSet,
    ExamViewSet,
    GradebookView,
    HomeworkSubmissionViewSet,
    HomeworkViewSet,
    LearningObjectiveViewSet,
    LessonPlanViewSet,
    PeriodGridView,
    PermissionSlipAcknowledgeView,
    PermissionSlipConsoleCreateView,
    PermissionSlipConsolePageView,
    PermissionSlipConsoleRosterView,
    PermissionSlipConsoleTallyView,
    ReportCardPolicyView,
    ReportCardViewSet,
    StudentAttainmentView,
    StudentGradesView,
    StudentHomeworkView,
    StudentPermissionSlipView,
    StudentReportCardView,
    StudentTimetableView,
    SubjectViewSet,
    TeacherPermissionSlipTallyView,
    TeacherPermissionSlipView,
    TermViewSet,
    TimetableSlotViewSet,
    TimetableSubstitutionViewSet,
)

router = DefaultRouter()
router.register(r'academic-years', AcademicYearViewSet, basename='academic-years')
router.register(r'terms', TermViewSet, basename='terms')
router.register(r'subjects', SubjectViewSet, basename='subjects')
router.register(r'class-groups', ClassGroupViewSet, basename='class-groups')
router.register(r'class-enrollments', ClassEnrollmentViewSet, basename='class-enrollments')
router.register(r'class-subjects', ClassSubjectViewSet, basename='class-subjects')
router.register(r'learning-objectives', LearningObjectiveViewSet, basename='learning-objectives')
router.register(r'assessments', AssessmentViewSet, basename='assessments')
router.register(r'timetable/slots', TimetableSlotViewSet, basename='timetable-slots')
router.register(r'timetable/substitutions', TimetableSubstitutionViewSet, basename='timetable-substitutions')
router.register(r'homework', HomeworkViewSet, basename='homework')
router.register(r'homework-submissions', HomeworkSubmissionViewSet, basename='homework-submissions')
router.register(r'exams', ExamViewSet, basename='exams')
router.register(r'exam-questions', ExamQuestionViewSet, basename='exam-questions')
router.register(r'exam-attempts', ExamAttemptViewSet, basename='exam-attempts')
router.register(r'report-cards', ReportCardViewSet, basename='report-cards')
router.register(r'lesson-plans', LessonPlanViewSet, basename='lesson-plans')
router.register(r'teacher/broadcasts', BroadcastViewSet, basename='teacher-broadcasts')

urlpatterns = [
    path('gradebook/', GradebookView.as_view(), name='gradebook'),
    path('students/<int:student_id>/attainment/', StudentAttainmentView.as_view(), name='student-attainment'),
    path('students/<int:student_id>/grades/', StudentGradesView.as_view(), name='student-grades'),
    path('students/<int:student_id>/homework/', StudentHomeworkView.as_view(), name='student-homework'),
    path('students/<int:student_id>/report-cards/', StudentReportCardView.as_view(), name='student-report-cards'),
    path('students/<int:student_id>/timetable/', StudentTimetableView.as_view(), name='student-timetable'),
    path('students/<int:student_id>/permission-slips/', StudentPermissionSlipView.as_view(), name='student-permission-slips'),
    path('teacher/permission-slips/', TeacherPermissionSlipView.as_view(), name='teacher-permission-slips'),
    path('teacher/permission-slips/<int:slip_id>/consent-tally/', TeacherPermissionSlipTallyView.as_view(), name='teacher-permission-slip-tally'),
    path('permission-slips/<int:slip_id>/acknowledge/', PermissionSlipAcknowledgeView.as_view(), name='permission-slip-acknowledge'),

    path('schools/<int:school_id>/report-card-policy/', ReportCardPolicyView.as_view(), name='report-card-policy'),
    path('schools/<int:school_id>/broadcast-policy/', BroadcastPolicyView.as_view(), name='broadcast-policy'),
    path('schools/<int:school_id>/period-grid/', PeriodGridView.as_view(), name='period-grid'),
    path('schools/<int:school_id>/expected-periods/', ExpectedPeriodsView.as_view(), name='expected-periods'),
    path('', include(router.urls)),
]
