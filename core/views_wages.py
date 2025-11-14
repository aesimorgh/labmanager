# core/views_wages.py
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.db.models import Sum, Q
from urllib.parse import urlencode
from .models import Order, StageInstance, StageWorkLog, StageTemplate, Technician, WagePayout
from .forms_wages import StageWorkLogPublicForm, WagePayoutNewForm, WagePayoutConfirmForm
from decimal import Decimal
import jdatetime
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.template.loader import render_to_string


def _money_fa(n: int | float | str):
    try:
        n = int(float(n or 0))
        s = f"{n:,}".replace(",", "٬")
        return s.translate(str.maketrans("0123456789","۰۱۲۳۴۵۶۷۸۹"))
    except Exception:
        return str(n)

@require_http_methods(["GET"])
def workbench_order(request, order_id: int):
    """
    ورک‌بنچ ساده برای ثبت دستمزدهای این سفارش:
    - لیست StageInstanceهای سفارش
    - فرم سریع ثبت StageWorkLog برای یک مرحله
    - جدول لاگ‌های ثبت‌شده + جمع کل دستمزد
    """
    order = get_object_or_404(Order, pk=order_id)
    stages = StageInstance.objects.filter(order=order).order_by("order_index", "id")

    # لاگ‌ها و جمع
    logs = (
        StageWorkLog.objects
        .filter(order=order)
        .select_related("stage_tpl", "technician", "stage_inst")
        .order_by("-created_at", "-id")
    )
    total_wage = logs.aggregate(s=Sum("total_wage"))["s"] or Decimal("0.00")

    # اگر ?stage_inst= داده شده باشد، فرم را به آن مرحله bind می‌کنیم
    stage_inst_id = request.GET.get("stage_inst")
    stage_inst = StageInstance.objects.filter(pk=stage_inst_id, order=order).first() if stage_inst_id else None
    initial = {"order": order}
    if stage_inst:
        initial["stage_inst"] = stage_inst.id
        if stage_inst.template_id:
            initial["stage_tpl"] = stage_inst.template_id

    form = StageWorkLogPublicForm(initial=initial)

    ctx = dict(
        order=order,
        stages=stages,
        form=form,
        logs=logs,
        total_wage_fa=_money_fa(total_wage),
    )
    return render(request, "core/workbench_order.html", ctx)

@require_http_methods(["POST"])
def worklog_create(request):
    """
    ساخت لاگ دستمزد از ورک‌بنچ.
    - اگر finished_at خالی باشد، خودکار «امروزِ جلالی» ست می‌شود (بر اساس منطق: ثبت مبلغ در زمان اتمام مرحله)
    - اگر stage_inst موجود باشد و finished_at پر شود → done_date و status مرحله نیز به‌روزرسانی می‌شود.
    - نرخ هر واحد اگر خالی باشد، در مدل StageWorkLog به‌صورت خودکار resolve می‌شود.
    """
    form = StageWorkLogPublicForm(request.POST)
    if not form.is_valid():
        order = form.cleaned_data.get("order") if hasattr(form, "cleaned_data") else None
        order_id = getattr(order, "id", None)
        messages.error(request, "ورود اطلاعات نامعتبر است. لطفاً بررسی کنید.")
        if order_id:
            return redirect(reverse("core:core_workbench_order", args=[order_id]))
        return redirect("/")

    # ابتدا بدون ذخیره‌ کردن، شیء را می‌گیریم تا بتوانیم تاریخ پایان و ... را ست کنیم
    log: StageWorkLog = form.save(commit=False)

    # اگر تاریخ پایان خالی است، امروز (جلالی) را بگذار
    if not log.finished_at:
        try:
            log.finished_at = jdatetime.date.today()
        except Exception:
            # اگر هر دلیلی جلالی در دسترس نبود، اجازه بده مدل با None ذخیره کند
            pass

    # ذخیرهٔ لاگ (مدل خودش unit_wage/total_wage را در save() محاسبه می‌کند)
    log.save()

    # اگر مرحلهٔ سفارش مشخص است و تاریخ پایان ثبت شد، اتمام مرحله را هم ست کن
    if log.stage_inst_id and log.finished_at:
        try:
            si = log.stage_inst  # select_related در save() نداریم، ولی اینجا lazy-load می‌شود
            # اگر done_date خالی است یا وضعیت نهایی نیست، به‌روزرسانی کن
            changed = False
            if not si.done_date:
                si.done_date = log.finished_at
                changed = True
            if getattr(si, "status", None) != StageInstance.Status.DONE:
                si.status = StageInstance.Status.DONE
                changed = True
            if changed:
                si.save(update_fields=["done_date", "status", "updated_at"] if hasattr(si, "updated_at") else ["done_date", "status"])
        except Exception:
            # اگر هر مشکلی بود، فقط لاگ دستمزد ثبت شده بماند
            pass

    messages.success(request, f"لاگ دستمزد ثبت شد: مبلغ کل {_money_fa(log.total_wage)} تومان.")
    return redirect(reverse("core:core_workbench_order", args=[log.order_id]))

