// Материалы аналитики банка: документ, дата, темы, метод извлечения; открытие PDF по presigned-ссылке.
import type { CSSProperties } from 'react';
import { get } from '../../api/client';
import { useAnalyticsDocuments } from '../../api/hooks';
import { CardSkeleton, EmptyState, ErrorBox, Pill } from '../../components/ui';
import { useToast } from '../../components/Toasts';
import { absoluteUrl, formatDate, formatPercent } from '../../lib/format';
import { DocumentPdfMIcon } from '@alfalab/icons-glyph/DocumentPdfMIcon';

const EXTRACTION: Record<string, string> = { text_layer: 'текстовый слой', ocr: 'распознано OCR', vlm: 'распознано VLM' };

export function MaterialsPage() {
  const docs = useAnalyticsDocuments();
  const toast = useToast();

  const open = async (id: string) => {
    const win = window.open('', '_blank');
    try {
      const res = await get<{ url: string }>(`/analytics/documents/${id}/download`);
      if (win) {
        win.opener = null;
        win.location.href = absoluteUrl(res.url);
      }
    } catch (e) {
      win?.close();
      toast.show({ title: 'Документ недоступен', body: e instanceof Error ? e.message : '', tone: 'error' });
    }
  };

  if (docs.isLoading) return <CardSkeleton />;
  if (docs.isError) return <ErrorBox error={docs.error} onRetry={() => docs.refetch()} />;
  if (!docs.data?.length) return <EmptyState title="Материалов пока нет" />;

  return (
    <div>
      <p className="text-ink-2">
        Аналитика Альфа-Банка, на которую опираются инсайты: каждый вывод со ссылкой на документ и страницу. Материалы с пометкой «ДЕМО» — синтетика для
        офлайн-демонстрации, не аналитика банка.
      </p>
      <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
        {docs.data.map((d, i) => (
          <button
            key={d.document_id}
            style={{ '--i': Math.min(i + 1, 12) } as CSSProperties}
            className="stagger pressable card flex flex-col gap-1.5 p-6 text-left hover:-translate-y-0.5 hover:shadow-lift"
            onClick={() => open(d.document_id)}
          >
            <DocumentPdfMIcon className="text-accent" />
            <div className="font-bold">{d.title}</div>
            <div className="text-[13px] text-ink-2">
              {formatDate(d.doc_date)}, {d.pages} стр., {EXTRACTION[d.extraction] ?? d.extraction}
              {d.ocr_confidence != null && ` (${formatPercent(d.ocr_confidence)})`}
            </div>
            <div className="flex flex-wrap gap-1">
              {d.topics.map((t) => (
                <Pill key={t}>{t}</Pill>
              ))}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
