#!/bin/bash
# Start frontend dev server (port 5173)

export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
    source "$NVM_DIR/nvm.sh"
    nvm use 20 2>/dev/null || nvm use default 2>/dev/null || true
fi

echo "Node.js: $(node --version 2>/dev/null || echo 'not found')"

cd "$(dirname "$0")/../frontend"
npm install --silent && npm run dev
