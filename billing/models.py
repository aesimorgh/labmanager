from decimal import Decimal, ROUND_HALF_UP
from django.db import models
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from django.db.models.functions import Coalesce
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT   = 'draft',   'Draft'
        ISSUED  = 'issued',  'Issued'
        PARTIAL = 'partial', 'Partial Paid'
        PAID    = 'paid',    'Paid'

    # دکتر (مدل در core)
    doctor = models.ForeignKey('core.Doctor', on_delete=models.PROTECT,
                               related_name='invoices', null=True, blank=True)

    # مشخصات/بازه
    code         = models.CharField(max_length=32, unique=True, blank=True)
    period_from  = models.DateField(null=True, blank=True)
    period_to    = models.DateField(null=True, blank=True)

    # مبالغ
    subtotal          = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    previous_balance  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    payments_applied  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    grand_total       = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    amount_due        = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    status    = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    issued_at = models.DateTimeField(null=True, blank=True)
    notes     = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['doctor']),
            models.Index(fields=['status']),
            models.Index(fields=['issued_at']),
        ]
        ordering = ['-issued_at', '-created_at']

    def __str__(self):
        label = self.code or f'Draft #{self.id}'
        return f'{label} – {self.doctor or "—"}'

    # ---------- جمع‌زن‌ها ----------
    def recompute_totals(self):
        """
        جمع‌ها را از روی خطوط محاسبه می‌کند.
        - subtotal = sum(unit_count * unit_price)
        - grand_total = sum(line_total)
        - amount_due = grand_total  (فعلاً پرداختی نداریم)
        """
        # subtotal (محاسبه‌ی جداگانه از روی unit_count * unit_price)
        subtotal = self.lines.annotate(
            line_calc=ExpressionWrapper(
                F('unit_count') * F('unit_price'),
                output_field=DecimalField(max_digits=14, decimal_places=2)
            )
        ).aggregate(s=Coalesce(Sum('line_calc'), Decimal('0')))['s']

        # discount_total نداریم؛ اگر لازم شد اضافه می‌کنیم. payments_applied هم فعلاً 0 است.
        grand_total = self.lines.aggregate(
            s=Coalesce(Sum('line_total'), Decimal('0'))
        )['s']

        self.subtotal = subtotal
        self.grand_total = grand_total
        # previous_balance + payments_applied فعلاً تاثیری نمی‌گذارند
        self.amount_due = grand_total
        self.save(update_fields=['subtotal', 'grand_total', 'amount_due'])


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='lines')

    # هر سفارش فقط یک‌بار فاکتور شود:
    order = models.OneToOneField('core.Order', on_delete=models.PROTECT, related_name='invoice_line')

    description     = models.CharField(max_length=255, blank=True)
    unit_count      = models.PositiveIntegerField(default=1)
    unit_price      = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    line_total      = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['invoice']),
        ]

    def __str__(self):
        return f'Line #{self.id} of {self.invoice}'


class DoctorPayment(models.Model):
    doctor = models.ForeignKey('core.Doctor', on_delete=models.PROTECT, related_name='payments')
    date   = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=64, blank=True)  # cash, card, transfer, ...
    note   = models.CharField(max_length=255, blank=True)
    
    ALLOC_STATUS_CHOICES = [
        ('unallocated', 'بدون تخصیص'),
        ('partial', 'بخشی تخصیص‌یافته'),
        ('allocated', 'کامل تخصیص‌یافته'),
    ]
    allocation_status = models.CharField(
        max_length=20,
        choices=ALLOC_STATUS_CHOICES,
        default='unallocated',
        db_index=True,
    )


    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['doctor', 'date']),
        ]
        ordering = ['-date', '-id']

    def __str__(self):
        return f'Payment {self.amount} for {self.doctor} on {self.date}'

    def recompute_allocation_status(self, save=True):
        """
        وضعیت تخصیص را بر اساس جمع تخصیص‌های مرتبط با این پرداخت محاسبه می‌کند.
        قواعد:
            - allocated   : اگر مجموع تخصیص‌ها >= مبلغ پرداخت
            - partial     : اگر 0 < مجموع تخصیص‌ها < مبلغ پرداخت
            - unallocated : اگر مجموع تخصیص‌ها == 0
        """
        from .models import PaymentAllocation  # import محلی برای جلوگیری از وابستگی دایره‌ای

        total_alloc = (
            PaymentAllocation.objects
            .filter(payment=self)
            .aggregate(s=Sum('amount_allocated'))
            .get('s') or Decimal('0')
        )

        # نرمال‌سازی به دو رقم اعشار (مثل فیلدهای Decimal در DB)
        total_alloc = (total_alloc or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        amt = (self.amount or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        if total_alloc <= Decimal('0.00'):
            new_status = 'unallocated'
        elif total_alloc >= amt:
            new_status = 'allocated'
        else:
            new_status = 'partial'

        if getattr(self, 'allocation_status', None) != new_status:
            self.allocation_status = new_status
            if save:
                try:
                    self.save(update_fields=['allocation_status'])
                except Exception:
                    # اگر مدل قدیمی باشد و فیلد هنوز اضافه نشده باشد (در محیط‌های قدیمی)، بی‌صدا رد شو
                    pass
        return new_status


class PaymentAllocation(models.Model):
    """تخصیص پرداخت‌ها به فاکتور (FIFO). هر ردیف بخشی از یک پرداخت را به یک فاکتور لینک می‌کند."""
    payment = models.ForeignKey(DoctorPayment, on_delete=models.CASCADE, related_name='allocations')
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='allocations')
    amount_allocated = models.DecimalField(max_digits=14, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['payment']),
            models.Index(fields=['invoice']),
        ]
        unique_together = [('payment', 'invoice')]

    def __str__(self):
        return f'Alloc {self.amount_allocated} → {self.invoice} (from {self.payment})'


