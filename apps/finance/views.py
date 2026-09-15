from decimal import Decimal
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.pagination import StandardCursorPagination
from apps.core.services import audit
from apps.identity.permissions import HasRequiredPermission
from apps.finance.models import (
    Discount,
    DiscountStatus,
    FeePlan,
    FeeType,
    SiblingDiscountPolicy,
    StudentFeeAssignment,
)
from apps.finance.serializers import (
    DiscountSerializer,
    FeePlanSerializer,
    FeeTypeSerializer,
    SiblingDiscountPolicySerializer,
    StudentFeeAssignmentSerializer,
)
from apps.finance.services import approve_discount, create_discount_with_approval_check
from educore.middleware.tenancy import get_current_foundation_id


class FeeTypeViewSet(viewsets.ModelViewSet):
    """Fee catalogue items management (spec/06 §2)."""
    serializer_class = FeeTypeSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'finance.invoice.read',
        'retrieve': 'finance.invoice.read',
        'create': 'finance.invoice.write',
        'update': 'finance.invoice.write',
        'partial_update': 'finance.invoice.write',
        'destroy': 'finance.invoice.write',
    }

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return FeeType.objects.none()
        qs = FeeType.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('-created_at')
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        return qs

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id()
        fee_type = serializer.save(foundation_id=foundation_id)
        audit(
            action='finance.fee_type.created',
            entity_type='FeeType',
            entity_id=fee_type.id,
            foundation_id=foundation_id,
            school_id=fee_type.school_id,
            diff={'code': fee_type.code, 'amount': str(fee_type.default_amount)}
        )

    def perform_destroy(self, instance):
        instance.delete()
        audit(
            action='finance.fee_type.deleted',
            entity_type='FeeType',
            entity_id=instance.id,
            foundation_id=instance.foundation_id,
            school_id=instance.school_id,
            diff={'code': instance.code}
        )


class FeePlanViewSet(viewsets.ModelViewSet):
    """Fee plans for grade levels and academic years (spec/06 §2, FIN-001)."""
    serializer_class = FeePlanSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'finance.invoice.read',
        'retrieve': 'finance.invoice.read',
        'create': 'finance.invoice.write',
        'update': 'finance.invoice.write',
        'partial_update': 'finance.invoice.write',
        'destroy': 'finance.invoice.write',
        'assign': 'finance.invoice.write',
    }

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return FeePlan.objects.none()
        qs = FeePlan.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('-created_at')
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        return qs

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id()
        plan = serializer.save(foundation_id=foundation_id)
        audit(
            action='finance.fee_plan.created',
            entity_type='FeePlan',
            entity_id=plan.id,
            foundation_id=foundation_id,
            school_id=plan.school_id,
            diff={'name': plan.name, 'year': plan.academic_year}
        )

    @action(detail=True, methods=['post'], url_path='assign')
    def assign(self, request, pk=None):
        """Assign fee plan to students/classes (spec/06 §8)."""
        plan = self.get_object()
        student_ids = request.data.get('student_ids', [])
        period = request.data.get('start_period', timezone.now().strftime('%Y-%m'))

        created_count = 0
        for s_id in student_ids:
            for line in plan.lines:
                ft_id = line.get('fee_type_id')
                if ft_id:
                    StudentFeeAssignment.objects.update_or_create(
                        foundation_id=plan.foundation_id,
                        student_id=s_id,
                        fee_type_id=ft_id,
                        start_period=period,
                        defaults={
                            'amount': Decimal(str(line.get('amount', '0.00'))),
                            'currency': line.get('currency', 'IDR'),
                            'source': 'GRADE',
                            'is_active': True,
                            'deleted_at': None,
                        }
                    )
                    created_count += 1

        return Response({'status': 'ok', 'assigned': created_count})


class StudentFeeAssignmentViewSet(viewsets.ModelViewSet):
    """Direct fee overrides for students (FIN-001)."""
    serializer_class = StudentFeeAssignmentSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'finance.invoice.read',
        'retrieve': 'finance.invoice.read',
        'create': 'finance.invoice.write',
        'update': 'finance.invoice.write',
        'partial_update': 'finance.invoice.write',
        'destroy': 'finance.invoice.write',
    }

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return StudentFeeAssignment.objects.none()
        qs = StudentFeeAssignment.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('-created_at')
        student_id = self.request.query_params.get('student_id')
        if student_id:
            qs = qs.filter(student_id=student_id)
        return qs

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id()
        serializer.save(foundation_id=foundation_id)


class DiscountViewSet(viewsets.ModelViewSet):
    """Tuition discounts and waivers with approval gating (spec/06 §2, FIN-007)."""
    serializer_class = DiscountSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'finance.invoice.read',
        'retrieve': 'finance.invoice.read',
        'create': 'finance.invoice.write',
        'update': 'finance.invoice.write',
        'partial_update': 'finance.invoice.write',
        'destroy': 'finance.invoice.write',
        'approve': 'finance.invoice.write',
    }

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return Discount.objects.none()
        qs = Discount.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('-created_at')
        student_id = self.request.query_params.get('student_id')
        if student_id:
            qs = qs.filter(student_id=student_id)
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    def create(self, request, *args, **kwargs):
        foundation_id = get_current_foundation_id()
        data = request.data

        from apps.identity.models import Student
        student = Student.objects.get(id=data['student'], foundation_id=foundation_id)
        fee_type = FeeType.objects.filter(id=data.get('fee_type'), foundation_id=foundation_id).first() if data.get('fee_type') else None

        discount = create_discount_with_approval_check(
            foundation_id=foundation_id,
            student=student,
            fee_type=fee_type,
            type=data.get('type', 'PERCENT'),
            value=Decimal(str(data['value'])),
            reason=data.get('reason', ''),
            valid_from=data['valid_from'],
            valid_to=data.get('valid_to'),
            user=request.user,
        )

        serializer = self.get_serializer(discount)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """Approve a pending discount request (FIN-007)."""
        discount = self.get_object()
        approved = approve_discount(discount, request.user)
        serializer = self.get_serializer(approved)
        return Response(serializer.data)


class SiblingDiscountPolicyViewSet(viewsets.ModelViewSet):
    """Sibling discount rules per school (FIN-006)."""
    serializer_class = SiblingDiscountPolicySerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'finance.invoice.read',
        'retrieve': 'finance.invoice.read',
        'create': 'finance.invoice.write',
        'update': 'finance.invoice.write',
        'partial_update': 'finance.invoice.write',
        'destroy': 'finance.invoice.write',
    }

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return SiblingDiscountPolicy.objects.none()
        qs = SiblingDiscountPolicy.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('child_order')
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        return qs

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id()
        serializer.save(foundation_id=foundation_id)
