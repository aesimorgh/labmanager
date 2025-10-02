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

        # ماندهٔ جاری واقعی: جمع بدهیِ باز فاکتورهای صادرشده
        # (amount_due = جمع خطوط - تخفیف‌ها - تخصیص پرداخت + مانده قبلی)
        running_balance = amount_due_issued


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


# ========== NEW: InvoiceListView (فقط اضافه شده؛ هیچ کد قبلی تغییر نکرد) ==========
@method_decorator(login_required, name='dispatch')
class InvoiceListView(View):
    """
    لیست فاکتورها با امکان فیلتر ساده (q, status) و محاسبهٔ مقادیر نمایشی.
    قالب: billing/invoice_list.html
    """
    def get(self, request: HttpRequest) -> HttpResponse:
        from billing.models import Invoice
        qs = Invoice.objects.all().select_related('doctor').order_by('-issued_at', '-id')

        q = (request.GET.get('q') or '').strip()
        status = (request.GET.get('status') or '').strip()

        if q:
            # جستجو در کد فاکتور یا نام دکتر (اگر فیلد name داشته باشد)
            qs = qs.filter(Q(code__icontains=q) | Q(doctor__name__icontains=q))

        if status:
            qs = qs.filter(status=status)

        invoices = list(qs)

        # محاسبهٔ امن برای نمایش (بدون دست‌کاری پایگاه‌داده)
        for inv in invoices:
            try:
                inv.recompute_totals()
            except Exception:
                pass
            d = _compute_display_totals(inv)
            inv.total_amount = d['total_amount']
            inv.amount_due = d['amount_due']

        # وضعیت‌ها برای فیلتر (اگر choices تعریف باشد)
        try:
            status_choices = getattr(Invoice.Status, 'choices', [('draft', 'Draft'), ('issued', 'Issued')])
        except Exception:
            status_choices = [('draft', 'Draft'), ('issued', 'Issued')]

        ctx = {
            "invoices": invoices,
            "q": q,
            "status": status,
            "status_choices": status_choices,
        }
        return render(request, "billing/invoice_list.html", ctx)

# ========== NEW: DoctorListView (لیست پزشک‌ها برای رفتن به حساب دکتر) ==========
@method_decorator(login_required, name='dispatch')
class DoctorListView(View):
    """
    نمایش لیست دکترها با جستجو؛ هر ردیف لینک به حساب دکتر دارد.
    قالب: billing/doctor_list.html
    """
    def get(self, request: HttpRequest) -> HttpResponse:
        from core.models import Doctor
        q = (request.GET.get('q') or '').strip()
        qs = Doctor.objects.all().order_by('name')
        if q:
            qs = qs.filter(Q(name__icontains=q))
        doctors = list(qs)
        ctx = {
            "doctors": doctors,
            "q": q,
        }
        return render(request, "billing/doctor_list.html", ctx)

# ========== NEW: OpenInvoicesReportView (گزارش مطالبات باز) ==========
@method_decorator(login_required, name='dispatch')
class OpenInvoicesReportView(View):
    """
    گزارش مطالبات باز: فقط فاکتورهای صادرشده‌ای که هنوز مانده دارند.
    قالب: billing/report_open_invoices.html
    """
    def get(self, request: HttpRequest) -> HttpResponse:
        from decimal import Decimal
        from billing.models import Invoice

        issued_val = getattr(Invoice.Status, 'ISSUED', 'issued')

        qs = (
            Invoice.objects
            .filter(status=issued_val)
            .select_related('doctor')
            .prefetch_related('lines')
            .order_by('-issued_at', '-id')
        )

        # فیلتر جستجو (اختیاری): کد فاکتور یا نام پزشک
        q = (request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(doctor__name__icontains=q))

        invoices = []
        sum_total = Decimal('0')
        sum_alloc = Decimal('0')
        sum_due   = Decimal('0')

        for inv in qs:
            # محاسبهٔ امنِ مقادیر نمایشی از روی خطوط و تخصیص‌ها
            try:
                inv.recompute_totals()
            except Exception:
                pass

            d = _compute_display_totals(inv)
            due = d['amount_due'] or Decimal('0')

            if due > 0:
                # مقادیر نمایشی برای استفاده در قالب
                inv.total_amount_display = d['total_amount']
                inv.allocated_display    = d['allocated']
                inv.outstanding_display  = due
                invoices.append(inv)

                sum_total += d['total_amount'] or Decimal('0')
                sum_alloc += d['allocated'] or Decimal('0')
                sum_due   += due

        ctx = {
            "invoices": invoices,
            "q": q,
            "sum_total": sum_total,
            "sum_alloc": sum_alloc,
            "sum_due": sum_due,
            "count": len(invoices),
        }
        return render(request, "billing/report_open_invoices.html", ctx)

