// static/js/sortableTable.js
function getCellValue(cell) {
  const v = cell?.getAttribute?.("data-sort-value");
  return v != null ? v : (cell?.textContent ?? "").trim();
}

function asNumber(v) {
  if (typeof v === "number") return v;
  const s = String(v).replace(/\s+/g, "").replace(",", ".");
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : NaN;
}

function asTime(v) {
  if (typeof v === "number") return v;
  const s = String(v).trim();
  
  // Формат времени HH:MM или HH:MM:SS
  const timeRegex = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/;
  const m = s.match(timeRegex);
  
  if (m) {
    const [, hours, minutes, seconds = "0"] = m;
    const totalSeconds = parseInt(hours) * 3600 + parseInt(minutes) * 60 + parseInt(seconds);
    return totalSeconds;
  }
  
  return NaN;
}

function asDate(v) {
  if (v instanceof Date) return v;
  const s = String(v).trim();
  
  // Сначала проверяем, не является ли это чистым временем
  const timeValue = asTime(s);
  if (!isNaN(timeValue)) {
    // Для чистого времени создаем дату с фиксированной датой (1970-01-01)
    return new Date(0, 0, 1, Math.floor(timeValue / 3600), Math.floor((timeValue % 3600) / 60), timeValue % 60);
  }
  
  // Пытаемся разобрать разные форматы дат
  const formats = [
    // DD.MM.YYYY HH:MM:SS
    /^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$/,
    // YYYY-MM-DD HH:MM:SS
    /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$/,
    // MM/DD/YYYY HH:MM:SS
    /^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$/
  ];
  
  for (const regex of formats) {
    const m = s.match(regex);
    if (m) {
      let year, month, day, hour = 0, minute = 0, second = 0;
      
      if (regex === formats[0]) {
        // DD.MM.YYYY
        [, day, month, year, hour, minute, second] = m;
      } else if (regex === formats[1]) {
        // YYYY-MM-DD
        [, year, month, day, hour, minute, second] = m;
      } else if (regex === formats[2]) {
        // MM/DD/YYYY
        [, month, day, year, hour, minute, second] = m;
      }
      
      // Преобразуем в числа и создаем дату
      year = parseInt(year);
      month = parseInt(month) - 1; // месяцы в JS: 0-11
      day = parseInt(day);
      hour = parseInt(hour || 0);
      minute = parseInt(minute || 0);
      second = parseInt(second || 0);
      
      const date = new Date(year, month, day, hour, minute, second);
      if (!isNaN(date.getTime())) {
        return date;
      }
    }
  }
  
  // Пробуем стандартный парсер
  const timestamp = Date.parse(s);
  if (!isNaN(timestamp)) {
    return new Date(timestamp);
  }
  
  // Если ничего не помогло, возвращаем невалидную дату
  return new Date(NaN);
}

const defaultTypeParsers = {
  text: (v) => String(v).toLowerCase(),
  number: (v) => asNumber(v),
  time: (v) => asTime(v),
  date: (v) => {
    const date = asDate(v);
    return isNaN(date.getTime()) ? Infinity : date.getTime();
  },
};

function autodetectType(values) {
  const sample = values.slice(0, 10);
  let validNumbers = 0;
  let validDates = 0;
  let validTimes = 0;
  
  sample.forEach(v => {
    const num = asNumber(v);
    const date = asDate(v);
    const time = asTime(v);
    
    if (!isNaN(num)) validNumbers++;
    if (!isNaN(date.getTime())) validDates++;
    if (!isNaN(time)) validTimes++;
  });
  
  // Если большинство значений - чистое время, используем тип "time"
  if (validTimes >= validDates && validTimes > sample.length * 0.7) {
    return "time";
  }
  // Предпочитаем даты, если большинство значений - валидные даты
  if (validDates >= validNumbers && validDates > sample.length * 0.7) {
    return "date";
  }
  if (validNumbers === sample.length && sample.length > 0) {
    return "number";
  }
  return "text";
}

export class SortableTable {
  constructor(table, options = {}) {
    if (!(table instanceof HTMLTableElement)) throw new Error("SortableTable: table must be HTMLTableElement");
    this.table = table;
    this.tbody = table.tBodies[0];
    if (!this.tbody) throw new Error("SortableTable: table requires <tbody>");
    this.locale = options.locale || undefined;
    this.columns = options.columns || [];
    this.state = { index: -1, dir: "asc" };
    this.headers = Array.from(table.tHead?.rows?.[0]?.cells || []);
    this.headers.forEach((th, i) => this.decorateHeader(th, i));

    // ▼ Всегда сортируем по первому столбцу (если он есть и есть строки)
    if (this.headers.length > 0 && this.tbody.rows.length > 0) {
      this.sortBy(0, "asc");
    }
  }