@require_http_methods(["POST"])
def worklog_delete(request, pk: int):
    log = get_object_or_404(StageWorkLog, pk=pk)
    order_id = log.order_id
    log.delete()
    messages.success(request, "لاگ حذف شد.")
    return redirect(reverse("core:core_workbench_order", args=[order_id]))

# برای Export
import io
import xlsxwriter
from weasyprint import HTML

# --- کمکی‌ها (اگر قبلاً در همین فایل تعریف نکردی، بگذار باشند)
def _esc(s):
    try:
        return (s or "").replace("<", "&lt;").replace(">", "&gt;")
    except Exception:
        return s

def _money_fa(n: int | float | str):
    try:
        n = int(float(n or 0))
        s = f"{n:,}".replace(",", "٬")
        return s.translate(str.maketrans("0123456789","۰۱۲۳۴۵۶۷۸۹"))
    except Exception:
        return str(n)

def _parse_jdate(s):
    if not s:
        return None
    try:
        y, m, d = [int(p) for p in s.replace("-", "/").split("/")[:3]]
        return jdatetime.date(y, m, d)
    except Exception:
        return None

@require_http_methods(["GET", "POST"])
def wages_payout_new(request):
    """
    گام ۱: انتخاب تکنسین و بازهٔ جلالی برای شروع فرآیند تسویه.
    GET: نمایش فرم
    POST: اعتبارسنجی و ریدایرکت به پیش‌نمایش با querystring
    """
    if request.method == "POST":
        form = WagePayoutNewForm(request.POST)
        if form.is_valid():
            tech = form.cleaned_data["technician"]
            start_g = form.cleaned_data.get("period_start_j")  # معمولاً datetime.date (میلادی)
            end_g   = form.cleaned_data.get("period_end_j")

            # تبدیل تاریخ میلادی فرم به جلالی
            start_j = None
            end_j = None
            if start_g:
                start_j = jdatetime.date.fromgregorian(date=start_g)
            if end_g:
                end_j = jdatetime.date.fromgregorian(date=end_g)

            params = {"technician": tech.id}
            if start_j:
                params["period_start_j"] = start_j.strftime("%Y-%m-%d")
            if end_j:
                params["period_end_j"] = end_j.strftime("%Y-%m-%d")

            url = reverse("core:wages_payout_preview") + "?" + urlencode(params)
            return redirect(url)

    else:
        # 🔹 در حالت GET همیشه یک فرم خالی می‌سازیم
        form = WagePayoutNewForm()

    return TemplateResponse(request, "core/wages_payout_new.html", {"form": form})


