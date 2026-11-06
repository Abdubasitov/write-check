// Общий слой реального времени. Пока только режим ws (polling — позже).
(function () {
  "use strict";

  var FINAL_CODES = { 4000: "finished", 4001: "kicked", 4003: "denied" };
  var RETRY_MS = 1500;

  // handlers.role — "teacher" | "student": роль задаёт страница, а не cookies.
  // handlers.pingMs — студент шлёт ping, чтобы сервер знал, что вкладка жива.
  function connect(code, handlers) {
    var ws = null;
    var stopped = false;
    var queue = [];

    if (handlers.pingMs) {
      setInterval(function () {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send('{"type":"ping"}');  // без очереди
      }, handlers.pingMs);
    }

    function open() {
      var proto = location.protocol === "https:" ? "wss://" : "ws://";
      ws = new WebSocket(proto + location.host + "/ws/rooms/" + encodeURIComponent(code) + "/" + handlers.role + "/");

      ws.onopen = function () {
        handlers.onStatus && handlers.onStatus(true);
        while (queue.length) ws.send(queue.shift());
      };

      ws.onmessage = function (e) {
        var data;
        try { data = JSON.parse(e.data); } catch (err) { return; }
        handlers.onMessage(data);
      };

      ws.onclose = function (e) {
        if (stopped) return;
        var reason = FINAL_CODES[e.code];
        if (reason) {
          stopped = true;
          handlers.onEnd && handlers.onEnd(reason);
          return;
        }
        handlers.onStatus && handlers.onStatus(false);
        setTimeout(open, RETRY_MS);
      };
    }

    open();

    return {
      send: function (type, extra) {
        var msg = JSON.stringify(Object.assign({ type: type }, extra || {}));
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(msg);
        else queue.push(msg);
      },
      isOpen: function () { return !!ws && ws.readyState === WebSocket.OPEN; },
    };
  }

  // Обратный отсчёт до автосброса. Сервер присылает deadline и своё now —
  // по разнице учитываем расхождение часов компьютера и сервера.
  // Возвращает update(state); update.attach(el)/detach(el) — доп. места (мини-окно).
  function countdown(el, prefix) {
    var els = [el];
    var deadlineMs = null;
    var offsetMs = 0;

    function fmt(sec) {
      var m = Math.floor(sec / 60), s = sec % 60;
      return m + ":" + (s < 10 ? "0" : "") + s;
    }

    function tick() {
      var text = null;
      if (deadlineMs !== null) {
        var left = Math.max(0, Math.ceil((deadlineMs - (Date.now() + offsetMs)) / 1000));
        text = left > 0 ? fmt(left) : "…";
      }
      els.forEach(function (e) {
        var p = e.getAttribute("data-prefix");
        if (p === null) p = prefix === undefined ? "Сброс через " : prefix;
        e.hidden = text === null;
        // внутри может быть иконка — пишем только в .cd-text
        if (text !== null) (e.querySelector(".cd-text") || e).textContent = text === "…" ? "Сброс…" : p + text;
      });
    }

    setInterval(tick, 250);
    function update(state) {
      deadlineMs = state.deadline ? state.deadline * 1000 : null;
      if (state.now) offsetMs = state.now * 1000 - Date.now();
      tick();
    }
    update.attach = function (e) { if (e && els.indexOf(e) === -1) els.push(e); tick(); };
    update.detach = function (e) { els = els.filter(function (x) { return x !== e; }); };
    return update;
  }

  // Иконка Lucide из спрайта (static/icons/sprite.svg, ISC). doc — для мини-окна.
  var SVG_NS = "http://www.w3.org/2000/svg";
  function icon(name, doc) {
    doc = doc || document;
    var svg = doc.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var use = doc.createElementNS(SVG_NS, "use");
    use.setAttribute("href", new URL(window.ICONS, location.href).href + "#" + name);
    svg.append(use);
    return svg;
  }

  // Кнопка = иконка + подпись
  function setIconLabel(el, name, label) {
    el.textContent = "";
    var span = el.ownerDocument.createElement("span");
    span.textContent = label;
    el.append(icon(name, el.ownerDocument), span);
  }

  window.Realtime = { connect: connect, countdown: countdown, icon: icon, setIconLabel: setIconLabel };
})();
