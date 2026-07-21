---
code_file: frontend/src/pages/MarketplacePage.tsx
last_verified: 2026-07-21
stub: false
---

# MarketplacePage.tsx

Full-page Skill Marketplace — the left-sidebar first-class entry (route
`/app/marketplace`, lazy-loaded like the other pages). Browse-first UX:
debounced search + client-side category filter chips + responsive card grid
(md:2 / xl:3 cols) + the shared SkillDetailSheet. Reuses the exact same
hooks (`useSkillMarketplace`) and shared `MarketplaceCard` as the Skill
tab's dialog (`MarketplaceBrowser`) — one install pipeline, two entrances.
Category filtering is client-side over the search page (catalog is small);
switch to the server `category` param if the catalog outgrows one page.
