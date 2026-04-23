#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TAURI_DIR="$PROJECT_ROOT/tauri"
SRC_TAURI="$TAURI_DIR/src-tauri"
RESOURCES_DIR="$SRC_TAURI/resources"
PYTHON_DIR="$RESOURCES_DIR/python"
PROJ_DIR="$RESOURCES_DIR/project"
NODE_DIR="$RESOURCES_DIR/nodejs"

# Node.js version — pinned to an LTS so claude-code / lark-cli compatibility is
# predictable. Bump deliberately; we take on the support tail whenever we bump.
# Override via env if you need to test a different version.
NODE_VERSION="${NODE_VERSION:-v22.11.0}"

echo "=== NarraNexus Desktop Build ==="
echo "Project root: $PROJECT_ROOT"
echo ""

# Step 0: Clean previous build artifacts
echo "--- Step 0: Cleaning previous build ---"
rm -rf "$PYTHON_DIR"
rm -rf "$PROJ_DIR"
rm -rf "$NODE_DIR"
rm -rf "$RESOURCES_DIR/venv"
rm -rf "$SRC_TAURI/target"
mkdir -p "$PYTHON_DIR"
mkdir -p "$PROJ_DIR"
mkdir -p "$NODE_DIR"
echo "Clean done"

# Step 1: Build frontend
echo ""
echo "--- Step 1: Building frontend ---"
cd "$PROJECT_ROOT/frontend"
npm ci
npm run build
echo "Frontend build complete"

# Step 2: Download standalone Python
echo ""
echo "--- Step 2: Downloading standalone Python ---"
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
    PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260325/cpython-3.13.12%2B20260325-aarch64-apple-darwin-install_only.tar.gz"
else
    PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260325/cpython-3.13.12%2B20260325-x86_64-apple-darwin-install_only.tar.gz"
fi

curl -L -o /tmp/python-standalone.tar.gz "$PYTHON_URL"
tar xzf /tmp/python-standalone.tar.gz -C "$PYTHON_DIR" --strip-components=1
rm /tmp/python-standalone.tar.gz
echo "Python downloaded: $("$PYTHON_DIR/bin/python3" --version)"

# Step 3: Install Python dependencies directly into standalone Python
#
# NON-editable install (`pip install .` — no `-e`).
#
# Editable installs drop a `.pth` / `__editable__` file into site-packages whose
# contents are the ABSOLUTE path to the build machine's source tree
# (e.g. /Users/builder/NarraNexus/src). When the dmg is installed on another
# machine at /Applications/NarraNexus.app/Contents/..., that path no longer
# exists and every `import xyz_agent_context` / `import backend` blows up
# with ModuleNotFoundError. We saw this on fresh-machine installs.
#
# A wheel install copies the real files into site-packages, so the bundle is
# fully relocatable — move the .app anywhere and the imports still resolve.
echo ""
echo "--- Step 3: Installing Python dependencies ---"
"$PYTHON_DIR/bin/python3" -m pip install --no-cache-dir "$PROJECT_ROOT" 2>&1 | tail -5
echo "Python dependencies installed"

# Step 3.5: Bundle Node.js + CLI runtime dependencies.
#
# Why bundled, not user-installed:
#   claude_agent_sdk (our hard Python dep) spawns the `claude` CLI as a
#   subprocess — which is a Node.js script shipped via npm. A Mac end-user may
#   not have Node.js at all, and even if they do, Finder-launched .app
#   subprocesses get the launchd minimal PATH (`/usr/bin:/bin:/usr/sbin:/sbin`)
#   that usually doesn't include ~/.npm-global/bin, /opt/homebrew/bin, etc.
#   The only robust answer is to ship node + the CLIs inside the app bundle
#   and have process_manager.rs prepend their directories to PATH for every
#   spawned Python service.
#
# lark-cli is shipped the same way — previously run.sh did a best-effort
# `npm install -g @larksuite/cli`, which again required node on the user
# machine. Bundling it eliminates that dependency too.
#
# Bundle layout:
#   resources/nodejs/
#     bin/node                    ← interpreter for shebangs
#     bin/npm                     ← (only used at build time)
#     lib/node_modules/           ← node's own stdlib
#     node_modules/               ← our installed packages (claude-code, lark-cli)
#     node_modules/.bin/claude    ← shim exposed on PATH
#     node_modules/.bin/lark-cli  ← shim exposed on PATH
echo ""
echo "--- Step 3.5: Downloading Node.js $NODE_VERSION ---"
if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
    NODE_ARCH="arm64"
