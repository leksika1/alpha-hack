import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { get, patch, post, put, qs } from './client';
import type {
  ActionIn,
  AnalyticsDocument,
  BankProduct,
  CrmRequest,
  Digest,
  Feed,
  FeedbackIn,
  FeedFilter,
  FeedSummary,
  Graph,
  InsightOut,
  ManualNewsItem,
  ManualNewsOut,
  Me,
  Notifications,
  Profile,
  ProfileUpdate,
  QaMessage,
  Role,
  SourceClick,
  StateAction,
  Trace,
  UiEventType,
} from './types';

export const keys = {
  me: ['me'] as const,
  summary: ['feed-summary'] as const,
  feed: (companyId: string, filter: FeedFilter, period: string) => ['feed', companyId, filter, period] as const,
  feedAll: ['feed'] as const,
  insight: (id: string) => ['insight', id] as const,
  trace: (id: string) => ['trace', id] as const,
  questions: (id: string) => ['questions', id] as const,
  askHistory: (companyId: string) => ['ask-history', companyId] as const,
  profile: (companyId: string) => ['profile', companyId] as const,
  graph: (companyId: string) => ['graph', companyId] as const,
  digest: (companyId: string, period: string) => ['digest', companyId, period] as const,
  notifications: ['notifications'] as const,
  requests: ['requests'] as const,
  products: ['bank-products'] as const,
  documents: ['analytics-documents'] as const,
};

export function useMe(enabled = true) {
  return useQuery({ queryKey: keys.me, queryFn: () => get<Me>('/me'), enabled, staleTime: 60_000 });
}

export function useFeedSummary() {
  return useQuery({ queryKey: keys.summary, queryFn: () => get<FeedSummary>('/feed/summary'), refetchInterval: 60_000 });
}

export function useFeed(companyId: string | undefined, filter: FeedFilter, period: string) {
  return useQuery({
    queryKey: keys.feed(companyId ?? '', filter, period),
    queryFn: () => get<Feed>(`/feed${qs({ company_id: companyId, filter, period: period || undefined, limit: 50 })}`),
    enabled: !!companyId,
    placeholderData: keepPreviousData,
  });
}

export function useInsight(id: string | undefined, via?: string, position?: number) {
  return useQuery({
    queryKey: keys.insight(id ?? ''),
    queryFn: () => get<InsightOut>(`/insights/${id}${qs({ via, position })}`),
    enabled: !!id,
  });
}

export function useTrace(id: string | undefined, enabled: boolean) {
  return useQuery({ queryKey: keys.trace(id ?? ''), queryFn: () => get<Trace>(`/insights/${id}/trace`), enabled: !!id && enabled });
}

export function useInsightQuestions(id: string | undefined) {
  return useQuery({ queryKey: keys.questions(id ?? ''), queryFn: () => get<QaMessage[]>(`/insights/${id}/questions`), enabled: !!id });
}

export function useAskHistory(companyId: string | undefined) {
  return useQuery({
    queryKey: keys.askHistory(companyId ?? ''),
    queryFn: () => get<QaMessage[]>(`/ask/history${qs({ company_id: companyId })}`),
    enabled: !!companyId,
  });
}

export function useProfile(companyId: string | undefined) {
  return useQuery({ queryKey: keys.profile(companyId ?? ''), queryFn: () => get<Profile>(`/companies/${companyId}/profile`), enabled: !!companyId });
}

export function useGraph(companyId: string | undefined) {
  return useQuery({
    queryKey: keys.graph(companyId ?? ''),
    queryFn: () => get<Graph>(`/companies/${companyId}/graph${qs({ limit: 30 })}`),
    enabled: !!companyId,
  });
}

export function useDigest(companyId: string | undefined, period: 'day' | 'week') {
  return useQuery({
    queryKey: keys.digest(companyId ?? '', period),
    queryFn: () => get<Digest>(`/digest${qs({ company_id: companyId, period })}`),
    enabled: !!companyId,
  });
}

export function useNotifications(enabled = true) {
  return useQuery({ queryKey: keys.notifications, queryFn: () => get<Notifications>('/notifications?limit=30'), enabled });
}

export function useRequests() {
  return useQuery({ queryKey: keys.requests, queryFn: () => get<CrmRequest[]>('/me/requests') });
}

export function useBankProducts() {
  return useQuery({ queryKey: keys.products, queryFn: () => get<BankProduct[]>('/bank-products'), staleTime: Infinity });
}

export function useAnalyticsDocuments() {
  return useQuery({ queryKey: keys.documents, queryFn: () => get<AnalyticsDocument[]>('/analytics/documents') });
}

// ---- мутации ----

export function useSetRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ companyId, role }: { companyId: string; role: Role }) => put(`/me/companies/${companyId}/role`, { role }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.me });
      qc.invalidateQueries({ queryKey: keys.feedAll });
      qc.invalidateQueries({ queryKey: keys.summary });
      qc.invalidateQueries({ queryKey: ['digest'] });
    },
  });
}

export function useInsightState(insightId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (action: StateAction) => post(`/insights/${insightId}/state`, { action }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.insight(insightId) });
      qc.invalidateQueries({ queryKey: keys.feedAll });
      qc.invalidateQueries({ queryKey: keys.summary });
    },
  });
}

export interface FeedbackResult {
  applied?: string[];
  hidden?: boolean;
  [key: string]: unknown;
}

export function useFeedback(insightId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FeedbackIn) => post<FeedbackResult>(`/insights/${insightId}/feedback`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.insight(insightId) });
      qc.invalidateQueries({ queryKey: keys.feedAll });
      qc.invalidateQueries({ queryKey: keys.summary });
    },
  });
}

export interface ActionResult {
  request_id?: string;
  external_ref?: string;
  status?: string;
  message?: string;
  [key: string]: unknown;
}

export function useCreateAction(insightId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ActionIn) => post<ActionResult>(`/insights/${insightId}/actions`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.requests }),
  });
}

export function useSourceClick(insightId: string) {
  return useMutation({
    mutationFn: (sourceId: string) => post<SourceClick>(`/insights/${insightId}/source-click`, { source_id: sourceId }),
  });
}

export function useUpdateProfile(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProfileUpdate) => patch<Record<string, unknown>>(`/companies/${companyId}/profile`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.profile(companyId) });
      qc.invalidateQueries({ queryKey: keys.feedAll });
      qc.invalidateQueries({ queryKey: keys.summary });
    },
  });
}

export function useReadNotification() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number | 'all') => (id === 'all' ? post('/notifications/read-all') : post(`/notifications/${id}/read`)),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.notifications }),
  });
}

export function useManualNews() {
  return useMutation({ mutationFn: (items: ManualNewsItem[]) => post<ManualNewsOut>('/admin/news', { items }) });
}

// События интерфейса для воронки CJM; ошибки не мешают пользователю.
export function trackEvent(type: UiEventType, companyId: string, insightId?: string | null, payload: Record<string, unknown> = {}) {
  post('/events', { type, company_id: companyId, insight_id: insightId ?? null, payload }).catch(() => undefined);
}
