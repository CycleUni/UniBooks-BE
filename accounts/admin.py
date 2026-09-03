from django.contrib import admin
from django import forms
from django.utils import timezone
from django.contrib.auth.forms import UserChangeForm
from .models import User, School, RegionVerification

class UserAdminForm(UserChangeForm):
    class Meta:
        model = User
        fields = '__all__'

from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

class RegionVerificationInline(admin.TabularInline):
    model = RegionVerification
    extra = 0

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserAdminForm
    list_display = ('email', 'last_name', 'first_name', 'is_active', 'is_staff')
    search_fields = ('email', 'first_name', 'last_name')
    list_filter = ('is_active', 'is_staff')
    ordering = ('email',)
    inlines = [RegionVerificationInline]

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'managed_regions')}),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Important dates', {'fields': ('last_login',)}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password'),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        if not request.user.is_superuser:
            if obj and obj.is_superuser:
                return ['is_active', 'is_staff', 'is_superuser']
            if obj and obj.is_staff and request.user != obj:
                return ['is_active']
        return super().get_readonly_fields(request, obj)

    def delete_queryset(self, request, queryset):
        # ModelAdmin's default here is queryset.delete() — Django's bulk
        # delete, which issues raw SQL and never calls an instance's
        # overridden delete(). User.delete() anonymizes instead of removing
        # the row precisely because Order/Review CASCADE from both buyer and
        # seller; the bulk path would silently go around that and do the
        # real CASCADE delete the override exists to prevent. The admin's
        # single-object "Delete" button already calls obj.delete() and does
        # not need this — only "Delete selected" does.
        for obj in queryset:
            obj.delete()

@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ('name', 'email_domain', 'region')
    search_fields = ('name', 'email_domain')
    list_filter = ('region',)
