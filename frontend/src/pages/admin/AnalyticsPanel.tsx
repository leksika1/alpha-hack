// Аналитика банка: загрузка PDF (`POST /admin/analytics`, тело — сам файл) и все материалы со статусом обработки.
// Пока документ обрабатывается (текст или OCR, нарезка, индексация), список обновляется сам.
import { useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@alfalab/core-components/button';
import { get, rawFetch, toApiError } from '../../api/client';
import type { AdminAnalyticsDocument, AnalyticsUploadOut } from '../../api/types';
import { CardSkeleton, ErrorBox, H3, Note, Pill } from '../../components/ui';
import { formatDate, formatPercent } from '../../lib/format';

const STATUS: Record<string, { label: string; tone: 'green' | 'orange' | 'red' | 'grey' }> = {
  ready: { label: 'Готов', tone: 'green' },
  processing: { label: 'Обрабатывается', tone: 'orange' },
  failed: { label: 'Ошибка', tone: 'red' },
  error: { label: 'Ошибка', tone: 'red' },
};

const EXTRACTION: Record<string, string> = { text_layer: 'текстовый слой', ocr: 'распознан OCR', vlm: 'распознан VLM', mixed: 'текст и распознавание' };

async function upload(file: File): Promise<AnalyticsUploadOut> {
  const res = await rawFetch(`/admin/analytics?file_name=${encodeURIComponent(file.name)}`, {
    method: 'POST',
    body: file,
    headers: { 'Content-Type': 'application/pdf' },
  });
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as AnalyticsUploadOut;
}

export function AnalyticsPanel() {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const docs = useQuery({
    queryKey: ['admin-analytics'],
    queryFn: () => get<AdminAnalyticsDocument[]>('/admin/analytics'),
    refetchInterval: (q) => (q.state.data?.some((d) => d.status === 'processing') ? 5_000 : false),
  });
  const send = useMutation({
    mutationFn: upload,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-analytics'] });
      qc.invalidateQueries({ queryKey: ['analytics-documents'] });
    },
  });

  const pick = (file: File | undefined) => {
    if (file) send.mutate(file);
    if (input.current) input.current.value = '';
  };

  return (
    <section className="card p-6">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <H3 className="mb-0">Аналитика банка (PDF)</H3>
        <input ref={input} type="file" accept="application/pdf,.pdf" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
        <Button view="accent" size={40} loading={send.isPending} onClick={() => input.current?.click()}>
          Загрузить PDF
        </Button>
      </div>
      <p className="mb-3 text-[13px] text-ink-2">
        Документ сразу появится со статусом «Обрабатывается»: воркер извлечёт текст или распознает сканы, разрежет на фрагменты и проиндексирует. Пользователям
        виден только готовый материал.
      </p>
      {send.isError && <Note tone="warning">{send.error.message}</Note>}
      {send.data && (
        <Note>
          {send.data.duplicate ? `«${send.data.title}» уже загружен.` : `«${send.data.title}» принят${send.data.queued ? ' и поставлен в обработку' : ''}.`}
        </Note>
      )}

      {docs.isLoading && <CardSkeleton rows={3} />}
      {docs.isError && <ErrorBox error={docs.error} onRetry={() => docs.refetch()} />}
      {docs.data?.length === 0 && <p className="text-ink-2">Материалов пока нет.</p>}
      {!!docs.data?.length && (
        <ul className="divide-y divide-line">
          {docs.data.map((d) => {
            const st = STATUS[d.status] ?? { label: d.status, tone: 'grey' as const };
            return (
              <li key={d.document_id} className="flex items-start justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <div className="font-semibold">{d.title}</div>
                  <div className="flex flex-wrap gap-x-3 text-xs text-ink-2">
                    <span className="break-all">{d.file_name}</span>
                    <span>загружен {formatDate(d.created_at, true)}</span>
                    {d.status === 'ready' && (
                      <>
                        <span>страниц: {d.pages}</span>
                        <span>фрагментов: {d.chunks}</span>
                        {d.extraction && <span>{EXTRACTION[d.extraction] ?? d.extraction}</span>}
                        {d.ocr_confidence != null && <span>уверенность OCR {formatPercent(d.ocr_confidence)}</span>}
                      </>
                    )}
                  </div>
                  {d.error && <div className="text-xs break-words text-neg">{d.error}</div>}
                </div>
                <Pill tone={st.tone}>{st.label}</Pill>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