# ========== NEW: MonthlySalesReportView (گزارش فروش ماهانه) ==========
# ========== NEW: MonthlySalesReportView (گزارش فروش ماهانه - برچسب جلالی) ==========
# ========== NEW: MonthlySalesReportView (گزارش فروش ماهانه - برچسب جلالی + CSV) ==========
@method_decorator(login_required, name='dispatch')
class MonthlySalesReportView(View):
    """
    جمع مبلغ فاکتورهای «صادرشده» به تفکیک ماه/سال شمسی.
    - مبلغ مبنا: total_amount پس از تخفیف‌های خطوط و تخفیف فاکتور (بدون previous_balance).
    - فیلتر اختیاری: ?q=جستجو در کد/نام دکتر
    - اگر ?format=csv باشد، خروجی CSV دانلود می‌شود.
    قالب HTML: billing/report_monthly_sales.html
    """
    def get(self, request: HttpRequest) -> HttpResponse:
        import csv
        from decimal import Decimal
        from django.http import HttpResponse
        from billing.models import Invoice
        from jalali_date import datetime2jalali  # تبدیل میلادی به جلالی

        issued_val = getattr(Invoice.Status, 'ISSUED', 'issued')

        qs = (
            Invoice.objects
            .filter(status=issued_val)
            .select_related('doctor')
            .prefetch_related('lines')
            .order_by('-issued_at', '-id')
        )

        # فیلتر جستجو اختیاری
        q = (request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(doctor__name__icontains=q))

        # جمع به تفکیک سال/ماه شمسی
        buckets = {}  # key: (jy, jm) -> {"count": n, "sum": Decimal}
        grand_sum = Decimal('0')
        grand_count = 0

        for inv in qs:
            issued_at = getattr(inv, 'issued_at', None)
            if not issued_at:
                continue

            try:
                inv.recompute_totals()
            except Exception:
                pass

            d = _compute_display_totals(inv)
            amt = d['total_amount'] or Decimal('0')
            if amt < 0:
                amt = Decimal('0')

            jdt = datetime2jalali(issued_at)
            jy, jm = jdt.year, jdt.month

            key = (jy, jm)
            if key not in buckets:
                buckets[key] = {"count": 0, "sum": Decimal('0')}
            buckets[key]["count"] += 1
            buckets[key]["sum"] += amt

            grand_sum += amt
            grand_count += 1

        # خروجی مرتب‌شده از جدید به قدیم
        rows = []
        for (jy, jm), v in buckets.items():
            rows.append({
                "year": jy,
                "month": jm,
                "label": f"{jy:04d}/{jm:02d}",
                "count": v["count"],
                "sum":  v["sum"],
            })
        rows.sort(key=lambda r: (r["year"], r["month"]), reverse=True)

        # اگر CSV خواسته شده بود
        if (request.GET.get('format') or '').lower() == 'csv':
            month_names = {
                1: "فروردین‌ماه", 2: "اردیبهشت‌ماه", 3: "خرداد‌ماه", 4: "تیر‌ماه",
                5: "مرداد‌ماه", 6: "شهریور‌ماه", 7: "مهر‌ماه", 8: "آبان‌ماه",
                9: "آذر‌ماه", 10: "دی‌ماه", 11: "بهمن‌ماه", 12: "اسفند‌ماه",
            }
            resp = HttpResponse(content_type='text/csv; charset=utf-8')
            resp['Content-Disposition'] = 'attachment; filename="monthly_sales.csv"'
            writer = csv.writer(resp)
            writer.writerow(["سال", "ماه (عدد)", "نام ماه", "تعداد فاکتور", "جمع فروش (ریال)"])
            for r in rows:
                jy = r["year"]
                jm = r["month"]
                name = month_names.get(jm, "-")
                writer.writerow([jy, jm, f"{name}", r["count"], int(r["sum"] or 0)])
            return resp

        # HTML (پیش‌فرض)
        ctx = {
            "rows": rows,
            "q": q,
            "grand_sum": grand_sum,
            "grand_count": grand_count,
        }
        return render(request, "billing/report_monthly_sales.html", ctx)

