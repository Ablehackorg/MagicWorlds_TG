// static/admin_panel/js/valueFilters.js
// Кнопки-фильтры по значениям столбцов. Совместимо с внешней пагинацией.

export function initValueFilters(opts) {
  const {
    tableSelector,
    columns = [],                         // заголовки <th>, по которым строятся кнопки
    controlsContainerSelector,            // контейнер, где размещена кнопка "Добавить"
    beforeSelector,                       // селектор элемента, перед которым вставляется панель
    resort = null,                        // колбэк для пересортировки (например, () => initSortableTables())
    debug = false,
  } = opts || {};

  const log = (...a) => { if (debug) console.log("[valueFilters]", ...a); };
  const norm = (s) => String(s ?? "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();

  // Инлайновые стили для панели и кнопок.
  (() => {
    const id = "vf-inline-style";
    if (!document.getElementById(id)) {
      const st = document.createElement("style");
      st.id = id;
      st.textContent = `
        .value-filter-bar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-right:8px;}
        .vf-btn{border-radius:12px !important;}
      `;
      document.head.appendChild(st);
    }
  })();

  const table = document.querySelector(tableSelector);
  if (!table) { log("table not found:", tableSelector); return; }

  const thead = table.tHead || table.querySelector("thead");
  const tbody = table.tBodies?.[0] || table.querySelector("tbody");
  if (!thead || !tbody) { log("thead/tbody missing"); return; }

  // Добавляем data-атрибут для фильтрации
  table.dataset.filterActive = "false";
  table.dataset.filterColumn = "";
  table.dataset.filterValue = "";

  // Определение индексов столбцов по заголовкам.
  const ths = Array.from(thead.querySelectorAll("th"));
  const thTexts = ths.map(th => norm(th.textContent));
  const colIndices = [];
  for (const name of columns) {
    const idx = thTexts.findIndex(t => t === norm(name));
    if (idx !== -1) colIndices.push({ name, index: idx });
    else log(`header "${name}" not found`, thTexts);
  }
  if (!colIndices.length) { log("no matching columns"); return; }

  // Сбор уникальных значений из ВСЕХ строк таблицы
  const collectValues = () => {
    const map = new Map();
    const allRows = Array.from(table.querySelectorAll("tbody tr"));
    
    for (const { name, index } of colIndices) {
      const s = new Set();
      allRows.forEach(tr => {
        const cell = tr.children[index];
        const v = norm(cell?.getAttribute?.("data-sort-value") ?? cell?.textContent);
        if (v) s.add(v);
      });
      map.set(name, Array.from(s).sort((a,b)=>a.localeCompare(b)));
    }
    return map;
  };
  
  const valuesByColumn = collectValues();

  // Вставка панели кнопок перед "Добавить".
  const host = document.querySelector(controlsContainerSelector);
  if (!host) { log("controlsHost not found:", controlsContainerSelector); return; }
  const beforeEl = host.querySelector(beforeSelector);

  const bar = document.createElement("div");
  bar.className = "value-filter-bar";
  if (beforeEl) host.insertBefore(bar, beforeEl); else host.appendChild(bar);

  let allButtons = [];
  let active = null;

  const resetButtons = () => {
    allButtons.forEach(b => {
      b.classList.remove("active", "border-primary", "text-primary");
      b.classList.add("btn-outline-secondary");
    });
  };
  
  const activateButton = (btn) => {
    btn.classList.add("active", "border-primary", "text-primary");
    btn.classList.remove("btn-outline-secondary");
  };

  const applyFilter = (colIndex, value) => {
    // Устанавливаем фильтр в data-атрибуты таблицы
    table.dataset.filterActive = "true";
    table.dataset.filterColumn = colIndex;
    table.dataset.filterValue = value;
    
    // Вызываем событие для обновления пагинации
    table.dispatchEvent(new CustomEvent('filter', {
      detail: { colIndex, value }
    }));
  };

  const clearFilter = () => {
    table.dataset.filterActive = "false";
    table.dataset.filterColumn = "";
    table.dataset.filterValue = "";
    
    // Вызываем событие для обновления пагинации
    table.dispatchEvent(new CustomEvent('filter', {
      detail: { colIndex: null, value: null }
    }));
  };

  // Рендер плоского ряда кнопок (без заголовков столбцов).
  for (const { name, index } of colIndices) {
    const vals = valuesByColumn.get(name) || [];
    for (const val of vals) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-outline-secondary btn-sm vf-btn";
      btn.textContent = val;
      btn.title = name;
      btn.dataset.filterColumn = name;
      btn.dataset.filterValue = val;

      btn.addEventListener("click", () => {
        // Повторный клик по активной кнопке — отмена фильтра
        if (btn.classList.contains("active")) {
          active = null;
          resetButtons();
          clearFilter();
          return;
        }
        
        // Переключение на другой фильтр
        resetButtons();
        activateButton(btn);
        active = { colIndex: index, value: val };
        applyFilter(index, val);
      });

      bar.appendChild(btn);
      allButtons.push(btn);
    }
  }

  log("buttons:", allButtons.length);
}