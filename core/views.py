from django.shortcuts import render, redirect
from .forms import PatientForm, OrderForm
from .models import Order

def home(request):
    """
    صفحه اصلی: ثبت بیمار + سفارش جدید و نمایش لیست سفارش‌ها
    """
    if request.method == 'POST':
        patient_form = PatientForm(request.POST)
        order_form = OrderForm(request.POST)
        if patient_form.is_valid() and order_form.is_valid():
            patient = patient_form.save()
            order = order_form.save(commit=False)
            order.patient = patient
            order.save()
            return redirect('home')
    else:
        patient_form = PatientForm()
        order_form = OrderForm()

    orders = Order.objects.select_related('patient').all().order_by('-created_at')

    return render(
        request,
        'core/home.html',   # مسیر درست قالب
        {
            'patient_form': patient_form,
            'order_form': order_form,
            'orders': orders
        }
    )




