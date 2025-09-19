from django.shortcuts import render, redirect
from .forms import OrderForm
from .models import Order

def home(request):
    order_form = OrderForm(request.POST or None, prefix='order')

    if request.method == "POST":
        if order_form.is_valid():
            order_form.save()
            return redirect('home')

    orders = Order.objects.all().order_by('-created_at')

    context = {
        'order_form': order_form,
        'orders': orders,
    }

    return render(request, 'core/home.html', context)

















