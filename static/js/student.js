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
  var pending = null;  // {action: "mark"|"unmark"|"away"|"back"|"ask"|"resolve", round, timer} — ждём подтверждения
  var errorText = "";
  var choiceOpen = false;  // раскрыт выбор «Вопрос / Ошибка в коде» (страница или мини-окно)

  // когда state подтверждает действие
  var CONFIRMED = {
    mark: function (s) { return s.marked; },
    unmark: function (s) { return !s.marked; },
    away: function (s) { return s.away; },
    back: function (s) { return !s.away; },
    ask: function (s) { return !!s.question; },
    resolve: function (s) { return !s.question; },
  };
  var ERRORS = {
    mark: "Не получилось отметить: урок уже закончился.",
    unmark: "Не получилось отменить отметку.",
    away: "Не получилось отметить, что вы вышли. Нажмите ещё раз.",
    back: "Не получилось вернуться. Нажмите ещё раз.",
    ask: "Вопрос не отправлен. Нажмите ещё раз.",
    resolve: "Не получилось снять вопрос. Нажмите ещё раз.",
  };

  function ordinal(n) { return n + "-й"; }  // 1-й, 2-й, 3-й… — в мужском роде у всех чисел «-й»

  // --- отметка: отправить → ждать state → или откатить ----------------------

  function clearPending() {
    if (pending) clearTimeout(pending.timer);
    pending = null;
  }

  function send(action, extra) {
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
    conn.send(action, extra);
    render();
  }

  function ask(type) {
    choiceOpen = false;
    send("ask", { question_type: type });
  }

  function onState(msg) {
    state = msg;
    var markAction = pending && (pending.action === "mark" || pending.action === "unmark");
    if (pending && ((markAction && state.round !== pending.round) || CONFIRMED[pending.action](state))) {
      clearPending();  // сервер подтвердил (или начался новый раунд)
      errorText = "";
    }
    render();
  }

  function onError(msg) {
    if (!ERRORS[msg.action]) return;
    clearPending();
    errorText = ERRORS[msg.action];
    render();
  }

  // --- отрисовка: страница и мини-окно из одного state ---------------------

  function render() {
    if (ended) return;
    var active = state.status === "active";
    var pipOpen = mini !== null;
    var marking = pending && pending.action === "mark";
    var unmarking = pending && pending.action === "unmark";
    var away = !!state.away;
    var busy = pending && (pending.action === "away" || pending.action === "back");

    $("round").textContent = state.round;
    $("round-chip").hidden = !active;
    // вышедшему отсчёты не нужны — он не участвует в раунде
    updateCountdown(active && !away && !state.auto_next_at ? state : {});
    updateAuto(active && !away ? state : {});
    $("view-active").hidden = pipOpen || !active || away;
    $("view-away").hidden = pipOpen || !active || !away;
    $("view-pip").hidden = !pipOpen;
    $("btn-away").disabled = !!busy;
    $("btn-back").disabled = !!busy;
    $("btn-back").querySelector(".btn-label").textContent = busy ? "Возвращаюсь…" : "Я вернулся";

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
    // ошибка «Я вышел/вернулся» видна и на экране «Вы вышли»
    if (away && errorText) $("view-away").querySelector(".hint").textContent = errorText;
    else $("view-away").querySelector(".hint").textContent = "Агай видит, что вас нет. Вернётесь — нажмите кнопку.";

    // ✋ вопрос: открыт → «Вопрос отправлен · Вы N-й в очереди» + «Разобрался»; «Записал» работает как обычно
    var asked = !!state.question;
    var asking = pending && pending.action === "ask";
    var resolving = pending && pending.action === "resolve";
    if (asked) choiceOpen = false;
    var queue = state.queue_pos ? "Вы " + ordinal(state.queue_pos) + " в очереди" : "";
    $("question-open").hidden = !asked;
    $("queue-text").textContent = queue;
    $("btn-resolve").disabled = !!resolving;
    $("btn-resolve").querySelector(".btn-label").textContent = resolving ? "Снимаю…" : "Разобрался";
    $("btn-ask").hidden = asked;
    $("btn-ask").disabled = !!asking;
    $("btn-ask").setAttribute("aria-expanded", String(choiceOpen));
    $("ask-choice").hidden = !choiceOpen || asked;
    renderMini(active, marking, unmarking, away, busy, asked, asking, resolving, queue);
  }

  function renderMini(active, marking, unmarking, away, busy, asked, asking, resolving, queue) {
    if (!mini) return;
    var e = mini.el;
    e.round.textContent = state.round;
    e.mark.hidden = !active || away || state.marked;
    e.mark.disabled = !!marking;
    e["mark-label"].textContent = marking ? "Отмечаю…" : "Записал";
    e.marked.hidden = !active || away || !state.marked;
    e["away-box"].hidden = !active || !away;
    e.bottom.hidden = !active || away;
    e.ask.hidden = asked;
    e.ask.disabled = !!asking;
    e.ask.setAttribute("aria-expanded", String(choiceOpen));
    e["q-choice"].hidden = !choiceOpen || asked || away;
    e["q-open"].hidden = !asked || away;
    e["q-text"].textContent = queue || "Вопрос отправлен";
    e.resolve.disabled = !!resolving;
    e.resolve.textContent = resolving ? "Снимаю…" : "Разобрался";
    e.away.disabled = !!busy;
    e.back.disabled = !!busy;
    e["back-label"].textContent = busy ? "Возвращаюсь…" : "Я вернулся";
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
      height: 215,
      onOpen: function (doc) {
        var el = {};
        doc.querySelectorAll("[data-mini]").forEach(function (n) { el[n.getAttribute("data-mini")] = n; });
        el.mark.addEventListener("click", function () { send("mark"); });
        el.unmark.addEventListener("click", function () { send("unmark"); });
        el.away.addEventListener("click", function () { send("away"); });
        el.back.addEventListener("click", function () { send("back"); });
        el.ask.addEventListener("click", toggleChoice);
        el["ask-question"].addEventListener("click", function () { ask("question"); });
        el["ask-bug"].addEventListener("click", function () { ask("bug"); });
        el.resolve.addEventListener("click", function () { send("resolve"); });
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
  $("btn-away").addEventListener("click", function () { send("away"); });
  function toggleChoice() { choiceOpen = !choiceOpen; render(); }
  $("btn-ask").addEventListener("click", toggleChoice);
  $("ask-question").addEventListener("click", function () { ask("question"); });
  $("ask-bug").addEventListener("click", function () { ask("bug"); });
  $("btn-resolve").addEventListener("click", function () { send("resolve"); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && choiceOpen) { choiceOpen = false; render(); $("btn-ask").focus(); }
  });
  $("btn-back").addEventListener("click", function () { send("back"); });
  if (Pip.supported) {
    $("btn-pip").addEventListener("click", openMini);
    $("btn-pip-return").addEventListener("click", function () { Pip.close(); });
  }

  render();
})();
