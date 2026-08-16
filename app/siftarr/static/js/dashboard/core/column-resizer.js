export class ColumnResizer {
  constructor() {
    this.storageKey = 'siftarr_col_widths';
    this.activeHandle = null;
    this.activeCol = null;
    this.startX = 0;
    this.startWidth = 0;
    this.minWidth = 60;
    this.tables = document.querySelectorAll('table.data-resizable');
    this.savedWidths = this.loadWidths();
    this.init();
  }
  loadWidths() {
    try {
      const stored = localStorage.getItem(this.storageKey);
      return stored ? JSON.parse(stored) : {};
    } catch {
      return {};
    }
  }
  saveWidths() {
    localStorage.setItem(this.storageKey, JSON.stringify(this.savedWidths));
  }
  init() {
    this.attachHandles();
    document.addEventListener('mousemove', (event) => this.onMouseMove(event));
    document.addEventListener('mouseup', () => this.endResize());
    document.addEventListener('mouseleave', () => this.endResize());
  }
  // Refreshed tabs replace their table markup, so handles are re-attachable
  // without duplicating the document-level drag listeners.
  attachHandles() {
    this.tables = document.querySelectorAll('table.data-resizable');
    this.tables.forEach((table) => {
      var _a;
      const tableId = table.id;
      const tableWidths = (_a = this.savedWidths)[tableId] ?? (_a[tableId] = {});
      table.querySelectorAll('th[data-col-key]').forEach((th) => {
        const colKey = th.dataset.colKey;
        if (!colKey) return;
        const col = table.querySelector(`col[data-col-key="${colKey}"]`);
        if (!col) return;
        const savedWidth = tableWidths[colKey];
        if (savedWidth) col.style.width = savedWidth + 'px';
        const handle = th.querySelector('.resize-handle');
        if (handle) {
          handle.addEventListener('mousedown', (event) => this.startResize(event, col));
        }
      });
    });
  }
  startResize(event, col) {
    event.preventDefault();
    event.stopPropagation();
    this.activeHandle = event.target instanceof HTMLElement ? event.target : null;
    this.activeCol = col;
    this.startX = event.clientX;
    this.startWidth = parseInt(col.style.width) || col.offsetWidth || 100;
    this.activeHandle?.classList.add('dragging');
    document.body.classList.add('resizing');
  }
  onMouseMove(event) {
    if (!this.activeHandle || !this.activeCol) return;
    const dx = event.clientX - this.startX;
    this.activeCol.style.width = Math.max(this.minWidth, this.startWidth + dx) + 'px';
  }
  endResize() {
    var _a;
    if (!this.activeHandle || !this.activeCol) return;
    const tableId = this.activeCol.closest('table')?.id;
    const colKey = this.activeCol.dataset.colKey;
    if (!tableId || !colKey) return;
    const finalWidth = parseInt(this.activeCol.style.width) || this.activeCol.offsetWidth;
    const tableWidths = (_a = this.savedWidths)[tableId] ?? (_a[tableId] = {});
    tableWidths[colKey] = finalWidth;
    this.saveWidths();
    this.activeHandle.classList.remove('dragging');
    document.body.classList.remove('resizing');
    this.activeHandle = null;
    this.activeCol = null;
  }
}
