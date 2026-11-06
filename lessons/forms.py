from django import forms


class TeacherPasswordForm(forms.Form):
    password = forms.CharField(
        label="Пароль агая",
        strip=False,
        widget=forms.PasswordInput(attrs={"autofocus": True, "autocomplete": "current-password"}),
    )


class JoinForm(forms.Form):
    name = forms.CharField(
        label="Имя",
        min_length=2,
        max_length=30,
        error_messages={
            "required": "Введите имя",
            "min_length": "Имя — от 2 до 30 символов",
            "max_length": "Имя — от 2 до 30 символов",
        },
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
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
