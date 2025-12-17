from django.db import models
from django_jalali.db import models as jmodels
from django.core.exceptions import ValidationError
from decimal import Decimal
from django.utils import timezone


# -----------------------------
# Models
# -----------------------------
class Patient(models.Model):
    name       = models.CharField(max_length=200)
    phone      = models.CharField(max_length=20, blank=True, null=True)
    email      = models.EmailField(blank=True, null=True)
    address    = models.TextField(blank=True, null=True)
    birth_date = jmodels.jDateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Material(models.Model):
    name          = models.CharField(max_length=100)
    purchase_date = jmodels.jDateField(null=True, blank=True)
    quantity      = models.PositiveIntegerField(default=0)
    unit          = models.CharField(max_length=50, blank=True, null=True)

    def __str__(self):
        return self.name

from decimal import Decimal
class Order(models.Model):
    # Choices داخل کلاس
    ORDER_TYPES = [
        ('crown_pfm', 'Crown(P.F.M)'),
        ('crown_zirconia', 'Crown(Zirconia)'),
        ('implant_pfm', 'Implant(P.F.M)'),
        ('implant_zirconia', 'Implant(Zirconia)'),
        ('post_core_np', 'Post & Core(N.P)'),
        ('post_core_npg', 'Post & Core(N.P.G)'),
        ('laminat_press', 'Laminat(Press)'),
        ('laminat_zirconia', 'Laminat(Zirconia)'),
        ('jig_special', 'Jig & Special Trey'),
        ('full_waxup', 'Full Wax up'),
        ('denture', 'Denture'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]

    patient       = models.ForeignKey(Patient, on_delete=models.CASCADE)
    order_date    = jmodels.jDateField(null=True, blank=True)
    doctor        = models.CharField(max_length=100, blank=True, null=True)
    shade         = models.CharField(max_length=100, blank=True, null=True)
    order_type    = models.CharField(max_length=50, choices=ORDER_TYPES, blank=True, null=True)
    unit_count    = models.PositiveIntegerField(default=1)
    price         = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    serial_number = models.CharField(max_length=50, blank=True, null=True)
    status        = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    due_date      = jmodels.jDateField(null=True, blank=True)
    notes         = models.TextField(blank=True, null=True)
    teeth_fdi = models.CharField("کدهای FDI", max_length=128, blank=True, default="")
    created_at    = models.DateTimeField(auto_now_add=True)
    # داخل مدل Order:
    shipped_date = jmodels.jDateField(null=True, blank=True, verbose_name="تاریخ ارسال (واقعی)")


    # 🆕 فیلد محاسبه‌ای برای قیمت کل سفارش
    @property
    def total_price(self):
        if self.price and self.unit_count:
            return self.price * self.unit_count
        return 0

    @property
    def patient_name(self):
        return self.patient.name if self.patient else ""

    @property
    def material_cogs(self):
        """
        جمع بهای تمام‌شدهٔ متریال‌های مصرفی این سفارش بر اساس کارتکس:
        sum( |qty| * unit_cost_effective ) برای movement_type='issue'
        """
        from django.db.models import Sum, F
        from django.db.models.functions import Coalesce
        from decimal import Decimal
        agg = self.stock_movements.filter(movement_type='issue').annotate(
            cost_line=F('unit_cost_effective') * (F('qty') * -1)  # qty منفی است → قدر مطلق
        ).aggregate(s=Coalesce(Sum('cost_line'), Decimal('0.00')))
        return agg['s'] or Decimal('0.00')

    def material_qty_by_item(self):
        """
        خروجی: دیکشنری {item_id: qty_consumed} برای این سفارش (فقط issue ها).
        """
        from django.db.models import Sum
        qs = self.stock_movements.filter(movement_type='issue').values('item_id').annotate(
            qty=Sum('qty')
        )
        # qty ها منفی‌اند → به قدر مطلق تبدیل می‌کنیم
        return {row['item_id']: -row['qty'] for row in qs}

    def __str__(self):
        return f"Order #{self.id} - {self.patient_name}"

    @property
    def digital_lab_cost(self) -> Decimal:
        """
        جمع خالص هزینه لاب دیجیتال برای این سفارش:
        sum(charge_amount) - sum(credit_amount) از همهٔ انتقال‌های غیرلغو.
        (بدون ORM expressions تا خطای mixed types رخ ندهد.)
        """
        total_charge = Decimal('0')
        total_credit = Decimal('0')

        for charge, credit, status in self.digital_lab_transfers.values_list(
            'charge_amount', 'credit_amount', 'status'
        ):
            if status == 'cancelled':
                continue
            total_charge += (charge or Decimal('0'))
            total_credit += (credit or Decimal('0'))

        return total_charge - total_credit

    from django.db.models import Sum
    from decimal import Decimal

    @property
    def wages_total(self) -> Decimal:
        val = self.stage_worklogs.aggregate(s=Sum('total_wage'))['s'] if hasattr(self, 'stage_worklogs') else None
        return val or Decimal('0.00')

    

class Accounting(models.Model):
    order        = models.ForeignKey(Order, on_delete=models.CASCADE)
    payment_date = jmodels.jDateField(null=True, blank=True, verbose_name="تاریخ پرداخت")
    amount       = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="مبلغ پرداخت")
    method       = models.CharField(max_length=50, blank=True, null=True, verbose_name="روش پرداخت")
    date         = jmodels.jDateField(null=True, blank=True, verbose_name="تاریخ ثبت")

    def __str__(self):
        return f"حسابداری #{self.id} - {self.order.patient_name}"

    class Meta:
        verbose_name = "گزارش مالی"
        verbose_name_plural = "گزارش مالی"


