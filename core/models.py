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
    created_at    = models.DateTimeField(auto_now_add=True)

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















