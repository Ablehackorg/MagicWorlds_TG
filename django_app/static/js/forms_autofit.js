// Автоподгонка ширины для коротких инпутов (text, time, number),
// чтобы не занимали всю строку. Ненавязчиво: уважает .fit и тип поля.

(function () {
  function shouldAutoFit(el) {
    if (!(el instanceof HTMLInputElement)) return false;
    const t = (el.getAttribute('type') || 'text').toLowerCase();
    // подгоняем только понятные короткие поля
    return ['text', 'time', 'number', 'date'].includes(t) || el.classList.contains('fit');
  }

  function measureTextWidth(text, el) {
    const s = window.getComputedStyle(el);
    const canvas = measureTextWidth._c || (measureTextWidth._c = document.createElement('canvas'));
    const ctx = canvas.getContext('2d');
    ctx.font = [s.fontStyle, s.fontVariant, s.fontWeight, s.fontSize, s.fontFamily].join(' ');
    const m = ctx.measureText(text || el.placeholder || '');
    // Добавим небольшой люфт + паддинги
    const padding = parseFloat(s.paddingLeft) + parseFloat(s.paddingRight) + 12;
    return Math.ceil(m.width + padding);
  }

  function applyWidth(el) {
    if (!shouldAutoFit(el)) return;
    el.style.display = 'inline-block';
    el.style.width = 'auto';
    el.style.maxWidth = '100%';

    // auto → посчитаем фактическую ширину
    const w = measureTextWidth(el.value, el);
    // ограничим минималкой и не вываливаемся
    const min = el.getAttribute('data-minch') ? parseInt(el.getAttribute('data-minch'), 10) : 10;
    const minPx = Math.max(w, min * 8); // грубая оценка ch→px
    el.style.width = Math.min(Math.max(minPx, 0), el.parentElement.clientWidth || 9999) + 'px';
  }

  function init() {
    const inputs = document.querySelectorAll('input.form-control, input.form-control.fit');
    inputs.forEach(el => {
      if (!shouldAutoFit(el)) return;
      applyWidth(el);
      el.addEventListener('input', () => applyWidth(el));
      el.addEventListener('change', () => applyWidth(el));
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Пересчитать при ресайзе окна
  window.addEventListener('resize', () => {
    document.querySelectorAll('input.form-control, input.form-control.fit').forEach(applyWidth);
  });
})();