else
    NODE_ARCH="x64"
fi
NODE_TARBALL="node-${NODE_VERSION}-darwin-${NODE_ARCH}.tar.gz"
NODE_URL="https://nodejs.org/dist/${NODE_VERSION}/${NODE_TARBALL}"

curl -L --fail -o "/tmp/$NODE_TARBALL" "$NODE_URL"
tar xzf "/tmp/$NODE_TARBALL" -C "$NODE_DIR" --strip-components=1
rm "/tmp/$NODE_TARBALL"
echo "Node.js downloaded: $("$NODE_DIR/bin/node" --version)"

# Step 3.6: Install bundled CLIs (claude-code + lark-cli) into $NODE_DIR
#
# We install as *local* packages (no `-g`) so everything lives inside
# $NODE_DIR/node_modules/ and the shim binaries end up at
# $NODE_DIR/node_modules/.bin/. A local install keeps the bundle fully
# self-contained — no writing to /usr/local/, no sudo, no host state.
#
# `npm config set prefix` isolation prevents npm from reaching into the
# user's global prefix (e.g. ~/.npm-global) and picking up / clobbering their
# installs during build.
echo ""
echo "--- Step 3.6: Installing bundled CLIs (claude-code + lark-cli) ---"
cat > "$NODE_DIR/package.json" <<'NODEPKG'
{
  "name": "narranexus-bundled-clis",
  "private": true,
  "version": "0.0.0",
  "description": "Bundled CLI runtime for NarraNexus desktop — not published.",
  "dependencies": {
    "@anthropic-ai/claude-code": "latest",
    "@larksuite/cli": "latest"
  }
}
NODEPKG

# Use the bundled npm + node exclusively by prepending $NODE_DIR/bin to PATH
# and pointing npm's prefix at $NODE_DIR itself. --omit=dev is belt-and-braces
# (no devDeps declared) and --no-audit/--no-fund shave a chunk of startup.
(
    cd "$NODE_DIR"
    PATH="$NODE_DIR/bin:$PATH" \
    "$NODE_DIR/bin/npm" install \
        --omit=dev --no-audit --no-fund --loglevel=error \
        2>&1 | tail -10
)

# Sanity-check: the shims must exist at the path process_manager.rs will
# prepend to PATH at runtime. If either is missing the build is broken — fail
# loudly here rather than ship a half-working dmg.
for bin in claude lark-cli; do
    if [ ! -x "$NODE_DIR/node_modules/.bin/$bin" ]; then
        echo "ERROR: bundled CLI shim missing: $NODE_DIR/node_modules/.bin/$bin"
        echo "       npm install step above likely failed; re-run with verbose logging."
        exit 1
    fi
done
echo "Bundled CLIs installed:"
echo "  claude:   $("$NODE_DIR/node_modules/.bin/claude" --version 2>&1 | head -1 || echo '(version check failed)')"
echo "  lark-cli: $("$NODE_DIR/node_modules/.bin/lark-cli" --version 2>&1 | head -1 || echo '(version check failed)')"

# Step 4: Copy project source
echo ""
echo "--- Step 4: Copying project source ---"
rm -rf "$PROJ_DIR"
mkdir -p "$PROJ_DIR"
rsync -a \
    --exclude='node_modules' --exclude='.venv' --exclude='.git' \
    --exclude='desktop' --exclude='tauri' --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='.env' --exclude='logs' \
    --exclude='.claude' --exclude='.codex' --exclude='.worktrees' \
    --exclude='.evermemos' --exclude='related_project' \
    --exclude='sessions' --exclude='deploy' --exclude='tests' \
    --exclude='* 2' --exclude='.ruff_cache' --exclude='.pytest_cache' \
    --exclude='.vscode' --exclude='.DS_Store' --exclude='*.log' \
    --exclude='image.png' --exclude='.vite' \
    "$PROJECT_ROOT/" "$PROJ_DIR/"
