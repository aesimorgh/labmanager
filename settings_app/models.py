from django.db import models


class LabProfile(models.Model):
    lab_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    logo = models.ImageField(upload_to="lab_logo/", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Lab Profile"
        verbose_name_plural = "Lab Profiles"

    def __str__(self):
        return self.lab_name or "Lab Profile"

