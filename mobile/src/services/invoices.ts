import { apiClient } from './api.ts';
import type { InvoiceItem } from '../types/index.ts';

export async function fetchInvoicesForChild(studentId: number): Promise<InvoiceItem[]> {
  const response = await apiClient.get<{ results: InvoiceItem[] } | InvoiceItem[]>(
    `/invoices/?student_id=${studentId}`
  );
  const data = response.data as any;
  return Array.isArray(data) ? data : data.results;
}