echo "Project source copied"

# Step 5: Clean ALL extended attributes in tauri dir (macOS resource fork issue)
#
# macOS codesign refuses any file carrying extended attributes like
# com.apple.ResourceFork / com.apple.FinderInfo. iCloud, Spotlight, tar/rsync,
# even the linker can add them. `xattr -cr` clears them recursively; we do it
# multiple times around the build because cargo tauri's bundle step re-copies
# files and new xattrs can appear in between cleanup and codesign.
echo ""
echo "--- Step 5: Cleaning extended attributes ---"
# Prevent macOS cp/tar from writing AppleDouble (._*) sidecar files that carry xattrs
export COPYFILE_DISABLE=1
xattr -cr "$TAURI_DIR" 2>/dev/null || true
# Also strip any leftover ._ AppleDouble files
find "$TAURI_DIR" -name '._*' -delete 2>/dev/null || true
echo "xattr cleaned"

# Step 6: Build + bundle via cargo tauri. Its internal codesign WILL likely
# fail because macOS adds xattrs to the binary between the bundle copy and
# the sign step, and we can't hook in between. We let it try anyway — by
# then the .app directory is already assembled, so step 7 can clean xattrs
# and sign manually.
#
# Signing modes driven by the APPLE_SIGNING_IDENTITY env var:
#   unset / empty   → default to '-' (ad-hoc). Self-distribution only; users
#                     get a Gatekeeper warning and need right-click → Open.
#   '-'             → ad-hoc. Same as above, just explicit.
#   "Developer ID Application: Name (TEAMID)"
#                   → real Developer ID signing. Hardened runtime +
#                     entitlements kick in automatically in step 7. If
#                     APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID
#                     are also set, step 8 will notarize.
#   "Apple Development: …"
#                   → development-team signing. Works on the machines in the
#                     team's provisioning profile; NOT notarizable.
#
# An empty string causes tauri to report "no identity found" and bail before
# the .app is in a usable state, so we always fall back to '-'.
echo ""
echo "--- Step 6: Building Tauri app (sign may fail — fallback in step 7) ---"
cd "$TAURI_DIR"

if [ -z "${APPLE_SIGNING_IDENTITY:-}" ]; then
    export APPLE_SIGNING_IDENTITY='-'
    echo "  APPLE_SIGNING_IDENTITY unset → using ad-hoc ('-')"
else
    echo "  APPLE_SIGNING_IDENTITY='${APPLE_SIGNING_IDENTITY}'"
fi

# Real-sign detection: any identity that isn't '-' gets hardened runtime +
# entitlements + (if notarization creds present) notarization.
case "$APPLE_SIGNING_IDENTITY" in
    '-'|'')  IS_REAL_SIGN=0 ;;
    *)       IS_REAL_SIGN=1 ;;
esac

# Temporarily disable -e so a signing failure here doesn't kill the script.
# We only need the .app directory to exist for step 7 to succeed.
set +e
cargo tauri build
CARGO_EXIT=$?
set -e
if [ $CARGO_EXIT -ne 0 ]; then
    echo "cargo tauri build exited $CARGO_EXIT (expected if codesign failed)"
fi

# Step 7: Escape iCloud sync, clean xattrs, manually codesign, build DMG.
#
# Why ditto to /tmp:
# If the project lives under ~/Documents (default macOS iCloud Drive path),
# iCloud continuously re-writes com.apple.FinderInfo / ResourceFork metadata
# to every file in the bundle. `xattr -cr` clears them, then iCloud puts
# them back the instant codesign starts reading — a losing race. /tmp is
# never iCloud-managed, so once we ditto the .app over there with
# --noextattr --noqtn (which strips xattrs and the quarantine flag during
# the copy), they stay clean.
#
# All subsequent signing / DMG creation happens inside /tmp, then we copy
# the final artifacts back under the project tree for the developer to find.
echo ""
echo "--- Step 7: Signing & packaging ---"
SRC_APP_DIR="$SRC_TAURI/target/release/bundle/macos/NarraNexus.app"
if [ ! -d "$SRC_APP_DIR" ]; then
    echo "Error: .app bundle not found at $SRC_APP_DIR"
    echo "cargo tauri build failed before creating the .app (exit=$CARGO_EXIT)."
    echo "Check step 6 output for the real error."
    exit 1
