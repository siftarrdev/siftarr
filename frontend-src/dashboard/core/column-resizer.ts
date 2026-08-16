type SavedWidths = Record<string, Record<string, number>>;

export class ColumnResizer {
  private tables: NodeListOf<HTMLTableElement>;
  private readonly storageKey = 'siftarr_col_widths';
  private savedWidths: SavedWidths;
  private activeHandle: HTMLElement | null = null;
  private activeCol: HTMLTableColElement | null = null;
  private startX = 0;
  private startWidth = 0;
  private readonly minWidth = 60;

  constructor() {
    this.tables = document.querySelectorAll<HTMLTableElement>('table.data-resizable');
    this.savedWidths = this.loadWidths();
    this.init();
  }

  private loadWidths(): SavedWidths {
    try {
      const stored = localStorage.getItem(this.storageKey);
      return stored ? (JSON.parse(stored) as SavedWidths) : {};
    } catch {
      return {};
    }
  }

  private saveWidths(): void {
    localStorage.setItem(this.storageKey, JSON.stringify(this.savedWidths));
  }

  private init(): void {
    this.attachHandles();
    document.addEventListener('mousemove', (event) => this.onMouseMove(event));
    document.addEventListener('mouseup', () => this.endResize());
    document.addEventListener('mouseleave', () => this.endResize());
  }

  // Refreshed tabs replace their table markup, so handles are re-attachable
  // without duplicating the document-level drag listeners.
  attachHandles(): void {
    this.tables = document.querySelectorAll<HTMLTableElement>('table.data-resizable');
    this.tables.forEach((table) => {
      const tableId = table.id;
      const tableWidths = (this.savedWidths[tableId] ??= {});

      table.querySelectorAll<HTMLTableCellElement>('th[data-col-key]').forEach((th) => {
        const colKey = th.dataset.colKey;
        if (!colKey) return;

        const col = table.querySelector<HTMLTableColElement>(`col[data-col-key="${colKey}"]`);
        if (!col) return;

        const savedWidth = tableWidths[colKey];
        if (savedWidth) col.style.width = savedWidth + 'px';
        const handle = th.querySelector<HTMLElement>('.resize-handle');
        if (handle) {
          handle.addEventListener('mousedown', (event) => this.startResize(event, col));
        }
      });
    });
  }

  private startResize(event: MouseEvent, col: HTMLTableColElement): void {
    event.preventDefault();
    event.stopPropagation();
    this.activeHandle = event.target instanceof HTMLElement ? event.target : null;
    this.activeCol = col;
    this.startX = event.clientX;
    this.startWidth = parseInt(col.style.width) || col.offsetWidth || 100;
    this.activeHandle?.classList.add('dragging');
    document.body.classList.add('resizing');
  }

  private onMouseMove(event: MouseEvent): void {
    if (!this.activeHandle || !this.activeCol) return;
    const dx = event.clientX - this.startX;
    this.activeCol.style.width = Math.max(this.minWidth, this.startWidth + dx) + 'px';
  }

  private endResize(): void {
    if (!this.activeHandle || !this.activeCol) return;

    const tableId = this.activeCol.closest('table')?.id;
    const colKey = this.activeCol.dataset.colKey;
    if (!tableId || !colKey) return;

    const finalWidth = parseInt(this.activeCol.style.width) || this.activeCol.offsetWidth;
    const tableWidths = (this.savedWidths[tableId] ??= {});
    tableWidths[colKey] = finalWidth;
    this.saveWidths();

    this.activeHandle.classList.remove('dragging');
    document.body.classList.remove('resizing');
    this.activeHandle = null;
    this.activeCol = null;
  }
}
