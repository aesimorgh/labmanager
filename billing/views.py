from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.utils.translation import gettext as _
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.db.models import F, ExpressionWrapper, DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP
import uuid
from django.views.decorators.clickjacking import xframe_options_exempt
from django.urls import reverse
from django.db import transaction, IntegrityError
from core.models import Order
from .forms import InvoiceDraftFilterForm


def _filter_by_doctor(qs, doctor_obj):
    """
    فیلتر سفارش‌ها بر اساس نام دکتر (Order.doctor متنی است).
    """
    if doctor_obj:
        name = (getattr(doctor_obj, "name", "") or "").strip()
        if name:
            return qs.filter(doctor__iexact=name)
    return qs


@method_decorator(login_required, name='dispatch')
@method_decorator([login_required, xframe_options_exempt], name='dispatch')
class InvoiceCreateDraftView(View):
    """
    صفحه‌ی «ایجاد فاکتور (Draft)».
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        form = InvoiceDraftFilterForm(request.GET or None)
        orders = None

        if form.is_valid():
            doctor = form.cleaned_data['doctor']
            period_from = form.cleaned_data['period_from']
            period_to = form.cleaned_data['period_to']
            include_already = form.cleaned_data['include_already_invoiced']

            # فقط سفارش‌های تحویل‌شده در بازه shipped_date
            qs = Order.objects.all().filter(status='delivered')
            qs = qs.filter(shipped_date__gte=period_from, shipped_date__lte=period_to)

            if doctor:
                qs = _filter_by_doctor(qs, doctor)

            # حذف سفارش‌های قبلاً فاکتور شده، مگر اینکه کاربر تیک زده باشد
            if not include_already:
                qs = qs.filter(invoice_line__isnull=True)

            # جمع خط (unit_count * price) اگر فیلدها موجود باشند
            try:
                qs = qs.annotate(
                    line_total_calc=ExpressionWrapper(
                        F('unit_count') * F('price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2)
                    )
                )
            except Exception:
                pass

            orders = qs.order_by('-shipped_date', '-id')

        context = {"form": form, "orders": orders}
        return render(request, "billing/invoice_create_draft_app.html", context)

    def post(self, request: HttpRequest) -> HttpResponse:
        """
        ساخت پیش‌نویس فاکتور از سفارش‌های انتخاب‌شده
        + قفل سروری: جلوگیری از دوباره‌فاکتورشدن
        """
        order_ids = request.POST.getlist("order_ids")
        if not order_ids:
            return HttpResponse(_("هیچ سفارشی انتخاب نشده است."), status=400)

        from billing.models import Invoice, InvoiceLine
        from core.models import Order, Doctor

        # جدا کردن سفارش‌های قابل فاکتور از قبلاً فاکتورشده
        eligible_qs = Order.objects.filter(id__in=order_ids, invoice_line__isnull=True)
        already_qs = Order.objects.filter(id__in=order_ids, invoice_line__isnull=False)

        if not eligible_qs.exists():
            return HttpResponse(_("همهٔ سفارش‌های انتخاب‌شده قبلاً فاکتور شده‌اند."), status=400)

        # خواندن داده‌های فرم
        doctor_id = request.POST.get("doctor")
        period_from = request.POST.get("period_from")
        period_to = request.POST.get("period_to")

        doctor_obj = None
        if doctor_id:
            try:
                doctor_obj = Doctor.objects.get(pk=doctor_id)
            except Doctor.DoesNotExist:
                doctor_obj = None

        # کُد موقت Draft
        draft_code = f"DRAFT-{timezone.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

        invoice = Invoice.objects.create(
            code=draft_code,
            doctor=doctor_obj,
            status=Invoice.Status.DRAFT,
            notes=f"فاکتور پیش‌نویس از {eligible_qs.count()} سفارش",
        )

        # ذخیرهٔ بازهٔ تاریخ (در صورت وجود روی مدل)
        if hasattr(invoice, "period_from"):
            invoice.period_from = period_from
        if hasattr(invoice, "period_to"):
            invoice.period_to = period_to
        invoice.save()

        # ساخت خطوط فقط برای سفارش‌های «قابل فاکتور»
        for o in eligible_qs:
            try:
                uc = o.unit_count or 1
                up = o.price or Decimal("0")
                uc = Decimal(str(uc))
                up = Decimal(str(up))
                tp = getattr(o, "total_price", None)
                try:
                    tp = Decimal(str(tp)) if tp not in (None, "") else None
                except Exception:
                    tp = None
                line_total = tp if (tp is not None and tp > 0) else (uc * up)

                InvoiceLine.objects.create(
                    invoice=invoice,
                    order=o,
                    description=f"Order #{o.id}",
                    unit_count=uc,
                    unit_price=up,
                    discount_amount=Decimal("0"),
                    line_total=line_total,
                )
            except Exception as ex:
                print("InvoiceLine error:", ex)

        # اگر به هر دلیل خطی ساخته نشد، پیش‌نویس را حذف کن
        if not invoice.lines.exists():
            invoice.delete()
            return HttpResponse(_("هیچ خطی ساخته نشد. احتمالاً همهٔ سفارش‌ها قبلاً فاکتور شده بودند."), status=400)

        # محاسبهٔ جمع‌ها
        try:
            invoice.recompute_totals()
        except Exception as ex:
            print("recompute_totals error:", ex)

        # ری‌دایرکت به جزئیات با حفظ embed=1 در صورت وجود
        embed = (request.GET.get("embed") == "1") or (request.POST.get("embed") == "1")
        detail_url = reverse("billing:invoice_detail", kwargs={"pk": invoice.id})
        if embed:
            detail_url = f"{detail_url}?embed=1"
        return redirect(detail_url)


# ===== Helper: نرمال‌سازی ورودی عددی (فارسی/عربی/ویرگول) به Decimal =====
def _to_decimal(val, default=None):
    """
    '۱۲۳,۴۵۶٫۷۸' یا '123,456.78' یا '۱۲۳۴' → Decimal
    اگر نشد، default برمی‌گرداند.
    """
    if val in (None, ""):
        return default
    s = str(val).strip()
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬,", "0123456789" "0123456789" "..,")
    s = s.translate(trans).replace(",", "").strip()
    try:
        return Decimal(s)
    except Exception:
        return default


# --- NEW: نام فیلد مبلغ در PaymentAllocation را به‌صورت داینامیک پیدا کن
def _alloc_field_name():
    try:
        from billing.models import PaymentAllocation
        names = {f.name for f in PaymentAllocation._meta.get_fields()}
        if 'amount' in names:
            return 'amount'
        if 'amount_allocated' in names:
            return 'amount_allocated'
    except Exception:
        pass
    return None


def _compute_display_totals(invoice):
    """
    محاسبهٔ مقادیر نمایشی با درنظرگرفتن:
      - جمع خطوط
      - تخفیف خطوط
      - تخفیف فاکتور (invoice.discount_amount)
      - ماندهٔ قبلی (previous_balance)
      - مجموع پرداخت‌های تخصیص‌یافته (PaymentAllocation) → کسر از بدهی
    """
    from decimal import Decimal
    from django.db.models import Sum
    from django.db.models.functions import Coalesce
    try:
        from billing.models import PaymentAllocation
    except Exception:
        PaymentAllocation = None  # اگر مدل هنوز ساخته نشده

    # جمع خطوط و تخفیف خطوط
    agg = invoice.lines.aggregate(
        sum_lines=Coalesce(Sum('line_total'), Decimal('0')),
        sum_line_discounts=Coalesce(Sum('discount_amount'), Decimal('0')),
    )
    sum_lines = agg['sum_lines'] or Decimal('0')
    sum_line_discounts = agg['sum_line_discounts'] or Decimal('0')

    # تخفیف فاکتور
    inv_disc = getattr(invoice, 'discount_amount', None)
    try:
        inv_disc = Decimal(str(inv_disc)) if inv_disc not in (None, "") else Decimal('0')
    except Exception:
        inv_disc = Decimal('0')

    # کل پس از تخفیف‌ها
    total_amount = (sum_lines - sum_line_discounts - inv_disc)
    if total_amount < 0:
        total_amount = Decimal('0')

    # ماندهٔ قبلی
    prev = getattr(invoice, 'previous_balance', None)
    try:
        previous_balance = Decimal(str(prev)) if prev not in (None, "") else Decimal('0')
    except Exception:
        previous_balance = Decimal('0')

    # مجموع تخصیصِ پرداخت‌ها (با تشخیص نام فیلد)
    allocated = Decimal('0')
    if PaymentAllocation:
        field = _alloc_field_name()
        if field:
            allocated = PaymentAllocation.objects.filter(invoice=invoice).aggregate(
                s=Coalesce(Sum(field), Decimal('0'))
            )['s'] or Decimal('0')

    # بدهی نهایی
    amount_due = total_amount - allocated + previous_balance
    if amount_due < 0:
        amount_due = Decimal('0')

    # تزریق برای استفاده در قالب
    try:
        invoice.total_amount = total_amount
        invoice.amount_due = amount_due
    except Exception:
        pass

    return {
        "sum_lines": sum_lines,
        "sum_discounts": (sum_line_discounts + inv_disc),
        "total_amount": total_amount,
        "previous_balance": previous_balance,
        "allocated": allocated,
        "amount_due": amount_due,
    }

# ===== Helper: مجموع پرداخت‌های تخصیص‌یافته به این فاکتور =====
def _paid_total_for_invoice(invoice):
    """
    جمع PaymentAllocation.amount_allocated برای این فاکتور.
    اگر مدل/فیلد نبود یا خطایی شد، صفر برمی‌گرداند.
    """
    from billing.models import PaymentAllocation
    try:
        paid = PaymentAllocation.objects.filter(invoice=invoice).aggregate(
            s=Coalesce(Sum('amount_allocated'), Decimal('0'))
        )['s'] or Decimal('0')
    except Exception:
        paid = Decimal('0')
    return paid



def _allocate_payment_fifo(payment):
    """
    تخصیص پرداخت به فاکتورهای صادرشدهٔ همان دکتر به ترتیب قدیمی‌ترین → جدیدترین.
    نکته مهم: در این نسخه هیچ فیلدی روی Invoice به‌صورت مستقیم تغییر داده نمی‌شود؛
    فقط رکوردهای PaymentAllocation ساخته می‌شوند و مانده‌ها در نمایش از روی
    مجموع تخصیص‌ها محاسبه می‌شود.
    """
    from decimal import Decimal
    from billing.models import Invoice, PaymentAllocation

    remaining = Decimal(payment.amount or 0)
    if remaining <= 0:
        return

    # فقط فاکتورهای ISSUED همان دکتر، قدیمی‌ترها اول
    issued_val = getattr(Invoice.Status, 'ISSUED', 'issued')
    invoices = (
        Invoice.objects
        .filter(doctor=payment.doctor, status=issued_val)
        .order_by('issued_at', 'id')
    )

    field = _alloc_field_name()
    if not field:
        # اگر فیلد مبلغ در PaymentAllocation را پیدا نکردیم، تخصیص انجام نده
        return

    for inv in invoices:
        if remaining <= 0:
            break

        # جمع‌ها را بروز کن و بدهی باز «خالص» را بگیر
        try:
            inv.recompute_totals()
        except Exception:
            pass
        d = _compute_display_totals(inv)  # شامل کسر تخصیص‌های قبلی است
        open_due = d['amount_due']       # توجه: دیگر دوباره چیزی از آن کم نمی‌کنیم

        if open_due <= 0:
            continue

        alloc_amt = remaining if remaining <= open_due else open_due
        if alloc_amt <= 0:
            continue

        # ایجاد رکورد تخصیص
        kwargs = {'payment': payment, 'invoice': inv, field: alloc_amt}
        PaymentAllocation.objects.create(**kwargs)

        # مهم: اینجا دیگر inv.amount_due را دستکاری/ذخیره نمی‌کنیم
        remaining -= alloc_amt
        if remaining <= 0:
            break


@method_decorator([login_required, xframe_options_exempt], name='dispatch')
class InvoiceDetailView(View):
    """
    نمایش جزئیات یک فاکتور + ذخیره‌ی تخفیف/ماندهٔ قبلی
    """
    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        from billing.models import Invoice
        invoice = get_object_or_404(Invoice, pk=pk)

        # تازه‌سازی جمع‌ها از روی خطوط
        try:
            invoice.recompute_totals()
        except Exception as ex:
            print("recompute_totals error on detail:", ex)

        # جمع تخفیف خطوط
        discount_total_ctx = invoice.lines.aggregate(
            s=Coalesce(Sum('discount_amount'), Decimal('0'))
        )['s']

        # اعداد خامِ قابل پرداخت (بدون در نظر گرفتن پرداخت‌های قبلی)
        d = _compute_display_totals(invoice)  # total_amount, previous_balance, amount_due (خام)
        amount_due_raw_ctx = d['amount_due']

        # مجموع پرداخت‌های تخصیص‌یافته به این فاکتور
        paid_total_ctx = Decimal('0')
        try:
            from billing.models import PaymentAllocation
            paid_total_ctx = PaymentAllocation.objects.filter(invoice=invoice).aggregate(
                s=Coalesce(Sum('amount_allocated'), Decimal('0'))
            )['s'] or Decimal('0')
        except Exception:
            pass

        # مانده نهایی بعد از کسر پرداخت‌های قبلی
        amount_due_after_payments_ctx = amount_due_raw_ctx - paid_total_ctx
        if amount_due_after_payments_ctx < 0:
            amount_due_after_payments_ctx = Decimal('0')

        # ✅ فقط اضافه: پاس دادن پروفایل لابراتوار برای لوگو/اطلاعات بانکی
        LAB_PROFILE = None
        try:
            from .models import LabProfile
            LAB_PROFILE = LabProfile.objects.first()
        except Exception:
            LAB_PROFILE = None

        # خروجی به قالب
        ctx = {
            "invoice": invoice,
            "discount_total_ctx": discount_total_ctx,
            "amount_due_raw_ctx": amount_due_raw_ctx,
            "paid_total_ctx": paid_total_ctx,
            "amount_due_after_payments_ctx": amount_due_after_payments_ctx,
            "LAB_PROFILE": LAB_PROFILE,  # ← فقط این مورد جدید است
        }
        return render(request, "billing/invoice_detail.html", ctx)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from billing.models import Invoice
        invoice = get_object_or_404(Invoice, pk=pk)

        updated_fields = []

        # مانده قبلی
        if 'previous_balance' in request.POST and hasattr(invoice, 'previous_balance'):
            cur = getattr(invoice, 'previous_balance') or Decimal('0')
            new_val = _to_decimal(request.POST.get('previous_balance'), cur)
            if new_val is not None and new_val != cur:
                invoice.previous_balance = new_val
                updated_fields.append('previous_balance')

        # تخفیف فاکتور (و توزیع به خطوط به‌صورت نسبتی با گرد کردن Bankers/نیم‌بالا)
        if 'discount_amount' in request.POST:
            D = _to_decimal(request.POST.get('discount_amount'), Decimal('0')) or Decimal('0')
            if D < 0:
                D = -D
            if hasattr(invoice, 'discount_amount'):
                cur = getattr(invoice, 'discount_amount') or Decimal('0')
                if D != cur:
                    invoice.discount_amount = D
                    updated_fields.append('discount_amount')

            lines_qs = invoice.lines.all().order_by('id')
            subtotal = lines_qs.aggregate(s=Coalesce(Sum('line_total'), Decimal('0')))['s'] or Decimal('0')
            if subtotal <= 0 or not lines_qs.exists():
                # اگر ساب‌توتر سالم نبود، تخفیف خطوط را صفر کن
                for ln in lines_qs:
                    if getattr(ln, 'discount_amount', None) not in (None, Decimal('0')):
                        ln.discount_amount = Decimal('0')
                        try:
                            ln.save(update_fields=['discount_amount'])
                        except Exception:
                            ln.save()
            else:
                lines = list(lines_qs)
                allocated = Decimal('0')
                for i, ln in enumerate(lines):
                    if i < len(lines) - 1:
                        base = (Decimal(ln.line_total or 0) / subtotal) * D
                        amt = base.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                        allocated += amt
                    else:
                        amt = (D - allocated).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                        if amt < 0:
                            amt = Decimal('0')
                    # سقف: تخفیف خط از جمع خط بیشتر نشود
                    try:
                        max_cap = Decimal(ln.line_total or 0)
                        if amt > max_cap:
                            amt = max_cap
                    except Exception:
                        pass
                    if getattr(ln, 'discount_amount', None) != amt:
                        ln.discount_amount = amt
                        try:
                            ln.save(update_fields=['discount_amount'])
                        except Exception:
                            ln.save()

        if updated_fields:
            try:
                invoice.save(update_fields=updated_fields)
            except Exception as ex:
                print("invoice save error:", ex)

        try:
            invoice.recompute_totals()
        except Exception as ex:
            print("recompute_totals error on post:", ex)

        _ = _compute_display_totals(invoice)

        # برگشت به جزئیات با embed
        embed = (request.GET.get("embed") == "1") or (request.POST.get("embed") == "1")
        url = reverse("billing:invoice_detail", kwargs={"pk": invoice.id})
        if embed:
            url = f"{url}?embed=1"
        return redirect(url)



# ===== Helpers =====

def _generate_invoice_code():
    """
    تولید کد نهایی فاکتور: INV-YYYYMM-### (شماره‌گذاری ماهانه)
    - وضعیت را فیلتر نمی‌کند؛ همهٔ کدهای هم‌پیشوند را بررسی می‌کند.
    - بزرگ‌ترین شماره را می‌یابد و سپس تا پیدا شدن یک کد آزاد جلو می‌رود.
    """
    from billing.models import Invoice
    now = timezone.now()
    prefix = f"INV-{now.strftime('%Y%m')}-"  # مثل INV-202509-

    existing = Invoice.objects.filter(code__startswith=prefix).values_list('code', flat=True)
    max_seq = 0
    for c in existing:
        try:
            seq = int(str(c).split('-')[-1])
            if seq > max_seq:
                max_seq = seq
        except Exception:
            continue

    seq = max_seq + 1
    # اطمینان مضاعف
    while Invoice.objects.filter(code=f"{prefix}{seq:03d}").exists():
        seq += 1
    return f"{prefix}{seq:03d}"


@method_decorator(login_required, name='dispatch')
class InvoiceIssueView(View):
    """
    صدور فاکتور (Draft -> Issued) + تولید کد نهایی + ثبت زمان صدور
    """
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from billing.models import Invoice
        invoice = get_object_or_404(Invoice, pk=pk)

        # فقط Draft قابل صدور است
        if getattr(Invoice.Status, 'DRAFT', 'draft') != invoice.status and invoice.status != 'draft':
            return HttpResponseForbidden("Only draft invoices can be issued.")

        # یک‌بار محاسبهٔ جمع‌ها
        try:
            invoice.recompute_totals()
        except Exception:
            pass

        # تولید کد یکتا + ریتری روی برخورد احتمالی
        attempts = 5
        with transaction.atomic():
            for _ in range(attempts):
                try:
                    final_code = _generate_invoice_code()
                    invoice.code = final_code
                    invoice.status = getattr(Invoice.Status, 'ISSUED', 'issued')
                    if hasattr(invoice, 'issued_at'):
                        invoice.issued_at = timezone.now()
                    invoice.save(update_fields=['code', 'status', *( ['issued_at'] if hasattr(invoice, 'issued_at') else [] )])
                    break
                except IntegrityError:
                    # برخورد نادر؛ دوباره سعی کن
                    continue
            else:
                return HttpResponse(_("خطا در تولید کد یکتای فاکتور."), status=500)

        return redirect("billing:invoice_detail", pk=invoice.id)


@method_decorator(login_required, name='dispatch')
class InvoiceDeleteDraftView(View):
    """
    حذف پیش‌نویس فاکتور (فقط Draft).
    """
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from billing.models import Invoice
        invoice = get_object_or_404(Invoice, pk=pk)

        if getattr(Invoice.Status, 'DRAFT', 'draft') != invoice.status and invoice.status != 'draft':
            return HttpResponseForbidden("Only draft invoices can be deleted.")

        invoice.delete()
        return redirect("billing:invoice_create_draft")


@method_decorator(login_required, name='dispatch')
class InvoicePrintView(View):
    """
    نمای چاپی فاکتور (برای پرینت/ذخیره به PDF با Print مرورگر)
    """
    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        from billing.models import Invoice
        from .models import LabProfile
        invoice = get_object_or_404(Invoice, pk=pk)

        # تازه‌سازی جمع‌ها
        try:
            invoice.recompute_totals()
        except Exception as ex:
            print("recompute_totals error on print:", ex)

        # جمع تخفیف خطوط برای نمایش
        discount_total_ctx = invoice.lines.aggregate(
            s=Coalesce(Sum('discount_amount'), Decimal('0'))
        )['s']

        # جمع‌های نمایشی (بدون پرداخت)
        totals = _compute_display_totals(invoice)

        # پرداخت‌های قبلی این فاکتور
        paid_total_ctx = _paid_total_for_invoice(invoice)

        # مانده نهایی پس از پرداخت‌ها
        amount_due_after_payments_ctx = totals['amount_due'] - paid_total_ctx
        if amount_due_after_payments_ctx < 0:
            amount_due_after_payments_ctx = Decimal('0')

        # === اضافه‌شده: پاس دادن پروفایل به قالب (اگر باشد) ===
        LAB_PROFILE = None
        try:
            from .models import LabProfile
            LAB_PROFILE = LabProfile.objects.first()
        except Exception:
            LAB_PROFILE = None
        # ========================================================

        ctx = {
            "invoice": invoice,
            "discount_total_ctx": discount_total_ctx,
            "paid_total_ctx": paid_total_ctx,
            "amount_due_after_payments_ctx": amount_due_after_payments_ctx,
            "LAB_PROFILE": LAB_PROFILE,  # اضافه شد
        }

        # ⬅️ فقط این خط عوض شد تا ctx (که LAB_PROFILE داخلشه) به قالب برسد
        return render(request, "billing/invoice_print.html", ctx)




# ============ فاز ۲ — گام ۲.۱: DoctorAccountView (فقط خواندنی/متنی برای تست) ============
@method_decorator(login_required, name='dispatch')
class DoctorAccountView(View):
    """
    صفحهٔ حساب دکتر — رندر قالب billing/doctor_account.html
    جمع‌ها از روی خطوط محاسبه می‌شوند تا حتی اگر فیلدهای ذخیره‌شده ناقص بود،
    نمایش دقیق باشد.
    """
    def get(self, request: HttpRequest, doctor_id: int) -> HttpResponse:
        from core.models import Doctor
        from billing.models import Invoice
        try:
            from billing.models import DoctorPayment
        except Exception:
            DoctorPayment = None  # اگر مدل هنوز ساخته نشده

        doctor = get_object_or_404(Doctor, pk=doctor_id)

        # فاکتورهای دکتر + خطوط برای محاسبه
        invoices_qs = (
            Invoice.objects
            .filter(doctor=doctor)
            .prefetch_related('lines')
            .order_by('-issued_at', '-id')
        )
        invoices = list(invoices_qs)

        # محاسبهٔ امن از روی خطوط و تزریق مقادیر نمایشی به هر فاکتور
        total_amount_all = Decimal('0')
        issued_val = getattr(Invoice.Status, 'ISSUED', 'issued')
        total_amount_issued = Decimal('0')
        amount_due_issued = Decimal('0')

        for inv in invoices:
            try:
                inv.recompute_totals()
            except Exception:
                pass
            d = _compute_display_totals(inv)
            inv.total_amount = d['total_amount']
            inv.amount_due   = d['amount_due']

            total_amount_all += d['total_amount']
            if inv.status == issued_val:
                total_amount_issued += d['total_amount']
                amount_due_issued   += d['amount_due']

        # پرداخت‌ها (اگر مدل حاضر باشد) — دقت: فیلد تاریخ = 'date'
        payments = []
        total_paid = Decimal('0')
        if DoctorPayment:
            payments = list(
                DoctorPayment.objects.filter(doctor=doctor).order_by('-date', '-id')
            )
            total_paid = DoctorPayment.objects.filter(doctor=doctor).aggregate(
                s=Coalesce(Sum('amount'), Decimal('0'))
            )['s'] or Decimal('0')

        # ماندهٔ جاری: جمع فاکتورهای صادرشده - پرداخت‌ها
        running_balance = (total_amount_issued - total_paid)

        context = {
            "doctor": doctor,
            "invoices": invoices,
            "payments": payments,
            "invoice_count": len(invoices),
            "total_amount_all": total_amount_all,
            "total_paid": total_paid,
            "running_balance": running_balance,
        }
        return render(request, "billing/doctor_account.html", context)


@method_decorator(login_required, name='dispatch')
class DoctorPaymentCreateView(View):
    """
    ثبت پرداخت جدید برای دکتر + تخصیص FIFO به فاکتورهای صادرشده.
    ورودی‌های POST:
      - amount : مبلغ (فارسی/لاتین، مثل "۲,۵۰۰,۰۰۰")
      - date   : تاریخ میلادی به فرم YYYY/MM/DD یا YYYY-MM-DD (اختیاری؛ خالی = امروز)
      - method : روش پرداخت (اختیاری)
      - note   : توضیحات (اختیاری)
    """
    def post(self, request: HttpRequest, doctor_id: int) -> HttpResponse:
        from core.models import Doctor
        doctor = get_object_or_404(Doctor, pk=doctor_id)

        # مدل‌ها
        try:
            from billing.models import DoctorPayment
        except Exception:
            return HttpResponse(_("مدل پرداخت در سیستم موجود نیست."), status=500)

        # ورودی‌ها
        amount_raw = request.POST.get('amount')
        date_raw   = request.POST.get('date')   # YYYY/MM/DD یا YYYY-MM-DD
        method     = (request.POST.get('method') or '').strip()
        note       = (request.POST.get('note') or '').strip()

        # مبلغ معتبر؟
        amt = _to_decimal(amount_raw, None)
        if amt is None or amt <= 0:
            return HttpResponse(_("مبلغ پرداخت نامعتبر است."), status=400)

        # تاریخ (میلادی) ساده
        from datetime import datetime
        pay_date = None
        if date_raw:
            s = str(date_raw).strip()
            trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "0123456789"*2)
            s = s.translate(trans).replace('.', '/').replace('-', '/').replace(' ', '')
            try:
                pay_date = datetime.strptime(s, "%Y/%m/%d").date()
            except Exception:
                pay_date = None
        if not pay_date:
            pay_date = timezone.localdate()

        # ساخت رکورد پرداخت
        payment = DoctorPayment.objects.create(
            doctor=doctor,
            amount=amt,
            date=pay_date,
            method=method[:50],
            note=note
        )

        # تخصیص FIFO
        try:
            _allocate_payment_fifo(payment)
        except Exception as ex:
            # اگر تخصیص مشکل داشت، پرداخت ثبت می‌ماند؛ فقط لاگ کن
            print("FIFO allocation error:", ex)

        # بازگشت به صفحهٔ حساب دکتر
        return redirect(f"/billing/doctor/{doctor.id}/account/")















