// colToggles.js - версия с исправленными стилями выравнивания
function initColToggles() {
  document.querySelectorAll(".col-toggle-btn").forEach((btn, idx) => {
    const h1 = btn.closest(".d-flex")?.querySelector("h1");
    if (!h1) return;

    const table = btn.closest(".container-fluid")?.querySelector("table[data-sortable]");
    if (!table) return;

    // Создаём уникальный ключ для этой таблицы
    const tableId = table.id || `table-${idx}-${h1.textContent.trim().replace(/\s+/g, '-').toLowerCase()}`;
    const storageKey = `colToggles-${tableId}`;

    // Загружаем сохранённое состояние
    let hiddenColumns = [];
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        hiddenColumns = JSON.parse(saved);
      }
    } catch (e) {
      console.warn('Ошибка загрузки состояния столбцов:', e);
    }

    // создаём меню
    const menu = document.createElement("div");
    menu.className = "col-toggle-menu";
    menu.innerHTML = `
      <div class="d-flex justify-content-between align-items-center mb-2">
        <strong>Отображение столбцов</strong>
        <button type="button" class="btn btn-sm btn-outline-secondary reset-cols">Сбросить</button>
      </div>
      <div class="col-toggle-list mt-2"></div>
    `;
    document.body.appendChild(menu);

    const list = menu.querySelector(".col-toggle-list");

    // генерируем чекбоксы
    const headers = table.querySelectorAll("thead th");
    const checkboxes = [];
    
    headers.forEach((th, colIdx) => {
      const name = th.textContent.trim() || `Столбец ${colIdx + 1}`;
      const id = `col-toggle-${idx}-${colIdx}`;
      const isHidden = hiddenColumns.includes(colIdx);
      
      const item = document.createElement("div");
      item.className = "form-check col-toggle-item";
      item.innerHTML = `
        <label class="col-toggle-custom-checkbox">
          <input type="checkbox" class="col-toggle-checkbox" id="${id}" data-col="${colIdx}" ${isHidden ? '' : 'checked'}>
          <span class="col-toggle-checkmark"></span>
          <span class="col-toggle-label">${name}</span>
        </label>
      `;
      list.appendChild(item);

      const checkbox = item.querySelector('input');
      checkboxes.push(checkbox);

      // Применяем сохранённое состояние при инициализации
      if (isHidden) {
        table.querySelectorAll("tr").forEach((row) => {
          const cells = row.querySelectorAll("th, td");
          if (cells[colIdx]) {
            cells[colIdx].style.display = "none";
          }
        });
      }
    });

    // Функция сохранения состояния
    function saveColumnState() {
      const hiddenCols = checkboxes
        .map((cb, idx) => ({ cb, idx }))
        .filter(({ cb }) => !cb.checked)
        .map(({ idx }) => idx);
      
      try {
        localStorage.setItem(storageKey, JSON.stringify(hiddenCols));
      } catch (e) {
        console.warn('Ошибка сохранения состояния столбцов:', e);
      }
    }

    // Функция сброса состояния
    function resetColumnState() {
      // Показываем все столбцы
      table.querySelectorAll("tr").forEach((row) => {
        const cells = row.querySelectorAll("th, td");
        cells.forEach(cell => {
          cell.style.display = "";
        });
      });
      
      // Сбрасываем все чекбоксы
      checkboxes.forEach(cb => {
        cb.checked = true;
      });
      
      // Удаляем сохранённое состояние
      try {
        localStorage.removeItem(storageKey);
      } catch (e) {
        console.warn('Ошибка сброса состояния столбцов:', e);
      }
    }

    // показать/спрятать меню
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (menu.style.display === "block") {
        menu.style.display = "none";
        return;
      }
      
      // Сначала показываем меню для вычисления размеров
      menu.style.display = "block";
      menu.style.visibility = "hidden"; // скрываем для вычислений
      
      const rect = btn.getBoundingClientRect();
      const menuRect = menu.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      
      // Позиционируем справа от кнопки
      let left = rect.right + 5;
      let top = rect.bottom;
      
      // Если не помещается справа - позиционируем слева
      if (left + menuRect.width > viewportWidth - 10) {
        left = rect.left - menuRect.width - 5;
      }
      
      // Если не помещается снизу - позиционируем сверху
      if (top + menuRect.height > viewportHeight - 10) {
        top = rect.top - menuRect.height - 5;
      }
      
      // Гарантируем, что меню не выйдет за границы viewport
      left = Math.max(10, Math.min(left, viewportWidth - menuRect.width - 10));
      top = Math.max(10, Math.min(top, viewportHeight - menuRect.height - 10));
      
      // Устанавливаем финальную позицию
      menu.style.position = "fixed";
      menu.style.left = left + "px";
      menu.style.top = top + "px";
      menu.style.visibility = "visible";
      menu.style.zIndex = "10000";
    });

    // клик вне меню → закрыть
    document.addEventListener("click", (e) => {
      if (!menu.contains(e.target) && !btn.contains(e.target)) {
        menu.style.display = "none";
      }
    });

    // обработка чекбоксов
    list.addEventListener("change", (e) => {
      if (!e.target.matches("input[data-col]")) return;
      const colIdx = +e.target.dataset.col;
      const show = e.target.checked;
      
      table.querySelectorAll("tr").forEach((row) => {
        const cells = row.querySelectorAll("th, td");
        if (cells[colIdx]) {
          cells[colIdx].style.display = show ? "" : "none";
        }
      });
      
      saveColumnState();
    });

    // обработка кнопки сброса
    menu.querySelector('.reset-cols').addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      resetColumnState();
      menu.style.display = 'none';
    });

    // Закрытие меню при нажатии Escape
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && menu.style.display === 'block') {
        menu.style.display = 'none';
      }
    });

    // Закрытие меню при изменении размера окна
    window.addEventListener('resize', () => {
      if (menu.style.display === 'block') {
        menu.style.display = 'none';
      }
    });

    // Закрытие меню при скролле
    window.addEventListener('scroll', () => {
      if (menu.style.display === 'block') {
        menu.style.display = 'none';
      }
    });
  });
}

