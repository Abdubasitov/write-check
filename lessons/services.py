"""Логика комнат. Consumer и views только вызывают эти функции."""
import secrets
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import Mark, Room, Student

ROOM_TTL = timedelta(hours=12)  # старше — комнаты как будто нет
ONLINE_WINDOW = timedelta(seconds=40)  # «в сети» = был ping за последние 40 с (ping раз в 15 с)
TIMER_MIN, TIMER_MAX = 1, 60 * 60
AUTO_NEXT_MIN, AUTO_NEXT_MAX = 1, 60  # задержка авто-раунда, с


class NameTaken(Exception):
    pass


def live_rooms():
    """Комнаты, которые ещё работают: не завершены и моложе 12 часов."""
    return Room.objects.exclude(status=Room.Status.FINISHED).filter(created_at__gt=timezone.now() - ROOM_TTL)


def cleanup_old_rooms():
    """Удаляет комнаты старше 12 часов (вместе со студентами и отметками)."""
    deleted, _ = Room.objects.filter(created_at__lte=timezone.now() - ROOM_TTL).delete()
    return deleted


def new_token():
    return secrets.token_urlsafe(32)


def _random_code(length):
    first = str(secrets.randbelow(9) + 1)  # без ведущего нуля — удобнее диктовать
    rest = "".join(str(secrets.randbelow(10)) for _ in range(length - 1))
    return first + rest


def create_room():
    """Создаёт комнату с уникальным кодом из 4–6 цифр."""
    cleanup_old_rooms()  # ленивая уборка заодно освобождает коды
    for length in (4,) * 20 + (5,) * 20 + (6,) * 20:
        code = _random_code(length)
        if Room.objects.filter(code=code).exists():
            continue
        try:
            with transaction.atomic():
                return Room.objects.create(code=code, teacher_token=new_token())
        except IntegrityError:
            continue
    raise RuntimeError("Не удалось подобрать свободный код комнаты")


def open_room(code):
    """Комната, в которую ещё можно войти, или None.

    Урок начинается сразу: комнаты, созданные до этого в статусе waiting, становятся active.
    """
    if not code or not code.isdigit():
        return None
    room = live_rooms().filter(code=code).first()
    if room is not None and room.status == Room.Status.WAITING:
        start(room)
    return room


def join_room(room, name, token):
    """Возвращает (student, reconnected). Тот же токен — тот же студент."""
    existing = Student.objects.filter(room=room, token=token).first()
    if existing:
        return existing, True
    # SQLite lower()/LIKE не понимают кириллицу — сравниваем в Python
    key = name.strip().casefold()
    if any(n.strip().casefold() == key for n in room.students.values_list("name", flat=True)):
        raise NameTaken
    try:
        with transaction.atomic():
            return Student.objects.create(room=room, name=name, token=token), False
    except IntegrityError:
        raise NameTaken


# --- живой урок -------------------------------------------------------------


# Порядок групп в списке агая: с вопросом — в самом верху, кого ждём — дальше, записавшие — внизу.
GROUP_ORDER = {"question": 0, "wait": 1, "offline": 2, "away": 3, "done": 4}
QUESTION_TYPES = {"question", "bug"}


def student_group(online, marked, away, question=None):
    if away:
        return "away"
    if question:
        return "question"
    if marked:
        return "done"
    return "wait" if online else "offline"


def sort_students(students):
    """Сначала по группе (GROUP_ORDER); вопросы — по очереди (кто раньше спросил — выше),
    остальные — по алфавиту без учёта регистра."""
    def key(s):
        asked = s.get("question_at") or 0 if s["group"] == "question" else 0
        return (GROUP_ORDER[s["group"]], asked, s["name"].casefold(), s["id"])
    return sorted(students, key=key)