@receiver(post_save, sender=PaymentAllocation)
def _on_alloc_saved(sender, instance, **kwargs):
    """بعد از ایجاد/ویرایش هر تخصیص، وضعیت پرداخت به‌روز شود."""
    try:
        pay = instance.payment
        if hasattr(pay, 'recompute_allocation_status'):
            pay.recompute_allocation_status(save=True)
    except Exception:
        pass


@receiver(post_delete, sender=PaymentAllocation)
def _on_alloc_deleted(sender, instance, **kwargs):
    """بعد از حذف تخصیص، وضعیت پرداخت به‌روز شود."""
    try:
        pay = instance.payment
        if hasattr(pay, 'recompute_allocation_status'):
            pay.recompute_allocation_status(save=True)
    except Exception:
        pass


# =====================[ NEW ]=====================
class LabProfile(models.Model):
    """
    تنظیمات برند/حساب بانکی لابراتوار (برای استفاده در چاپ فاکتور و سایر صفحات).
    از ادمین یک رکورد بسازید؛ در ویوها با objects.first() می‌خوانیم.
    """
    name   = models.CharField(max_length=120, default="Academy Dental Lab")
    slogan = models.CharField(max_length=160, blank=True, default="")
    # دو روش برای لوگو: یا فایل آپلودی، یا مسیر استاتیک (برای {% static %})
    logo_file       = models.ImageField(upload_to="lab/", null=True, blank=True)
    logo_static_path = models.CharField(
        max_length=255,
        blank=True,
        help_text="مثال: img/academy-logo.png (برای استفاده با {% static %})"
    )

    # اطلاعات بانکی
    card_no      = models.CharField(max_length=64, blank=True)   # مثال: 2828 3597 3310 6104
    iban         = models.CharField(max_length=64, blank=True)   # مثال: IR80 0120 0100 0000 1226 4712 50
    account_name = models.CharField(max_length=120, blank=True)  # مثال: زهرا پیشکاری

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Lab profile"
        verbose_name_plural = "Lab profile"

    def __str__(self):
        return self.name or "Lab"

    def get_logo_url(self):
        """اگر فایل آپلودی موجود بود، URL برمی‌گرداند؛ وگرنه None."""
        if self.logo_file:
            try:
                return self.logo_file.url
            except Exception:
                pass
        return None

# =====================[ Expenses ]=====================
from decimal import Decimal
from django.db import models

class Expense(models.Model):
    class Category(models.TextChoices):
        # --- دسته‌های «هزینه جاری» (جدید، مطابق درخواست) ---
        RENT        = 'rent',        'اجاره/شارژ'
        UTILITIES   = 'utilities',   'قبض'
        COURIER     = 'courier',     'پیک'
        TRANSPORT   = 'transport',   'حمل و نقل'
        HOME        = 'home',        'مخارج جاری منزل'
        INSTALLMENT = 'installment', 'قسط'
        PETTY_CASH  = 'petty_cash',  'تنخواه لابراتوار'
        MISC        = 'misc',        'سایر'

        # --- دسته‌های قدیمی (برای سازگاری با رکوردهای قبلی؛ در فرم جدید نمایش نمی‌دهیم) ---
        MATERIALS = 'materials', 'مواد و متریال'
        REPAIRS   = 'repairs',   'تعمیرات/نگهداری'
        SALARY    = 'salary',    'دستمزد/حق‌الزحمه'

    date       = models.DateField()
    category   = models.CharField(max_length=24, choices=Category.choices, db_index=True)
    amount     = models.DecimalField(max_digits=14, decimal_places=2)
    note       = models.CharField(max_length=255, blank=True)
    attachment = models.FileField(upload_to='expenses/', null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['category']),
        ]
        ordering = ['-date', '-id']

    def __str__(self):
        return f'Expense {self.amount} on {self.date} ({self.get_category_display()})'


