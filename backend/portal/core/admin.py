from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from portal.core.models import AppRole, Membership, Organization, Setting, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "name", "is_active", "is_staff", "timezone")
    search_fields = ("email", "name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("name", "timezone")}),
        ("Access", {"fields": ("is_active", "is_staff", "is_superuser", "groups")}),
    )
    add_fieldsets = ((None, {"fields": ("email", "name", "password1", "password2")}),)


admin.site.register(Organization)
admin.site.register(Membership)
admin.site.register(AppRole)
admin.site.register(Setting)