def room_state(room):
    """Полное состояние комнаты для агая: список (уже отсортирован), отметки, счётчик.

    Вышедшие («Я вышел») не входят ни в marked, ни в total.
    """
    room.refresh_from_db()
    students = list(room.students.all())
    fresh_after = timezone.now() - ONLINE_WINDOW
    mark_times = dict(
        Mark.objects.filter(student__room=room, round=room.round).values_list("student_id", "created_at")
    )
    rows = []
    for s in students:
        away = s.away_since is not None
        marked = s.id in mark_times and not away
        online = is_online(s, fresh_after)
        after = None
        if marked and room.round_started_at:
            after = max(0, int((mark_times[s.id] - room.round_started_at).total_seconds()))
        question = s.question_type if not away else None
        rows.append({
            "id": s.id,
            "name": s.name,
            "online": online,
            "marked": marked,
            "away": away,
            "away_since": s.away_since.timestamp() if away else None,
            "marked_after": after,  # «Когда»: секунд от начала раунда до отметки
            "question": question,  # None | "question" | "bug"
            "question_at": s.question_at.timestamp() if question and s.question_at else None,
            "group": student_group(online, marked, away, question),
        })
    present = [r for r in rows if not r["away"]]
    ordered = sort_students(rows)
    queue = [r for r in ordered if r["group"] == "question"]
    for pos, r in enumerate(queue, start=1):
        r["queue_pos"] = pos
    return {
        "code": room.code,
        "status": room.status,
        "round": room.round,
        "timer_seconds": room.timer_seconds,
        "auto_next_enabled": room.auto_next_enabled,
        "auto_next_seconds": room.auto_next_seconds,
        "students": ordered,
        "marked": sum(1 for r in present if r["marked"]),
        "total": len(present),
        "away": len(rows) - len(present),
        "questions": len(queue),
    }


def student_view(state, student_id):
    """Что видит конкретный студент из общего состояния."""
    me = next((s for s in state["students"] if s["id"] == student_id), None)
    return {
        "status": state["status"],
        "round": state["round"],
        "marked": bool(me and me["marked"]),
        "away": bool(me and me["away"]),
        # только свой вопрос и место в очереди — чужих имён студенту не шлём
        "question": me["question"] if me else None,
        "queue_pos": me.get("queue_pos") if me else None,
        "name": me["name"] if me else "",
        "deadline": state.get("deadline"),
        "auto_next_at": state.get("auto_next_at"),
        "now": state.get("now"),
    }


def start(room):
    """Совместимость: раньше урок начинали кнопкой. Теперь комната сразу active."""
    if room.status == Room.Status.WAITING:
        room.status = Room.Status.ACTIVE
        room.save(update_fields=["status"])


def mark(student):
    room = Room.objects.get(pk=student.room_id)
    if room.status != Room.Status.ACTIVE:
        return False
    if Student.objects.filter(pk=student.pk, away_since__isnull=False).exists():
        return False  # вышел — сначала «Я вернулся»
    try:
        with transaction.atomic():
            Mark.objects.get_or_create(student=student, round=room.round)
    except IntegrityError:
        pass  # двойной клик — отметка уже есть
    return True


def unmark(student):
    room = Room.objects.get(pk=student.room_id)
    Mark.objects.filter(student=student, round=room.round).delete()


def set_away(student, away):
    """«Я вышел» / «Я вернулся». Отметка текущего раунда снимается: вернулся — снова «Ждём».
    Сброс раунда «вышел» не снимает."""
    if away:
        Student.objects.filter(pk=student.pk, away_since__isnull=True).update(away_since=timezone.now())
        resolve(student)  # вышел — вопрос снимается
    else:
        Student.objects.filter(pk=student.pk).update(away_since=None)
    unmark(student)


def ask(student, question_type):
    """«✋ Вопрос»: тип question/bug. Повторный — меняет тип, место в очереди сохраняется.
    False — неверный тип или студент «вышел»."""
    if question_type not in QUESTION_TYPES:
        return False
    current = Student.objects.filter(pk=student.pk, away_since__isnull=True).first()
    if current is None:
        return False
    current.question_type = question_type
    current.question_at = current.question_at or timezone.now()
    current.save(update_fields=["question_type", "question_at"])
    return True


def resolve(student):
    """«Разобрался» (студент) — вопрос снят."""
    Student.objects.filter(pk=student.pk).update(question_type=None, question_at=None)


def answer(room, student_id):
    """«Ответил» (агай): снимает вопрос студента своей комнаты. True — был открытый вопрос."""
    return Student.objects.filter(room=room, pk=student_id, question_type__isnull=False).update(
        question_type=None, question_at=None
    ) > 0


