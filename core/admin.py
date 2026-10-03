from django.contrib import admin
from .models import AuditEvent, City

@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ('kind', 'user', 'created_at')
    search_fields = ('kind', 'user__email')
    list_filter = ('kind', 'created_at')


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'region', 'sort_order')
    search_fields = ('name', 'code')
    list_filter = ('region',)
