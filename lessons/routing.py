from django.urls import path

from .consumers import RoomConsumer

# Роль задаёт страница: панель агая и страница студента подключаются к разным путям.
# Так один браузер может держать обе роли в разных вкладках.
websocket_urlpatterns = [
    path("ws/rooms/<str:code>/teacher/", RoomConsumer.as_asgi(), {"role": "teacher"}),
    path("ws/rooms/<str:code>/student/", RoomConsumer.as_asgi(), {"role": "student"}),
]
