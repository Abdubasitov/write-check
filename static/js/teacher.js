// Панель агая: счётчик, список, кнопки.
(function () {
  "use strict";

  var state = JSON.parse(document.getElementById("initial-state").textContent);
  var $ = function (id) { return document.getElementById(id); };
  // урок начинается сразу; waiting — только у старых комнат, сервер переводит их в active
  var STATUS_TEXT = { waiting: "Урок идёт", active: "Урок идёт", finished: "Урок завершён" };

  // Сервер присылает учеников уже отсортированными: группа (вопрос → ждём → вкладка закрыта →
  // вышел → записал), внутри — по алфавиту. Здесь только отрисовка.
  var STATUS = {
    wait: { icon: "hourglass", text: "Ждём" },
    offline: { icon: "wifi-off", text: "Вкладка закрыта",
               title: "Страница студента закрыта или нет интернета" },
    away: { icon: "door-open", text: "Вышел", title: "Студент нажал «Я вышел» — не считается в счётчике" },
    done: { icon: "check", text: "Записал" },
  };
  // группа question: статус — по типу вопроса
  var QUESTION = {
    question: { icon: "circle-help", text: "Вопрос", notify: "✋ Вопрос" },
    bug: { icon: "bug", text: "Ошибка в коде", notify: "🐞 Ошибка в коде" },
  };
  var WAITING_GROUPS = { wait: 1, offline: 1 };
  var clockOffset = 0;  // серверное время − локальное, для «вышел N мин назад» и «ждёт 1:20»

  function waiting() { return state.students.filter(function (s) { return WAITING_GROUPS[s.group]; }); }
  function questions() { return state.students.filter(function (s) { return s.group === "question"; }); }
  function serverNow() { return Date.now() + clockOffset; }

  function mmss(sec) { var m = Math.floor(sec / 60), s = sec % 60; return m + ":" + (s < 10 ? "0" : "") + s; }

  function whenText(s) {
    if (s.group === "question" && s.question_at) {
      return "ждёт " + mmss(Math.max(0, Math.floor((serverNow() - s.question_at * 1000) / 1000)));
    }
    if (s.group === "done" && s.marked_after != null) return mmss(s.marked_after);
    if (s.group === "away" && s.away_since) {  // «Вышел» уже в пилюле — здесь только давность
      var min = Math.floor((serverNow() - s.away_since * 1000) / 60000);
      return min < 1 ? "только что" : min + " мин назад";
    }
    return "";
  }

  function whenTitle(s) {
    if (s.group === "done") return "Записал через " + whenText(s) + " после начала раунда";
    if (s.group === "away") return "Вышел " + whenText(s);
    if (s.group === "question") return "Ждёт ответа " + whenText(s).replace("ждёт ", "");
    return "";
  }

  // Аватар: 2 буквы (фамилия + имя), спокойный цвет детерминированно из имени
  function initials(name) {
    var parts = name.trim().split(/\s+/);
    return ((parts[0] || "").charAt(0) + (parts[1] || "").charAt(0)).toUpperCase() || "?";
  }
  var AVATAR_HUES = [210, 160, 30, 280, 340, 190, 100, 250];
  function avatarHue(name) {
    var h = 0;
    for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
    return AVATAR_HUES[h % AVATAR_HUES.length];
  }

  // Пилюля статуса: иконка + текст на цветном фоне. small — вторая, «Внимание»
  function pill(kind, icon, text, small, title) {
    var p = document.createElement("span");
    p.className = "pill pill-" + kind + (small ? " pill-sm" : "");
    if (title) p.title = title;
    var t = document.createElement("span");
    t.textContent = text;
    p.append(Realtime.icon(icon), t);
    return p;
  }

  // Чип ученика для мини-окна. doc — документ мини-окна.
  function studentChip(doc, s) {
    var li = doc.createElement("li");
    var offline = s.group === "offline";
    var q = s.group === "question";
    li.className = "st st-wait" + (offline ? " st-offline" : "") + (q ? " st-question" : "");
    li.title = q ? QUESTION[s.question].text + " — " + s.name : offline ? s.name + " — вкладка закрыта" : s.name;
    if (q) li.append(Realtime.icon("hand", doc));
    else if (offline) li.append(Realtime.icon("wifi-off", doc));
    else {
      var dot = doc.createElement("span");
      dot.className = "st-dot";
      dot.setAttribute("aria-hidden", "true");
      li.append(dot);
    }
    var name = doc.createElement("span");
    name.className = "st-name";
    name.textContent = s.name;
    li.append(name);
    return li;
  }

  // «Ждём · 3» — число жирным
  function setGroupTitle(el, title, count) {
    var doc = el.ownerDocument;
    var b = doc.createElement("b");
    b.textContent = count;
    el.textContent = "";
    el.append(doc.createTextNode(title + " · "), b);
  }

  function emptyNote(doc, empty) {
    var p = doc.createElement("p");
    p.className = "group-empty" + (empty.good ? " is-good" : "");
    if (empty.icon) p.append(Realtime.icon(empty.icon, doc));
    p.append(doc.createTextNode(empty.text));
    return p;
  }

  // Строка таблицы: [аватар + имя] | пилюля статуса (+ пилюля «Внимания») | когда | «Ответил» ×
  // Крестик виден при наведении/фокусе, место под него зарезервировано.
  var ATTENTION_STATUS = {
    yes: { kind: "att-yes", icon: "ear", text: "Слушает" },
    no: { kind: "att-no", icon: "triangle-alert", text: "Не ответил", title: "Не ответил на «Внимание»" },
  };

  function fillRow(row, s) {
    var st = s.group === "question" ? QUESTION[s.question] || QUESTION.question : STATUS[s.group] || STATUS.wait;
    var kind = s.group === "question" ? (s.question === "bug" ? "bug" : "question") : s.group;
    var att = state.attention && s.group !== "away" ? (s.attention_ack ? "yes" : "no") : null;
    row.className = "roster-row r-" + s.group + (att ? " att-" + att : "");
    row.textContent = "";

    var name = document.createElement("span");
    name.className = "r-name";
    name.setAttribute("role", "cell");
    var av = document.createElement("span");
    av.className = "avatar";
    av.setAttribute("aria-hidden", "true");
    av.style.setProperty("--h", avatarHue(s.name));
    av.textContent = initials(s.name);
    var nm = document.createElement("span");
    nm.className = "r-name-text";
    nm.title = s.name;
    nm.textContent = s.name;
    name.append(av, nm);

    var status = document.createElement("span");
    status.className = "r-status";
    status.setAttribute("role", "cell");
    status.append(pill(kind, st.icon, st.text, false, st.title));
    if (att) {  // во время «Внимания» основной статус остаётся, рядом — маленькая вторая пилюля
      var a = ATTENTION_STATUS[att];
      status.append(pill(a.kind, a.icon, a.text, true, a.title || "«Внимание»: " + a.text));
    }

    var when = document.createElement("span");
    when.className = "r-when";
    when.setAttribute("role", "cell");
    var whenSpan = document.createElement("span");
    whenSpan.className = "r-when-text";
    whenSpan.textContent = whenText(s);
    when.append(whenSpan);
    when.title = whenTitle(s);

    var cell = document.createElement("span");
    cell.className = "r-actions";
    cell.setAttribute("role", "cell");
    if (s.group === "question") {  // «Ответил» — видна всегда у строк с вопросом
      var ans = document.createElement("button");
      ans.type = "button";
      ans.className = "btn btn-sm btn-answer";
      ans.setAttribute("aria-label", "Ответил: " + s.name);
      ans.append(Realtime.icon("check"), document.createTextNode("Ответил"));
      ans.addEventListener("click", function () { conn.send("answer", { student_id: s.id }); });
      cell.append(ans);
    }
    var x = document.createElement("button");
    x.type = "button";
    x.className = "st-kick";
    x.append(Realtime.icon("x"));
    x.title = "Удалить " + s.name;
    x.setAttribute("aria-label", "Удалить " + s.name);
    x.addEventListener("click", function () {
      if (confirm("Удалить " + s.name + "?")) conn.send("kick", { student_id: s.id });
    });
    cell.append(x);

    row.append(name, status, when, cell);
  }

  // --- звук «записали все»: Web Audio, без файлов -------------------------
  var SOUND_KEY = "zap_sound";            // "on" | "off"
  var VARIANT_KEY = "zap_sound_variant";  // "chord" | "bell" | "double"
  var C5 = 523.25, E5 = 659.25, G5 = 783.99, A5 = 880;

  // Каждый вариант — список нот: [частота, начало (с), длительность (с), громкость, форма, обертоны].
  // Обертон — [множитель частоты, доля громкости]. Атака 10 мс, затем экспоненциальное затухание.
  var VARIANTS = {
    // «успех»: восходящее арпеджио C5→E5→G5 через 90 мс, лёгкий 2-й обертон — «колокольчиком», ~0.75 с
    chord: [
      [C5, 0.00, 0.55, 0.22, "sine", [[1, 1], [2, 0.18]]],
      [E5, 0.09, 0.55, 0.22, "sine", [[1, 1], [2, 0.18]]],
      [G5, 0.18, 0.57, 0.24, "sine", [[1, 1], [2, 0.18]]],
    ],
    // одна чистая нота с долгим затуханием; негармонические обертоны как у колокольчика
    bell: [[A5, 0, 1.2, 0.35, "sine", [[1, 1], [2.76, 0.22], [5.4, 0.07]]]],
    // два коротких тона, как прежний сигнал, но мягче: треугольник и плавная атака
    double: [
      [660, 0.00, 0.22, 0.30, "triangle", [[1, 1]]],
      [880, 0.18, 0.26, 0.30, "triangle", [[1, 1]]],
    ],
  };
  // вопрос студента — отдельный сигнал: два коротких высоких восходящих «пинга» (D6 → G6),
  // заметно отличается от аккорда «все записали»; в выбор вариантов не входит
  var QUESTION_SOUND = [
    [1174.66, 0.00, 0.14, 0.22, "sine", [[1, 1], [2, 0.12]]],
    [1567.98, 0.16, 0.20, 0.22, "sine", [[1, 1], [2, 0.12]]],
  ];

  function load(key, fallback) {
    try { var v = localStorage.getItem(key); return v === null ? fallback : v; } catch (e) { return fallback; }
  }
  function save(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }

  var soundOn = load(SOUND_KEY, "on") !== "off";
  var qSoundOn = load("zap_sound_question", "on") !== "off";  // «Сигнал о вопросе» — отдельно
  var variant = load(VARIANT_KEY, "chord");
  if (!VARIANTS[variant]) variant = "chord";
  var audioCtx = null;
  var doneRound = null;  // раунд, в котором уже прозвучал сигнал

  function unlockAudio() {  // браузер разрешает звук только после действия пользователя
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === "suspended") return audioCtx.resume();
    } catch (e) {}
    return Promise.resolve();
  }
  document.addEventListener("pointerdown", unlockAudio);
  document.addEventListener("keydown", unlockAudio);

  function synth(name) {
    var ctx = audioCtx, t0 = ctx.currentTime + 0.02;
    (name === "question" ? QUESTION_SOUND : VARIANTS[name]).forEach(function (n) {
      var start = t0 + n[1], end = start + n[2];
      n[5].forEach(function (partial) {
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = n[4];
        osc.frequency.value = n[0] * partial[0];
        var peak = n[3] * partial[1];
        // рампы на gain — без щелчков в начале и в конце
        gain.gain.setValueAtTime(0, start);
        gain.gain.linearRampToValueAtTime(peak, start + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.0001, end);
        osc.connect(gain).connect(ctx.destination);
        osc.start(start);
        osc.stop(end + 0.05);
      });
    });
  }

  function play(name) {
    if (!audioCtx) return;  // пользователь ещё ничего не нажимал — браузер звук не даст
    if (audioCtx.state === "running") synth(name);
    else audioCtx.resume().then(function () { synth(name); }, function () {});
  }

  function allDone() { return state.status === "active" && state.total > 0 && state.marked === state.total; }

  // «Внимание»: все присутствующие нажали «Я слушаю» → короткий сигнал (по переключателю
  // «Сигнал, когда записали все» — тот же выбранный звук) и надпись «Все слушают»
  function allListening() { return !!state.attention && state.total > 0 && state.attention_acked === state.total; }
  var listenedAt = null;  // attention_at, для которого сигнал уже прозвучал
  function checkListening(initial) {
    if (!allListening() || listenedAt === state.attention_at) return;
    listenedAt = state.attention_at;
    if (!initial && soundOn) play(variant);
  }

  function toggleAttention() {
    conn.send(state.attention ? "attention_end" : "attention");
  }

  function renderAttention() {
    var on = !!state.attention;
    var btn = $("btn-attention");
    Realtime.setIconLabel(btn, on ? "play" : "megaphone", on ? "Продолжить урок" : "Внимание");
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", String(on));
    btn.title = on ? "Снять «Внимание» у всех студентов" : "Всем студентам: красный экран «Посмотрите на доску»";
    $("attention-box").hidden = !on;
    $("att-acked").textContent = state.attention_acked || 0;
    $("att-total").textContent = state.total;
    $("att-all").hidden = !allListening();
    $("attention-box").classList.toggle("is-all", allListening());
  }

  function checkSound(initial) {
    if (!allDone() || doneRound === state.round) return;
    doneRound = state.round;
    if (!initial && soundOn) play(variant);
  }

  // --- меню-popover в верхней полосе («Звук», «Авто-раунд») -----------------
  // Клик по кнопке — открыть/закрыть; Esc — закрыть и вернуть фокус; клик вне — закрыть.
  // focusTarget() — куда ставить фокус при открытии. Открыто не больше одного меню.
  var menus = [];
  function popMenu(rootId, btnId, popId, focusTarget) {
    var m = { root: $(rootId), btn: $(btnId), pop: $(popId) };
    m.set = function (open, focusBack) {
      if (open) menus.forEach(function (o) { if (o !== m && !o.pop.hidden) o.set(false); });
      m.pop.hidden = !open;
      m.btn.setAttribute("aria-expanded", String(open));
      if (open) focusTarget().focus();
      else if (focusBack) m.btn.focus();
    };
    m.btn.addEventListener("click", function () { m.set(m.pop.hidden); });
    menus.push(m);
    return m;
  }
  document.addEventListener("pointerdown", function (e) {
    menus.forEach(function (m) { if (!m.pop.hidden && !m.root.contains(e.target)) m.set(false); });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    menus.forEach(function (m) {
      // Esc в поле «Своё» сначала сворачивает поле (см. ниже), меню остаётся
      if (!m.pop.hidden && !e.defaultPrevented) m.set(false, true);
    });
  });
  function checkedOf(radios) { return radios.filter(function (r) { return r.checked; })[0]; }

  // --- меню звука: вкл/выкл, вариант, «Проверить» ---------------------------
  var soundBtn = $("sound-toggle");
  var variantRadios = [].slice.call(document.querySelectorAll('input[name="sound-variant"]'));

  function renderSound() {
    var any = soundOn || qSoundOn;
    Realtime.setIconLabel(soundBtn, any ? "bell" : "bell-off", "Звук");
    soundBtn.classList.toggle("is-off", !any);
    soundBtn.title = any ? "Звук — сигналы и уведомления" : "Звук выкл";
    $("sound-on").checked = soundOn;
    $("q-sound-on").checked = qSoundOn;
    $("sound-variants").classList.toggle("is-off", !soundOn);
    variantRadios.forEach(function (r) { r.checked = r.value === variant; });
    renderNotify();
  }

  // --- системные уведомления о вопросе (Notification API) --------------------
  // Разрешение спрашиваем только по клику агая; показываем, только если панель не в фокусе.
  function renderNotify() {
    var status = $("notify-status"), btn = $("notify-enable");
    if (!("Notification" in window)) {
      btn.hidden = true;
      status.textContent = "недоступны в этом браузере";
      return;
    }
    var p = Notification.permission;
    btn.hidden = p !== "default";
    status.className = "notify-status" + (p === "denied" ? " is-bad" : "");
    status.textContent = p === "granted" ? "включены"
      : p === "denied" ? "заблокированы — разрешите уведомления для этого сайта (значок слева в адресной строке)"
      : "";
  }
  $("notify-enable").addEventListener("click", function () {
    try {
      var r = Notification.requestPermission(renderNotify);  // старые браузеры — колбэк
      if (r && r.then) r.then(renderNotify, renderNotify);
    } catch (e) { renderNotify(); }
  });

  function notifyQuestion(s) {
    try {
      if (!("Notification" in window) || Notification.permission !== "granted" || document.hasFocus()) return;
      var n = new Notification((QUESTION[s.question] || QUESTION.question).notify + ": " + s.name, {
        body: "Урок " + state.code, tag: "zap-question-" + s.id,
      });
      n.onclick = function () { window.focus(); n.close(); };
    } catch (e) {}
  }

  popMenu("sound-menu", "sound-toggle", "sound-pop", function () {
    return checkedOf(variantRadios) || $("sound-on");
  });
  $("sound-on").addEventListener("change", function () {
    soundOn = this.checked;
    save(SOUND_KEY, soundOn ? "on" : "off");
    renderSound();
  });
  variantRadios.forEach(function (r) {
    r.addEventListener("change", function () {
      if (!r.checked) return;
      variant = r.value;
      save(VARIANT_KEY, variant);
    });
  });
  $("sound-test").addEventListener("click", function () {
    // клик сам по себе разрешает звук; ждём resume, чтобы первая проверка тоже прозвучала
    unlockAudio().then(function () { play(variant); }, function () {});
  });
  $("q-sound-on").addEventListener("change", function () {
    qSoundOn = this.checked;
    save("zap_sound_question", qSoundOn ? "on" : "off");
    renderSound();
  });
  $("q-sound-test").addEventListener("click", function () {
    unlockAudio().then(function () { play("question"); }, function () {});
  });

  // --- новый вопрос: звук (если вкл), чип мигает, title вкладки, мини-окно, уведомление ----
  var knownQuestions = null;  // id -> question_at; null — ещё не было state (при загрузке не сигналим)
  var baseTitle = document.title;

  function checkQuestions() {
    var now = {}, fresh = [];
    questions().forEach(function (s) {
      now[s.id] = s.question_at;
      if (knownQuestions && knownQuestions[s.id] !== s.question_at) fresh.push(s);
    });
    knownQuestions = now;
    if (!fresh.length) return;
    var s = fresh[fresh.length - 1];
    if (qSoundOn) play("question");
    blinkChip();
    if (document.hidden) document.title = "✋ Вопрос — " + s.name;  // пока агай не вернётся на вкладку
    flashMini();
    notifyQuestion(s);
  }
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) document.title = baseTitle;
  });

  function blinkChip() {
    var chip = $("q-chip");
    chip.classList.remove("is-blink");
    void chip.offsetWidth;  // перезапуск анимации
    chip.classList.add("is-blink");
  }
  $("q-chip").addEventListener("animationend", function () { this.classList.remove("is-blink"); });
  $("q-chip").addEventListener("click", function () {
    $("roster-card").scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "start" });
  });

  // --- меню «Авто-раунд»: настройка хранится в комнате на сервере -----------
  var autoBtn = $("auto-toggle");
  var delayRadios = [].slice.call(document.querySelectorAll('input[name="auto-delay"]'));
  var DELAY_PRESETS = ["3", "5", "10"];
  var autoEditing = false;   // агай вводит «Своё» — выбор не трогаем
  var autoByPointer = false; // «Своё» мышью — сразу фокус в поле; стрелками — не уводим фокус

  function fmtSec(v) { return String(Number(v)) + " с"; }

  function renderAuto() {
    var on = !!state.auto_next_enabled, sec = state.auto_next_seconds;
    // «Авто-раунд · 3 с» / «Авто-раунд выкл»; на узкой полосе слово «Авто-раунд» прячется (CSS)
    var label = $("auto-toggle-label"), word = document.createElement("span");
    word.className = "auto-word";
    word.textContent = on ? "Авто-раунд · " : "Авто-раунд ";
    label.textContent = "";
    label.append(word, document.createTextNode(on ? fmtSec(sec) : "выкл"));
    autoBtn.classList.toggle("is-off", !on);
    autoBtn.title = on
      ? "Все записали → новый раунд через " + fmtSec(sec)
      : "Авто-раунд выключен: когда все записали, нажмите «Сбросить» сами";
    $("auto-on").checked = on;
    $("auto-delays").classList.toggle("is-off", !on);
    var v = String(Number(sec)), custom = DELAY_PRESETS.indexOf(v) === -1;
    $("auto-custom-label").textContent = custom ? "Своё · " + fmtSec(sec) : "Своё";
    if (autoEditing) return;
    delayRadios.forEach(function (r) { r.checked = r.value === (custom ? "custom" : v); });
    $("auto-custom").hidden = true;
  }

  function sendAuto(extra) {
    conn.send("set_auto_next", Object.assign({ enabled: !!state.auto_next_enabled }, extra));
  }

  function openAutoCustom(focus) {
    autoEditing = true;
    $("auto-custom").hidden = false;
    $("auto-custom-input").value = Number(state.auto_next_seconds) || "";
    if (focus) { $("auto-custom-input").focus(); $("auto-custom-input").select(); }  // сразу печатать поверх
  }

  function closeAutoCustom() {
    autoEditing = false;
    $("auto-custom").hidden = true;
    renderAuto();
  }

  popMenu("auto-menu", "auto-toggle", "auto-pop", function () {
    return checkedOf(delayRadios) || $("auto-on");
  });
  $("auto-on").addEventListener("change", function () {
    sendAuto({ enabled: this.checked });  // задержку не трогаем
  });
  $("auto-delays").addEventListener("pointerdown", function () { autoByPointer = true; });
  // «Своё» уже выбрано — change не придёт, раскрываем по клику на подпись
  $("auto-delay-custom").parentNode.addEventListener("click", function () {
    if ($("auto-delay-custom").checked && !autoEditing) openAutoCustom(true);
  });
  delayRadios.forEach(function (r) {
    r.addEventListener("change", function () {
      if (!r.checked) return;
      var pointer = autoByPointer;
      autoByPointer = false;
      if (r.value === "custom") { openAutoCustom(pointer); return; }
      autoEditing = false;
      $("auto-custom").hidden = true;
      sendAuto({ seconds: Number(r.value) });
    });
  });
  function applyAutoCustom() {
    var input = $("auto-custom-input");
    if (!input.value || !input.checkValidity()) { input.reportValidity(); return; }
    sendAuto({ seconds: Number(input.value) });
    autoEditing = false;
    $("auto-custom").hidden = true;  // значение покажет подпись «Своё · N с», когда придёт state
    $("auto-delay-custom").focus();
  }
  $("auto-custom-apply").addEventListener("click", applyAutoCustom);
  $("auto-custom-input").addEventListener("keydown", function (e) {
    if (e.key === "Enter") applyAutoCustom();
    else if (e.key === "Escape") { e.preventDefault(); closeAutoCustom(); $("auto-delay-custom").focus(); }
  });

  // --- скрыть / показать код: помним по коду комнаты ------------------------
  var CODE_KEY = "zap_code_hidden_" + state.code;

  function setCodeHidden(hidden) {
    $("code-banner").hidden = hidden;
    $("teacher-top").classList.toggle("code-hidden", hidden);
    $("code-chip").hidden = !hidden;
    save(CODE_KEY, hidden ? "1" : "0");
  }
  $("btn-hide-code").addEventListener("click", function () { setCodeHidden(true); $("code-chip").focus(); });
  $("code-chip").addEventListener("click", function () { setCodeHidden(false); $("btn-hide-code").focus(); });
  setCodeHidden(load(CODE_KEY, "0") === "1");  // по умолчанию показан — код нужно продиктовать

  // --- таймер раунда -------------------------------------------------------
  var updateCountdown = Realtime.countdown($("countdown"));
  var radios = [].slice.call(document.querySelectorAll('input[name="timer"]'));
  var presets = radios.map(function (r) { return r.value; }).filter(function (v) { return v !== "custom"; });
  var byPointer = false;  // «Своё» мышью — сразу фокус в поле; стрелками — не уводим фокус

  // Поле «Своё» раскрыто только пока агай вводит число; после «Ок» сворачивается,
  // а значение видно в самом сегменте: «Своё · 15 с». Клик по нему снова раскрывает поле.
  var customLabel = document.querySelector('label[for="timer-custom-radio"]');
  var editing = false;

  function openCustom(focus) {
    editing = true;
    $("custom-timer").hidden = false;
    if (state.timer_seconds) $("timer-custom").value = state.timer_seconds;
    if (focus) { $("timer-custom").focus(); $("timer-custom").select(); }  // сразу печатать поверх
  }

  function closeCustom() {
    editing = false;
    $("custom-timer").hidden = true;
    renderTimer();
  }

  function renderTimer() {
    var v = state.timer_seconds ? String(state.timer_seconds) : "";
    var custom = v !== "" && presets.indexOf(v) === -1;
    customLabel.textContent = custom ? "Своё · " + v + " с" : "Своё";
    if (editing) return;  // агай вводит своё значение — выбор не трогаем
    radios.forEach(function (r) { r.checked = r.value === (custom ? "custom" : v); });
    $("custom-timer").hidden = true;
  }

  document.querySelector(".segmented").addEventListener("pointerdown", function () { byPointer = true; });
  // «Своё» уже выбрано — change не придёт, поэтому раскрываем по клику на подпись
  customLabel.addEventListener("click", function () {
    if ($("timer-custom-radio").checked && !editing) openCustom(true);
  });
  radios.forEach(function (r) {
    r.addEventListener("change", function () {
      if (!r.checked) return;
      var pointer = byPointer;
      byPointer = false;
      if (r.value === "custom") { openCustom(pointer); return; }
      editing = false;
      $("custom-timer").hidden = true;
      conn.send("set_timer", { seconds: r.value ? Number(r.value) : null });
    });
  });

  function applyCustom() {
    var input = $("timer-custom");
    if (!input.value || !input.checkValidity()) { input.reportValidity(); return; }
    conn.send("set_timer", { seconds: Number(input.value) });
    editing = false;
    $("custom-timer").hidden = true;  // свернули — значение покажет сегмент, когда придёт state
    $("timer-custom-radio").focus();
  }
  $("timer-apply").addEventListener("click", applyCustom);
  $("timer-custom").addEventListener("keydown", function (e) {
    if (e.key === "Enter") applyCustom();
    else if (e.key === "Escape") { closeCustom(); $("timer-custom-radio").focus(); }
  });

  // --- авто-новый раунд: все записали → сервер сам начнёт новый через N с ----
  var updateAuto = Realtime.countdown($("auto-next"), "", {
    field: "auto_next_at",
    format: function (s) { return s + "…"; },
    doneText: "Новый раунд…",
  });
  $("btn-hold").addEventListener("click", function () { conn.send("hold_auto"); });

  function render() {
    var active = state.status === "active";
    var done = allDone();
    renderTimer();
    renderAuto();
    // пока идёт отсчёт авто-раунда, строку таймера раунда не показываем — один отсчёт за раз
    updateCountdown(state.auto_next_at ? { now: state.now } : state);
    updateAuto(state);
    $("all-done").hidden = !(done && !state.auto_next_enabled);  // авто-раунд выкл — сбрасывает агай
    $("auto-held").hidden = !(state.auto_held && done && state.auto_next_enabled);

    $("marked").textContent = state.marked;
    $("total").textContent = state.total;
    $("round").textContent = state.round;
    $("status-text").textContent = STATUS_TEXT[state.status] || "";
    $("status-chip").setAttribute("data-status", state.status);
    $("round-part").hidden = !active;
    var pct = state.total ? Math.round((state.marked / state.total) * 100) : 0;
    $("progress-fill").style.width = pct + "%";
    $("progress").setAttribute("aria-valuemax", state.total);
    $("progress").setAttribute("aria-valuenow", state.marked);
    $("btn-reset").hidden = !active;
    renderAttention();

    if (state.now) clockOffset = state.now * 1000 - Date.now();
    renderRoster();
    renderMini(active);
  }

  // Таблица учеников. Строки переиспользуются по id, поэтому при смене порядка
  // (нажал «Записал» → вниз, «Отменить» → наверх) их можно плавно передвинуть: FLIP, 200 мс.
  var rows = {};  // id -> элемент строки
  var reduceMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };

  function renderRoster() {
    var root = $("roster");
    // фокус на крестике переживает перерисовку: запоминаем, у кого он был
    var active = document.activeElement;
    var focusedRow = active && active.closest && active.closest(".roster-row");
    var focusedId = focusedRow && root.contains(focusedRow) ? focusedRow.getAttribute("data-id") : null;
    var focusedSel = active && active.classList && active.classList.contains("btn-answer") ? ".btn-answer" : ".st-kick";

    var before = {};  // First: где строки были
    Object.keys(rows).forEach(function (id) { before[id] = rows[id].getBoundingClientRect().top; });

    var seen = {};
    state.students.forEach(function (s) {
      var id = String(s.id);
      seen[id] = true;
      var row = rows[id];
      if (!row) {
        row = rows[id] = document.createElement("div");
        row.setAttribute("role", "row");
        row.setAttribute("data-id", id);
      }
      fillRow(row, s);
      root.append(row);  // в порядке сервера
    });
    Object.keys(rows).forEach(function (id) {
      if (!seen[id]) { rows[id].remove(); delete rows[id]; }
    });

    // Last → Invert → Play
    if (!reduceMotion.matches) {
      state.students.forEach(function (s) {
        var id = String(s.id), row = rows[id];
        if (before[id] === undefined || !row.animate) return;
        var dy = before[id] - row.getBoundingClientRect().top;
        if (Math.abs(dy) < 1) return;
        row.animate([{ transform: "translateY(" + dy + "px)" }, { transform: "none" }],
                    { duration: 200, easing: "ease-out" });
      });
    }

    // сводка: «✋ Вопросы 2 · Ждём 3 · Записали 18 · Вышли 1»; «Ждём» = не записали из присутствующих
    var nq = questions().length;
    var summary = $("roster-summary");
    summary.textContent = "";
    if (state.students.length) {
      if (nq) {
        var qs = document.createElement("span");
        qs.className = "summary-q";
        qs.append(Realtime.icon("hand"), document.createTextNode("Вопросы " + nq));
        summary.append(qs, document.createTextNode(" · "));
      }
      summary.append(document.createTextNode("Ждём " + (state.total - state.marked) + " · Записали " + state.marked
        + " · Вышли " + (state.away || 0)));
    }
    $("roster-empty").hidden = state.students.length > 0;
    document.querySelector(".roster-header").hidden = !state.students.length;
    $("q-chip").hidden = !nq;
    $("q-count").textContent = nq;
    $("q-chip").title = nq ? "Вопросы: " + nq + " — показать в списке" : "";

    if (focusedId && rows[focusedId]) {
      var again = rows[focusedId].querySelector(focusedSel) || rows[focusedId].querySelector(".st-kick");
      if (again) again.focus();
    }
  }

  // «ждёт 1:20» и «вышел N мин назад» идут без новых state — обновляем раз в секунду
  setInterval(function () {
    state.students.forEach(function (s) {
      if ((s.group !== "away" && s.group !== "question") || !rows[s.id]) return;
      rows[s.id].querySelector(".r-when-text").textContent = whenText(s);
    });
  }, 1000);

  // --- мини-окно агая: счётчик, прогресс, короткий список, «Сбросить» -------
  var mini = null;  // {doc, el}

  // В окошке: вопросы («✋ Фамилия Имя», жёлтые) сверху, затем «Ждём» (вышедших не показываем);
  // отсчёт авто-раунда — тот же, что в панели.
  function renderMini(active) {
    if (!mini) return;
    var e = mini.el, doc = mini.doc;
    e.code.textContent = state.code;
    e.round.textContent = state.round;
    e.marked.textContent = state.marked;
    e.total.textContent = state.total;
    e.fill.style.width = (state.total ? Math.round((state.marked / state.total) * 100) : 0) + "%";
    e.reset.hidden = !active;
    var on = !!state.attention;
    e["att-count"].hidden = !on;
    e["att-text"].textContent = allListening() ? "Все слушают" : "Слушают " + (state.attention_acked || 0) + " / " + state.total;
    e.attention.classList.toggle("is-on", on);
    e["attention-label"].textContent = on ? "Продолжить" : "Внимание";
    e.attention.title = on ? "Продолжить урок — снять «Внимание»" : "Внимание — всем посмотреть на доску";
    var qs = questions();
    e.questions.textContent = "";
    qs.forEach(function (s) { e.questions.append(studentChip(doc, s)); });
    e.questions.hidden = !qs.length;
    var items = waiting();
    setGroupTitle(e["list-title"], "Ждём", items.length);
    e.list.textContent = "";
    items.forEach(function (s) { e.list.append(studentChip(doc, s)); });
    e.empty.textContent = "";
    var allIn = state.total > 0 && state.marked === state.total;
    e.empty.hidden = items.length > 0 || (state.total > 0 && !allIn);
    if (!items.length && (allIn || !state.total)) {
      e.empty.append(emptyNote(doc, allIn
        ? { icon: "check-check", text: "Все записали", good: true }
        : { text: "Пока никого. Продиктуйте код классу." }));
    }
  }

  // новый вопрос → мини-окно коротко подсвечивается
  function flashMini() {
    if (!mini) return;
    var root = mini.doc.querySelector(".mini");
    root.classList.remove("is-alert");
    void root.offsetWidth;
    root.classList.add("is-alert");
  }

  function openMini() {
    Pip.open({
      templateId: "mini-teacher-tpl",
      cssHref: $("btn-mini").getAttribute("data-css"),
      width: 300,
      height: 340,
      onOpen: function (doc) {
        var el = {};
        doc.querySelectorAll("[data-mini]").forEach(function (n) { el[n.getAttribute("data-mini")] = n; });
        el.reset.addEventListener("click", resetRound);
        el.hold.addEventListener("click", function () { conn.send("hold_auto"); });
        el.attention.addEventListener("click", toggleAttention);
        el.expand.addEventListener("click", function () { Pip.close(); window.focus(); });
        doc.addEventListener("pointerdown", unlockAudio);  // клик в окошке тоже разрешает звук
        mini = { doc: doc, el: el };
        updateCountdown.attach(el.countdown);
        updateAuto.attach(el.auto);
        $("btn-mini").setAttribute("aria-pressed", "true");  // ширина не прыгает — меняем только состояние
        $("btn-mini").title = "Закрыть мини-окно";
        render();
      },
      onClose: function () {
        if (mini) { updateCountdown.detach(mini.el.countdown); updateAuto.detach(mini.el.auto); }
        mini = null;
        $("btn-mini").setAttribute("aria-pressed", "false");
        $("btn-mini").title = "Маленькое окно поверх VS Code и презентации";
      },
    }).catch(function () {});
  }

  if (Pip.supported) {
    $("btn-mini").addEventListener("click", function () {
      if (Pip.isOpen()) Pip.close(); else openMini();
    });
  }

  function showFinished() {
    Pip.close();
    updateCountdown({});
    updateAuto({});
    document.querySelectorAll(".page > :not(#finished)").forEach(function (el) { el.hidden = true; });
    $("finished").hidden = false;
  }

  var conn = Realtime.connect(state.code, {
    role: "teacher",
    onMessage: function (msg) {
      if (msg.type === "state") { state = msg; render(); checkSound(false); checkQuestions(); checkListening(false); }
      else if (msg.type === "finished") showFinished();
    },
    onStatus: function (ok) { $("conn").hidden = ok; },
    onEnd: function (reason) {
      if (reason === "finished") showFinished();
      else location.href = "/teacher/new/";  // доступ потерян — на вход агая
    },
  });

  // шлём раунд, который видим: если сервер уже начал новый (таймер/авто-раунд), второго сброса не будет
  function resetRound() { conn.send("reset", { round: state.round }); }
  $("btn-reset").addEventListener("click", resetRound);
  $("btn-attention").addEventListener("click", toggleAttention);
  $("btn-finish").addEventListener("click", function () {
    if (confirm("Завершить урок? Код перестанет работать.")) conn.send("finish");
  });

  renderSound();
  render();
  checkSound(true);  // если все уже записали до загрузки страницы — не пищим
  checkQuestions();  // вопросы, открытые до загрузки, — без сигнала (запоминаем как известные)
  checkListening(true);
})();