class OrderEvent(models.Model):
    class EventType(models.TextChoices):
        # عمومی
        CREATED = 'created', 'ثبت سفارش'
        RECEIVED_IN_LAB = 'received_in_lab', 'دریافت در لابراتوار'
        IN_PROGRESS = 'in_progress', 'در حال انجام'
        SENT_TO_CLINIC = 'sent_to_clinic', 'ارسال به مطب'
        RETURNED_FROM_CLINIC = 'returned_from_clinic', 'بازگشت از مطب'
        SENT_TO_DIGITAL = 'sent_to_digital_lab', 'ارسال به لابراتوار دیجیتال'
        RECEIVED_FROM_DIGITAL = 'received_from_digital_lab', 'دریافت از لابراتوار دیجیتال'
        ADJUSTMENT = 'adjustment', 'اصلاح/ری‌ورک'
        GLAZE = 'glaze', 'گلیز نهایی'
        FINAL_SHIPMENT = 'final_shipment', 'ارسال نهایی'
        DELIVERED = 'delivered', 'تحویل قطعی'
        NOTE = 'note', 'یادداشت'
        # Crown / PFM
        FRAME_TRY_IN = 'frame_try_in', 'امتحان فریم'
        PORCELAIN_TRY_IN = 'porcelain_try_in', 'امتحان پرسلن'
        # Implant
        COMPONENTS_RECEIVED = 'components_received', 'دریافت قطعات از مطب'
        DURAL_TRY_IN = 'dural_try_in', 'امتحان دورالی'
        WAX_RIM_RECORD_BITE = 'wax_rim_record_bite', 'Wax rim & Record bite'

    class Direction(models.TextChoices):
        LAB_TO_CLINIC = 'lab→clinic', 'لابراتوار → مطب'
        CLINIC_TO_LAB = 'clinic→lab', 'مطب → لابراتوار'
        LAB_TO_DIGITAL = 'lab→digital', 'لابراتوار → دیجیتال'
        DIGITAL_TO_LAB = 'digital→lab', 'دیجیتال → لابراتوار'
        INTERNAL = 'internal', 'داخلی'

    order = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=50, choices=EventType.choices)
    happened_at = jmodels.jDateField(verbose_name='تاریخ وقوع')
    direction = models.CharField(max_length=20, choices=Direction.choices, blank=True)
    stage = models.CharField(max_length=100, blank=True)  # مثل crown/implant و زیرمرحله (اختیاری)
    stage_instance = models.ForeignKey(
        'StageInstance',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='events',
        verbose_name='مرحله مرتبط (اختیاری)',
    )
    notes = models.TextField(blank=True)
    attachment = models.FileField(upload_to='order_events/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['happened_at', 'id']

    def __str__(self):
        return f"{self.order_id} - {self.event_type} - {self.happened_at}"


class ProductionEvent(models.Model):
    class EventType(models.TextChoices):
        STAGE_CHANGE = 'stage_change', 'تغییر مرحله'
        CLINIC_SEND = 'clinic_send', 'ارسال به مطب'
        CLINIC_RECEIVE = 'clinic_receive', 'دریافت از مطب'
        DIGITAL_SEND = 'digital_send', 'ارسال به لاب دیجیتال'
        DIGITAL_RECEIVE = 'digital_receive', 'دریافت از لاب دیجیتال'
        NOTE = 'note', 'یادداشت'

    order = models.ForeignKey(
        'Order', on_delete=models.CASCADE, related_name='production_events', verbose_name='سفارش'
    )
    stage_key = models.CharField(max_length=100, blank=True, verbose_name='کلید مرحله')
    stage_label = models.CharField(max_length=150, blank=True, verbose_name='عنوان مرحله')
    event_type = models.CharField(max_length=30, choices=EventType.choices, verbose_name='نوع رویداد')
    responsible = models.CharField(max_length=120, blank=True, verbose_name='مسئول/اپراتور')
    started_at = jmodels.jDateField(null=True, blank=True, verbose_name='تاریخ شروع/وقوع')
    finished_at = jmodels.jDateField(null=True, blank=True, verbose_name='تاریخ پایان')
    payload = models.JSONField(default=dict, blank=True, verbose_name='جزئیات تکمیلی')
    notes = models.TextField(blank=True, verbose_name='یادداشت')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-started_at', '-id']
        verbose_name = 'رویداد تولید'
        verbose_name_plural = 'رویدادهای تولید'

    def __str__(self):
        return f"#{self.order_id} · {self.get_event_type_display()}"
    

# --- Doctor master data ---
class Doctor(models.Model):
    name   = models.CharField(max_length=120, unique=True, verbose_name="نام دکتر/مطب")
    clinic = models.CharField(max_length=150, blank=True, verbose_name="کلینیک/آدرس کوتاه")
    phone  = models.CharField(max_length=50, blank=True, verbose_name="تلفن")
    code   = models.CharField(max_length=30, blank=True, verbose_name="کد داخلی/ارجاع")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "دکتر"
        verbose_name_plural = "دکترها"
        ordering = ["name"]

    def __str__(self):
        return self.name

class Product(models.Model):
    code = models.SlugField(max_length=50, unique=True, verbose_name="کد محصول (لاتین/slug)")
    name = models.CharField(max_length=120, verbose_name="نام محصول")
    category = models.CharField(max_length=80, blank=True, default="", verbose_name="دسته‌بندی")
    default_unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        verbose_name="قیمت واحد پیش‌فرض (اختیاری)"
    )
    is_active = models.BooleanField(default=True, verbose_name="فعال؟")
    notes = models.TextField(blank=True, default="", verbose_name="توضیحات")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "محصول"
        verbose_name_plural = "محصولات"
        ordering = ["name", "code"]

    def __str__(self):
        return f"{self.name} ({self.code})"

