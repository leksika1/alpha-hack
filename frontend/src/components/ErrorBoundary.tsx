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
        <div className="error-box" role="alert">
          <div className="error-box__title">Не удалось показать экран</div>
          <div className="error-box__text">{this.state.error.message}</div>
          <button className="link-btn" onClick={() => this.setState({ error: null })}>
            Попробовать снова
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
