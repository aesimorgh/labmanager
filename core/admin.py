from django.contrib import admin
from django_jalali.admin.filters import JDateFieldListFilter  # برای فیلتر تاریخ شمسی
from .models import Patient, Material, Order, Payment

# ---------- Patient ----------
@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'phone', 'email', 'created_at')
    search_fields = ('first_name', 'last_name', 'phone', 'email')
    list_filter = (
        ('created_at', JDateFieldListFilter),  # نمایش تاریخ شمسی در فیلتر
    )

# ---------- Material ----------
@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('name', 'quantity', 'unit')
    search_fields = ('name',)

# ---------- Order ----------
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('patient', 'doctor', 'order_type', 'price', 'status', 'created_at', 'due_date')
    list_filter  = ('status', 'doctor', ('created_at', JDateFieldListFilter), ('due_date', JDateFieldListFilter))
    search_fields= ('patient__first_name', 'patient__last_name', 'doctor')

# ---------- Payment ----------
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('order', 'amount', 'date', 'method')
    list_filter  = (('date', JDateFieldListFilter), 'method')
    search_fields = ('order__patient__first_name', 'order__patient__last_name')


