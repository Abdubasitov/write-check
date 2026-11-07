// Мини-окно поверх всех окон (Document Picture-in-Picture, Chrome/Edge 116+).
// Окно не держит своего соединения: страница сама рисует state и в себя, и в окно.
(function () {
  "use strict";

  var supported = "documentPictureInPicture" in window;
  var current = null;

  // Открывать только из обработчика клика — иначе браузер откажет.
  // opts: {templateId, cssHref, width, height, onOpen(doc), onClose()}
  function open(opts) {
    if (!supported) return Promise.resolve(null);
    if (current) { current.focus && current.focus(); return Promise.resolve(current); }
    return window.documentPictureInPicture
      .requestWindow({ width: opts.width, height: opts.height })
      .then(function (pip) {
        var doc = pip.document;
        doc.title = document.title;
        var meta = doc.createElement("meta");
        meta.name = "color-scheme";
        meta.content = "dark";  // только тёмная тема
        doc.documentElement.style.background = "#141513";  // без белой вспышки до загрузки mini.css
        doc.head.append(meta);
        var link = doc.createElement("link");
        link.rel = "stylesheet";
        link.href = new URL(opts.cssHref, location.href).href;
        doc.head.append(link);

        var tpl = document.getElementById(opts.templateId);
        doc.body.append(doc.importNode(tpl.content, true));
        // ссылки на спрайт иконок — абсолютные, у окна свой адрес документа
        doc.querySelectorAll("use").forEach(function (u) {
          u.setAttribute("href", new URL(u.getAttribute("href"), location.href).href);
        });

        current = pip;
        pip.addEventListener("pagehide", function () {
          current = null;
          opts.onClose && opts.onClose();
        });
        opts.onOpen && opts.onOpen(doc, pip);
        return pip;
      });
  }

  function close() {
    if (current) current.close();  // pagehide вызовет onClose
  }

  function isOpen() { return current !== null; }

  // Скрыть кнопки мини-окна, если браузер не умеет
  if (!supported) {
    document.addEventListener("DOMContentLoaded", function () {
      document.querySelectorAll("[data-pip-only]").forEach(function (el) { el.remove(); });
    });
  }

  window.Pip = { supported: supported, open: open, close: close, isOpen: isOpen };
})();
