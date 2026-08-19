import { useEffect, useRef } from 'react';

/**
 * Dismiss a popover when the user interacts anywhere outside it, or presses
 * Escape.
 *
 * Why a document-level listener and not a full-screen backdrop `<div>`:
 * `position: fixed` resolves against the nearest transformed ancestor, so a
 * backdrop rendered inside an animated row (any element keeping a transform,
 * e.g. `animate-slide-up` with fill: forwards) only covers that row — clicks
 * elsewhere on the page never reach it and the popover appears un-closable.
 * A capture-phase pointerdown listener has no such trap and also survives
 * `stopPropagation` in intermediate handlers.
 *
 * Attach the returned ref to the element that contains BOTH the trigger and
 * the popover panel; interactions inside it are ignored.
 */
export function useDismissOnOutside<T extends HTMLElement>(
  active: boolean,
  onDismiss: () => void,
): React.RefObject<T | null> {
  const ref = useRef<T>(null);
  // Latest-callback ref so an inline `onDismiss` closure doesn't force the
  // listeners to re-subscribe on every render while the popover is open.
  const dismissRef = useRef(onDismiss);
  useEffect(() => {
    dismissRef.current = onDismiss;
  });

  useEffect(() => {
    if (!active) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        dismissRef.current();
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') dismissRef.current();
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [active]);

  return ref;
}