class StageTemplate(models.Model):
    """
    مراحل استانداردِ تولید برای هر محصول (Product).
    مثال key ها: scan, design, mill, porcelain, qc, ready
    """
    product = models.ForeignKey('Product', on_delete=models.CASCADE, related_name='stages', verbose_name="محصول")
    key = models.SlugField(max_length=50, verbose_name="کلید مرحله (slug لاتین)")  # یکتا در هر محصول
    stage_key = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        db_index=True,
        verbose_name="کلید مشترک مرحله (برای گروه‌بندی مراحل مشابه بین محصولات)"
    )
    label = models.CharField(max_length=120, verbose_name="عنوان مرحله برای نمایش")
    order_index = models.PositiveSmallIntegerField(verbose_name="ترتیب مرحله")
    default_duration_days = models.PositiveSmallIntegerField(default=1, verbose_name="مدت پیشنهادی (روز)")
    is_active = models.BooleanField(default=True, verbose_name="فعال؟")
    notes = models.TextField(blank=True, default="", verbose_name="توضیحات")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    base_wage = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name="دستمزد پایه هر واحد (تومان)"
    )

    class Meta:
        verbose_name = "مرحلهٔ محصول"
        verbose_name_plural = "مراحل محصولات"
        ordering = ["product", "order_index"]
        constraints = [
            models.UniqueConstraint(fields=["product", "key"], name="uniq_stage_key_per_product"),
            models.UniqueConstraint(fields=["product", "order_index"], name="uniq_stage_order_per_product"),
        ]

    def __str__(self):
        return f"{self.product.name} → {self.label} ({self.key})"


