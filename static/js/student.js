// Страница студента: сразу кнопка «Записал» (+ мини-окно поверх всех окон).
// «Отмечено» показываем только после подтверждения сервером — никаких тихих оптимистичных отметок.
(function () {
  "use strict";

  var CONFIRM_TIMEOUT_MS = 3000;
  var PING_MS = 15000;

  var state = JSON.parse(document.getElementById("initial-state").textContent);
  var $ = function (id) { return document.getElementById(id); };
  var ended = false;
  var updateCountdown = Realtime.countdown($("countdown"));
  // все записали → «новый раунд через 3…», потом кнопка «Записал» появится сама
  var updateAuto = Realtime.countdown($("auto-next"), "", {
    field: "auto_next_at",
    format: function (s) { return s + "…"; },
    doneText: "Новый раунд…",
  });
  var mini = null;     // {doc, el} пока мини-окно открыто
  var pending = null;  // {action: "mark"|"unmark", round, timer} — ждём подтверждения
  var errorText = "";

  // --- отметка: отправить → ждать state → или откатить ----------------------

  function clearPending() {
    if (pending) clearTimeout(pending.timer);
    pending = null;
  }

  function send(action) {
    if (pending) return;
    if (!conn.isOpen()) {
      errorText = "Нет связи с сервером — отметка не отправлена. Переподключаемся, нажмите ещё раз через пару секунд.";
      render();
      return;
    }
    errorText = "";
    pending = {
      action: action,
      round: state.round,
      timer: setTimeout(function () {
        pending = null;
        errorText = "Сервер не ответил — отметка не сохранена. Нажмите ещё раз.";
        render();
      }, CONFIRM_TIMEOUT_MS),
    };
    conn.send(action);
    render();
  }

  function onState(msg) {
    state = msg;
    if (pending && (state.round !== pending.round || state.marked === (pending.action === "mark"))) {
      clearPending();  // сервер подтвердил (или начался новый раунд)
      errorText = "";
    }
    render();
  }

  function onError(msg) {
    if (msg.action !== "mark" && msg.action !== "unmark") return;
    clearPending();
    errorText = msg.action === "mark"
      ? "Не получилось отметить: урок уже закончился."
      : "Не получилось отменить отметку.";
    render();
  }

  // --- отрисовка: страница и мини-окно из одного state ---------------------

  function render() {
    if (ended) return;
    var active = state.status === "active";
    var pipOpen = mini !== null;
    var marking = pending && pending.action === "mark";
    var unmarking = pending && pending.action === "unmark";

    $("round").textContent = state.round;
    $("round-chip").hidden = !active;  // до старта раунд не показываем
    updateCountdown(active && !state.auto_next_at ? state : {});
    updateAuto(active ? state : {});
    $("view-active").hidden = pipOpen || !active;
    $("view-pip").hidden = !pipOpen;

    var btn = $("btn-mark");
    btn.hidden = state.marked;
    btn.disabled = !!marking;
    btn.setAttribute("aria-busy", String(!!marking));
    btn.querySelector(".btn-label").textContent = marking ? "Отмечаю…" : "Записал";
    $("marked-box").hidden = !state.marked;
    $("btn-unmark").disabled = !!unmarking;
    $("btn-unmark").textContent = unmarking ? "Отменяю…" : "Отменить";
    $("mark-error").hidden = !errorText;
    $("mark-error").textContent = errorText;
    renderMini(active, marking, unmarking);
  }

  function renderMini(active, marking, unmarking) {
    if (!mini) return;
    var e = mini.el;
    e.round.textContent = state.round;
    e.mark.hidden = !active || state.marked;
    e.mark.disabled = !!marking;
    e["mark-label"].textContent = marking ? "Отмечаю…" : "Записал";
    e.marked.hidden = !active || !state.marked;
    e.unmark.disabled = !!unmarking;
    e.unmark.textContent = unmarking ? "Отменяю…" : "Отменить";
    e.error.hidden = !errorText;
    e.error.textContent = errorText ? "Не сохранено — нажмите ещё раз" : "";
    e.error.title = errorText;
  }

  function openMini() {
    Pip.open({
      templateId: "mini-student-tpl",
      cssHref: ZAP.miniCss,
      width: 240,
      height: 165,
      onOpen: function (doc) {
        var el = {};
        doc.querySelectorAll("[data-mini]").forEach(function (n) { el[n.getAttribute("data-mini")] = n; });
        el.mark.addEventListener("click", function () { send("mark"); });
        el.unmark.addEventListener("click", function () { send("unmark"); });
        el.expand.addEventListener("click", function () { Pip.close(); window.focus(); });
        mini = { doc: doc, el: el };
        updateCountdown.attach(el.countdown);
        updateAuto.attach(el.auto);
        render();
      },
      onClose: function () {
        if (mini) { updateCountdown.detach(mini.el.countdown); updateAuto.detach(mini.el.auto); }
        mini = null;
        render();
      },
    }).catch(function () {});  // пользователь закрыл/браузер отказал — остаёмся на странице
  }

  // --- конец урока / удаление ----------------------------------------------

  function showMessage(text, linkText, href) {
    ended = true;
    clearPending();
    Pip.close();
    updateCountdown({});
    updateAuto({});
    $("view-active").hidden = true;
    $("view-pip").hidden = true;
    $("round-chip").hidden = true;
    $("conn").hidden = true;
    $("message-text").textContent = text;
    $("message-link").textContent = linkText;
    $("message-link").href = href;
    $("view-message").hidden = false;
  }

  function kicked() {
    showMessage("Вы удалены из урока", "Ввести код", ZAP.joinUrl);
    setTimeout(function () { location.href = ZAP.joinUrl; }, 2500);
  }

  var conn = Realtime.connect(ZAP.code, {
    role: "student",
    pingMs: PING_MS,
    onMessage: function (msg) {
      if (msg.type === "state") onState(msg);
      else if (msg.type === "error") onError(msg);
      else if (msg.type === "kicked") kicked();
      else if (msg.type === "finished") showMessage("Урок завершён", "На главную", "/");
    },
    onStatus: function (ok) { if (!ended) $("conn").hidden = ok; },
    onEnd: function (reason) {
      if (ended) return;
      if (reason === "kicked") kicked();
      else if (reason === "finished") showMessage("Урок завершён", "На главную", "/");
      else showMessage("Урок не найден", "Ввести код", ZAP.joinUrl);
    },
  });

  $("btn-mark").addEventListener("click", function () { send("mark"); });
  $("btn-unmark").addEventListener("click", function () { send("unmark"); });
  if (Pip.supported) {
    $("btn-pip").addEventListener("click", openMini);
    $("btn-pip-return").addEventListener("click", function () { Pip.close(); });
  }

  render();
})();
