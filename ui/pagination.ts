export interface PageWindow<T> {
  items: T[];
  page: number;
  pageCount: number;
  start: number;
  end: number;
  total: number;
}

export function paginateItems<T>(items: readonly T[], requestedPage: number, requestedPageSize: number): PageWindow<T> {
  const pageSize = Number.isFinite(requestedPageSize)
    ? Math.max(1, Math.floor(requestedPageSize))
    : 1;
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const page = Number.isFinite(requestedPage)
    ? Math.min(pageCount, Math.max(1, Math.floor(requestedPage)))
    : 1;
  const offset = (page - 1) * pageSize;
  const endOffset = Math.min(offset + pageSize, items.length);

  return {
    items: items.slice(offset, endOffset),
    page,
    pageCount,
    start: items.length ? offset + 1 : 0,
    end: endOffset,
    total: items.length,
  };
}