@require_http_methods(["GET", "POST"])
def wages_payout_preview(request):
    """
    گام ۲: پیش‌نمایش تسویه:
      - GET: تکنسین و بازه را از querystring می‌خواند و لاگ‌های DONE و تسویه‌نشده را نشان می‌دهد.
      - POST: فرم تأیید را می‌گیرد، WagePayout می‌سازد و لاگ‌ها را تسویه می‌کند.
    """
    # ----------------------
    # حالت POST: تأیید تسویه
    # ----------------------
    if request.method == "POST":
        confirm_form = WagePayoutConfirmForm(request.POST)
        if not confirm_form.is_valid():
            # اگر فرم تأیید نامعتبر بود، باید دوباره پیش‌نمایش را با همان بازه و تکنسین نشان دهیم
            tech = None
            tech_id = confirm_form.data.get("technician_id")
            if tech_id:
                try:
                    tech = Technician.objects.get(pk=int(tech_id))
                except Technician.DoesNotExist:
                    tech = None

            start_jd = _parse_jdate(confirm_form.data.get("period_start_j") or "")
            end_jd   = _parse_jdate(confirm_form.data.get("period_end_j") or "")

            logs_qs = StageWorkLog.objects.filter(
                technician=tech,
                status=StageWorkLog.Status.DONE,
            ).filter(
                Q(is_settled=False) | Q(is_settled__isnull=True)
            )

            if start_jd:
                logs_qs = logs_qs.filter(finished_at__gte=start_jd)
            if end_jd:
                logs_qs = logs_qs.filter(finished_at__lte=end_jd)

            logs_qs = logs_qs.select_related("order", "stage_tpl", "stage_inst").order_by("finished_at", "id")
            gross_total = logs_qs.aggregate(s=Sum("total_wage"))["s"] or Decimal("0.00")

            ctx = {
                "technician": tech,
                "start": start_jd,
                "end": end_jd,
                "logs": logs_qs,
                "gross_total": gross_total,
                "_money_fa": _money_fa,  # اگر در تمپلیت استفاده نمی‌کنی، ضرری ندارد
                "form_confirm": confirm_form,
            }
            return TemplateResponse(request, "core/wages_payout_preview.html", ctx)

        # فرم تأیید معتبر است → ساخت تسویه
        tech_id = confirm_form.cleaned_data["technician_id"]
        technician = get_object_or_404(Technician, pk=tech_id)

        start_jd = _parse_jdate(confirm_form.cleaned_data.get("period_start_j") or "")
        end_jd   = _parse_jdate(confirm_form.cleaned_data.get("period_end_j") or "")

        logs_qs = StageWorkLog.objects.filter(
            technician=technician,
            status=StageWorkLog.Status.DONE,
        ).filter(
            Q(is_settled=False) | Q(is_settled__isnull=True)
        )

        if start_jd:
            logs_qs = logs_qs.filter(finished_at__gte=start_jd)
        if end_jd:
            logs_qs = logs_qs.filter(finished_at__lte=end_jd)

        logs_qs = logs_qs.select_related("order", "stage_tpl", "stage_inst").order_by("finished_at", "id")

        if not logs_qs.exists():
            messages.warning(request, "هیچ لاگ تسویه‌نشده‌ای برای این بازه یافت نشد.")
            return redirect(reverse("core:wages_payout_new"))

        gross_total = logs_qs.aggregate(s=Sum("total_wage"))["s"] or Decimal("0.00")
        deductions = confirm_form.cleaned_data.get("deductions_total") or Decimal("0.00")
        bonus      = confirm_form.cleaned_data.get("bonus_total") or Decimal("0.00")
        net_payable = gross_total - deductions + bonus

        payout = WagePayout.objects.create(
            technician=technician,
            period_start_j=start_jd,
            period_end_j=end_jd,
            status=WagePayout.Status.CONFIRMED,
            gross_total=gross_total,
            deductions_total=deductions,
            bonus_total=bonus,
            net_payable=net_payable,
            note=confirm_form.cleaned_data.get("note") or "",
            payment_ref=confirm_form.cleaned_data.get("payment_ref") or "",
        )

        # به‌روزرسانی لاگ‌ها: اتصال به payout و علامت‌گذاری به‌عنوان تسویه‌شده
        settled_date = jdatetime.date.today()
        StageWorkLog.objects.filter(pk__in=logs_qs.values_list("pk", flat=True)).update(
            payout=payout,
            is_settled=True,
            settled_at_j=settled_date,
        )

        messages.success(request, f"تسویهٔ دستمزد با موفقیت ایجاد شد. خالص قابل پرداخت: {net_payable} تومان.")
        return redirect(reverse("core:wages_payout_detail", args=[payout.id]))

    # ----------------------
    # حالت GET: نمایش پیش‌نمایش
    # ----------------------
    # این‌جا دیگر از فرم استفاده نمی‌کنیم؛ مثل wages_report مستقیم querystring را می‌خوانیم
    tech_id = request.GET.get("technician")
    if not tech_id:
        messages.error(request, "تکنسین مشخص نشده است.")
        return redirect(reverse("core:wages_payout_new"))

    try:
        technician = Technician.objects.get(pk=int(tech_id))
    except (Technician.DoesNotExist, ValueError):
        messages.error(request, "تکنسین انتخاب‌شده یافت نشد.")
        return redirect(reverse("core:wages_payout_new"))

    start_str = (request.GET.get("period_start_j") or "").strip()
    end_str   = (request.GET.get("period_end_j") or "").strip()

    start_jd = _parse_jdate(start_str)
    end_jd   = _parse_jdate(end_str)

    logs_qs = StageWorkLog.objects.filter(
        technician=technician,
        status=StageWorkLog.Status.DONE,
    ).filter(
        Q(is_settled=False) | Q(is_settled__isnull=True)
    )

    if start_jd:
        logs_qs = logs_qs.filter(finished_at__gte=start_jd)
    if end_jd:
        logs_qs = logs_qs.filter(finished_at__lte=end_jd)

    logs_qs = logs_qs.select_related("order", "stage_tpl", "stage_inst").order_by("finished_at", "id")

    if not logs_qs.exists():
        messages.warning(request, "هیچ لاگ تسویه‌نشده‌ای برای این بازه یافت نشد.")
        return redirect(reverse("core:wages_payout_new"))

    gross_total = logs_qs.aggregate(s=Sum("total_wage"))["s"] or Decimal("0.00")

    # فرم تأیید با مقداردهی اولیه
    form_confirm = WagePayoutConfirmForm(initial={
        "technician_id": technician.id,
        "period_start_j": start_str,
        "period_end_j": end_str,
        "deductions_total": Decimal("0.00"),
        "bonus_total": Decimal("0.00"),
    })

    ctx = {
        "technician": technician,
        "start": start_jd,
        "end": end_jd,
        "logs": logs_qs,
        "gross_total": gross_total,
        "_money_fa": _money_fa,  # اگر در تمپلیت حذفش کرده‌ای، بودنش ضرری ندارد
        "form_confirm": form_confirm,
    }
    return TemplateResponse(request, "core/wages_payout_preview.html", ctx)


