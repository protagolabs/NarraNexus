/**
 * @file_name: AttachmentBlobLinks.test.tsx
 * @description: Which attachment blobs may become a clickable link.
 *
 * A blob: URL inherits the app's origin, so opening one renders it as a
 * same-origin document and an SVG document runs its scripts with the viewer's
 * session. Both renderers go through their real hook here; only the authed fetch
 * and the jsdom-missing createObjectURL are stubbed.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';

const fetchAttachmentBlob = vi.fn();
const fetchBusAttachmentBlob = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    fetchAttachmentBlob: (...a: unknown[]) => fetchAttachmentBlob(...a),
    fetchBusAttachmentBlob: (...a: unknown[]) => fetchBusAttachmentBlob(...a),
  },
}));

import { AttachmentImage } from '../AttachmentImage';
import { BusAttachmentList } from '../BusAttachmentList';
import type { BusAttachment } from '@/types';

const BLOB_URL = 'blob:http://localhost/attachment';
const OPENABLE = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
const NOT_OPENABLE = ['image/svg+xml', 'text/html', 'application/xhtml+xml', ''];

const busImage: BusAttachment = {
  file_id: 'att_1',
  mime_type: 'image/png',
  original_name: 'chart',
  size_bytes: 10,
  category: 'image',
  rel_path: 'user_a/_shared/bus_files/att_1',
};

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => BLOB_URL);
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function rendered(container: HTMLElement): Promise<HTMLImageElement> {
  return waitFor(() => {
    const img = container.querySelector('img');
    if (!img) throw new Error('image not rendered yet');
    return img;
  });
}

describe('AttachmentImage', () => {
  test.each(OPENABLE)('%s opens in a new tab', async (type) => {
    fetchAttachmentBlob.mockResolvedValue(new Blob(['x'], { type }));
    const { container } = render(
      <AttachmentImage agentId="a" userId="u" fileId="f" alt="pic" zoomable />,
    );
    await rendered(container);
    expect(container.querySelector('a')?.getAttribute('href')).toBe(BLOB_URL);
  });

  test.each(NOT_OPENABLE)('%j renders as an image with no link', async (type) => {
    fetchAttachmentBlob.mockResolvedValue(new Blob(['<svg onload="x()"/>'], { type }));
    const { container } = render(
      <AttachmentImage agentId="a" userId="u" fileId="f" alt="pic" zoomable />,
    );
    const img = await rendered(container);
    expect(img.getAttribute('src')).toBe(BLOB_URL);
    expect(container.querySelector('a')).toBeNull();
  });

  test('without zoomable there is never a link', async () => {
    fetchAttachmentBlob.mockResolvedValue(new Blob(['x'], { type: 'image/png' }));
    const { container } = render(<AttachmentImage agentId="a" userId="u" fileId="f" alt="pic" />);
    await rendered(container);
    expect(container.querySelector('a')).toBeNull();
  });
});

describe('BusAttachmentList image thumbnail', () => {
  test.each(OPENABLE)('%s opens in a new tab', async (type) => {
    fetchBusAttachmentBlob.mockResolvedValue(new Blob(['x'], { type }));
    const { container } = render(<BusAttachmentList attachments={[busImage]} />);
    await rendered(container);
    expect(container.querySelector('a')?.getAttribute('href')).toBe(BLOB_URL);
  });

  test.each(NOT_OPENABLE)('%j renders as an image with no link', async (type) => {
    fetchBusAttachmentBlob.mockResolvedValue(new Blob(['<svg onload="x()"/>'], { type }));
    const { container } = render(<BusAttachmentList attachments={[busImage]} />);
    const img = await rendered(container);
    expect(img.getAttribute('src')).toBe(BLOB_URL);
    expect(container.querySelector('a')).toBeNull();
  });
});
