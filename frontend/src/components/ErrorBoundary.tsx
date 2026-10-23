import { Component, type ReactNode } from 'react';

// Ошибка рендера одного экрана не должна оставлять пользователя с пустой страницей.
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-start gap-2 rounded-card border-l-4 border-accent bg-white p-6" role="alert">
          <div className="font-bold">Не удалось показать экран</div>
          <div>{this.state.error.message}</div>
          <button className="p-0 text-info hover:underline" onClick={() => this.setState({ error: null })}>
            Попробовать снова
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