# =========================
# Lab-wide Settings (singleton)
# =========================
class LabSettings(models.Model):
    lab_name          = models.CharField(max_length=200, verbose_name="نام لابراتوار", blank=True, default="")
    address           = models.TextField(verbose_name="آدرس", blank=True, default="")
    phone             = models.CharField(max_length=50, verbose_name="تلفن", blank=True, default="")
    whatsapp          = models.CharField(max_length=50, verbose_name="واتس‌اپ", blank=True, default="")
    currency          = models.CharField(max_length=20, verbose_name="واحد پول", default="تومان", blank=True)
    tax_rate          = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="نرخ مالیات (%)", default=0)
    default_due_days  = models.PositiveSmallIntegerField(verbose_name="مهلت پیش‌فرض (روز)", default=7)
    jalali_enabled    = models.BooleanField(verbose_name="تقویم جلالی فعال باشد؟", default=True)
    logo              = models.ImageField(upload_to='settings/', null=True, blank=True, verbose_name="لوگو (اختیاری)")

    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "تنظیمات لابراتوار"
        verbose_name_plural = "تنظیمات لابراتوار"
        # فقط یک رکورد نگه می‌داریم؛ ترتیب اهمیتی ندارد

    def __str__(self):
        return self.lab_name or "تنظیمات لابراتوار"

    @classmethod
    def get_solo(cls):
        """
        همیشه رکورد با pk=1 را برمی‌گرداند؛ اگر نبود می‌سازد.
        """
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

class StageInstance(models.Model):
    class Status(models.TextChoices):
        PENDING     = 'pending',     'در صف'
        IN_PROGRESS = 'in_progress', 'در حال انجام'
        DONE        = 'done',        'انجام شد'
        BLOCKED     = 'blocked',     'متوقف'

    order        = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='stages')
    template     = models.ForeignKey('StageTemplate', on_delete=models.SET_NULL, null=True, blank=True, related_name='instances')

    # Snapshot از Template برای پایداری
    key          = models.CharField(max_length=100, db_index=True)     # مثل crown.frame
    label        = models.CharField(max_length=200)                     # «امتحان فریم»
    order_index  = models.PositiveSmallIntegerField(default=0)          # ترتیب نمایش

    # تاریخ‌ها (جلالی)
    planned_date = jmodels.jDateField(null=True, blank=True)            # برنامه‌ی پیش‌بینی‌شده
    started_date = jmodels.jDateField(null=True, blank=True)
    done_date    = jmodels.jDateField(null=True, blank=True)

    status       = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    notes        = models.TextField(blank=True)

    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "مرحلهٔ سفارش"
        verbose_name_plural = "مراحل سفارش"
        ordering = ['order', 'order_index', 'id']
        unique_together = (('order', 'key'),)  # جلوگیری از تکرار یک مرحله برای یک سفارش

    def __str__(self):
        return f"#{self.order_id} · {self.label} ({self.status})"


class Technician(models.Model):
    """
    پرسنل/تکنسین لابراتوار. فعلاً ساده؛ اگر بعداً خواستی می‌تونی به User وصلش کنی.
    """
    name = models.CharField(max_length=120, unique=True, verbose_name="نام تکنسین")
    role = models.CharField(max_length=80, blank=True, default="", verbose_name="نقش/تخصص")
    is_active = models.BooleanField(default=True, verbose_name="فعال؟")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تکنسین"
        verbose_name_plural = "تکنسین‌ها"
        ordering = ["name"]

    def __str__(self):
        return self.name


