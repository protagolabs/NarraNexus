export const plugin = {
  activate(host) {
    const { react } = host.libs;
    host.i18n.addResourceBundle('en', { title: 'Hello World' });
    host.register(host.registries.pages, 'acme.hello_world.home', { path: 'x/hello', element: () => react.createElement('p', null, host.i18n.t('title')), guard: 'protected', layout: 'app' });
    host.register(host.registries.panels, 'acme.hello_world.panel', { component: ({ agentId }) => react.createElement('p', null, 'panel for ' + agentId) });
    host.register(host.registries.commands, 'acme.hello_world.wave', { label: 'Wave', run: () => { globalThis.__hello_waved = true; } });
    host.register(host.registries.themes, 'acme.hello_world.theme', { displayName: 'Hello dark', tokens: { '--nm-ink': '#eee' }, dark: true });
  },
};
