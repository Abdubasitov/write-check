// Панель агая: счётчик, список, кнопки.
(function () {
  "use strict";

  var state = JSON.parse(document.getElementById("initial-state").textContent);
  var $ = function (id) { return document.getElementById(id); };
  // урок начинается сразу; waiting — только у старых комнат, сервер переводит их в active
  var STATUS_TEXT = { waiting: "Урок идёт", active: "Урок идёт", finished: "Урок завершён" };

  var OFFLINE_NOTE = "вкладка закрыта";  // только для скринридера и подсказки — имя важнее

  // Группы списка: «Ждём» сверху (сначала те, у кого вкладка открыта) и «Записали» ниже.
  // Порядок внутри — как входили.
  function onlineFirst(list) {
    return list.filter(function (s) { return s.online; }).concat(list.filter(function (s) { return !s.online; }));
  }

  function groupsFor() {
    var all = state.students;
    return [
      { key: "wait", title: "Ждём", items: onlineFirst(all.filter(function (s) { return !s.marked; })),
        empty: all.length
          ? { icon: "check-check", text: "Все записали", good: true }
          : { text: "Пока никого. Продиктуйте код классу." } },
      { key: "done", title: "Записали", items: all.filter(function (s) { return s.marked; }),
        empty: { text: "Пока никто" } },
    ];
  }

  // Чип ученика. doc — документ страницы или мини-окна. withKick — крестик удаления.
  function studentChip(doc, s, kind, withKick) {
    var li = doc.createElement("li");
    li.className = "st st-" + kind + (s.online ? "" : " st-offline");
    li.setAttribute("data-id", s.id);
    li.title = s.online ? s.name : s.name + " — вкладка закрыта (страница студента закрыта или нет интернета)";

    if (!s.online) li.append(Realtime.icon("wifi-off", doc));
    else if (kind === "done") li.append(Realtime.icon("check", doc));
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

    if (!s.online) {
      var note = doc.createElement("span");
      note.className = "sr-only";
      note.textContent = ", " + OFFLINE_NOTE;
      li.append(note);
    }

    if (withKick) {
      var x = doc.createElement("button");
      x.type = "button";
      x.className = "st-kick";
      x.append(Realtime.icon("x", doc));
      x.title = "Удалить " + s.name;
      x.setAttribute("aria-label", "Удалить " + s.name);
      x.addEventListener("click", function () {
        if (confirm("Удалить " + s.name + "?")) conn.send("kick", { student_id: s.id });
      });
      li.append(x);
    }
    return li;
  }

  // «Ждём · 3» — число жирным
  function setGroupTitle(el, g) {
    var doc = el.ownerDocument;
    var b = doc.createElement("b");
    b.textContent = g.items.length;
    el.textContent = "";
    el.append(doc.createTextNode(g.title + " · "), b);
  }

  function emptyNote(doc, empty) {
    var p = doc.createElement("p");
    p.className = "group-empty" + (empty.good ? " is-good" : "");
    if (empty.icon) p.append(Realtime.icon(empty.icon, doc));
    p.append(doc.createTextNode(empty.text));
    return p;
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

  function load(key, fallback) {
    try { var v = localStorage.getItem(key); return v === null ? fallback : v; } catch (e) { return fallback; }
  }
  function save(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }

  var soundOn = load(SOUND_KEY, "on") !== "off";
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
    VARIANTS[name].forEach(function (n) {
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
    Realtime.setIconLabel(soundBtn, soundOn ? "bell" : "bell-off", "Звук");
    soundBtn.classList.toggle("is-off", !soundOn);
    soundBtn.title = soundOn ? "Звук вкл — выбрать сигнал" : "Звук выкл";
    $("sound-on").checked = soundOn;
    $("sound-variants").classList.toggle("is-off", !soundOn);
    variantRadios.forEach(function (r) { r.checked = r.value === variant; });
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

  // --- меню «Авто-раунд»: настройка хранится в комнате на сервере -----------
  var autoBtn = $("auto-toggle");
  var delayRadios = [].slice.call(document.querySelectorAll('input[name="auto-delay"]'));
  var DELAY_PRESETS = ["3", "5", "10"];
  var autoEditing = false;   // агай вводит «Своё» — выбор не трогаем
  var autoByPointer = false; // «Своё» мышью — сразу фокус в поле; стрелками — не уводим фокус

  function fmtSec(v) { return String(Number(v)) + " с"; }

  function renderAuto() {
    var on = !!state.auto_next_enabled, sec = state.auto_next_seconds;
    $("auto-toggle-label").textContent = on ? "Авто-раунд · " + fmtSec(sec) : "Авто-раунд выкл";
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
    if (focus) $("auto-custom-input").focus();
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

  // --- таймер автосброса ---------------------------------------------------
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
    if (focus) $("timer-custom").focus();
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
    // пока идёт отсчёт авто-раунда, строку автосброса не показываем — один отсчёт за раз
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

    renderRoster(active);
    renderMini(active);
  }

  function renderRoster(active) {
    // фокус на крестике переживает перерисовку: запоминаем, у кого он был
    var focused = document.activeElement && document.activeElement.closest && document.activeElement.closest(".st");
    var focusedId = focused ? focused.getAttribute("data-id") : null;

    var root = $("roster");
    root.textContent = "";
    groupsFor(active).forEach(function (g) {
      var section = document.createElement("section");
      section.className = "group group-" + g.key;
      var h = document.createElement("h3");
      h.className = "group-title";
      setGroupTitle(h, g);
      section.append(h);
      if (g.items.length) {
        var ul = document.createElement("ul");
        ul.className = "chips-list";
        g.items.forEach(function (s) { ul.append(studentChip(document, s, g.key, true)); });
        section.append(ul);
      } else {
        section.append(emptyNote(document, g.empty));
      }
      root.append(section);
    });

    if (focusedId) {
      var again = root.querySelector('.st[data-id="' + focusedId + '"] .st-kick');
      if (again) again.focus();
    }
  }

  // --- мини-окно агая: счётчик, прогресс, короткий список, «Сбросить» -------
  var mini = null;  // {doc, el}

  // В окошке — только первая группа «Ждём»; отсчёт авто-раунда — тот же, что в панели.
  function renderMini(active) {
    if (!mini) return;
    var e = mini.el, doc = mini.doc;
    e.code.textContent = state.code;
    e.round.textContent = state.round;
    e.marked.textContent = state.marked;
    e.total.textContent = state.total;
    e.fill.style.width = (state.total ? Math.round((state.marked / state.total) * 100) : 0) + "%";
    e.reset.hidden = !active;
    var g = groupsFor(active)[0];
    setGroupTitle(e["list-title"], g);
    e.list.textContent = "";
    g.items.forEach(function (s) { e.list.append(studentChip(doc, s, g.key, false)); });
    e.empty.textContent = "";
    e.empty.hidden = g.items.length > 0;
    if (!g.items.length) e.empty.append(emptyNote(doc, g.empty));
  }

  function openMini() {
    Pip.open({
      templateId: "mini-teacher-tpl",
      cssHref: $("btn-mini").getAttribute("data-css"),
      width: 300,
      height: 300,
      onOpen: function (doc) {
        var el = {};
        doc.querySelectorAll("[data-mini]").forEach(function (n) { el[n.getAttribute("data-mini")] = n; });
        el.reset.addEventListener("click", resetRound);
        el.hold.addEventListener("click", function () { conn.send("hold_auto"); });
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
      if (msg.type === "state") { state = msg; render(); checkSound(false); }
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
  $("btn-finish").addEventListener("click", function () {
    if (confirm("Завершить урок? Код перестанет работать.")) conn.send("finish");
  });

  renderSound();
  render();
  checkSound(true);  // если все уже записали до загрузки страницы — не пищим
})();
