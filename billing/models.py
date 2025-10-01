from decimal import Decimal
from django.db import models
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from django.db.models.functions import Coalesce


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

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['doctor', 'date']),
        ]
        ordering = ['-date', '-id']

    def __str__(self):
        return f'Payment {self.amount} for {self.doctor} on {self.date}'


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