@require_http_methods(["GET"])
def wages_payout_detail(request, payout_id: int):
    """
    گام ۳: مشاهدهٔ جزئیات تسویه و لاگ‌های مرتبط.
    (در این گام فعلاً فقط نمایش می‌دهیم؛ تغییر status/لغو را در گام‌های بعدی اضافه می‌کنیم.)
    """
    payout = get_object_or_404(WagePayout, pk=payout_id)
    logs_qs = (StageWorkLog.objects
               .filter(payout=payout)
               .select_related("order", "stage_tpl", "stage_inst")
               .order_by("finished_at", "id"))

    ctx = {
        "payout": payout,
        "logs": logs_qs,
        "_money_fa": _money_fa,
    }
    return TemplateResponse(request, "core/wages_payout_detail.html", ctx)


@require_http_methods(["GET"])
def wages_report(request):
    """
    گزارش دستمزد تکنسین‌ها با فیلترهای:
      technician (نام)، start/end (جلالی)، product (کُد محصول)، stage (برچسب مرحله)
    + Export: Excel (xlsxwriter) و PDF (weasyprint)
    """
    # --------- ورودی‌ها ---------
    tech_name = (request.GET.get("technician") or "").strip()
    start_str = (request.GET.get("start") or "").strip()
    end_str   = (request.GET.get("end") or "").strip()
    product   = (request.GET.get("product") or "").strip()
    stage_q   = (request.GET.get("stage") or "").strip()

    start_jd = _parse_jdate(start_str)
    end_jd   = _parse_jdate(end_str)

    # --------- مدل‌ها ---------
    from .models import StageWorkLog, Technician, Product, StageTemplate

    # محصولات برای کشو
    products_qs = Product.objects.all().order_by("name")

    # مرحله‌ها بر اساس محصول انتخاب‌شده (اگر خالی، همه)
    stages_qs = (StageTemplate.objects
                 .filter(product__code=product) if product else StageTemplate.objects.all())
    stages_qs = stages_qs.order_by("product__name", "order_index", "label")

    # تکنسین‌ها
    tech_names = list(Technician.objects.order_by("name").values_list("name", flat=True))

    # --------- کوئری گزارش ---------
    logs = (StageWorkLog.objects
            .select_related("technician", "stage_tpl", "stage_inst", "order", "stage_tpl__product")
            .order_by("-finished_at", "-id"))

    if tech_name:
        logs = logs.filter(technician__name=tech_name)
    if start_jd:
        logs = logs.filter(finished_at__gte=start_jd)
    if end_jd:
        logs = logs.filter(finished_at__lte=end_jd)
    if product:
        logs = logs.filter(Q(stage_tpl__product__code=product) | Q(order__order_type=product))
    if stage_q:
        logs = logs.filter(Q(stage_tpl__label=stage_q) | Q(stage_inst__label=stage_q))

    total_wage = logs.aggregate(s=Sum("total_wage"))["s"] or 0
    by_tech  = (logs.values("technician__name").annotate(total=Sum("total_wage")).order_by("technician__name"))
    by_stage = (logs.values("stage_tpl__label").annotate(total=Sum("total_wage")).order_by("stage_tpl__label"))

    # --------- Export Excel ---------
    if "export_excel" in request.GET:
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet("گزارش دستمزد")

        fmt_h = wb.add_format({'bold': True, 'align':'center', 'valign':'vcenter', 'bg_color':'#E0F2FE', 'border':1})
        fmt   = wb.add_format({'align':'center', 'valign':'vcenter', 'border':1})
        fmt_r = wb.add_format({'align':'right',  'valign':'vcenter', 'border':1})

        headers = ['ID','تکنسین','مرحله','محصول','تعداد','نرخ واحد','مبلغ کل','تاریخ پایان']
        for c,h in enumerate(headers): ws.write(0, c, h, fmt_h)
        ws.set_column(0, 0, 8)
        ws.set_column(1, 1, 18)
        ws.set_column(2, 3, 22)
        ws.set_column(4, 6, 16)
        ws.set_column(7, 7, 14)

        r = 1
        for l in logs[:5000]:
            stage_label = ""
            prod_name = ""
            if getattr(l, "stage_tpl_id", None) and getattr(l.stage_tpl, "label", None):
                stage_label = l.stage_tpl.label
                prod_name = getattr(getattr(l.stage_tpl, "product", None), "name", "") or (getattr(getattr(l, "order", None), "order_type", "") or "")
            elif getattr(l, "stage_inst_id", None) and getattr(l.stage_inst, "label", None):
                stage_label = l.stage_inst.label or ""
                prod_name = getattr(getattr(l, "order", None), "order_type", "") or ""
            ws.write(r,0, l.id, fmt)
            ws.write(r,1, getattr(l.technician,'name','—'), fmt)
            ws.write(r,2, stage_label, fmt)
            ws.write(r,3, prod_name or '—', fmt)
            ws.write(r,4, getattr(l, 'quantity', 0) or 0, fmt)
            ws.write(r,5, _money_fa(getattr(l, 'unit_wage', 0)), fmt_r)
            ws.write(r,6, _money_fa(getattr(l, 'total_wage', 0)), fmt_r)
            ws.write(r,7, str(getattr(l, 'finished_at', '') or ''), fmt)
            r += 1

        # جمع
        ws.write(r, 0, 'جمع کل', fmt_h)
        for c in range(1,6): ws.write(r, c, '', fmt_h)
        ws.write(r, 6, _money_fa(total_wage), fmt_h)
        ws.write(r, 7, '', fmt_h)

        wb.close()
        output.seek(0)
        resp = HttpResponse(output.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp['Content-Disposition'] = 'attachment; filename=wages_report.xlsx'
        return resp

    # --------- Export PDF ---------
    if "export_pdf" in request.GET:
        html_str = render_to_string('core/wages_report.html', {
            'is_export': True,
            'tech_name': tech_name, 'start': start_str, 'end': end_str, 'product': product, 'stage_q': stage_q,
            'products_qs': products_qs, 'stages_qs': stages_qs, 'tech_names': tech_names,
            'logs': logs[:1000], 'total_wage': total_wage, 'by_tech': by_tech, 'by_stage': by_stage,
            '_money_fa': _money_fa,
        })
        pdf = HTML(string=html_str, base_url=request.build_absolute_uri('/')).write_pdf()
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = 'attachment; filename="wages_report.pdf"'
        return resp

    # --------- رندر HTML تمپلیت ---------
    ctx = {
        'is_export': False,
        'tech_name': tech_name, 'start': start_str, 'end': end_str, 'product': product, 'stage_q': stage_q,
        'products_qs': products_qs, 'stages_qs': stages_qs, 'tech_names': tech_names,
        'logs': logs[:1000], 'total_wage': total_wage, 'by_tech': by_tech, 'by_stage': by_stage,
        '_money_fa': _money_fa,
    }
    return TemplateResponse(request, 'core/wages_report.html', ctx)