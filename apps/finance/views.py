from decimal import Decimal
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardCursorPagination
from apps.core.services import audit
from apps.identity.permissions import HasRequiredPermission
from apps.identity.models import School
from apps.finance.models import (
    Discount,
    DiscountStatus,
    FeePlan,
    FeeType,
    Invoice,
    InvoiceInstallment,
    InvoiceLine,
    InvoiceStatus,
    InvoiceWriteOffRequest,
    InvoiceWriteOffStatus,
    LedgerEntry,
    LedgerJournal,
    Payment,
    PaymentAllocation,
    PaymentIntent,
    PaymentStatus,
    BankSftpConfig,
    SchoolArrearsPolicy,
    SchoolConvenienceFeePolicy,
    SchoolQrisConfig,
    SiblingDiscountPolicy,
    StudentCreditBalance,
    StudentFeeAssignment,
    StudentVirtualAccount,
)
from apps.finance.serializers import (
    CashPaymentCreateSerializer,
    CreateInstallmentPlanSerializer,
    DiscountSerializer,
    FeePlanSerializer,
    FeeTypeSerializer,
    InvoiceInstallmentSerializer,
    InvoiceSerializer,
    InvoiceWriteOffRequestCreateSerializer,
    InvoiceWriteOffRequestSerializer,
    InvoiceWriteOffResolveSerializer,
    LedgerEntrySerializer,
    LedgerJournalSerializer,
    ManualPaymentCreateSerializer,
    ManualPaymentVerifySerializer,
    PaymentAllocationSerializer,
    PaymentIntentCreateSerializer,
    PaymentIntentSerializer,
    PaymentProofUploadSerializer,
    PaymentSerializer,
    BankSftpConfigSerializer,
    BankSftpConfigUpdateSerializer,
    SchoolArrearsPolicySerializer,
    SchoolArrearsPolicyUpdateSerializer,
    SchoolConvenienceFeePolicySerializer,
    SchoolConvenienceFeePolicyUpdateSerializer,
    SchoolQrisConfigSerializer,
    SchoolQrisConfigUpdateSerializer,
    SiblingDiscountPolicySerializer,
    StudentFeeAssignmentSerializer,
    StudentVirtualAccountSerializer,
)

from apps.finance.services import (
    InstallmentPlanAlreadyExistsError,
    InvalidInstallmentError,
    InvalidProofFileError,
    approve_discount,
    approve_invoice_write_off,
    cancel_installment_plan,
    cancel_invoice,
    create_discount_with_approval_check,
    create_installment_plan,
    calculate_convenience_fee,
    generate_monthly_invoices,
    get_ar_aging_report,
    get_bank_sftp_configs,
    get_effective_convenience_fee_policy,
    get_school_arrears_policy,
    get_school_qris_config,
    reject_invoice_write_off,
    request_invoice_write_off,
    set_bank_sftp_config,
    set_school_convenience_fee_policy,
    set_school_qris_config,
    store_payment_proof_file,
    write_off_invoice,
)
from educore.middleware.tenancy import get_current_foundation_id, tenant_context


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