# =====================[ Inventory (Materials) ]=====================
# فاز ۱: اسکلت انبار برای خرید/لات/کارتکس/مصرف استاندارد (BOM)/مصرف واقعی به سفارش

class MaterialItem(models.Model):
    """
    کارت کالا: متریال‌های مصرفی (پرسلین، فلز، آکریل، اباتمنت، رنگ، …)
    """
    class ItemType(models.TextChoices):
        MATERIAL = 'material', 'متریال'
        TOOL     = 'tool',     'ابزار مصرفی'

    
    class Category(models.TextChoices):
        PORCELAIN = 'porcelain', 'پرسلین'
        METAL     = 'metal',     'فلز/آلیاژ'
        ACRYLIC   = 'acrylic',   'آکریل/رزین'
        ABUTMENT  = 'abutment',  'اباتمنت/قطعات'
        COLOR     = 'color',     'رنگ/استین'
        OTHER     = 'other',     'سایر'

    code        = models.SlugField(max_length=60, unique=True, verbose_name="کد کالا (slug)")
    name        = models.CharField(max_length=160, verbose_name="نام متریال")
    item_type   = models.CharField(max_length=16, choices=ItemType.choices, default='material', verbose_name="نوع آیتم")
    shade_enabled = models.BooleanField(default=False, verbose_name="دارای رنگ/Shade؟")
    pack_size   = models.PositiveIntegerField(null=True, blank=True, verbose_name="تعداد در هر باکس (اختیاری)")
    category    = models.CharField(max_length=30, choices=Category.choices, db_index=True)
    class UOM(models.TextChoices):
        GRAM = 'g', 'گرم'
        KILOGRAM = 'kg', 'کیلوگرم'
        MILLILITER = 'ml', 'میلی‌لیتر'
        LITER = 'l', 'لیتر'
        PIECE = 'pcs', 'عدد'
        BOX = 'box', 'باکس'

    uom = models.CharField(
        max_length=16,
        choices=UOM.choices,
        default='g',
        verbose_name="واحد پایه (g|kg|ml|l|pcs|box)"
    )
    min_stock   = models.DecimalField(max_digits=12, decimal_places=3, default=0, verbose_name="حداقل موجودی")
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True, verbose_name="عمر مفید (روز)")
    is_active   = models.BooleanField(default=True)
    notes       = models.TextField(blank=True, default="")

    # 🆕 وضعیت لحظه‌ای (برای سرعت و ثبات محاسبات COGS)
    stock_qty     = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('0.000'), verbose_name="موجودی فعلی")
    avg_unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), verbose_name="میانگین موزون فعلی")

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['category', 'is_active']),
        ]
        ordering = ['name', 'code']
        verbose_name = "کالای متریال"
        verbose_name_plural = "کالاهای متریال"

    def __str__(self):
        return f"{self.name} ({self.code})"

    def recompute_snapshot(self):
        """
        در صورت نیاز، می‌تواند با جمع حرکات کارتکس، stock_qty/avg_unit_cost را بازسازی کند.
        (ساده‌سازی: avg بر اساس فرمول متعارف میانگین موزون از خریدها)
        """
        from django.db.models import Sum, F
        # موجودی = مجموع qty حرکات
        qty = self.movements.aggregate(s=Coalesce(Sum('qty'), Decimal('0')))['s'] or Decimal('0')
        # مجموع ارزش وارده از خریدها = جمع (qty_in * unit_cost) برای PURCHASEهای مثبت
        purchases = self.movements.filter(movement_type='purchase', qty__gt=0).annotate(
            val=ExpressionWrapper(F('qty') * F('unit_cost_effective'), output_field=DecimalField(max_digits=16, decimal_places=2))
        ).aggregate(s=Coalesce(Sum('val'), Decimal('0')))['s'] or Decimal('0')
        avg = Decimal('0.00')
        if qty and qty > 0:
            # میانگین تقریبی از ارزش خریدها/كل موجودی
            avg = (purchases / qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        self.stock_qty = qty
        self.avg_unit_cost = avg
        self.save(update_fields=['stock_qty', 'avg_unit_cost'])


class MaterialLot(models.Model):
    """
    لات/پارت خرید: قیمت واحد واقعی و تاریخ انقضا/مصرف
    """
    item          = models.ForeignKey('MaterialItem', on_delete=models.PROTECT, related_name='lots')
    lot_code      = models.CharField(max_length=80, blank=True, default="", verbose_name="کد لات/سری")
    vendor        = models.CharField(max_length=160, blank=True, default="", verbose_name="تأمین‌کننده")
    purchase_date = models.DateField()
    qty_in        = models.DecimalField(max_digits=12, decimal_places=3, verbose_name="تعداد/وزن خرید")
    unit_cost     = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="قیمت واحد")
    currency      = models.CharField(max_length=8, blank=True, default="IRR")
    expire_date   = models.DateField(null=True, blank=True)
    invoice_no    = models.CharField(max_length=80, blank=True, default="")
    shade_code     = models.CharField(max_length=16, blank=True, default="", verbose_name="رنگ/Shade (در صورت نیاز)")
    start_use_date = models.DateField(null=True, blank=True, verbose_name="تاریخ آغاز مصرف")
    end_use_date   = models.DateField(null=True, blank=True, verbose_name="تاریخ اتمام مصرف")
    allocated      = models.BooleanField(default=False, verbose_name="لات تخصیص یافته؟")
    allocated_at   = models.DateTimeField(null=True, blank=True, verbose_name="زمان تخصیص")
    attachment    = models.FileField(upload_to='inventory/purchases/', null=True, blank=True)
    notes         = models.TextField(blank=True, default="")

    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['item']),
            models.Index(fields=['purchase_date']),
            models.Index(fields=['expire_date']),
            models.Index(fields=['allocated']),
        ]
        ordering = ['-purchase_date', '-id']
        verbose_name = "لات خرید متریال"
        verbose_name_plural = "لات‌های خرید متریال"
    
    def clean(self):
        """
        نگهبان بازهٔ مصرف:
        - end_use_date نباید قبل از start_use_date باشد
        - اگر آیتم رنگ‌محور است، shade_code باید پر باشد
        - برای همان (item, shade_code) هیچ لات دیگری نباید بازهٔ هم‌پوشان داشته باشد
        """
        super().clean()

        # نرمال‌سازی رنگ
        shade = (self.shade_code or "").strip()

        # اگر آیتم رنگ‌محور است، رنگ الزامی است
        try:
            if self.item and getattr(self.item, "shade_enabled", False) and not shade:
                raise ValidationError("برای این متریال، وارد کردن رنگ (Shade) الزامی است.")
        except Exception:
            # اگر self.item هنوز ست نشده باشد، از این چک عبور می‌کنیم
            pass

        # sanity: ترتیب تاریخ‌ها
        if self.start_use_date and self.end_use_date:
            if self.end_use_date < self.start_use_date:
                raise ValidationError("تاریخ اتمام مصرف نمی‌تواند قبل از تاریخ آغاز مصرف باشد.")

            # هم‌پوشانی بازه با لات‌های دیگرِ همین آیتم/رنگ
            qs = MaterialLot.objects.filter(
                item=self.item,
                shade_code=shade,
                start_use_date__isnull=False,
                end_use_date__isnull=False,
                start_use_date__lte=self.end_use_date,
                end_use_date__gte=self.start_use_date,
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)

            if qs.exists():
                other = qs.order_by('-id').first()
                raise ValidationError(f"بازهٔ مصرف با لات دیگری هم‌پوشانی دارد (Lot ID {other.id}).")

    
    def __str__(self):
        return f"Lot {self.lot_code or self.id} · {self.item.code}"

