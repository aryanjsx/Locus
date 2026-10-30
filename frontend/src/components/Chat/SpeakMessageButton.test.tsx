import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../lib/store', () => ({
  useAppStore: (selector: (state: { settings: { voiceOutputEnabled: boolean } }) => unknown) =>
    selector({ settings: { voiceOutputEnabled: true } }),
}));
vi.mock('../../hooks/useTts', () => ({ useTts: vi.fn() }));

import { useTts } from '../../hooks/useTts';
import { SpeakMessageButton } from './SpeakMessageButton';

describe('read-aloud feedback', () => {
  it('shows a playback error beside the message that failed', () => {
    vi.mocked(useTts).mockReturnValue({
      available: true,
      state: 'idle',
      speakingId: null,
      error: 'Playback failed',
      errorId: 'failed-message',
      speak: vi.fn(async () => {}),
      stop: vi.fn(),
      isLoading: false,
      isSpeaking: false,
    });

    const failed = renderToStaticMarkup(
      <SpeakMessageButton messageId="failed-message" content="Hello" />,
    );
    const other = renderToStaticMarkup(
      <SpeakMessageButton messageId="other-message" content="Hello" />,
    );

    expect(failed).toContain('role="alert"');
    expect(failed).toContain('Playback failed');
    expect(other).not.toContain('Playback failed');
  });
});