class StageRate(models.Model):
    """
    نرخ توافقی تاریخ‌دار برای یک تکنسین در یک مرحله‌ی خاص (StageTemplate).
    اگر برای (stage, technician) رکوردی در تاریخ موثر وجود داشته باشد، از این نرخ استفاده می‌شود؛
    وگرنه از StageTemplate.base_wage استفاده می‌کنیم.
    """
    stage = models.ForeignKey('StageTemplate', on_delete=models.CASCADE, related_name='rates', verbose_name="مرحله")
    technician = models.ForeignKey('Technician', on_delete=models.CASCADE, related_name='stage_rates', verbose_name="تکنسین")
    rate = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="نرخ هر واحد (تومان)")
    effective_from = jmodels.jDateField(verbose_name="تاریخ شروع اعتبار")
    note = models.CharField(max_length=200, blank=True, default="", verbose_name="توضیح")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "نرخ مرحله (تکنسین)"
        verbose_name_plural = "نرخ‌های مرحله (تکنسین)"
        ordering = ["stage", "technician", "-effective_from"]
        indexes = [
            models.Index(fields=["stage", "technician", "effective_from"]),
        ]
        constraints = [
            # برای هر (stage, technician, effective_from) تکراری نباشد
            models.UniqueConstraint(fields=["stage", "technician", "effective_from"], name="uniq_stage_rate_per_date"),
        ]

    def __str__(self):
        return f"{self.stage.label} • {self.technician.name} • از {self.effective_from}"


