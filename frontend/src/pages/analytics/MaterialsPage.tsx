// Материалы аналитики банка: документ, дата, темы, метод извлечения; открытие PDF по presigned-ссылке.
import { get } from '../../api/client';
import { useAnalyticsDocuments } from '../../api/hooks';
import { CardSkeleton, EmptyState, ErrorBox } from '../../components/ui';
import { useToast } from '../../components/Toasts';
import { formatDate, formatPercent } from '../../lib/format';
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
        win.location.href = res.url;
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
    <div className="materials">
      <p className="muted">
        Аналитика Альфа-Банка, на которую опираются инсайты: каждый вывод со ссылкой на документ и страницу. Материалы с пометкой «ДЕМО» —
        синтетика для офлайн-демонстрации, не аналитика банка.
      </p>
      <div className="materials__grid">
        {docs.data.map((d) => (
          <button key={d.document_id} className="card card--pad material" onClick={() => open(d.document_id)}>
            <DocumentPdfMIcon className="material__icon" />
            <div className="material__title">{d.title}</div>
            <div className="muted small">
              {formatDate(d.doc_date)} · {d.pages} стр. · {EXTRACTION[d.extraction] ?? d.extraction}
              {d.ocr_confidence != null && ` (${formatPercent(d.ocr_confidence)})`}
            </div>
            <div className="material__topics">
              {d.topics.map((t) => (
                <span key={t} className="pill pill--grey">
                  {t}
                </span>
              ))}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
