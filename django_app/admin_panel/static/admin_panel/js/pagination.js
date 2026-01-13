// pagination.js — версия с поддержкой фильтрации
export function initTablePagination(defaultPerPage = 20) {
  document.querySelectorAll('table[data-paginate]').forEach((table) => {
    let allRows = Array.from(table.querySelectorAll('tbody tr'));
    const container = table.closest('.table-responsive');

    // контейнер под панель
    let pagination = container.querySelector('.table-pagination-extended');
    if (!pagination) {
      pagination = document.createElement('div');
      pagination.className = 'table-pagination-extended';
      container.after(pagination);
    }

    // настройки
    const savedPerPage = parseInt(localStorage.getItem('tablePerPage') || defaultPerPage);
    let perPage = savedPerPage;
    let currentPage = 1;

    // Функция для получения отфильтрованных строк
    function getFilteredRows() {
      const isFilterActive = table.dataset.filterActive === "true";
      
      if (!isFilterActive) {
        return allRows;
      }
      
      const filterColumn = parseInt(table.dataset.filterColumn);
      const filterValue = table.dataset.filterValue;
      
      if (isNaN(filterColumn) || !filterValue) {
        return allRows;
      }
      
      return allRows.filter(row => {
        const cell = row.cells[filterColumn];
        if (!cell) return false;
        
        const cellValue = cell.getAttribute("data-sort-value") ?? cell.textContent;
        const normalizedValue = String(cellValue ?? "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
        
        return normalizedValue === filterValue;
      });
    }

    function render() {
      // Обновляем список всех строк (на случай изменений в таблице)
      allRows = Array.from(table.querySelectorAll('tbody tr'));
      
      // Получаем отфильтрованные строки
      const filteredRows = getFilteredRows();
      const totalRows = filteredRows.length;
      const totalPages = Math.ceil(totalRows / perPage);
      
      // Корректируем текущую страницу
      if (currentPage > totalPages) currentPage = totalPages || 1;
      if (currentPage < 1) currentPage = 1;

      const start = (currentPage - 1) * perPage;
      const end = start + perPage;

      // Скрываем все строки сначала
      allRows.forEach(row => {
        row.style.display = 'none';
      });

      // Показываем только строки текущей страницы из отфильтрованного набора
      filteredRows.slice(start, end).forEach(row => {
        row.style.display = '';
      });

      renderPagination(totalPages, start, end, filteredRows.length);
    }

    function renderPagination(totalPages, start, end, filteredCount) {
      const startItem = filteredCount ? start + 1 : 0;
      const endItem = Math.min(end, filteredCount);
      const totalItems = allRows.length;

      // генерация страниц
      let pageButtons = [];
      const maxVisible = 5;

      function addPage(num) {
        pageButtons.push(`<button class="page-btn ${num===currentPage?'active':''}" data-page="${num}">${num}</button>`);
      }

      if (totalPages <= maxVisible + 2) {
        for (let i=1;i<=totalPages;i++) addPage(i);
      } else {
        if (currentPage <= 3) {
          for (let i=1;i<=maxVisible;i++) addPage(i);
          pageButtons.push('<span class="dots">…</span>');
          addPage(totalPages);
        } else if (currentPage >= totalPages - 2) {
          addPage(1);
          pageButtons.push('<span class="dots">…</span>');
          for (let i=totalPages-maxVisible+1;i<=totalPages;i++) addPage(i);
        } else {
          addPage(1);
          pageButtons.push('<span class="dots">…</span>');
          for (let i=currentPage-1;i<=currentPage+1;i++) addPage(i);
          pageButtons.push('<span class="dots">…</span>');
          addPage(totalPages);
        }
      }

      // Показываем информацию о фильтрации
      const filterInfo = table.dataset.filterActive === "true" 
        ? ` (отфильтровано из ${totalItems})`
        : '';

      pagination.innerHTML = `
        <div class="pagination-left">
          <span>Показывать по:
            <select class="rows-per-page">
              ${[5,10,15,20,25,30,40,50].map(v => `
                <option value="${v}" ${v===perPage?'selected':''}>${v}</option>
              `).join('')}
            </select>
          </span>
        </div>

        <div class="pagination-center">
          <span>Страница:</span>
          <button class="prev" ${currentPage===1?'disabled':''}>«</button>
          ${pageButtons.join('')}
          <button class="next" ${currentPage===totalPages?'disabled':''}>»</button>
        </div>

        <div class="pagination-right">
          <span>Отображение элементов ${startItem}–${endItem} из ${filteredCount}${filterInfo}</span>
        </div>
      `;

      // обработчики
      pagination.querySelector('.rows-per-page').addEventListener('change', (e) => {
        perPage = parseInt(e.target.value);
        localStorage.setItem('tablePerPage', perPage);
        currentPage = 1;
        render();
      });

      pagination.querySelectorAll('.page-btn').forEach(btn =>
        btn.addEventListener('click', e => {
          currentPage = parseInt(e.target.dataset.page);
          render();
        })
      );

      pagination.querySelector('.prev')?.addEventListener('click', () => {
        if (currentPage > 1) { currentPage--; render(); }
      });

      pagination.querySelector('.next')?.addEventListener('click', () => {
        if (currentPage < totalPages) { currentPage++; render(); }
      });
    }

    // Обработчики событий сортировки и фильтрации
    table.addEventListener('sort', () => {
      currentPage = 1;
      render();
    });

    table.addEventListener('filter', () => {
      currentPage = 1;
      render();
    });

    // Инициализация
    render();
  });
}