class StageDefault(models.Model):
    """
    پیش‌فرض‌های متریال برای هر کلید مشترک مرحله (stage_key).
    هر ردیف یعنی: این متریال در مرحله‌ای با این کلید مصرف می‌شود.
    """
    stage_key = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name="کلید مشترک مرحله (stage_key)"
    )
    material = models.ForeignKey(
        'MaterialItem',
        on_delete=models.PROTECT,
        related_name='stage_defaults',
        verbose_name="متریال"
    )
    shade_sensitive = models.BooleanField(default=False, verbose_name="وابسته به رنگ/Shade؟")
    is_active = models.BooleanField(default=True, verbose_name="فعال؟")
    note = models.CharField(max_length=200, blank=True, default="", verbose_name="یادداشت")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "پیش‌فرض متریالِ مرحله"
        verbose_name_plural = "پیش‌فرض‌های متریالِ مرحله"
        ordering = ['stage_key', 'material']
        constraints = [
            models.UniqueConstraint(fields=['stage_key', 'material'], name='uniq_stage_default_stage_material'),
        ]
        indexes = [
            models.Index(fields=['stage_key', 'material']),
        ]

    def __str__(self):
        mat = None
        try:
            # اگر material ست نشده باشد، دسترسی مستقیم خطا می‌دهد
            mat = self.material.name if getattr(self, "material_id", None) else None
        except Exception:
            mat = None
        return f"{self.stage_key} → {mat or '—'}"