# ========== NEW: DiscountsReportView (گزارش مجموع تخفیف‌ها به تفکیک ماه/سال شمسی) ==========
@method_decorator(login_required, name='dispatch')
class DiscountsReportView(View):
    """
    مجموع تخفیف‌ها (تخفیف خطوط + تخفیف فاکتور) به تفکیک ماه/سال جلالی.
    - فقط فاکتورهای صادرشده (ISSUED) محاسبه می‌شوند.
    - فیلتر اختیاری: ?q=... روی code یا نام دکتر.
    - HTML: billing/report_discounts.html
    - CSV:  /billing/reports/discounts/?format=csv  (UTF-8-SIG مناسب اکسل)
    """
    def get(self, request: HttpRequest) -> HttpResponse:
        from decimal import Decimal
        from billing.models import Invoice
        from django.db.models import Sum
        from django.db.models.functions import Coalesce
        from jalali_date import datetime2jalali
        from django.db.models import Q
        import csv

        # ماه‌های فارسی برای نمایش
        month_names = {
            1: "فروردین‌ماه", 2: "اردیبهشت‌ماه", 3: "خرداد‌ماه", 4: "تیر‌ماه",
            5: "مرداد‌ماه", 6: "شهریور‌ماه", 7: "مهرماه", 8: "آبان‌ماه",
            9: "آذرماه", 10: "دی‌ماه", 11: "بهمن‌ماه", 12: "اسفندماه",
        }

        issued_val = getattr(Invoice.Status, 'ISSUED', 'issued')
        qs = (
            Invoice.objects
            .filter(status=issued_val)
            .select_related('doctor')
            .prefetch_related('lines')
            .order_by('-issued_at', '-id')
        )

        q = (request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(doctor__name__icontains=q))

        buckets = {}  # key: (jy, jm) -> {"count": n, "discount_sum": Decimal}
        grand_discount = Decimal('0')
        grand_count = 0

        for inv in qs:
            issued_at = getattr(inv, 'issued_at', None)
            if not issued_at:
                continue

            # مجموع تخفیف خطوط
            line_disc = inv.lines.aggregate(
                s=Coalesce(Sum('discount_amount'), Decimal('0'))
            )['s'] or Decimal('0')

            # تخفیف سطح فاکتور
            try:
                inv_disc = Decimal(str(getattr(inv, 'discount_amount', 0) or 0))
            except Exception:
                inv_disc = Decimal('0')

            total_disc = line_disc + inv_disc
            if total_disc < 0:
                total_disc = Decimal('0')

            jdt = datetime2jalali(issued_at)
            jy, jm = jdt.year, jdt.month
            key = (jy, jm)
            if key not in buckets:
                buckets[key] = {"count": 0, "discount_sum": Decimal('0')}
            buckets[key]["count"] += 1
            buckets[key]["discount_sum"] += total_disc

            grand_discount += total_disc
            grand_count += 1

        # خروجی مرتب از جدید به قدیم
        rows = []
        for (jy, jm), v in buckets.items():
            rows.append({
                "year": jy,
                "month": jm,
                "month_name": month_names.get(jm, "-"),
                "label": f"{jy:04d}/{jm:02d}",
                "count": v["count"],
                "discount_sum": v["discount_sum"],
            })
        rows.sort(key=lambda r: (r["year"], r["month"]), reverse=True)

        # === CSV Export (UTF-8-SIG برای اکسل) ===
        if (request.GET.get('format') or '').lower() == 'csv':
            # پاسخ متنی با BOM
            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = 'attachment; filename="discounts_monthly.csv"'
            # BOM برای UTF-8-SIG
            response.write('\ufeff')
            w = csv.writer(response)
            w.writerow(['سال', 'ماه', 'برچسب', 'تعداد فاکتور', 'مجموع تخفیف'])
            for r in rows:
                w.writerow([
                    r['year'],
                    r['month_name'],
                    f"{r['month_name']} {r['year']}",
                    r['count'],
                    # مقدار خام بدون فرمت‌گذاری تا در اکسل قابل محاسبه باشد
                    str(r['discount_sum']),
                ])
            return response
        # =======================================

        ctx = {
            "rows": rows,
            "q": q,
            "grand_discount": grand_discount,
            "grand_count": grand_count,
        }
        return render(request, "billing/report_discounts.html", ctx)