class WagePayout(models.Model):
    """
    تسویهٔ دستمزد یک تکنسین در یک بازهٔ زمانی.
    لاگ‌های StageWorkLog از طریق فیلد payout به این مدل لینک می‌شوند.
    """
    class Status(models.TextChoices):
        DRAFT     = 'draft',     'پیش‌نویس'
        CONFIRMED = 'confirmed', 'تأییدشده'
        PAID      = 'paid',      'پرداخت‌شده'

    technician = models.ForeignKey(
        'Technician',
        on_delete=models.PROTECT,
        related_name='wage_payouts',
        verbose_name="تکنسین",
    )

    # بازهٔ تسویه (جلالی، هماهنگ با بقیهٔ سیستم)
    period_start_j = jmodels.jDateField(null=True, blank=True, verbose_name="تاریخ شروع بازه")
    period_end_j   = jmodels.jDateField(null=True, blank=True, verbose_name="تاریخ پایان بازه")

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name="وضعیت"
    )

    # اعداد اصلی تسویه
    gross_total      = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="جمع ناخالص دستمزد"
    )
    deductions_total = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="جمع کسورات"
    )
    bonus_total      = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="جمع پاداش"
    )
    net_payable      = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="خالص قابل پرداخت"
    )

    note = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name="توضیحات"
    )
    payment_ref = models.CharField(
        max_length=120,
        blank=True,
        default="",
        verbose_name="مرجع/شماره سند پرداخت"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاریخ آخرین ویرایش")

    class Meta:
        verbose_name = "تسویه دستمزد"
        verbose_name_plural = "تسویه‌های دستمزد"
        ordering = ['-period_end_j', '-id']

    def __str__(self):
        ps = self.period_start_j or ""
        pe = self.period_end_j or ""
        return f"{self.technician} • {ps} → {pe} • {self.net_payable} تومان"


class StageWorkLog(models.Model):
    """
    لاگ انجام یک مرحله برای یک سفارش.
    - اگر unit_wage وارد نشود، بر اساس StageRate فعال یا base_wage تعیین می‌شود.
    - total_wage همیشه هنگام ذخیره محاسبه می‌شود: quantity × unit_wage
    """
    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', 'در حال انجام'
        DONE        = 'done',        'انجام شد'
        CANCELLED   = 'cancelled',   'لغو شده'

    order      = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='stage_worklogs', verbose_name="سفارش")
    stage_inst = models.ForeignKey('StageInstance', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='worklogs', verbose_name="مرحلهٔ سفارش (اختیاری)")
    stage_tpl  = models.ForeignKey('StageTemplate', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='worklogs', verbose_name="مرحلهٔ مرجع (Template)")
    technician = models.ForeignKey('Technician', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='worklogs', verbose_name="تکنسین")

    # تاریخ‌ها (جلالی)
    started_at  = jmodels.jDateField(null=True, blank=True, verbose_name="تاریخ شروع")
    finished_at = jmodels.jDateField(null=True, blank=True, verbose_name="تاریخ پایان")

    # تعداد واحد کار انجام‌شده در این لاگ (برای سفارش‌های چندواحدی)
    quantity  = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1.00'), verbose_name="تعداد واحد")
    unit_wage = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="نرخ هر واحد (تومان)")
    total_wage = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), verbose_name="مبلغ کل (تومان)")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DONE, verbose_name="وضعیت")
    # وضعیت تسویهٔ این لاگ
    payout = models.ForeignKey(
        'WagePayout',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='worklogs',
        verbose_name="تسویه مرتبط",
    )
    is_settled = models.BooleanField(
        default=False,
        verbose_name="تسویه‌شده؟"
    )
    settled_at_j = jmodels.jDateField(
        null=True,
        blank=True,
        verbose_name="تاریخ تسویه"
    )
    note   = models.CharField(max_length=200, blank=True, default="", verbose_name="توضیح")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "لاگ مرحله (دستمزد)"
        verbose_name_plural = "لاگ‌های مرحله (دستمزد)"
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['order']),
            models.Index(fields=['stage_tpl']),
            models.Index(fields=['technician']),
        ]

    def __str__(self):
        st = self.stage_tpl.label if self.stage_tpl else (self.stage_inst.label if self.stage_inst else "مرحله")
        return f"#{self.order_id} • {st} • {self.total_wage} تومان"

    # ---------- منطق تعیین نرخ ----------
    def _resolve_unit_wage(self) -> Decimal:
        """
        اگر unit_wage به‌طور صریح داده نشده باشد:
          1) اگر تکنسین و stage_tpl مشخص هستند → آخرین StageRate با effective_from ≤ تاریخ مرجع (finished_at یا started_at یا امروز)
          2) اگر نرخ پیدا نشد → از base_wage خود stage_tpl
          3) در نهایت 0
        """
        # اگر قبلاً وارد شده، همان را نگه داریم
        if self.unit_wage is not None:
            return Decimal(self.unit_wage or 0)

        tpl = self.stage_tpl or (self.stage_inst.template if self.stage_inst else None)
        tech = self.technician

        # تاریخ مرجع
        ref_date = None
        for d in (self.finished_at, self.started_at):
            if d:
                ref_date = d
                break
        if ref_date is None:
            # اگر جلالی ندادی، امروز میلادی را به تاریخ جلالی تقریباً نیاز نداریم؛ چون StageRate.effective_from هم جلالی است.
            # کافیست مقایسه رشته/تاریخ جلالی صورت گیرد. اینجا ref_date را خالی می‌گذاریم تا «کمتر/مساوی امروز» هم پوشش دهد.
            pass

        # 1) StageRate
        if tpl and tech:
            qs = StageRate.objects.filter(stage=tpl, technician=tech)
            if ref_date:
                qs = qs.filter(effective_from__lte=ref_date)
            # اگر ref_date نبود، همان آخرین نرخ را می‌گیریم
            rate_row = qs.order_by('-effective_from', '-id').first()
            if rate_row and rate_row.rate:
                return Decimal(rate_row.rate)

        # 2) base_wage
        if tpl and tpl.base_wage is not None:
            return Decimal(tpl.base_wage or 0)

        # 3) صفر
        return Decimal('0')

    def save(self, *args, **kwargs):
        # stage_tpl را اگر خالی بود و stage_inst داریم، از آن پُر کن (برای گزارش‌گیری بهتر)
        if not self.stage_tpl and self.stage_inst and self.stage_inst.template_id:
            try:
                self.stage_tpl = self.stage_inst.template
            except Exception:
                pass

        # محاسبهٔ نرخ هر واحد در صورت نیاز
        if self.unit_wage is None:
            self.unit_wage = self._resolve_unit_wage()

        # محاسبه مبلغ کل
        q = Decimal(str(self.quantity or 0))
        u = Decimal(str(self.unit_wage or 0))
        self.total_wage = (q * u)

        super().save(*args, **kwargs)

