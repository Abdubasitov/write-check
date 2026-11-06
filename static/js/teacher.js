// Панель агая: счётчик, список, кнопки.
(function () {
  "use strict";

  var state = JSON.parse(document.getElementById("initial-state").textContent);
  var $ = function (id) { return document.getElementById(id); };
  var STATUS_TEXT = { waiting: "Ждём начала", active: "Урок идёт", finished: "Урок завершён" };

  var OFFLINE_NOTE = "вкладка закрыта";  // только для скринридера и подсказки — имя важнее

  // Группы списка. До старта — одна «Вошли». Во время урока — «Ждём» сверху
  // (сначала те, у кого вкладка открыта) и «Записали» ниже. Порядок внутри — как входили.
  function onlineFirst(list) {
    return list.filter(function (s) { return s.online; }).concat(list.filter(function (s) { return !s.online; }));
  }

  function groupsFor(active) {
    var all = state.students;
    if (!active) {
      return [{ key: "in", title: "Вошли", items: onlineFirst(all), empty: { text: "Пока никого. Продиктуйте код классу." } }];
    }
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
  var SOUND_KEY = "zap_sound";
  var soundOn = true;
  try { soundOn = localStorage.getItem(SOUND_KEY) !== "off"; } catch (e) {}
  var audioCtx = null;
  var doneRound = null;  // раунд, в котором уже прозвучал сигнал

  function unlockAudio() {  // браузер разрешает звук только после действия пользователя
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === "suspended") audioCtx.resume();
    } catch (e) {}
  }
  document.addEventListener("pointerdown", unlockAudio);
  document.addEventListener("keydown", unlockAudio);

  function beep() {
    if (!audioCtx || audioCtx.state !== "running") return;
    var t0 = audioCtx.currentTime;
    [[880, 0], [1320, 0.16]].forEach(function (n) {
      var osc = audioCtx.createOscillator();
      var gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.value = n[0];
      gain.gain.setValueAtTime(0.0001, t0 + n[1]);
      gain.gain.exponentialRampToValueAtTime(0.35, t0 + n[1] + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + n[1] + 0.28);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(t0 + n[1]);
      osc.stop(t0 + n[1] + 0.3);
    });
  }

  function allDone() { return state.status === "active" && state.total > 0 && state.marked === state.total; }

  function checkSound(initial) {
    if (!allDone() || doneRound === state.round) return;
    doneRound = state.round;
    if (!initial && soundOn) beep();
  }

  function renderSoundToggle() {
    var b = $("sound-toggle");
    Realtime.setIconLabel(b, soundOn ? "bell" : "bell-off", "Звук");
    b.setAttribute("aria-pressed", String(soundOn));
    b.title = soundOn ? "Звук вкл" : "Звук выкл";
    b.classList.toggle("is-off", !soundOn);
  }

  $("sound-toggle").addEventListener("click", function () {
    soundOn = !soundOn;
    try { localStorage.setItem(SOUND_KEY, soundOn ? "on" : "off"); } catch (e) {}
    renderSoundToggle();
  });

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

  function render() {
    var active = state.status === "active";
    renderTimer();
    updateCountdown(state);

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
    $("btn-start").hidden = state.status !== "waiting";
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

  // В окошке — только первая группа: до старта «Вошли», во время урока «Ждём».
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
      height: 260,
      onOpen: function (doc) {
        var el = {};
        doc.querySelectorAll("[data-mini]").forEach(function (n) { el[n.getAttribute("data-mini")] = n; });
        el.reset.addEventListener("click", function () { conn.send("reset"); });
        el.expand.addEventListener("click", function () { Pip.close(); window.focus(); });
        doc.addEventListener("pointerdown", unlockAudio);  // клик в окошке тоже разрешает звук
        mini = { doc: doc, el: el };
        updateCountdown.attach(el.countdown);
        $("btn-mini").setAttribute("aria-pressed", "true");  // ширина не прыгает — меняем только состояние
        $("btn-mini").title = "Закрыть мини-окно";
        render();
      },
      onClose: function () {
        if (mini) updateCountdown.detach(mini.el.countdown);
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

  $("btn-start").addEventListener("click", function () { conn.send("start"); });
  $("btn-reset").addEventListener("click", function () { conn.send("reset"); });
  $("btn-finish").addEventListener("click", function () {
    if (confirm("Завершить урок? Код перестанет работать.")) conn.send("finish");
  });

  renderSoundToggle();
  render();
  checkSound(true);  // если все уже записали до загрузки страницы — не пищим
})();
