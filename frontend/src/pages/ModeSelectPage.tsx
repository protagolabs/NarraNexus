/**
 * @file_name: ModeSelectPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: First-launch mode selection page
 *
 * Displays two large cards for choosing between Local Mode and Cloud Mode.
 * Cloud mode prompts for API URL before proceeding.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Monitor, Cloud, Sparkles, ArrowRight } from 'lucide-react';
import { Button, Input } from '@/components/ui';
import { useRuntimeStore } from '@/stores/runtimeStore';
import { cn } from '@/lib/utils';

interface ModeCardProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  selected?: boolean;
  onClick: () => void;
}

function ModeCard({ icon, title, description, selected, onClick }: ModeCardProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'group relative flex flex-col items-center gap-5 p-10 rounded-2xl',
        'bg-[var(--bg-secondary)] border',
        'hover:border-[var(--accent-primary)] hover:shadow-[0_0_30px_var(--accent-glow)]',
        'transition-all duration-300 cursor-pointer',
        'w-80',
        selected
          ? 'border-[var(--accent-primary)] shadow-[0_0_30px_var(--accent-glow)]'
          : 'border-[var(--border-default)]',
      )}
    >
      <div className="absolute inset-0 rounded-2xl bg-[var(--accent-primary)] opacity-0 group-hover:opacity-5 transition-opacity duration-300" />

      <div className="relative w-16 h-16 rounded-2xl bg-[var(--gradient-primary)] flex items-center justify-center shadow-[0_0_20px_var(--accent-glow)]">
        {icon}
        <div className="absolute -inset-1 rounded-2xl bg-[var(--accent-primary)] opacity-20 blur-md -z-10" />
      </div>

      <div className="text-center space-y-2">
        <h3 className="text-lg font-semibold text-[var(--text-primary)] group-hover:text-[var(--accent-primary)] transition-colors">
          {title}
        </h3>
        <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
          {description}
        </p>
      </div>
    </button>
  );
}

export function ModeSelectPage() {
  const navigate = useNavigate();
  const { setMode, setCloudApiUrl } = useRuntimeStore();
  const [showCloudConfig, setShowCloudConfig] = useState(false);
  const [apiUrl, setApiUrl] = useState('https://');

  const handleLocal = () => {
    setMode('local');
    navigate('/login');
  };

  const handleCloudSelect = () => {
    setShowCloudConfig(true);
  };

  const handleCloudConnect = () => {
    if (!apiUrl || apiUrl === 'https://') return;
    // Remove trailing slash
    const cleanUrl = apiUrl.replace(/\/+$/, '');
    setCloudApiUrl(cleanUrl);
    setMode('cloud-app');
    navigate('/login');
  };

  return (
    <div className="h-screen w-screen flex flex-col items-center justify-center bg-[var(--bg-deep)] gap-12">
      {/* Title */}
      <div className="text-center space-y-3 animate-fade-in">
        <div className="flex items-center justify-center gap-3 mb-2">
          <div className="w-10 h-10 rounded-xl bg-[var(--gradient-primary)] flex items-center justify-center shadow-[0_0_20px_var(--accent-glow)]">
            <Sparkles className="w-5 h-5 text-[var(--text-inverse)] dark:text-[var(--bg-deep)]" />
          </div>
        </div>
        <h1 className="text-3xl font-bold text-[var(--text-primary)] font-[family-name:var(--font-display)] tracking-tight">
          Welcome to <span className="text-[var(--accent-primary)]">NarraNexus</span>
        </h1>
        <p className="text-sm text-[var(--text-secondary)]">
          Choose how you want to run the platform
        </p>
      </div>

      {/* Mode cards */}
      <div className="flex gap-8 animate-fade-in" style={{ animationDelay: '0.1s' }}>
        <ModeCard
          icon={<Monitor className="w-7 h-7 text-[var(--text-inverse)] dark:text-[var(--bg-deep)]" />}
          title="Local Mode"
          description="Everything runs on your machine. Your data stays local. Offline capable."
          onClick={handleLocal}
        />
        <ModeCard
          icon={<Cloud className="w-7 h-7 text-[var(--text-inverse)] dark:text-[var(--bg-deep)]" />}
          title="Cloud Mode"
          description="Connect to cloud services. Access from any device. Share with others."
          selected={showCloudConfig}
          onClick={handleCloudSelect}
        />
      </div>

      {/* Cloud API URL input */}
      {showCloudConfig && (
        <div className="animate-fade-in flex flex-col items-center gap-4 w-full max-w-md">
          <div className="w-full space-y-2">
            <label className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
              Cloud Server URL
            </label>
            <div className="flex gap-2">
              <Input
                value={apiUrl}
                onChange={(e) => setApiUrl(e.target.value)}
                placeholder="https://api.your-server.com"
                className="flex-1"
              />
              <Button
                variant="accent"
                onClick={handleCloudConnect}
                disabled={!apiUrl || apiUrl === 'https://'}
              >
                Connect
                <ArrowRight className="w-4 h-4 ml-1" />
              </Button>
            </div>
            <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
              Enter the URL of your NarraNexus cloud server
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default ModeSelectPage;