# =====================[ Digital Lab Transfers ]=====================
class DigitalLabTransfer(models.Model):
    """
    ثبت ارسال سفارش به لابراتوار دیجیتال (برای فریم، میلینگ، طراحی و ...).
    هر ارسال یک نوبت مستقل است (attempt_no). ری‌ورک‌ها نوبت‌های بعدی‌اند.
    """
    class Status(models.TextChoices):
        SENT      = 'sent',      'ارسال‌شده'
        RECEIVED  = 'received',  'تحویل‌گرفته‌شده'
        CANCELLED = 'cancelled', 'لغو شده'

    # --- ارجاعات اصلی ---
    order = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='digital_lab_transfers', verbose_name="سفارش")

    # --- اطلاعات عمومی ارسال ---
    lab_name = models.CharField(max_length=160, verbose_name="نام لابراتوار دیجیتال")
    stage_name = models.CharField(max_length=160, verbose_name="مرحله مربوطه (مثلاً فریم، میلینگ، طراحی)")
    # کلید مرحله برای گزارش‌گیری/فیلتر (اختیاری)
    stage_key = models.CharField(max_length=50, blank=True, db_index=True, verbose_name="کلید مرحله (اختیاری)")

    # رنگ (Shade)؛ برای مراحل رنگ‌محور، اختیاری (مثلاً کاستوم اباتمنت نیازی ندارد)
    shade_code = models.CharField(max_length=16, blank=True, default="", verbose_name="رنگ (Shade)")

    sent_date = models.DateField(verbose_name="تاریخ ارسال")
    received_date = models.DateField(null=True, blank=True, verbose_name="تاریخ دریافت")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENT, verbose_name="وضعیت")

    # --- ری‌ورک / نوبت ---
    attempt_no = models.PositiveSmallIntegerField(default=1, db_index=True, verbose_name="نوبت (Attempt #)")
    is_redo = models.BooleanField(default=False, verbose_name="تکرار/ری‌ورک؟")
    redo_reason = models.CharField(max_length=200, blank=True, default="", verbose_name="علت تکرار")
    related_to = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='redo_children', verbose_name="مرتبط با نوبت قبلی"
    )

    # --- مبالغ این نوبت ---
    # توجه: فعلاً cost را نگه می‌داریم تا Data Migration انجام شود (بعداً حذف می‌شود)
    cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="(قدیمی) هزینه (تومان)")
    charge_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="مبلغ این نوبت (تومان)")
    credit_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="اعتبار/برگشت این نوبت (تومان)")

    # --- سایر ---
    note = models.CharField(max_length=200, blank=True, default="", verbose_name="توضیح")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['order']),
            models.Index(fields=['sent_date']),
            models.Index(fields=['status']),
            models.Index(fields=['attempt_no']),
        ]
        ordering = ['-sent_date', '-id']
        verbose_name = "ارسال به لاب دیجیتال"
        verbose_name_plural = "ارسال‌ها به لاب دیجیتال"

    def clean(self):
        """
        اعتبارسنجی نرم:
        - اگر تکرار است (is_redo) ولی «مرتبط با» خالی است → خطا.
        - اگر وضعیت لغو نیست، حداقل یکی از charge_amount یا credit_amount باید > 0 باشد.
        """
        errors = {}

        # ری‌ورک بدون مرجع قبلی مجاز نیست
        if self.is_redo and not self.related_to:
            errors['related_to'] = "وقتی «تکرار/ری‌ورک» تیک می‌خورد، باید «مرتبط با نوبت قبلی» را مشخص کنید."

        # الزام مبلغ برای رکوردهای غیرلغو
        if self.status != self.Status.CANCELLED:
            charge = (self.charge_amount or 0)
            credit = (self.credit_amount or 0)
            if (charge <= 0) and (credit <= 0):
                errors['charge_amount'] = "برای این نوبت، حداقل یکی از «مبلغ این نوبت» یا «اعتبار/برگشت» باید بزرگ‌تر از صفر باشد."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """
        منطق اتوماتیک نوبت برای ری‌ورک:
        - اگر related_to پر باشد و attempt_no از آن کمتر/مساوی بود، attempt_no = related_to.attempt_no + 1
        """
        if self.related_to:
            base_no = int(self.related_to.attempt_no or 1)
            if (self.attempt_no or 1) <= base_no:
                self.attempt_no = base_no + 1
            # اگر کاربر related_to را ست کرد ولی is_redo را تیک نزد، خودکار تیک بزنیم (بی‌ضرر و کمک‌کننده)
            if not self.is_redo:
                self.is_redo = True

        super().save(*args, **kwargs)

    
    def __str__(self):
        return f"{self.order_id} • {self.lab_name} • {self.stage_name} • Attempt#{self.attempt_no} • {self.sent_date}"