def _q2(x: Decimal) -> Decimal:
    """گرد کردن استاندارد ۲ رقم اعشار برای مبالغ"""
    if x is None:
        return Decimal('0.00')
    return Decimal(x).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def _q3(x: Decimal) -> Decimal:
    """گرد کردن استاندارد ۳ رقم اعشار برای مقادیر (وزن/تعداد)"""
    if x is None:
        return Decimal('0.000')
    return Decimal(x).quantize(Decimal('0.000'), rounding=ROUND_HALF_UP)


class StockMovement(models.Model):
    """
    کارتکس انبار: هر حرکت ورود/خروج/ضایعات/اصلاح.
    برای «issue به سفارش»، هزینه‌ی موثر لحظه‌ای در unit_cost_effective ذخیره می‌شود.
    """
    class MoveType(models.TextChoices):
        PURCHASE   = 'purchase',   'خرید'
        ISSUE      = 'issue',      'خروج به سفارش'
        RETURN_IN  = 'return_in',  'برگشت از سفارش'
        WASTE      = 'waste',      'ضایعات/تلفات'
        ADJ_POS    = 'adjust_pos', 'اصلاح افزایشی'
        ADJ_NEG    = 'adjust_neg', 'اصلاح کاهشی'
        STOCKTAKE  = 'stocktake',  'شمارش/انبارگردانی'

    item                = models.ForeignKey('MaterialItem', on_delete=models.PROTECT, related_name='movements')
    lot                 = models.ForeignKey('MaterialLot', on_delete=models.SET_NULL, null=True, blank=True, related_name='movements')
    movement_type       = models.CharField(max_length=20, choices=MoveType.choices, db_index=True)
    qty                 = models.DecimalField(max_digits=12, decimal_places=3, verbose_name="مقدار (+/-)")
    unit_cost_effective = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    happened_at         = models.DateField(db_index=True)
    # پیوند اختیاری به سفارش/محصول برای رهگیری COGS
    order               = models.ForeignKey('core.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_movements')
    product_code        = models.CharField(max_length=60, blank=True, default="")
    reason              = models.CharField(max_length=160, blank=True, default="")
    created_by          = models.CharField(max_length=120, blank=True, default="")
    created_at          = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['item', 'movement_type']),
            models.Index(fields=['order']),
            models.Index(fields=['happened_at']),
        ]
        ordering = ['-happened_at', '-id']
        verbose_name = "حرکت انبار"
        verbose_name_plural = "حرکت‌های انبار"

    def __str__(self):
        return f"{self.movement_type} · {self.item.code} · {self.qty}"

    @transaction.atomic
    def save(self, *args, **kwargs):
        """
        منطق اتمیک ثبت حرکت:
        - تعیین خودکار unit_cost_effective:
            * purchase: از unit_cost لات (اگر ست شده) یا مقدار ورودی
            * issue/waste: میانگین لحظه‌ای آیتم
            * return_in/adjust_*: اگر مقدار ورودی نبود، از میانگین لحظه‌ای
            - جلوگیری از موجودی منفی روی issue/waste/adjust_neg
            - به‌روزرسانی snapshot آیتم: stock_qty و avg_unit_cost
            نکته: ویرایش حرکات گذشته پیچیده است؛ این پیاده‌سازی فقط روی "ایجاد" محاسبه انجام می‌دهد.
           """
        is_create = self.pk is None

        # نرمال‌سازی نوع حرکت و علامت مقدار
        mt = self.movement_type
        qty = _q3(self.qty)

        if mt in ['purchase', 'return_in', 'adjust_pos', 'stocktake']:
            # باید مثبت باشد (stocktake می‌تواند مثبت یا منفی باشد، اما اینجا مثبت می‌گیریم و منفی را در adjust_neg می‌زنیم)
            if qty <= 0 and mt != 'stocktake':
                raise ValidationError("مقدار باید برای این نوع حرکت مثبت باشد.")
        elif mt in ['issue', 'waste', 'adjust_neg']:
            if qty >= 0:
                # برای خروج/ضایعات باید منفی باشد
                qty = _q3(Decimal('-1') * abs(qty))
                self.qty = qty
        else:
            # ناشناخته؟
            raise ValidationError("نوع حرکت نامعتبر است.")

        item = self.item
        prev_qty = _q3(item.stock_qty or Decimal('0'))
        prev_avg = _q2(item.avg_unit_cost or Decimal('0'))

        # تعیین هزینهٔ مؤثر این حرکت
        eff_cost = _q2(self.unit_cost_effective or Decimal('0.00'))
        lot_cost = _q2(self.lot.unit_cost) if self.lot_id else None

        if mt == 'purchase':
            # purchase: هزینهٔ مؤثر = قیمت واحد خرید
            if not lot_cost or lot_cost <= 0:
                # اگر lot.unit_cost نداریم، باید از unit_cost_effective ورودی استفاده شده باشد
                if eff_cost <= 0:
                    raise ValidationError("برای خرید، قیمت واحد معتبر لازم است (lot.unit_cost یا unit_cost_effective).")
                use_cost = eff_cost
            else:
                use_cost = lot_cost
            self.unit_cost_effective = _q2(use_cost)

            # میانگین جدید
            new_qty = _q3(prev_qty + qty)
            new_avg = _q2((prev_qty * prev_avg + abs(qty) * use_cost) / (new_qty if new_qty > 0 else 1))
            # آپدیت اسنپ‌شات آیتم
            item.stock_qty = new_qty
            item.avg_unit_cost = new_avg

        elif mt in ['issue', 'waste', 'adjust_neg']:
            # قبل از خروج، موجودی کافی؟
            if prev_qty + qty < 0:
                raise ValidationError("موجودی کافی نیست؛ این حرکت باعث موجودی منفی می‌شود.")

            use_cost = eff_cost if eff_cost > 0 else prev_avg
            self.unit_cost_effective = _q2(use_cost)

            # خروج روی میانگین اثری ندارد (تا وقتی موجودی > 0 بماند)
            new_qty = _q3(prev_qty + qty)
            new_avg = prev_avg if new_qty > 0 else _q2(Decimal('0.00'))

            item.stock_qty = new_qty
            item.avg_unit_cost = new_avg

        elif mt in ['return_in', 'adjust_pos', 'stocktake']:
            # برگشت/اصلاح افزایشی → اگر قیمت داده نشده، از میانگین فعلی
            use_cost = eff_cost if eff_cost > 0 else (lot_cost if lot_cost and lot_cost > 0 else prev_avg)
            self.unit_cost_effective = _q2(use_cost)

            new_qty = _q3(prev_qty + qty)
            # ورود افزایشی میانگین را تغییر می‌دهد
            new_avg = _q2((prev_qty * prev_avg + abs(qty) * use_cost) / (new_qty if new_qty > 0 else 1))

            item.stock_qty = new_qty
            item.avg_unit_cost = new_avg

        # ابتدا خود حرکت ذخیره شود (اگر ایجاد است)
        super().save(*args, **kwargs)

        # سپس اسنپ‌شات آیتم ذخیره شود
        item.save(update_fields=['stock_qty', 'avg_unit_cost'])

