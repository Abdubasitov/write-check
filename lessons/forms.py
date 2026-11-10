from django import forms

from .models import NAME_MAX, NAME_MIN

NAME_LENGTH_ERROR = f"Фамилия и имя — от {NAME_MIN} до {NAME_MAX} символов"


class TeacherPasswordForm(forms.Form):
    password = forms.CharField(
        label="Пароль агая",
        strip=False,
        widget=forms.PasswordInput(attrs={"autofocus": True, "autocomplete": "current-password"}),
    )


class JoinForm(forms.Form):
    name = forms.CharField(
        label="Фамилия и имя",
        min_length=NAME_MIN,
        max_length=NAME_MAX,
        error_messages={
            "required": "Введите фамилию и имя",
            "min_length": NAME_LENGTH_ERROR,
            "max_length": NAME_LENGTH_ERROR,
        },
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Иванов Иван"}),
    )
    code = forms.CharField(
        label="Код урока",
        max_length=6,
        error_messages={"required": "Введите код урока"},
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off"}),
    )

    def clean_name(self):
        # CharField уже обрезал пробелы по краям; схлопываем внутренние повторы
        return " ".join(self.cleaned_data["name"].split())

    def clean_code(self):
        return self.cleaned_data["code"].strip()