# ========== NEW: AgingReportView (محاسبهٔ باکت‌های 0–30 / 31–60 / 61–90 / 90+ روز) ==========
from decimal import Decimal
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
from django.views import View
from django.shortcuts import render
from django.db.models import Q

# ========== NEW: AgingReportView (سازگار با قالب report_aging.html) ==========
@method_decorator(login_required, name='dispatch')
class AgingReportView(View):
    """
    گزارش Aging بدهی‌ها برای فاکتورهای 'ISSUED' که هنوز تسویه کامل نشده‌اند.
    خروجی مطابق قالب report_aging.html:
      - totals: {total, b0_30, b31_60, b61_90, b90p}
      - rows:   [{doctor_id, doctor_name, b0_30, b31_60, b61_90, b90p, total, invoices}, ...]
    نکته: آرایهٔ invoices برای دریل‌دان است؛ اگر قالب استفاده نکند، مشکلی ایجاد نمی‌شود.
    """
    def get(self, request):
        from decimal import Decimal
        from billing.models import Invoice

        today = timezone.localdate()
        issued_val = getattr(Invoice.Status, 'ISSUED', 'issued')

        qs = (
            Invoice.objects
            .filter(status=issued_val)
            .select_related('doctor')
            .order_by('issued_at', 'id')
        )

        # فیلتر جستجو (اختیاری)
        q = (request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(doctor__name__icontains=q))

        # جمع‌های کل
        totals = {
            'b0_30': Decimal('0'),
            'b31_60': Decimal('0'),
            'b61_90': Decimal('0'),
            'b90p':  Decimal('0'),
            'total': Decimal('0'),
        }

        # گروه‌بندی بر اساس پزشک
        groups = {}  # key = doctor_id (یا None)

        for inv in qs:
            # بدهی باز این فاکتور = خطوط − تخفیف‌ها + ماندهٔ قبلی − پرداخت‌های تخصیص‌یافته
            try:
                inv.recompute_totals()
            except Exception:
                pass
            d = _compute_display_totals(inv)
            open_due = d.get('amount_due') or Decimal('0')
            if open_due <= 0:
                continue

            # سن بدهی از تاریخ صدور (در نبودِ issued_at، از created_at/امروز)
            issued_date = None
            try:
                issued_date = inv.issued_at.date() if getattr(inv, 'issued_at', None) else None
            except Exception:
                issued_date = None
            if not issued_date:
                created = getattr(inv, 'created_at', None)
                try:
                    issued_date = created.date() if created else timezone.localdate()
                except Exception:
                    issued_date = timezone.localdate()

            days = (timezone.localdate() - issued_date).days if issued_date else 0
            if   days <= 30: bucket = 'b0_30'
            elif days <= 60: bucket = 'b31_60'
            elif days <= 90: bucket = 'b61_90'
            else:            bucket = 'b90p'

            # جمع کل
            totals[bucket] += open_due
            totals['total'] += open_due

            # گروه پزشک
            doc = getattr(inv, 'doctor', None)
            doc_id = getattr(doc, 'id', None)
            doc_name = (getattr(doc, 'name', None) or (str(doc) if doc else '—')).strip() if doc else '—'

            row = groups.get(doc_id)
            if not row:
                row = {
                    'doctor_id': doc_id,
                    'doctor_name': doc_name,
                    'b0_30': Decimal('0'),
                    'b31_60': Decimal('0'),
                    'b61_90': Decimal('0'),
                    'b90p':  Decimal('0'),
                    'total': Decimal('0'),
                    'invoices': [],  # برای دریل‌دان
                }
                groups[doc_id] = row

            row[bucket] += open_due
            row['total'] += open_due

            # فاکتور را هم برای نمایش جزئیات ذخیره می‌کنیم
            row['invoices'].append({
                'id':   inv.id,
                'code': inv.code or f'#{inv.id}',
                # قالب از inv.issued_at|date... استفاده می‌کند؛ این مقدار را با fallback می‌دهیم
                'issued_at': getattr(inv, 'issued_at', None) or issued_date,
                'days': days,
                'bucket': bucket,
                'amount': open_due,
            })

        # خروجی سطرها (مرتب بر اساس نام پزشک)
        rows = sorted(groups.values(), key=lambda r: r['doctor_name'] or '')

        ctx = {
            'q': q,
            'totals': totals,
            'rows': rows,
        }

        # --- CSV export (UTF-8 with BOM تا اکسل فارسی را درست نشان دهد)
        if (request.GET.get('format') or '').lower() == 'csv':
            import csv
            from django.http import HttpResponse

            resp = HttpResponse(content_type='text/csv; charset=utf-8')
            resp['Content-Disposition'] = (
                'attachment; filename="aging_%s.csv"' % timezone.now().strftime('%Y%m%d_%H%M%S')
            )
            resp.write('\ufeff')  # BOM برای Excel

            w = csv.writer(resp)
            w.writerow(['پزشک', '۰–۳۰', '۳۱–۶۰', '۶۱–۹۰', '۹۰+', 'جمع'])
            for r in rows:
                w.writerow([
                    r['doctor_name'],
                    r['b0_30'], r['b31_60'], r['b61_90'], r['b90p'],
                    r['total'],
                ])
            w.writerow([
                'جمع کل',
                totals['b0_30'], totals['b31_60'], totals['b61_90'], totals['b90p'],
                totals['total'],
            ])
            return resp

        return render(request, "billing/report_aging.html", ctx)