# === Proxy for manual issues in Admin (نمای جدا برای «مصرف دستی» از دل کارتکس) ===
class ManualStockIssue(StockMovement):
    class Meta:
        proxy = True
        verbose_name = "مصرف دستی متریال (کارتکس)"
        verbose_name_plural = "مصرف‌های دستی متریال (کارتکس)"


class BOMRecipe(models.Model):
    """
    نسخه مصرف استاندارد برای هر محصول: به ازای هر «واحد سفارش» چقدر از هر متریال مصرف می‌شود.
    در ثبت مصرف واقعی، این مقادیر پیشنهاد می‌شوند ولی قابل ویرایش‌اند.
    """
    product      = models.ForeignKey('core.Product', on_delete=models.CASCADE, related_name='bom')
    item         = models.ForeignKey('MaterialItem', on_delete=models.PROTECT, related_name='bom_usages')
    qty_per_unit = models.DecimalField(max_digits=12, decimal_places=3, verbose_name="مقدار مصرف به‌ازای یک واحد")
    waste_factor = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="ضریب تلفات (%)")
    is_active    = models.BooleanField(default=True)

    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['product', 'is_active']),
        ]
        unique_together = (('product', 'item'),)
        verbose_name = "BOM مصرف استاندارد"
        verbose_name_plural = "BOM مصرف استاندارد"

    def __str__(self):
        return f"{self.product.code} → {self.item.code} ({self.qty_per_unit})"


