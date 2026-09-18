from django.contrib import admin
from .models import Listing

@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ('book', 'seller', 'school', 'price', 'condition', 'status', 'created_at')
    search_fields = ('book__title', 'seller__email', 'school__name', 'school__code')
    list_filter = ('status', 'condition', 'school')
