from django.contrib import admin
from .models import Order

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'buyer', 'seller', 'listing', 'status', 'total_amount', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('buyer__email', 'seller__email', 'book_title')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'created_at'