class StockIssue(models.Model):
    """
    ثبت «مصرف واقعی» برای هر سفارش؛
    با ذخیره‌ی qty_issued و لینک اختیاری به حرکت‌های کارتکس (برای رهگیری دقیق لات/هزینه).
    """
    order           = models.ForeignKey('core.Order', on_delete=models.CASCADE, related_name='stock_issues')
    item            = models.ForeignKey('MaterialItem', on_delete=models.PROTECT, related_name='issues')
    qty_issued      = models.DecimalField(max_digits=12, decimal_places=3)
    linked_moves    = models.ManyToManyField('StockMovement', related_name='linked_issues', blank=True)
    comment         = models.CharField(max_length=200, blank=True, default="")
    happened_at     = models.DateField(db_index=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['order']),
            models.Index(fields=['item']),
            models.Index(fields=['happened_at']),
        ]
        verbose_name = "مصرف متریال (واقعی)"
        verbose_name_plural = "مصرف‌های متریال (واقعی)"

    def __str__(self):
        return f"Order#{self.order_id} · {self.item.code} · {self.qty_issued}"

# =====================[ Fixed Assets: Equipment & Repairs ]=====================
from django.utils import timezone
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

class Equipment(models.Model):
    class Category(models.TextChoices):
        FURNACE  = 'furnace',  'کوره'
        MIXER    = 'mixer',    'میکسر/وکیوم'
        HANDTOOL = 'handtool', 'ابزار دستی'
        OTHER    = 'other',    'سایر'

    code            = models.SlugField(max_length=60, unique=True, verbose_name="کد تجهیز", help_text="انگلیسی/slug")
    name            = models.CharField(max_length=160, verbose_name="نام تجهیز")
    category        = models.CharField(max_length=40, choices=Category.choices, default='other', verbose_name="دسته")
    model           = models.CharField(max_length=120, blank=True, default="", verbose_name="مدل")
    serial_no       = models.CharField(max_length=120, blank=True, default="", verbose_name="سریال")
    vendor          = models.CharField(max_length=160, blank=True, default="", verbose_name="فروشنده/برند")
    location        = models.CharField(max_length=120, blank=True, default="", verbose_name="محل/بخش")
    is_active       = models.BooleanField(default=True, verbose_name="فعال")

    # داده‌های مالی برای استهلاک
    purchase_date   = models.DateField(null=True, blank=True, verbose_name="تاریخ خرید")
    purchase_cost   = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), verbose_name="قیمت خرید")
    salvage_value   = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), verbose_name="ارزش اسقاط")
    useful_life_m   = models.PositiveIntegerField(null=True, blank=True, verbose_name="عمر مفید (ماه)")
    start_use_date  = models.DateField(null=True, blank=True, verbose_name="شروع بهره‌برداری")

    # ارزش برآوردی فعلی (غیرحسابداری – برای گزارش مدیریت)
    estimated_value = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), verbose_name="ارزش برآوردی فعلی")

    attachment      = models.FileField(upload_to='equipment/', null=True, blank=True, verbose_name="پیوست")
    note            = models.TextField(blank=True, default="", verbose_name="یادداشت")

    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['category', 'is_active']),
        ]
        ordering = ['name', 'code']
        verbose_name = "تجهیز"
        verbose_name_plural = "تجهیزات"

    def __str__(self):
        return f"{self.name} ({self.code})"

    # ===== محاسبات استهلاک (خط مستقیم) =====
    @staticmethod
    def _months_between(d1: date, d2: date) -> int:
        if not d1 or not d2:
            return 0
        if d2 < d1:
            return 0
        return (d2.year - d1.year) * 12 + (d2.month - d1.month) + (1 if d2.day >= d1.day else 0)

    def months_used(self) -> int:
        start = self.start_use_date or self.purchase_date
        today = timezone.localdate()
        return self._months_between(start, today) if start else 0

    def monthly_depreciation(self) -> Decimal:
        cost = self.purchase_cost or Decimal('0.00')
        salvage = self.salvage_value or Decimal('0.00')
        life = self.useful_life_m or 0
        if life <= 0 or cost <= salvage:
            return Decimal('0.00')
        per = (cost - salvage) / Decimal(life)
        return per.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def accumulated_depreciation(self) -> Decimal:
        used = min(self.months_used(), self.useful_life_m or 0)
        acc = self.monthly_depreciation() * Decimal(used)
        return acc.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def book_value(self) -> Decimal:
        # ارزش دفتری = قیمت خرید - استهلاک انباشته (نه کمتر از ارزش اسقاط)
        cost = self.purchase_cost or Decimal('0.00')
        salvage = self.salvage_value or Decimal('0.00')
        bv = cost - self.accumulated_depreciation()
        if bv < salvage:
            bv = salvage
        return bv.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class Repair(models.Model):
    class PayMethod(models.TextChoices):
        CASH     = 'cash',     'نقد'
        CARD     = 'card',     'کارت'
        TRANSFER = 'transfer', 'حواله/کارت‌به‌کارت'
        OTHER    = 'other',    'سایر'

    equipment       = models.ForeignKey('Equipment', on_delete=models.CASCADE, related_name='repairs', verbose_name="تجهیز")
    title           = models.CharField(max_length=160, verbose_name="عنوان خرابی/سرویس")
    vendor          = models.CharField(max_length=160, blank=True, default="", verbose_name="تکنسین/شرکت")
    amount          = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="مبلغ")

    occurred_date   = models.DateField(verbose_name="تاریخ وقوع/انجام")
    paid_date       = models.DateField(null=True, blank=True, verbose_name="تاریخ پرداخت")
    payment_method  = models.CharField(max_length=16, choices=PayMethod.choices, blank=True, default='', verbose_name="روش پرداخت")

    attachment      = models.FileField(upload_to='repairs/', null=True, blank=True, verbose_name="پیوست")
    note            = models.TextField(blank=True, default="", verbose_name="یادداشت")

    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['equipment']),
            models.Index(fields=['occurred_date']),
            models.Index(fields=['paid_date']),
        ]
        ordering = ['-occurred_date', '-id']
        verbose_name = "تعمیر تجهیز"
        verbose_name_plural = "تعمیرات تجهیزات"

    def __str__(self):
        return f"تعمیر {self.equipment.code} • {self.title} • {self.amount}"

    def save(self, *args, **kwargs):
        if not self.paid_date:
            self.paid_date = self.occurred_date
        super().save(*args, **kwargs)

