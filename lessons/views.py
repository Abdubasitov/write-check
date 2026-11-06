import hmac
import time
from urllib.parse import quote, unquote

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from . import services, timers
from .forms import JoinForm, TeacherPasswordForm
from .models import Student

TIMER_PRESETS = [(60, "1 мин"), (120, "2"), (180, "3"), (300, "5")]

STUDENT_TOKEN_COOKIE = "zap_student"
STUDENT_NAME_COOKIE = "zap_name"
STUDENT_COOKIE_AGE = 60 * 60 * 24 * 30

FAILS_KEY = "teacher_fails"
ROOM_KEY = "teacher_room"
TOKEN_KEY = "teacher_token"


def _same(a, b):
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def _teacher_room(request):
    """Живая комната агая из сессии, или None."""
    code = request.session.get(ROOM_KEY)
    token = request.session.get(TOKEN_KEY)
    if not code or not token:
        return None
    room = services.open_room(code)
    if room and _same(room.teacher_token, token):
        return room
    return None


def _recent_fails(request):
    now = time.time()
    fails = [t for t in request.session.get(FAILS_KEY, []) if now - t < settings.TEACHER_FAIL_WINDOW]
    request.session[FAILS_KEY] = fails
    return fails


def home(request):
    return render(request, "lessons/home.html")


@require_http_methods(["GET", "POST"])
def teacher_new(request):
    room = _teacher_room(request)
    if room:  # сессия жива и урок не завершён — пароль повторно не спрашиваем
        return redirect("teacher", code=room.code)

    configured = bool(settings.TEACHER_PASSWORD)
    form = TeacherPasswordForm(request.POST or None)
    blocked = len(_recent_fails(request)) >= settings.TEACHER_MAX_FAILS
    status = 429 if blocked else 200

    if request.method == "POST" and configured and not blocked and form.is_valid():
        if _same(form.cleaned_data["password"], settings.TEACHER_PASSWORD):
            request.session.pop(FAILS_KEY, None)
            room = services.create_room()
            request.session.cycle_key()
            request.session[ROOM_KEY] = room.code
            request.session[TOKEN_KEY] = room.teacher_token
            return redirect("teacher", code=room.code)
        fails = _recent_fails(request) + [time.time()]
        request.session[FAILS_KEY] = fails
        if len(fails) >= settings.TEACHER_MAX_FAILS:
            blocked, status = True, 429
        else:
            form.add_error("password", "Неверный пароль")
            status = 403

    return render(
        request,
        "lessons/teacher_login.html",
        {"form": form, "blocked": blocked, "configured": configured},
        status=status,
    )


def teacher(request, code):
    room = _teacher_room(request)
    if not room or room.code != code:
        return redirect("teacher_new")
    return render(
        request,
        "lessons/teacher.html",
        {"room": room, "state": timers.state_for(room), "timer_presets": TIMER_PRESETS},
    )


@require_http_methods(["GET", "POST"])
def join(request):
    token = request.COOKIES.get(STUDENT_TOKEN_COOKIE) or services.new_token()

    if request.method == "POST":
        form = JoinForm(request.POST)
        if form.is_valid():
            room = services.open_room(form.cleaned_data["code"])
            if room is None:
                form.add_error("code", "Урок не найден")
            else:
                try:
                    student, _ = services.join_room(room, form.cleaned_data["name"], token)
                except services.NameTaken:
                    form.add_error("name", "Имя занято, добавьте фамилию")
                else:
                    response = redirect("room", code=room.code)
                    _remember_student(response, token, student.name)
                    return response
    else:
        form = JoinForm(
            initial={
                "name": unquote(request.COOKIES.get(STUDENT_NAME_COOKIE, "")),
                "code": request.GET.get("code", "")[:6],
            }
        )

    response = render(request, "lessons/join.html", {"form": form})
    if STUDENT_TOKEN_COOKIE not in request.COOKIES:
        _set_cookie(response, STUDENT_TOKEN_COOKIE, token, httponly=True)
    return response


def room(request, code):
    room = services.open_room(code)
    if room is None:
        messages.error(request, "Урок не найден")
        return redirect("join")
    token = request.COOKIES.get(STUDENT_TOKEN_COOKIE)
    student = Student.objects.filter(room=room, token=token).first() if token else None
    if student is None:
        return redirect(f"{reverse('join')}?code={room.code}")
    state = services.student_view(timers.state_for(room), student.id)
    return render(request, "lessons/student.html", {"room": room, "student": student, "state": state})


def _set_cookie(response, key, value, httponly):
    response.set_cookie(
        key,
        value,
        max_age=STUDENT_COOKIE_AGE,
        httponly=httponly,
        samesite="Lax",
        secure=not settings.DEBUG,
    )


def _remember_student(response, token, name):
    _set_cookie(response, STUDENT_TOKEN_COOKIE, token, httponly=True)
    _set_cookie(response, STUDENT_NAME_COOKIE, quote(name), httponly=False)
