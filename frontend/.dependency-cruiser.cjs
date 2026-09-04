/**
 * Dependency-direction gate for the plugin platform (batch 0).
 *
 * Rules are scoped to the future plugin host (src/plugins, src/contracts)
 * so they pass today and start biting the moment those directories appear.
 * Circular imports are reported as warnings until the existing cycles are
 * paid down; the rule exists so new cycles show up in review.
 */
module.exports = {
  forbidden: [
    {
      name: 'plugins-only-import-contracts',
      comment: 'src/plugins/** may import only itself and src/contracts/**',
      severity: 'error',
      from: { path: '^src/plugins/' },
      to: { pathNot: '^(src/plugins/|src/contracts/|node_modules/)' },
    },
    {
      name: 'contracts-are-a-leaf',
      comment: 'src/contracts/** must not import application code',
      severity: 'error',
      from: { path: '^src/contracts/' },
      to: { path: '^src/', pathNot: '^src/contracts/' },
    },
    {
      name: 'no-circular',
      severity: 'warn',
      from: {},
      to: { circular: true },
    },
  ],
  options: {
    doNotFollow: { path: 'node_modules' },
    tsConfig: { fileName: 'tsconfig.app.json' },
    tsPreCompilationDeps: true,
    exclude: { path: '__tests__|\\.test\\.|test-setup' },
  },
};
