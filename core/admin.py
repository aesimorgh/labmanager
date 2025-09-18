# core/admin.py
from django.contrib import admin
from jalali_date.admin import ModelAdminJalaliMixin
from .models import Patient, Order, Material, Payment
from .forms import PatientForm, OrderForm, MaterialForm, PaymentForm


# -----------------------------
# Patient Admin
# -----------------------------
@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    form = PatientForm
    list_display = ['name', 'phone', 'email', 'created_at']
    search_fields = ['name', 'phone', 'email']


# -----------------------------
# Order Admin
# -----------------------------
@admin.register(Order)
class OrderAdmin(ModelAdminJalaliMixin, admin.ModelAdmin):
    form = OrderForm

    # 🆕 متد برای نمایش نام بیمار
    def patient_name_display(self, obj):
        return obj.patient.name if obj.patient else getattr(obj, 'patient_name', '-')
    patient_name_display.short_description = 'نام بیمار'

    # 🆕 متد برای نمایش قیمت کل (جمع واحد × قیمت)
    def total_price_display(self, obj):
        try:
            return obj.unit_count * obj.price
        except Exception:
            return None
    total_price_display.short_description = 'قیمت کل (تومان)'

    list_display = [
        'id', 'patient_name_display', 'doctor', 'order_type', 'unit_count',
        'serial_number', 'price', 'total_price_display', 'shade',
        'status', 'due_date', 'created_at'
    ]

    list_filter = ['status', 'due_date']

    search_fields = [
        'doctor', 'order_type', 'shade', 'serial_number'
    ]

    # فقط نمایش، قابل ویرایش نیست
    readonly_fields = ['total_price_display']


# -----------------------------
# Material Admin
# -----------------------------
@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    form = MaterialForm
    list_display = ['name', 'quantity', 'unit']
    search_fields = ['name']


# -----------------------------
# Payment Admin
# -----------------------------
@admin.register(Payment)
class PaymentAdmin(ModelAdminJalaliMixin, admin.ModelAdmin):
    form = PaymentForm
    list_display = ['order', 'amount', 'date', 'method']
    list_filter = ['date', 'method']
    search_fields = ['method']






















































