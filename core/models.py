from django.db import models
from django.utils import timezone

# ---------- Patient ----------
class Patient(models.Model):
    first_name = models.CharField(max_length=100)
    last_name  = models.CharField(max_length=100, blank=True)
    phone      = models.CharField(max_length=30, blank=True)
    email      = models.EmailField(blank=True)
    address    = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()

# ---------- Material ----------
class Material(models.Model):
    name     = models.CharField(max_length=200)
    quantity = models.FloatField(default=0)
    unit     = models.CharField(max_length=20, default='pcs')

    def __str__(self):
        return self.name

# ---------- Order ----------
class Order(models.Model):
    STATUS_CHOICES = [
        ('received','Received'),
        ('in_progress','In Progress'),
        ('ready','Ready'),
        ('delivered','Delivered'),
        ('cancelled','Cancelled'),
    ]
    patient    = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='orders')
    doctor     = models.CharField(max_length=200, blank=True)
    order_type = models.CharField(max_length=150, blank=True)
    shade      = models.CharField(max_length=50, blank=True)
    price      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received')
    created_at = models.DateTimeField(auto_now_add=True)
    due_date   = models.DateField(null=True, blank=True)
    notes      = models.TextField(blank=True)

    def __str__(self):
        return f"Order #{self.id} - {self.patient}"

# ---------- Payment ----------
class Payment(models.Model):
    order  = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date   = models.DateTimeField(default=timezone.now)
    method = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return f"{self.amount} - {self.order}"




