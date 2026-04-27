/**
 * @file_name: ConfirmDialog.tsx
 * @description: Pure-React confirm/alert primitive + useConfirm hook.
 *
 * Tauri's wry webview does not render window.confirm / window.alert /
 * window.prompt, so any call to them resolves falsy and the surrounding
 * handler bails out silently. Every interactive confirmation in the app
 * goes through this hook instead so the DMG build behaves identically
 * to the browser dev server (rule #7).
 */

import { useCallback, useState, type ReactNode } from 'react';
import { Dialog, DialogContent, DialogFooter } from './Dialog';
import { Button } from './Button';

export interface ConfirmOptions {
  title?: string;
  message: ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
}

export interface AlertOptions {
  title?: string;
  message: ReactNode;
  okText?: string;
  danger?: boolean;
}

interface DialogState {
  mode: 'confirm' | 'alert';
  title: string;
  message: ReactNode;
  confirmText: string;
  cancelText?: string;
  danger: boolean;
  resolve: (value: boolean) => void;
}

export function useConfirm() {
  const [state, setState] = useState<DialogState | null>(null);

  const close = useCallback((value: boolean) => {
    setState((prev) => {
      prev?.resolve(value);
      return null;
    });
  }, []);

  const confirm = useCallback(
    (opts: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setState({
          mode: 'confirm',
          title: opts.title ?? 'Confirm',
          message: opts.message,
          confirmText: opts.confirmText ?? 'Confirm',
          cancelText: opts.cancelText ?? 'Cancel',
          danger: opts.danger ?? false,
          resolve,
        });
      }),
    []
  );

  const alert = useCallback(
    (opts: AlertOptions) =>
      new Promise<void>((resolve) => {
        setState({
          mode: 'alert',
          title: opts.title ?? 'Notice',
          message: opts.message,
          confirmText: opts.okText ?? 'OK',
          cancelText: undefined,
          danger: opts.danger ?? false,
          resolve: () => resolve(),
        });
      }),
    []
  );

  const dialog = state ? (
    <Dialog isOpen onClose={() => close(false)} title={state.title} size="md">
      <DialogContent>
        <div className="text-sm text-[var(--text-secondary)] whitespace-pre-wrap">
          {state.message}
        </div>
      </DialogContent>
      <DialogFooter>
        {state.mode === 'confirm' && (
          <Button variant="ghost" onClick={() => close(false)}>
            {state.cancelText}
          </Button>
        )}
        <Button
          variant={state.danger ? 'danger' : 'accent'}
          onClick={() => close(true)}
        >
          {state.confirmText}
        </Button>
      </DialogFooter>
    </Dialog>
  ) : null;

  return { confirm, alert, dialog };
}
