from django.shortcuts import render, redirect
from .forms import OrderForm, MaterialForm, PaymentForm
from .models import Order, Material, Payment

def home(request):
    order_form    = OrderForm(request.POST or None, prefix='order')
    material_form = MaterialForm(request.POST or None, prefix='material')
    payment_form  = PaymentForm(request.POST or None, prefix='payment')

    if request.method == "POST":
        form_saved = False
        if order_form.is_valid():
            order_form.save()
            form_saved = True
        elif material_form.is_valid():
            material_form.save()
            form_saved = True
        elif payment_form.is_valid():
            payment_form.save()
            form_saved = True

        if form_saved:
            return redirect('home')

    orders    = Order.objects.select_related('patient').all().order_by('-created_at')
    materials = Material.objects.all().order_by('name')
    payments  = Payment.objects.select_related('order', 'order__patient').all().order_by('-payment_date')

    context = {
        'order_form': order_form,
        'material_form': material_form,
        'payment_form': payment_form,
        'orders': orders,
        'materials': materials,
        'payments': payments,
    }

    return render(request, 'core/home.html', context)














