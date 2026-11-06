from django.contrib import admin

from .models import Mark, Room, Student


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("code", "status", "round", "created_at")
    list_filter = ("status",)
    exclude = ("teacher_token",)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("name", "room", "is_online", "joined_at")
    exclude = ("token",)


@admin.register(Mark)
class MarkAdmin(admin.ModelAdmin):
    list_display = ("student", "round", "created_at")