# ========== NEW: FinancialHomeView (پنل هاب مالی - با ماه جلالی در صورت وجود) ==========
from decimal import Decimal
import json
from datetime import timedelta, date as pydate
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
from django.views import View
from django.shortcuts import render
from django.db.models import Sum
from django.urls import reverse
from django.db.models import Q

class FinancialHomeView(View):  # ← فقط همین تغییر: حذف @method_decorator(login_required, ...)
    """
    داده‌های صفحه‌ی billing/financial_home.html را فراهم می‌کند:
      ctx = {
        'kpis': {
            'open_total', 'over_90',
            'this_month_payments',   # جمع پرداخت‌های «ماه جاری» (جلالی در صورت وجود jdatetime)
            'this_month_invoices',   # تعداد فاکتورهای «ماه جاری» (جلالی در صورت وجود jdatetime)
            'open_count',            # تعداد فاکتورهای باز
        },
        'monthly_json': '[{m:"YYYY-MM", sales:number, payments:number}, ...]',
        'latest_open_invoices': [...],
        'top_debtors': [...],
      }
    """
    template_name = "billing/financial_home.html"

    # --- کمک‌متدها ------------------------------------------------------------
    def _get_issued_date(self, inv, default_date):
        """تاریخ صدور امن (date): issued_at یا created_at یا today"""
        try:
            if getattr(inv, 'issued_at', None):
                return inv.issued_at.date()
        except Exception:
            pass
        try:
            cd = getattr(inv, 'created_at', None)
            if cd:
                return cd.date()
        except Exception:
            pass
        return default_date

    def _month_key(self, d):
        """کلید ماهیانه ساده: 'YYYY-MM' (برای نمودار؛ میلادی باقی می‌ماند)"""
        return f"{d.year:04d}-{d.month:02d}"

    def _last_12_months_keys(self, end_date):
        """لیست 12 ماه اخیر میلادی (برای نمودار)"""
        keys = []
        y, m = end_date.year, end_date.month
        for _ in range(12):
            keys.append((y, m))
            m -= 1
            if m == 0:
                y -= 1
                m = 12
        keys.reverse()
        return [f"{yy:04d}-{mm:02d}" for yy, mm in keys]

    def _current_month_range(self, today):
        """
        مرزهای «ماه جاری» را برمی‌گرداند (start, end, calendar):
        - اگر jdatetime نصب باشد: ماه جلالی
        - وگرنه: ماه میلادی
        """
        try:
            import jdatetime as jd  # pip install jdatetime
            j_today = jd.date.fromgregorian(date=today)
            jy, jm = j_today.year, j_today.month
            j_start = jd.date(jy, jm, 1)
            # اول ماه بعد جلالی
            if jm == 12:
                j_next = jd.date(jy + 1, 1, 1)
            else:
                j_next = jd.date(jy, jm + 1, 1)
            start = j_start.togregorian()
            end = j_next.togregorian() - timedelta(days=1)
            return start, end, 'jalali'
        except Exception:
            # میلادی
            start = today.replace(day=1)
            if today.month == 12:
                nstart = pydate(today.year + 1, 1, 1)
            else:
                nstart = pydate(today.year, today.month + 1, 1)
            end = nstart - timedelta(days=1)
            return start, end, 'gregorian'

    # --- اکشن اصلی ------------------------------------------------------------
    def get(self, request):
        from billing.models import Invoice

        today = timezone.localdate()
        month_keys = self._last_12_months_keys(today)

        # ظرف نمودار
        monthly_sales = {k: Decimal('0') for k in month_keys}
        monthly_pays  = {k: Decimal('0') for k in month_keys}

        # KPI ها
        open_total = Decimal('0')
        over_90 = Decimal('0')
        open_count = 0
        this_month_invoices = 0
        this_month_payments = Decimal('0')

        # مرزهای ماه جاری (جلالی در صورت وجود jdatetime)
        month_start, month_end, _cal = self._current_month_range(today)

        # فقط فاکتورهای ISSUED
        issued_val = getattr(Invoice.Status, 'ISSUED', 'issued')
        inv_qs = (Invoice.objects
                  .filter(status=issued_val)
                  .select_related('doctor')
                  .order_by('-issued_at', '-id'))

        latest_open = []
        latest_open_cap = 8

        # برای «۵ بدهکار برتر»
        debt_by_doctor = {}  # doc_id -> {'doctor_id', 'doctor_name', 'amount_due': Decimal}

        # پیمایش فاکتورها
        for inv in inv_qs:
            try:
                inv.recompute_totals()
            except Exception:
                pass

            try:
                dct = _compute_display_totals(inv)
            except Exception:
                dct = {}

            amount_due = dct.get('amount_due') or Decimal('0')
            issued_date = self._get_issued_date(inv, today)
            month_key = self._month_key(issued_date)

            # فروش ماهانه (برای نمودار؛ میلادی)
            gross = (dct.get('grand_total')
                     or dct.get('total')
                     or dct.get('subtotal')
                     or Decimal('0'))
            if month_key in monthly_sales:
                monthly_sales[month_key] += gross

            # بدهی باز / 90+
            if amount_due > 0:
                open_total += amount_due
                open_count += 1

                days = (today - issued_date).days if issued_date else 0
                if days > 90:
                    over_90 += amount_due

                # آخرین فاکتورهای باز
                if len(latest_open) < latest_open_cap:
                    try:
                        doc = getattr(inv, 'doctor', None)
                        doc_name = (getattr(doc, 'name', None) or (str(doc) if doc else '—')).strip()
                    except Exception:
                        doc_name = '—'
                    try:
                        url = reverse('billing:invoice_detail', args=[inv.id])
                    except Exception:
                        url = '#'
                    latest_open.append({
                        'id': inv.id,
                        'code': inv.code or f'#{inv.id}',
                        'doctor_name': doc_name,
                        'issued_at': getattr(inv, 'issued_at', None),
                        'amount_due': amount_due,
                        'url': url,
                    })

                # تجمیع برای «۵ بدهکار برتر»
                try:
                    doc = getattr(inv, 'doctor', None)
                    doc_id = getattr(doc, 'id', None)
                    doc_name = (getattr(doc, 'name', None) or (str(doc) if doc else '—')).strip()
                except Exception:
                    doc_id = None
                    doc_name = '—'
                grp = debt_by_doctor.get(doc_id)
                if not grp:
                    grp = {'doctor_id': doc_id, 'doctor_name': doc_name, 'amount_due': Decimal('0')}
                    debt_by_doctor[doc_id] = grp
                grp['amount_due'] += amount_due

            # ✅ تعداد فاکتورهای «ماه جاری» (جلالی/میلادی طبق _current_month_range)
            if issued_date and (month_start <= issued_date <= month_end):
                this_month_invoices += 1

        # پرداخت‌های «ماه جاری» (سازگار با DateField/DateTimeField)
        try:
            from billing.models import DoctorPayment as _PayModel
            pay_date_field = None
            for f in ('paid_at', 'payment_date', 'date', 'created_at'):
                if hasattr(_PayModel, f):
                    pay_date_field = f
                    break
            amt_field = 'amount' if hasattr(_PayModel, 'amount') else None

            if pay_date_field and amt_field:
                df = _PayModel._meta.get_field(pay_date_field)
                # فیلتر تاریخ بر اساس نوع فیلد
                if df.get_internal_type() == 'DateTimeField':
                    date_filter = {
                        f"{pay_date_field}__date__gte": month_start,
                        f"{pay_date_field}__date__lte": month_end,
                    }
                else:
                    date_filter = {
                        f"{pay_date_field}__gte": month_start,
                        f"{pay_date_field}__lte": month_end,
                    }
                this_month_payments = (
                    _PayModel.objects.filter(**date_filter).aggregate(s=Sum(amt_field))['s'] or Decimal('0')
                )

                # پرداخت‌های 12 ماه اخیر (برای نمودار؛ میلادی)
                all_pays = _PayModel.objects.all()
                for p in all_pays:
                    try:
                        pd = getattr(p, pay_date_field, None)
                        if pd is None:
                            continue
                        if hasattr(pd, 'date'):
                            pd = pd.date()
                        mk = self._month_key(pd)
                        if mk in monthly_pays:
                            monthly_pays[mk] += (getattr(p, amt_field, None) or Decimal('0'))
                    except Exception:
                        continue
            else:
                this_month_payments = Decimal('0')
        except Exception:
            this_month_payments = Decimal('0')

        # JSON نمودار
        monthly = []
        for k in month_keys:
            monthly.append({
                'm': k,
                'sales': float(monthly_sales.get(k, 0)),
                'payments': float(monthly_pays.get(k, 0)),
            })
        monthly_json = json.dumps(monthly, ensure_ascii=False)

        # ۵ بدهکار برتر
        top_debtors = sorted(
            debt_by_doctor.values(),
            key=lambda r: r['amount_due'],
            reverse=True
        )[:5]

        ctx = {
            'kpis': {
                'open_total': open_total,
                'over_90': over_90,
                'this_month_payments': this_month_payments,
                'this_month_invoices': this_month_invoices,
                'open_count': open_count,
            },
            'monthly_json': monthly_json,
            'latest_open_invoices': latest_open,
            'top_debtors': top_debtors,
        }
        return render(request, self.template_name, ctx)