// Стили для меню
const colToggleStyles = `
.col-toggle-menu {
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 12px;
  box-shadow: 0 4px 15px rgba(0,0,0,0.15);
  max-height: 400px;
  overflow-y: auto;
  display: none;
  min-width: 250px;
  max-width: 300px;
}

.col-toggle-list {
  max-height: 300px;
  overflow-y: auto;
}

/* Стили для кастомных галочек */
.col-toggle-custom-checkbox {
  display: flex !important;
  align-items: center !important;
  padding: 8px 4px !important;
  margin: 0 !important;
  cursor: pointer;
  position: relative;
  width: 100%;
  min-height: 32px;
  border-radius: 6px;
}

.col-toggle-custom-checkbox:hover {
  background-color: #f8f9fa;
}

.col-toggle-custom-checkbox input {
  position: absolute;
  opacity: 0;
  cursor: pointer;
  height: 0;
  width: 0;
}

/* Галочка - большая и очень заметная */
.col-toggle-checkmark {
  position: relative;
  display: inline-block;
  margin-right: 12px;
  width: 22px; /* Увеличено */
  height: 22px; /* Увеличено */
  flex-shrink: 0;
  background-color: transparent;
  border: 2px solid transparent;
  border-radius: 4px;
}

/* Неактивная галочка - очень жирная и тёмная серая */
.col-toggle-checkmark:before {
  content: "";
  position: absolute;
  left: 6px; /* Скорректировано */
  top: 2px; /* Скорректировано */
  width: 8px; /* Увеличено */
  height: 14px; /* Увеличено */
  border: solid #666; /* Темнее серый */
  border-width: 0 4px 4px 0; /* Увеличена толщина */
  transform: rotate(45deg);
  opacity: 0.9; /* Более видимая */
  transition: all 0.2s;
}

/* При наведении на неактивную - ещё темнее */
.col-toggle-custom-checkbox:hover .col-toggle-checkmark:before {
  opacity: 1;
  border-color: #444; /* Ещё темнее при наведении */
}

/* Активная галочка - очень жирная и яркая зеленая */
.col-toggle-custom-checkbox input:checked ~ .col-toggle-checkmark:before {
  border-color: #00c853; /* Яркий неоново-зеленый */
  border-width: 0 4px 4px 0; /* Увеличена толщина */
  opacity: 1; /* Полная видимость */
}

/* При наведении на активную - ещё ярче */
.col-toggle-custom-checkbox:hover input:checked ~ .col-toggle-checkmark:before {
  opacity: 1;
  border-color: #00e676; /* Неоново-зеленый при наведении */
}

/* Текст */
.col-toggle-label {
  flex: 1;
  margin-left: 0 !important;
  margin-bottom: 0 !important;
  padding-left: 0 !important;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 0.9rem;
  line-height: 1.2;
  color: #495057;
  font-weight: 600; /* Жирный текст */
}

.reset-cols {
  font-size: 0.75rem;
  padding: 3px 8px;
  white-space: nowrap;
}

`;

// Добавляем стили
if (!document.querySelector('#col-toggle-styles')) {
  const styleSheet = document.createElement('style');
  styleSheet.id = 'col-toggle-styles';
  styleSheet.textContent = colToggleStyles;
  document.head.appendChild(styleSheet);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initColToggles);
} else {
  initColToggles();
}