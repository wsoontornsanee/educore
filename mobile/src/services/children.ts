import { apiClient } from './api.ts';
import type { ChildSummary } from '../types/index.ts';

export async function fetchChildren(): Promise<ChildSummary[]> {
  const response = await apiClient.get<ChildSummary[]>('/me/children/');
  return response.data;
}
