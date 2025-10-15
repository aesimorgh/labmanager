from django.db import models
from django_jalali.db import models as jmodels


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

    def __str__(self):
        return f"Order #{self.id} - {self.patient_name}"


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
    label = models.CharField(max_length=120, verbose_name="عنوان مرحله برای نمایش")
    order_index = models.PositiveSmallIntegerField(verbose_name="ترتیب مرحله")
    default_duration_days = models.PositiveSmallIntegerField(default=1, verbose_name="مدت پیشنهادی (روز)")
    is_active = models.BooleanField(default=True, verbose_name="فعال؟")
    notes = models.TextField(blank=True, default="", verbose_name="توضیحات")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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









