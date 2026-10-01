from django.contrib import admin
from .models import Report, ChatReport

@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ('reporter', 'listing', 'status', 'created_at')
    search_fields = ('reporter__email', 'listing_ref', 'book_title', 'reason')
    list_filter = ('status', 'created_at')


@admin.register(ChatReport)
class ChatReportAdmin(admin.ModelAdmin):
    list_display = ('id', 'reporter', 'conversation', 'reported_party', 'reason', 'status', 'created_at')
    search_fields = ('reporter__email', 'reported_party__email', 'conversation__id')
    list_filter = ('status', 'reason', 'created_at')
