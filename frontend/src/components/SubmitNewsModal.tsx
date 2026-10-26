// «Прислать новость»: ссылка или текст идут тем же конвейером, что и источники; инсайт — только для своей компании.
// Ответ сервиса показываем как есть: недоступный ресурс, нарушение закона, дубль или не статья.
import { useEffect, useState } from 'react';
import { ModalDesktop } from '@alfalab/core-components/modal/desktop';
import { Button } from '@alfalab/core-components/button';
import { Input } from '@alfalab/core-components/input';
import { Textarea } from '@alfalab/core-components/textarea';
import { StatusBadge } from '@alfalab/core-components/status-badge';
import { useSubmitNews } from '../api/hooks';
import type { NewsSubmitOut } from '../api/types';
import { ApiError } from '../api/client';
import { Note, Segmented } from './ui';

type Mode = 'url' | 'text';

const STATUS: Record<NewsSubmitOut['status'], { title: string; badge: 'positive-checkmark' | 'neutral-information' | 'attention-alert' | 'negative-block' }> = {
  processed: { title: 'Новость обработана', badge: 'positive-checkmark' },
  duplicate: { title: 'Новость уже известна', badge: 'neutral-information' },
  unavailable: { title: 'Ресурс недоступен', badge: 'attention-alert' },
  blocked: { title: 'Материал не принят', badge: 'negative-block' },
  not_article: { title: 'Не похоже на новость', badge: 'attention-alert' },
};

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return 'Сегодня отправлено слишком много новостей — попробуйте завтра.';
    if (error.status === 422) return error.detail ? `Проверьте ссылку или текст: ${error.detail}` : 'Проверьте ссылку или текст.';
    return error.message;
  }
  return error instanceof Error ? error.message : 'Не удалось отправить новость.';
}

export function SubmitNewsModal({ companyId, open, onClose }: { companyId: string; open: boolean; onClose: () => void }) {
  const submit = useSubmitNews();
  const [mode, setMode] = useState<Mode>('url');
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [result, setResult] = useState<NewsSubmitOut | null>(null);

  useEffect(() => {
    if (open) {
      setResult(null);
      submit.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Те же границы, что у API: заголовок от 5 символов, текст от 40.
  const ready = mode === 'url' ? url.trim().length >= 10 : title.trim().length >= 5 && text.trim().length >= 40;

  const send = () =>
    submit.mutate(
      mode === 'url'
        ? { company_id: companyId, url: url.trim(), title: null, text: null }
        : { company_id: companyId, url: null, title: title.trim(), text: text.trim() },
      { onSuccess: setResult },
    );

  const again = () => {
    setResult(null);
    setUrl('');
    setTitle('');
    setText('');
  };

  const status = result ? STATUS[result.status] : null;

  return (
    <ModalDesktop open={open} onClose={onClose} size={600}>
      <ModalDesktop.Header hasCloser title={status?.title ?? 'Прислать новость'} />
      <ModalDesktop.Content>
        {result && status ? (
          <div className="flex flex-col gap-2.5 motion-safe:animate-fade-up">
            <StatusBadge view={status.badge} size={40} className="motion-safe:animate-pop-in" />
            <p className="text-[17px] font-semibold">{result.message}</p>
            {result.reason && <p className="text-ink-2">Причина: {result.reason}</p>}
            {result.title && (
              <p className="text-sm">
                {result.url ? (
                  <a href={result.url} target="_blank" rel="noreferrer">
                    {result.title}
                  </a>
                ) : (
                  result.title
                )}
              </p>
            )}
            {result.status === 'processed' && (
              <Note>
                {result.generation_queued
                  ? 'Событие значимо для компании: инсайт готовится и придёт в ленту и уведомлением.'
                  : result.relevant
                    ? 'Событие значимо для компании и уже учтено в ленте.'
                    : 'Событие не затрагивает профиль компании — в ленту оно не попадёт.'}
              </Note>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3.5">
            <p className="text-ink-2">
              Пришлите ссылку на статью или её текст: сервис проверит источник, найдёт событие и подготовит инсайт, если оно касается вашей компании.
            </p>
            <div className="-mb-3">
              <Segmented
                label="Что прислать"
                value={mode}
                onChange={setMode}
                options={[
                  { key: 'url', label: 'Ссылка' },
                  { key: 'text', label: 'Текст' },
                ]}
              />
            </div>
            {mode === 'url' ? (
              <Input label="Ссылка на новость" placeholder="https://" block value={url} maxLength={1000} onChange={(_, { value }) => setUrl(value)} />
            ) : (
              <>
                <Input label="Заголовок" block value={title} maxLength={300} onChange={(_, { value }) => setTitle(value)} />
                <Textarea label="Текст новости" block minRows={5} maxLength={20000} value={text} onChange={(_, { value }) => setText(value)} />
              </>
            )}
            {submit.isError && <Note tone="warning">{errorText(submit.error)}</Note>}
          </div>
        )}
      </ModalDesktop.Content>
      <ModalDesktop.Footer>
        <div className="flex gap-2">
          {result ? (
            <>
              <Button view="primary" size={48} onClick={onClose}>
                Готово
              </Button>
              <Button view="secondary" size={48} onClick={again}>
                Прислать ещё
              </Button>
            </>
          ) : (
            <>
              <Button view="accent" size={48} loading={submit.isPending} disabled={!ready} onClick={send}>
                Отправить
              </Button>
              <Button view="secondary" size={48} onClick={onClose}>
                Отмена
              </Button>
            </>
          )}
        </div>
      </ModalDesktop.Footer>
    </ModalDesktop>
  );
}
