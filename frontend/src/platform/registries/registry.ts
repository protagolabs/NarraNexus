/**
 * @file_name: registry.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The one registry shape every frontend contribution point uses.
 *
 * Mirrors the kernel's Python `Registry[T]`: entries keep registration order
 * (builtin shell entries first, plugins after), a duplicate id is an error
 * unless `replace` is passed, `register` returns a disposer, and `freeze`
 * ends registration. Consumers read through `list()` / `get()`; nothing
 * iterates a hardcoded array any more.
 */

export interface RegistryEntry<T> {
  id: string;
  value: T;
  owner: string;
}

export class RegistryConflictError extends Error {
  constructor(kind: string, id: string, owner: string) {
    super(`${kind}: "${id}" is already registered by "${owner}"`);
    this.name = 'RegistryConflictError';
  }
}

export interface RegisterOptions {
  owner?: string;
  replace?: boolean;
}

export class Registry<T> {
  readonly kind: string;
  private readonly entries = new Map<string, RegistryEntry<T>>();
  private frozen = false;
  private readonly listeners = new Set<() => void>();
  private cached: RegistryEntry<T>[] | null = null;

  constructor(kind: string) {
    this.kind = kind;
  }

  register(id: string, value: T, options: RegisterOptions = {}): () => void {
    if (this.frozen) throw new Error(`${this.kind}: registration is closed`);
    const owner = options.owner ?? 'builtin.ui';
    const existing = this.entries.get(id);
    if (existing && !options.replace) throw new RegistryConflictError(this.kind, id, existing.owner);
    const entry: RegistryEntry<T> = { id, value, owner };
    this.entries.set(id, entry);
    this.notify();
    return () => {
      if (this.entries.get(id) === entry) {
        this.entries.delete(id);
        this.notify();
      }
    };
  }

  get(id: string): T | undefined {
    return this.entries.get(id)?.value;
  }

  has(id: string): boolean {
    return this.entries.has(id);
  }

  list(): RegistryEntry<T>[] {
    return [...this.entries.values()];
  }

  /** Same content as `list()` but referentially stable until the next change. */
  snapshot(): RegistryEntry<T>[] {
    if (this.cached === null) this.cached = this.list();
    return this.cached;
  }

  ids(): string[] {
    return [...this.entries.keys()];
  }

  freeze(): void {
    this.frozen = true;
  }

  /** Subscribe to changes (plugins register after first render). */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private notify(): void {
    this.cached = null;
    for (const l of this.listeners) l();
  }
}