class InvoiceViewSet(viewsets.ModelViewSet):
    """Student invoice management and batch generation (spec/06 §3, §8)."""
    serializer_class = InvoiceSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'finance.invoice.read',
        'retrieve': 'finance.invoice.read',
        'create': 'finance.invoice.write',
        'update': 'finance.invoice.write',
        'partial_update': 'finance.invoice.write',
        'destroy': 'finance.invoice.write',
        'generate': 'finance.invoice.write',
        'cancel': 'finance.invoice.write',
        'write_off': 'finance.invoice.write',
        'installments': 'finance.invoice.read',
    }

    def get_required_permission(self):
        if self.action == 'installments':
            if self.request.method in ['GET', 'HEAD', 'OPTIONS']:
                return 'finance.invoice.read'
            return 'finance.invoice.write'
        return self.action_permissions.get(self.action)

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return Invoice.objects.none()
        qs = Invoice.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).prefetch_related('lines', 'installments').order_by('-created_at')

        user = self.request.user
        if not user or not user.is_authenticated:
            return Invoice.objects.none()

        if not user.is_superuser:
            from apps.identity.models import RoleAssignment
            from apps.identity.guardian_access import get_guardian_student_ids

            has_fnd_admin = RoleAssignment.all_tenants.filter(
                foundation_id=foundation_id,
                user=user,
                role__in=[
                    RoleAssignment.ROLE_FOUNDATION_ADMIN,
                    RoleAssignment.ROLE_FINANCE_OFFICER,
                ],
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                deleted_at__isnull=True,
            ).exists()

            if not has_fnd_admin:
                staff_school_ids = set(RoleAssignment.all_tenants.filter(
                    foundation_id=foundation_id,
                    user=user,
                    role__in=[
                        RoleAssignment.ROLE_SCHOOL_ADMIN,
                        RoleAssignment.ROLE_FINANCE_OFFICER,
                    ],
                    scope_type=RoleAssignment.SCOPE_SCHOOL,
                    deleted_at__isnull=True,
                ).values_list('scope_id', flat=True))

                financial_student_ids = get_guardian_student_ids(user, foundation_id, financial_only=True)

                if staff_school_ids and financial_student_ids:
                    from django.db.models import Q
                    qs = qs.filter(Q(school_id__in=staff_school_ids) | Q(student_id__in=financial_student_ids))
                elif staff_school_ids:
                    qs = qs.filter(school_id__in=staff_school_ids)
                elif financial_student_ids:
                    qs = qs.filter(student_id__in=financial_student_ids)
                else:
                    return Invoice.objects.none()
        
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
            
        student_id = self.request.query_params.get('student_id')
        if student_id:
            qs = qs.filter(student_id=student_id)
            
        period = self.request.query_params.get('period')
        if period:
            qs = qs.filter(period=period)
            
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        overdue_param = self.request.query_params.get('overdue')
        if overdue_param and overdue_param.lower() in ['true', '1']:
            qs = qs.filter(
                due_date__lt=timezone.localdate(),
                status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID],
            )

        return qs

    @action(detail=False, methods=['post'], url_path='generate')
    def generate(self, request):
        """Monthly batch invoice generation / dry-run preview (FIN-002, FIN-008)."""
        foundation_id = get_current_foundation_id()
        school_id = request.data.get('school_id')
        period = request.data.get('period')
        dry_run = request.data.get('dry_run', False)

        if not school_id or not period:
            return Response(
                {'error': _("Parameter school_id dan period wajib diisi.")},
                status=status.HTTP_400_BAD_REQUEST
            )

        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        with tenant_context(foundation_id):
            report = generate_monthly_invoices(
                school=school,
                period=period,
                dry_run=dry_run,
                triggered_by=request.user,
            )

        return Response(report, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        """Cancel an unpaid invoice (FIN-009)."""
        invoice = self.get_object()
        reason = request.data.get('reason', '')
        try:
            cancelled = cancel_invoice(invoice, request.user, reason)
            return Response(self.get_serializer(cancelled).data)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='write-off')
    def write_off(self, request, pk=None):
        """Write off an overdue invoice (FIN-009)."""
        invoice = self.get_object()
        reason = request.data.get('reason', '')
        try:
            written_off = write_off_invoice(invoice, request.user, reason)
            return Response(self.get_serializer(written_off).data)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='installments')
    def installments(self, request, pk=None):
        """
        Manage tuition installment plans (spec/06 §6, §8, FIN-030).
        - GET: List installments for this invoice.
        - POST: Create an installment plan (count or schedule).
        - DELETE: Cancel unpaid installments on this invoice.
        """
        invoice = self.get_object()

        if request.method == 'GET':
            installments_qs = invoice.installments.filter(deleted_at__isnull=True).order_by('installment_no')
            serializer = InvoiceInstallmentSerializer(installments_qs, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        elif request.method == 'POST':
            serializer = CreateInstallmentPlanSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data

            try:
                created = create_installment_plan(
                    invoice=invoice,
                    count=data.get('count'),
                    schedule=data.get('schedule'),
                    first_due_date=data.get('first_due_date'),
                    interval_days=data.get('interval_days', 30),
                    user=request.user,
                    notes=data.get('notes', ''),
                )
                output_serializer = InvoiceInstallmentSerializer(created, many=True)
                return Response(output_serializer.data, status=status.HTTP_201_CREATED)
            except InstallmentPlanAlreadyExistsError as e:
                return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
            except InvalidInstallmentError as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        elif request.method == 'DELETE':
            reason = request.data.get('reason', '') if isinstance(request.data, dict) else ''
            try:
                cancelled = cancel_installment_plan(invoice=invoice, user=request.user, reason=reason)
                output_serializer = InvoiceInstallmentSerializer(cancelled, many=True)
                return Response(output_serializer.data, status=status.HTTP_200_OK)
            except InvalidInstallmentError as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class PaymentIntentViewSet(viewsets.ModelViewSet):
    """Payment intents for VA and QRIS (spec/06 §2, §4)."""
    serializer_class = PaymentIntentSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'finance.invoice.read',
        'retrieve': 'finance.invoice.read',
        'create': 'finance.invoice.write',
    }

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return PaymentIntent.objects.none()
        qs = PaymentIntent.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('-created_at')
        student_id = self.request.query_params.get('student_id')
        if student_id:
            qs = qs.filter(student_id=student_id)
        return qs

    def create(self, request, *args, **kwargs):
        foundation_id = get_current_foundation_id()
        serializer = PaymentIntentCreateSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from apps.identity.models import Student
        invoices = list(Invoice.objects.filter(id__in=data['invoice_ids'], foundation_id=foundation_id))
        if not invoices:
            return Response({'error': _("Tagihan tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        student = invoices[0].student
        school = invoices[0].school

        from apps.finance.services.payments import create_payment_intent
        try:
            with tenant_context(foundation_id):
                intent = create_payment_intent(
                    school=school,
                    student=student,
                    invoice_ids=data['invoice_ids'],
                    method=data['method'],
                    bank=data.get('bank'),
                    provider_name=data.get('provider', 'MOCK'),
                )
            return Response(self.get_serializer(intent).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class PaymentViewSet(viewsets.ModelViewSet):
    """Payments, cash desk collection, and manual transfer verification (spec/06 §4)."""
    serializer_class = PaymentSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'finance.invoice.read',
        'retrieve': 'finance.invoice.read',
        'cash': 'finance.invoice.write',
        'manual': 'finance.invoice.write',
        'verify': 'finance.invoice.write',
        'upload_proof': 'finance.invoice.write',
    }

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return Payment.objects.none()
        qs = Payment.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).prefetch_related('allocations').order_by('-created_at')
        student_id = self.request.query_params.get('student_id')
        if student_id:
            qs = qs.filter(student_id=student_id)
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    @action(detail=False, methods=['post'], url_path='cash')
    def cash(self, request):
        """Cash collection at school desk with receipt generation (FIN-019)."""
        foundation_id = get_current_foundation_id()
        serializer = CashPaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from apps.identity.models import Student
        student = Student.objects.filter(id=data['student_id'], foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from apps.finance.services.payments import record_cash_payment
        try:
            with tenant_context(foundation_id):
                payment = record_cash_payment(
                    school=student.school,
                    student=student,
                    amount=data['amount'],
                    invoice_ids=data.get('invoice_ids'),
                    received_by=request.user,
                    notes=data.get('notes', ''),
                )
            return Response(self.get_serializer(payment).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='manual')
    def manual(self, request):
        """Submit manual bank transfer proof for verification (FIN-018)."""
        foundation_id = get_current_foundation_id()
        serializer = ManualPaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from apps.identity.models import Student
        student = Student.objects.filter(id=data['student_id'], foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from apps.finance.services.payments import submit_manual_transfer
        try:
            with tenant_context(foundation_id):
                payment = submit_manual_transfer(
                    school=student.school,
                    student=student,
                    amount=data['amount'],
                    invoice_ids=data.get('invoice_ids'),
                    proof_file=data.get('proof_file', ''),
                    channel=data.get('channel', 'MANUAL_TRANSFER'),
                    notes=data.get('notes', ''),
                )
            return Response(self.get_serializer(payment).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='upload-proof', parser_classes=[MultiPartParser])
    def upload_proof(self, request):
        """Upload a payment proof (manual transfer or static QRIS receipt) ahead of
        submitting it via `manual` (FIN-018)."""
        foundation_id = get_current_foundation_id()
        serializer = PaymentProofUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        student_id = request.data.get('student_id')
        from apps.identity.models import Student
        student = Student.objects.filter(id=student_id, foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        try:
            file_meta = store_payment_proof_file(student.school, serializer.validated_data['file'])
        except InvalidProofFileError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(file_meta, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='verify')
    def verify(self, request, pk=None):
        """Finance officer verifies or rejects manual transfer (FIN-018)."""
        foundation_id = get_current_foundation_id()
        payment = self.get_object()
        serializer = ManualPaymentVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from apps.finance.services.payments import verify_manual_transfer
        try:
            with tenant_context(foundation_id):
                verified = verify_manual_transfer(
                    payment=payment,
                    verified_by=request.user,
                    decision=data['decision'],
                    reason=data.get('reason', ''),
                )
            return Response(self.get_serializer(verified).data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


from rest_framework.views import APIView


class PaymentWebhookView(APIView):
    """Public signature-verified payment gateway webhook (FIN-013)."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, provider):
        payload = request.data
        headers = request.headers

        from apps.finance.services.payments import process_payment_webhook
        try:
            result = process_payment_webhook(
                provider_name=provider,
                payload=payload,
                headers=headers,
            )
            return Response(result, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class LedgerJournalViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only ledger journals and double-entry postings (FIN-021, FIN-022)."""
    serializer_class = LedgerJournalSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'finance.invoice.read',
        'retrieve': 'finance.invoice.read',
    }

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return LedgerJournal.objects.none()
        qs = LedgerJournal.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).prefetch_related('entries').order_by('-created_at')
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        ref_type = self.request.query_params.get('ref_type')
        if ref_type:
            qs = qs.filter(ref_type=ref_type)
        return qs


class StudentStatementView(APIView):
    """Ledger-backed student statement (spec/06 §8)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'finance.invoice.read'

    def get(self, request, pk):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return Response({'error': _("Konteks yayasan tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from apps.identity.models import Student
        student = Student.objects.filter(id=pk, foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        invoices = Invoice.objects.filter(student=student, foundation_id=foundation_id, deleted_at__isnull=True).order_by('-period')
        payments = Payment.objects.filter(student=student, foundation_id=foundation_id, status=PaymentStatus.SETTLED, deleted_at__isnull=True).order_by('-paid_at')
        credit_balance = StudentCreditBalance.objects.filter(student=student, foundation_id=foundation_id).first()

        data = {
            'student_id': student.id,
            'student_name': student.person.full_name,
            'credit_balance': {
                'amount': str(credit_balance.balance) if credit_balance else '0.00',
                'currency': credit_balance.currency if credit_balance else 'IDR',
            },
            'invoices': InvoiceSerializer(invoices, many=True).data,
            'payments': PaymentSerializer(payments, many=True).data,
        }
        return Response(data, status=status.HTTP_200_OK)


class SchoolQrisConfigView(APIView):
    """GET/PUT /schools/:school_id/qris-config/ — a school's static QRIS (FIN-010)."""
    permission_classes = [HasRequiredPermission]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_required_permission(self):
        return 'finance.invoice.write' if self.request.method == 'PUT' else 'finance.invoice.read'

    def get(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        config = get_school_qris_config(school)
        if not config:
            return Response({'error': _("Konfigurasi QRIS belum diatur.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(SchoolQrisConfigSerializer(config).data)

    def put(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        payload = SchoolQrisConfigUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            config = set_school_qris_config(
                school,
                qris_image=payload.validated_data.get('qris_image'),
                qris_payload=payload.validated_data.get('qris_payload', ''),
                is_active=payload.validated_data.get('is_active', True),
            )
        except InvalidProofFileError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SchoolQrisConfigSerializer(config).data, status=status.HTTP_200_OK)


class SchoolConvenienceFeePolicyView(APIView):
    """GET/PUT /schools/:school_id/convenience-fee-policy/ — a school's gateway
    convenience fee allocation and schedule (FIN-017). GET returns the platform
    default (PASSED_TO_PARENT, fee_value=0) when the school has no explicit
    override, consistent with get_effective_convenience_fee_policy.
    """
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'finance.invoice.write' if self.request.method == 'PUT' else 'finance.invoice.read'

    def get(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        config = SchoolConvenienceFeePolicy.objects.filter(school=school).first()
        if config:
            return Response(SchoolConvenienceFeePolicySerializer(config).data)
        default_policy = get_effective_convenience_fee_policy(school)
        return Response({**default_policy, 'fee_value': str(default_policy['fee_value'])})

    def put(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        payload = SchoolConvenienceFeePolicyUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        config = set_school_convenience_fee_policy(
            school,
            allocation=payload.validated_data['allocation'],
            fee_type=payload.validated_data['fee_type'],
            fee_value=payload.validated_data['fee_value'],
            is_active=payload.validated_data.get('is_active', True),
        )
        return Response(SchoolConvenienceFeePolicySerializer(config).data, status=status.HTTP_200_OK)


class BankSftpConfigListView(APIView):
    """GET/PUT /schools/:school_id/bank-sftp-configs/ — CMP-024/CMP-026 SFTP pull
    connection metadata. GET lists all bank configs for the school; PUT upserts
    one, keyed by 'bank_code' in the body. Never accepts a password/private key
    — see apps.finance.services.bank_sftp_pull's module docstring.
    """
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'finance.invoice.write' if self.request.method == 'PUT' else 'finance.invoice.read'

    def get(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        configs = get_bank_sftp_configs(school)
        return Response(BankSftpConfigSerializer(configs, many=True).data)

    def put(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        payload = BankSftpConfigUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        config = set_bank_sftp_config(
            school,
            bank_code=payload.validated_data['bank_code'],
            host=payload.validated_data['host'],
            port=payload.validated_data.get('port', 22),
            username=payload.validated_data.get('username', ''),
            remote_directory=payload.validated_data.get('remote_directory', '/'),
            file_format=payload.validated_data.get('file_format', 'MT940'),
            is_active=payload.validated_data.get('is_active', True),
        )
        return Response(BankSftpConfigSerializer(config).data, status=status.HTTP_200_OK)


class SchoolArrearsPolicyView(APIView):
    """GET/PUT /schools/:school_id/arrears-policy/ — a school's arrears reminder policy (spec/06 §6, FIN-026)."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'finance.invoice.write' if self.request.method in ['PUT', 'PATCH'] else 'finance.invoice.read'

    def get(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        policy = get_school_arrears_policy(school)
        return Response(SchoolArrearsPolicySerializer(policy).data, status=status.HTTP_200_OK)

    def put(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        payload = SchoolArrearsPolicyUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        policy = get_school_arrears_policy(school)
        if 'ladder_days' in payload.validated_data:
            policy.ladder_days = payload.validated_data['ladder_days']
        if 'is_active' in payload.validated_data:
            policy.is_active = payload.validated_data['is_active']
        if 'payment_deep_link_base' in payload.validated_data:
            policy.payment_deep_link_base = payload.validated_data['payment_deep_link_base']
        policy.save()

        return Response(SchoolArrearsPolicySerializer(policy).data, status=status.HTTP_200_OK)


class InvoiceWriteOffRequestViewSet(viewsets.ModelViewSet):
    """Approval workflow for bad debt write-offs (FIN-031)."""
    serializer_class = InvoiceWriteOffRequestSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'finance.invoice.read',
        'retrieve': 'finance.invoice.read',
        'create': 'finance.invoice.write',
        'approve': 'finance.invoice.write',
        'reject': 'finance.invoice.write',
    }

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return InvoiceWriteOffRequest.objects.none()
        qs = InvoiceWriteOffRequest.objects.filter(
            foundation_id=foundation_id,
            deleted_at__isnull=True
        ).select_related('invoice', 'school', 'requested_by', 'approved_by', 'journal').order_by('-created_at')

        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)

        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        invoice_id = self.request.query_params.get('invoice_id')
        if invoice_id:
            qs = qs.filter(invoice_id=invoice_id)

        return qs

    def create(self, request, *args, **kwargs):
        foundation_id = get_current_foundation_id()
        serializer = InvoiceWriteOffRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        invoice = Invoice.objects.filter(
            id=serializer.validated_data['invoice_id'],
            foundation_id=foundation_id,
            deleted_at__isnull=True
        ).first()
        if not invoice:
            return Response({'error': _("Tagihan tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        try:
            req_obj = request_invoice_write_off(
                invoice=invoice,
                user=request.user,
                reason=serializer.validated_data['reason'],
                amount=serializer.validated_data.get('amount'),
            )
            return Response(InvoiceWriteOffRequestSerializer(req_obj).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """Approve a bad debt write-off request (Foundation Admin required, FIN-031)."""
        instance = self.get_object()
        serializer = InvoiceWriteOffResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            approved = approve_invoice_write_off(
                request_obj=instance,
                user=request.user,
                notes=serializer.validated_data.get('notes', ''),
            )
            return Response(InvoiceWriteOffRequestSerializer(approved).data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        """Reject a bad debt write-off request (Foundation Admin required, FIN-031)."""
        instance = self.get_object()
        serializer = InvoiceWriteOffResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rejected = reject_invoice_write_off(
                request_obj=instance,
                user=request.user,
                notes=serializer.validated_data.get('notes', ''),
            )
            return Response(InvoiceWriteOffRequestSerializer(rejected).data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ArAgingView(APIView):
    """GET /api/v1/finance/ar-aging/ — AR Aging report across 0-30, 31-60, 61-90, 90+ buckets (FIN-029)."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'finance.invoice.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return Response({'error': _("Konteks yayasan diperlukan.")}, status=status.HTTP_400_BAD_REQUEST)

        as_of_str = request.query_params.get('as_of')
        as_of = None
        if as_of_str:
            from datetime import date
            try:
                as_of = date.fromisoformat(as_of_str)
            except ValueError:
                return Response({'error': _("Format as_of tidak valid. Gunakan YYYY-MM-DD.")}, status=status.HTTP_400_BAD_REQUEST)

        school_id = request.query_params.get('school_id')
        school = None
        if school_id:
            school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
            if not school:
                return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        class_group_id = request.query_params.get('class_group_id')
        student_id = request.query_params.get('student_id')

        report_data = get_ar_aging_report(
            foundation_id=foundation_id,
            as_of=as_of,
            school=school,
            class_group_id=class_group_id,
            student_id=student_id,
        )
        return Response(report_data, status=status.HTTP_200_OK)




# ---------------------------------------------------------------------------
# Step 8.1 — Gateway Reconciliation Views (FIN-024)
# ---------------------------------------------------------------------------

from apps.finance.models import (
    GatewaySettlementBatch,
    PaymentDiscrepancy,
    DiscrepancyResolution,
)
from apps.finance.services.reconciliation import (
    reconcile_bank_statement_file,
    resolve_discrepancy as svc_resolve_discrepancy,
)


class GatewaySettlementBatchSerializer:
    """Inline minimal serializer — avoids a separate serializers.py change for this slice."""
    @staticmethod
    def to_representation(batch):
        return {
            'id': batch.id,
            'provider': batch.provider,
            'settlement_date': str(batch.settlement_date),
            'status': batch.status,
            'fetched_at': batch.fetched_at.isoformat() if batch.fetched_at else None,
            'total_records': batch.total_records,
            'matched_count': batch.matched_count,
            'missing_count': batch.missing_count,
            'mismatch_count': batch.mismatch_count,
            'error_message': batch.error_message,
        }


class PaymentDiscrepancySerializer:
    """Inline minimal serializer for PaymentDiscrepancy."""
    @staticmethod
    def to_representation(d):
        return {
            'id': d.id,
            'batch_id': d.batch_id,
            'external_id': d.external_id,
            'discrepancy_type': d.discrepancy_type,
            'gateway_amount': str(d.gateway_amount),
            'gateway_fee': str(d.gateway_fee),
            'gateway_net': str(d.gateway_net),
            'system_amount': str(d.system_amount) if d.system_amount is not None else None,
            'currency': d.currency,
            'channel': d.channel,
            'bank': d.bank,
            'gateway_settled_at': d.gateway_settled_at.isoformat() if d.gateway_settled_at else None,
            'resolution': d.resolution,
            'resolved_by': d.resolved_by_id,
            'resolved_at': d.resolved_at.isoformat() if d.resolved_at else None,
            'resolution_notes': d.resolution_notes,
        }


class ReconciliationBatchListView(APIView):
    """GET /finance/reconciliation/batches/  — list settlement batches for the tenant.

    Query params:
    - settlement_date (YYYY-MM-DD): filter by date
    - provider: filter by provider name
    """
    permission_classes = [permissions.IsAuthenticated, HasRequiredPermission]
    required_permission = 'finance.payment.read'

    def get(self, request):
        foundation_id = request.user.foundation_id
        qs = GatewaySettlementBatch.objects.filter(
            foundation_id=foundation_id,
            deleted_at__isnull=True,
        ).order_by('-settlement_date', 'provider')

        if settlement_date := request.query_params.get('settlement_date'):
            qs = qs.filter(settlement_date=settlement_date)
        if provider := request.query_params.get('provider'):
            qs = qs.filter(provider__iexact=provider)

        data = [GatewaySettlementBatchSerializer.to_representation(b) for b in qs[:100]]
        return Response(data)


class ReconciliationBatchDetailView(APIView):
    """GET /finance/reconciliation/batches/<id>/  — single batch with discrepancies."""
    permission_classes = [permissions.IsAuthenticated, HasRequiredPermission]
    required_permission = 'finance.payment.read'

    def get(self, request, pk):
        foundation_id = request.user.foundation_id
        try:
            batch = GatewaySettlementBatch.objects.get(
                id=pk,
                foundation_id=foundation_id,
                deleted_at__isnull=True,
            )
        except GatewaySettlementBatch.DoesNotExist:
            return Response({'error': _("Batch tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        discrepancies = batch.discrepancies.filter(deleted_at__isnull=True)
        data = GatewaySettlementBatchSerializer.to_representation(batch)
        data['discrepancies'] = [PaymentDiscrepancySerializer.to_representation(d) for d in discrepancies]
        return Response(data)


class ReconciliationDiscrepancyResolveView(APIView):
    """POST /finance/reconciliation/discrepancies/<id>/resolve/

    Body: { "resolution": "MANUAL_SETTLED|WAIVED|ESCALATED", "notes": "..." }
    """
    permission_classes = [permissions.IsAuthenticated, HasRequiredPermission]
    required_permission = 'finance.payment.write'

    def post(self, request, pk):
        foundation_id = request.user.foundation_id
        resolution = request.data.get('resolution', '').upper()
        notes = request.data.get('notes', '')

        if not resolution:
            return Response(
                {'error': _("Field 'resolution' wajib diisi.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            discrepancy = svc_resolve_discrepancy(
                discrepancy_id=pk,
                resolution=resolution,
                resolved_by=request.user,
                foundation_id=foundation_id,
                notes=notes,
            )
        except PaymentDiscrepancy.DoesNotExist:
            return Response({'error': _("Discrepancy tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(PaymentDiscrepancySerializer.to_representation(discrepancy))


class ReconciliationBankStatementUploadView(APIView):
    """POST /finance/reconciliation/bank-statements/upload/  — spec/14 CMP-026.

    Upload a bank-provided MT940 or CAMT.053 statement file for direct-bank-VA
    settlement reconciliation (the file-based counterpart to the gateway
    reconciliation batches above — see reconcile_bank_statement_file).

    Body (multipart): file, format ('MT940'|'CAMT053'), bank_code (e.g. 'BCA'),
    settlement_date ('YYYY-MM-DD'), dry_run (optional, default false).
    """
    permission_classes = [permissions.IsAuthenticated, HasRequiredPermission]
    required_permission = 'finance.payment.write'
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        foundation_id = request.user.foundation_id
        uploaded_file = request.FILES.get('file')
        file_format = request.data.get('format', '')
        bank_code = request.data.get('bank_code', '')
        settlement_date_str = request.data.get('settlement_date', '')
        dry_run = str(request.data.get('dry_run', 'false')).lower() in ('true', '1')

        if not uploaded_file:
            return Response({'error': _("Field 'file' wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)
        if not bank_code:
            return Response({'error': _("Field 'bank_code' wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)

        try:
            settlement_date = timezone.datetime.strptime(settlement_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return Response(
                {'error': _("Field 'settlement_date' harus format YYYY-MM-DD.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_content = uploaded_file.read()
        content = raw_content.decode('utf-8', errors='replace') if file_format.upper() == 'MT940' else raw_content

        result = reconcile_bank_statement_file(
            file_content=content,
            file_format=file_format,
            bank_code=bank_code,
            settlement_date=settlement_date,
            foundation_id=foundation_id,
            dry_run=dry_run,
        )
        if 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)