fi

# Staging directory outside of any sync-watched tree.
STAGE_DIR="/tmp/narranexus-build-$$"
STAGE_APP="$STAGE_DIR/NarraNexus.app"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

# ditto with --noextattr --noqtn does the copy AND strips extended
# attributes / quarantine bits in one pass. More reliable than xattr -cr
# because the destination never has them in the first place.
echo "  Staging .app to $STAGE_APP (no extended attributes)..."
ditto --noextattr --noqtn "$SRC_APP_DIR" "$STAGE_APP"

# Belt-and-suspenders: also scrub anything ditto might have missed.
xattr -cr "$STAGE_APP" 2>/dev/null || true
find "$STAGE_APP" -name '._*' -delete 2>/dev/null || true
find "$STAGE_APP" -name '.DS_Store' -delete 2>/dev/null || true

# Sign the staged .app. Two code paths:
#
# 1. Ad-hoc ('-') — no Developer ID required; recipients get a Gatekeeper
#    warning and must right-click → Open on first launch.
# 2. Developer ID — full hardened-runtime sign with entitlements. This is
#    what notarization needs. We sign inner Mach-O binaries first (Python
#    interpreter, bundled node, dylibs) then the outer .app, otherwise
#    codesign rejects it with "resource envelope is obsolete".
#
# `--timestamp` is required for notarization; `--options runtime` enables
# hardened runtime; `--entitlements` applies the plist. `--force` + `--deep`
# together handle the case where cargo tauri already tried (and failed) to
# sign and left partial signatures in place.
ENTITLEMENTS_PLIST="$SRC_TAURI/entitlements.plist"

if [ "$IS_REAL_SIGN" -eq 1 ]; then
    echo "  Developer-ID signing staged .app with identity:"
    echo "    $APPLE_SIGNING_IDENTITY"

    # Sign inner Mach-O files first. find -perm +111 catches every
    # executable (Python, node, codesign hates when a parent is signed
    # before its children for hardened runtime).
    while IFS= read -r -d '' target; do
        # Skip symlinks (codesign follows them and may double-sign).
        [ -L "$target" ] && continue
        # Only try to sign Mach-O binaries / dylibs; text scripts and
        # data files are covered by the outer bundle seal.
        if file "$target" 2>/dev/null | grep -qE 'Mach-O|dynamically linked'; then
            codesign --force --timestamp --options runtime \
                --entitlements "$ENTITLEMENTS_PLIST" \
                --sign "$APPLE_SIGNING_IDENTITY" "$target" \
                2>/dev/null || echo "    (warn) could not sign $target"
        fi
    done < <(find "$STAGE_APP/Contents/Resources" -type f -print0 2>/dev/null)

    # Seal the outer bundle.
    codesign --force --deep --timestamp --options runtime \
        --entitlements "$ENTITLEMENTS_PLIST" \
        --sign "$APPLE_SIGNING_IDENTITY" "$STAGE_APP"

    codesign --verify --verbose=2 "$STAGE_APP" 2>&1 | head -5 || true
    # `spctl` checks Gatekeeper's view of the signature. Expected to fail
    # until notarization runs ("source=No Matching DeveloperID"); that's
    # just information, not a build failure.
    spctl --assess --verbose=4 --type execute "$STAGE_APP" 2>&1 | head -2 || true
    echo "  Developer-ID signing OK"
else
    # Ad-hoc sign.
    echo "  Ad-hoc signing staged .app..."
    codesign --force --deep --sign - "$STAGE_APP"
    codesign --verify --verbose=2 "$STAGE_APP" 2>&1 | head -3 || true
    echo "  Ad-hoc signing OK"
fi

