/**
 * The two fallbacks the directory and profile page rely on, and the one
 * invariant that labels come from the picker list rather than a private copy.
 */
import { describe, it, expect } from 'vitest';
import { Bot } from 'lucide-react';
import { formatFramework, frameworkBrandIcon, frameworkIconInvertsInDark } from '../frameworkBrand';
import { AGENT_FRAMEWORKS } from '../agentFramework';
import { OpenAIBrandIcon, ClaudeBrandIcon } from '@/components/icons/ModelBrandIcons';

describe('frameworkBrand', () => {
  it('labels every known framework exactly as the picker does', () => {
    for (const f of AGENT_FRAMEWORKS) expect(formatFramework(f.id)).toBe(f.label);
    expect(formatFramework('nexus_power')).toBe('NexusPower-beta');
  });

  it('title-cases an unknown id instead of mapping it to another brand', () => {
    expect(formatFramework('some_new_fw')).toBe('Some New Fw');
    expect(frameworkBrandIcon('some_new_fw')).toBe(Bot);
  });

  it('renders a missing id as — with the generic glyph, never a default brand', () => {
    expect(formatFramework(undefined)).toBe('—');
    expect(formatFramework(null)).toBe('—');
    expect(frameworkBrandIcon(undefined)).toBe(Bot);
  });

  it('knows which mark needs inverting in dark mode', () => {
    expect(frameworkIconInvertsInDark(OpenAIBrandIcon)).toBe(true);
    expect(frameworkIconInvertsInDark(ClaudeBrandIcon)).toBe(false);
    expect(frameworkIconInvertsInDark(Bot)).toBe(false);
  });
});