  decorateHeader(th, index) {
    if (th.dataset.sortable === "false" || th.classList.contains("no-sort")) return;
    th.style.cursor = "pointer";
    th.setAttribute("role", "columnheader");
    th.setAttribute("aria-sort", "none");
    th.dataset.sortable = "true";
    const arrow = document.createElement("span");
    arrow.className = "st-arrow";
    arrow.style.marginLeft = "6px";
    arrow.style.opacity = "0.6";
    arrow.style.display = "none";      // скрыто по умолчанию
    th.appendChild(arrow);
    th.addEventListener("click", (e) => {
      e.preventDefault();
      const current = (this.state.index === index) ? this.state.dir : null;
      const nextDir = current === "asc" ? "desc" : "asc";
      this.sortBy(index, nextDir);
    });
  }

  sortBy(index, dir = "asc") {
    const rows = Array.from(this.tbody.rows);
    if (!rows.length) return;
    
    // Собираем все данные из таблицы (все страницы)
    const data = rows.map((tr, origIndex) => {
      const cell = tr.cells[index];
      const raw = this.getRawValue(cell, tr, index);
      return { tr, origIndex, raw };
    });
    
    let type = this.getColumnType(index, data.map(d => d.raw));
    const comparator = this.getComparator(index, type);
    
    // Сортируем ВСЕ данные
    data.sort((a, b) => {
      const cmp = comparator(a.raw, b.raw);
      if (cmp !== 0) return cmp;
      return a.origIndex - b.origIndex;
    });
    
    if (dir === "desc") data.reverse();
    
    // Перемещаем все строки в отсортированном порядке
    const frag = document.createDocumentFragment();
    data.forEach(d => frag.appendChild(d.tr));
    
    // Очищаем tbody и вставляем отсортированные строки
    while (this.tbody.firstChild) {
      this.tbody.removeChild(this.tbody.firstChild);
    }
    this.tbody.appendChild(frag);
    
    this.updateHeaders(index, dir);
    this.state = { index, dir };
    
    // Вызываем событие для обновления пагинации
    this.table.dispatchEvent(new CustomEvent('sort', {
      detail: { index, dir }
    }));
  }

  getRawValue(cell, row, index) {
    const colCfg = this.columns[index];
    if (colCfg?.accessor) return colCfg.accessor(cell, row);
    
    // Специальная обработка для столбца статуса
    if (cell.classList.contains('col-status')) {
      const statusDot = cell.querySelector('.status-dot');
      if (statusDot) {
        return statusDot.classList.contains('active') ? 1 : 0;
      }
    }
    
    return getCellValue(cell);
  }

  getColumnType(index, values) {
    const optType = this.columns[index]?.type;
    if (optType) return optType;
    const th = this.headers[index];
    const thType = th?.dataset?.type;
    if (thType) return thType;
    return autodetectType(values);
  }

  getComparator(index, type) {
    const colCfg = this.columns[index];
    if (colCfg?.comparator) return colCfg.comparator;
    
    if (type === "number") {
      return (a, b) => {
        const na = asNumber(a), nb = asNumber(b);
        if (!Number.isFinite(na) && !Number.isFinite(nb)) return 0;
        if (!Number.isFinite(na)) return 1;
        if (!Number.isFinite(nb)) return -1;
        return na - nb;
      };
    }
    
    if (type === "time") {
      return (a, b) => {
        const ta = asTime(a), tb = asTime(b);
        const taValid = !isNaN(ta), tbValid = !isNaN(tb);
        
        if (!taValid && !tbValid) return 0;
        if (!taValid) return 1;  // невалидное время в конец
        if (!tbValid) return -1; // невалидное время в конец
        
        return ta - tb;
      };
    }
    
    if (type === "date") {
      return (a, b) => {
        const ta = asDate(a).getTime(), tb = asDate(b).getTime();
        const taValid = !isNaN(ta), tbValid = !isNaN(tb);
        
        if (!taValid && !tbValid) return 0;
        if (!taValid) return 1;  // невалидные даты в конец
        if (!tbValid) return -1; // невалидные даты в конец
        
        return ta - tb;
      };
    }
    
    return (a, b) => String(a).localeCompare(String(b), this.locale, { sensitivity: "base", numeric: true });
  }

  updateHeaders(activeIndex, dir) {
    this.headers.forEach((th, i) => {
      const arrow = th.querySelector(".st-arrow");
      if (!arrow) return;
      if (i === activeIndex) {
        th.setAttribute("aria-sort", dir === "asc" ? "ascending" : "descending");
        arrow.textContent = dir === "asc" ? "▲" : "▼";
        arrow.style.display = "inline";   // показываем стрелку
      } else {
        th.setAttribute("aria-sort", "none");
        arrow.textContent = "";
        arrow.style.display = "none";     // скрываем стрелку
      }
    });
  }
}

export function initSortableTables(selector = 'table[data-sortable]') {
  const instances = [];
  document.querySelectorAll(selector).forEach(tbl => {
    try { instances.push(new SortableTable(tbl)); } catch {}
  });
  return instances;
}