def reset(room, expected_round=None):
    """Новый раунд: round + 1, отметки обнуляются, таймер выключается (он только на один раунд).

    expected_round — раунд, который заканчиваем. Если он уже сменился (сработал таймер или
    авто-новый раунд), ничего не делаем и возвращаем None — так не бывает двойного сброса.
    Условный UPDATE атомарен и в SQLite, где select_for_update ничего не блокирует.
    """
    if expected_round is None:
        expected_round = Room.objects.values_list("round", flat=True).get(pk=room.pk)
    with transaction.atomic():
        updated = Room.objects.filter(pk=room.pk, round=expected_round).update(
            round=F("round") + 1, timer_seconds=None, round_started_at=timezone.now()
        )
        if not updated:
            return None
        # историю раундов не храним; отметки нового раунда (если уже успели) не трогаем
        Mark.objects.filter(student__room_id=room.pk, round__lte=expected_round).delete()
    return Room.objects.get(pk=room.pk)


def timer_reset(code, expected_round):
    """Автосброс по таймеру: только если комната жива, идёт урок и раунд не сменился."""
    room = live_rooms().filter(code=code, status=Room.Status.ACTIVE, round=expected_round).first()
    return reset(room, expected_round) if room else None


def all_marked(room):
    """Записали все ученики комнаты, кроме вышедших (как в счётчике: marked == total > 0)."""
    present = room.students.filter(away_since__isnull=True)
    total = present.count()
    marked = Mark.objects.filter(student__in=present, round=room.round).count()
    return total > 0 and marked == total


def auto_next(code, expected_round):
    """Авто-новый раунд: только если урок идёт, он включён, раунд тот же и по-прежнему записали все."""
    room = live_rooms().filter(code=code, status=Room.Status.ACTIVE, round=expected_round).first()
    if room is None or not room.auto_next_enabled or not all_marked(room):
        return None
    return reset(room, expected_round)


def parse_timer(value):
    """None/0/'' → без таймера; иначе целые секунды в допустимом диапазоне. ValueError — мусор."""
    if value in (None, "", 0, "0"):
        return None
    seconds = int(value)
    if not TIMER_MIN <= seconds <= TIMER_MAX:
        raise ValueError(seconds)
    return seconds


def set_timer(room, seconds):
    room.timer_seconds = seconds
    room.save(update_fields=["timer_seconds"])


def parse_auto_next(enabled, seconds):
    """Настройка авто-раунда от агая: enabled — bool, seconds — целое 1–60 (или None — не менять)."""
    if not isinstance(enabled, bool):
        raise ValueError(enabled)
    if seconds is None:
        return enabled, None
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float, str)):
        raise ValueError(seconds)
    value = float(seconds)
    if value != int(value) or not AUTO_NEXT_MIN <= value <= AUTO_NEXT_MAX:
        raise ValueError(seconds)
    return enabled, int(value)


def set_auto_next(room, enabled, seconds=None):
    room.auto_next_enabled = enabled
    fields = ["auto_next_enabled"]
    if seconds is not None:
        room.auto_next_seconds = seconds
        fields.append("auto_next_seconds")
    room.save(update_fields=fields)


def kick(room, student_id):
    """Удаляет студента из комнаты (имя освобождается). True, если удалён."""
    deleted, _ = Student.objects.filter(room=room, pk=student_id).delete()
    return deleted > 0


def finish(room):
    room.status = Room.Status.FINISHED
    room.save(update_fields=["status"])


def is_online(student, fresh_after=None):
    """В сети = открыт сокет И недавно был ping. Одно без другого не считается."""
    fresh_after = fresh_after or timezone.now() - ONLINE_WINDOW
    return bool(student.is_online and student.last_seen and student.last_seen >= fresh_after)


def set_online(student_id, online):
    """online=True — сокет открыт (и это заодно «видели сейчас»); False — сокетов не осталось."""
    fields = {"is_online": online}
    if online:
        fields["last_seen"] = timezone.now()
    Student.objects.filter(pk=student_id).update(**fields)


def touch(student_id):
    """Ping/действие студента. True, если до этого он считался не в сети (нужно разослать state)."""
    student = Student.objects.filter(pk=student_id).first()
    if student is None:
        return False
    was_online = is_online(student)
    Student.objects.filter(pk=student_id).update(is_online=True, last_seen=timezone.now())
    return not was_online


def online_ids(code):
    """Кто сейчас в сети в комнате (для фоновой проверки «протухших»). None — комнаты нет."""
    room = open_room(code)
    if room is None:
        return None
    fresh_after = timezone.now() - ONLINE_WINDOW
    return frozenset(s.id for s in room.students.all() if is_online(s, fresh_after))
