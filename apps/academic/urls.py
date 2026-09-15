from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.academic.views import (
    AcademicYearViewSet,
    AssessmentViewSet,
    ClassEnrollmentViewSet,
    ClassGroupViewSet,
    ClassSubjectViewSet,
    GradebookView,
    HomeworkSubmissionViewSet,
    HomeworkViewSet,
    LearningObjectiveViewSet,
    StudentAttainmentView,
    SubjectViewSet,
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

urlpatterns = [
    path('gradebook/', GradebookView.as_view(), name='gradebook'),
    path('students/<int:student_id>/attainment/', StudentAttainmentView.as_view(), name='student-attainment'),
    path('', include(router.urls)),
]
