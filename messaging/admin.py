from django.contrib import admin
from .models import Conversation

@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('id', 'listing', 'buyer', 'updated_at')
    search_fields = ('book_title', 'buyer__email')
