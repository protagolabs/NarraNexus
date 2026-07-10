/**
 * @file FeedbackDialog.test.tsx
 * @description Feedback dialog contract: submits category+text through
 * api.submitFeedback, thanks the user even when the relay throws (feedback
 * must never error at the user), and disables submit on empty text.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, test, vi, beforeEach } from 'vitest';
import { FeedbackDialog } from '../FeedbackDialog';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k),
  }),
}));

const mockSubmit = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { submitFeedback: (...a: unknown[]) => mockSubmit(...a) },
}));

beforeEach(() => {
  mockSubmit.mockReset();
});

function renderOpen() {
  return render(<FeedbackDialog isOpen={true} onClose={() => {}} />);
}

test('submits category and trimmed text via api', async () => {
  mockSubmit.mockResolvedValue({ ok: true, delivered: true });
  renderOpen();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'feature_gap' } });
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '  need dark mode  ' } });
  fireEvent.click(screen.getByText('feedback.submit'));
  await waitFor(() => expect(mockSubmit).toHaveBeenCalledWith('feature_gap', 'need dark mode'));
  expect(await screen.findByText('feedback.thanks')).toBeTruthy();
});

test('still thanks the user when the relay throws', async () => {
  mockSubmit.mockRejectedValue(new Error('network down'));
  renderOpen();
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'broken' } });
  fireEvent.click(screen.getByText('feedback.submit'));
  expect(await screen.findByText('feedback.thanks')).toBeTruthy();
});

test('submit disabled on empty text', () => {
  renderOpen();
  const btn = screen.getByText('feedback.submit').closest('button');
  expect(btn?.disabled).toBe(true);
  expect(mockSubmit).not.toHaveBeenCalled();
});