# Create DMG from the staged .app
echo ""
echo "--- Creating DMG ---"
DMG_DIR="$SRC_TAURI/target/release/bundle/dmg"
mkdir -p "$DMG_DIR"
DMG_PATH="$DMG_DIR/NarraNexus.dmg"
rm -f "$DMG_PATH"
STAGE_DMG="$STAGE_DIR/NarraNexus.dmg"
hdiutil create -volname NarraNexus -srcfolder "$STAGE_APP" -ov -format UDZO "$STAGE_DMG"
# Copy the finished DMG back under the project tree
cp "$STAGE_DMG" "$DMG_PATH"
echo "DMG created: $DMG_PATH"

# Create ZIP (fewer quarantine issues than DMG when distributed over web)
ZIP_PATH="$DMG_DIR/NarraNexus.zip"
rm -f "$ZIP_PATH"
STAGE_ZIP="$STAGE_DIR/NarraNexus.zip"
(cd "$STAGE_DIR" && ditto -c -k --keepParent "NarraNexus.app" "NarraNexus.zip")
cp "$STAGE_ZIP" "$ZIP_PATH"
echo "ZIP created: $ZIP_PATH"

# Step 8: Notarization (opt-in via env vars).
#
# Fires only when all three credentials are set AND we ran a real Developer
# ID sign in step 7. Otherwise this is a no-op.
#
# Required env vars:
#   APPLE_ID                      — Apple ID email
#   APPLE_APP_SPECIFIC_PASSWORD   — app-specific password from appleid.apple.com
#                                   (NOT your Apple ID login password)
#   APPLE_TEAM_ID                 — 10-char team identifier from developer.apple.com
#
# We submit the DMG (not the .app) because notarytool accepts both but DMGs
# are what end users download. Stapling attaches the notarization ticket to
# the DMG so Gatekeeper can verify offline on first open.
if [ "$IS_REAL_SIGN" -eq 1 ] \
   && [ -n "${APPLE_ID:-}" ] \
   && [ -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" ] \
   && [ -n "${APPLE_TEAM_ID:-}" ]; then
    echo ""
    echo "--- Step 8: Notarizing DMG ---"
    echo "  Submitting $STAGE_DMG to Apple notary service..."
    # --wait blocks until notarization finishes (typ. 1-5 min). --output-format
    # json would let us machine-parse, but for human-driven builds the plain
    # output is more informative when things go wrong.
    xcrun notarytool submit "$STAGE_DMG" \
        --apple-id "$APPLE_ID" \
        --password "$APPLE_APP_SPECIFIC_PASSWORD" \
        --team-id "$APPLE_TEAM_ID" \
        --wait
    echo "  Stapling ticket to DMG..."
    xcrun stapler staple "$STAGE_DMG"
    xcrun stapler validate "$STAGE_DMG"
    # Copy stapled DMG back over the unstapled one.
    cp "$STAGE_DMG" "$DMG_PATH"
    echo "  Notarization + stapling OK"
elif [ "$IS_REAL_SIGN" -eq 1 ]; then
    echo ""
    echo "--- Step 8: Notarization skipped ---"
    echo "  Developer-ID signed but APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID not set."
    echo "  DMG will work on signing machine but end users will see Gatekeeper warnings."
    echo "  To notarize, export those vars and re-run this script."
fi

# Also overwrite the .app under src-tauri with the signed-clean copy so
# `cargo tauri build` artifacts are consistent with the DMG contents.
rm -rf "$SRC_APP_DIR"
ditto --noextattr --noqtn "$STAGE_APP" "$SRC_APP_DIR"

# Clean up staging directory
rm -rf "$STAGE_DIR"
cd "$TAURI_DIR"

echo ""
echo "=== Build complete ==="
echo ""

# Show output
DMG=$(find "$SRC_TAURI/target/release/bundle/dmg/" -name "*.dmg" 2>/dev/null | head -1)
APP=$(find "$SRC_TAURI/target/release/bundle/macos/" -name "*.app" -maxdepth 1 2>/dev/null | head -1)

if [ -n "$DMG" ]; then
    ls -lh "$DMG"
    echo ""
    echo "Install: open $DMG"
else
    echo "APP: $APP"
    echo "Run: open \"$APP\""
fi
