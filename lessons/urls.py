from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("teacher/new/", views.teacher_new, name="teacher_new"),
    path("teacher/<str:code>/", views.teacher, name="teacher"),
    path("join/", views.join, name="join"),
    path("room/<str:code>/", views.room, name="room"),
]