# ===== Keep MaterialItem snapshot always consistent with cardex =====
@receiver(post_delete, sender=StockMovement)
def _recompute_item_snapshot_after_delete(sender, instance, **kwargs):
    """
    هر حرکت کارتکس که حذف شد (حتی با bulk delete)،
    اسنپ‌شات آیتم مربوطه را از روی کل کارتکس بازسازی کن.
    """
    try:
        instance.item.recompute_snapshot()
    except Exception:
        pass


@receiver(post_save, sender=StockMovement)
def _recompute_item_snapshot_after_save(sender, instance, **kwargs):
    """
    هر حرکت که ذخیره/ویرایش شد، برای اطمینان از هم‌خوانی،
    اسنپ‌شات آیتم دوباره از روی کارتکس بازسازی شود.
    (کمی هزینه‌ی محاسبه دارد، اما خطای موجودی منفیِ کاذب را برای همیشه می‌بندد.)
    """
    try:
        instance.item.recompute_snapshot()
    except Exception:
        pass


# =====================[ Digital Lab Charges ]=====================
class DigitalLabCharge(models.Model):
    """
    ثبت هزینه‌های خدمات لابراتوار دیجیتال (اسکن، طراحی، پرینت، میلینگ و ...)
    مرتبط با هر سفارش.
    """
    class ServiceType(models.TextChoices):
        SCAN     = 'scan',     'اسکن'
        DESIGN   = 'design',   'طراحی دیجیتال'
        PRINT    = 'print',    'پرینت سه‌بعدی'
        MILLING  = 'milling',  'میلینگ'
        PACKAGE  = 'package',  'پکج کامل'
        OTHER    = 'other',    'سایر خدمات'

    order       = models.ForeignKey('core.Order', on_delete=models.CASCADE, related_name='digital_charges', verbose_name="سفارش")
    vendor      = models.CharField(max_length=160, blank=True, default="", verbose_name="نام لابراتوار دیجیتال / فروشنده")
    service     = models.CharField(max_length=40, choices=ServiceType.choices, default='other', verbose_name="نوع خدمت")
    amount      = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="مبلغ (تومان)")
    payment_date = models.DateField(null=True, blank=True, verbose_name="تاریخ پرداخت")
    attachment  = models.FileField(upload_to='digital_lab/', null=True, blank=True, verbose_name="پیوست فاکتور/رسید")
    note        = models.CharField(max_length=200, blank=True, default="", verbose_name="توضیح")

    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['order']),
            models.Index(fields=['service']),
            models.Index(fields=['payment_date']),
        ]
        ordering = ['-payment_date', '-id']
        verbose_name = "هزینه لابراتوار دیجیتال"
        verbose_name_plural = "هزینه‌های لابراتوار دیجیتال"

    def __str__(self):
        return f"{self.order_id} • {self.get_service_display()} • {self.amount:,.0f} تومان"
