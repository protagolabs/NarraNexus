/**
 * @file_name: SlotOutlet.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: Mounts a component slot point: every visible entry, in order, each isolated so one plugin's render error never takes the surface down.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';

import { reportUiError } from './errorSink';
import { useRegistryEntries, visibleSlotEntries, type Registry, type SlotComponentDef, type SlotComponentProps, type WhenContext } from './registries';

class SlotBoundary extends Component<{ owner: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    reportUiError(error, { kind: 'render', source: this.props.owner, context: info.componentStack ?? 'slot' });
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

export interface SlotOutletProps extends SlotComponentProps {
  registry: Registry<SlotComponentDef>;
  ctx: WhenContext;
  className?: string;
  /** Wrapper element; omitted (default) renders the entries bare. */
  as?: 'div' | 'span';
}

export function SlotOutlet({ registry, ctx, className, as, ...props }: SlotOutletProps) {
  const entries = visibleSlotEntries(useRegistryEntries(registry), ctx);
  if (entries.length === 0) return null;
  const children = entries.map((e) => {
    const C = e.value.component;
    return (
      <SlotBoundary key={e.id} owner={e.owner}>
        <C {...props} />
      </SlotBoundary>
    );
  });
  if (!as) return <>{children}</>;
  const Wrapper = as;
  return (
    <Wrapper className={className} data-slot={registry.kind}>
      {children}
    </Wrapper>
  );
}
