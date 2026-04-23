// Lark skill-pack preflight.
//
// Scope, post-bundling:
//   After we started bundling Node.js + @larksuite/cli inside the dmg (see
//   scripts/build-desktop.sh step 3.5-3.6), we no longer need to `npm install
//   -g` anything at runtime — the CLI binaries are guaranteed present on the
//   bundled PATH via state::resolve_bundled_node_bins().
//
//   What is still runtime-installed is the **skill pack** (the `lark_skill`
//   MCP tool's SKILL.md knowledge under ~/.agents/skills/lark-*/). Those are
//   data files distributed via `npx skills add larksuite/cli -y -g`, not the
//   CLI itself, so they have to live in the user's home directory to be
//   visible to other tooling (claude-code's skill system in particular).
//
//   We invoke the bundled `npx` binary directly (`resources/nodejs/bin/npx`)
//   so this works even on Macs with zero host Node.js.
//
// Iron rule #7 reminder: scripts/run.sh still does a full
// `npm install -g @larksuite/cli` + `npx skills add ...` because dev mode
// uses the host's node. Bundle mode is simpler.

use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;
use tokio::process::Command;
use tokio::time::timeout;

use crate::state::{resolve_bundled_node_bins, resolve_resource_dir};

const LARK_SKILLS_INSTALL_TIMEOUT: Duration = Duration::from_secs(180);

/// Entry point — spawn as a detached task in setup(). Non-blocking.
///
/// IMPORTANT: we use `tauri::async_runtime::spawn`, NOT `tokio::spawn`.
/// Tauri's `setup()` callback fires from the Cocoa main thread during
/// `NSApplicationDidFinishLaunching`, where no tokio runtime handle is bound
/// to the current thread. A bare `tokio::spawn` panics with "there is no
/// reactor running", and because this path is inside an Objective-C → Rust
/// FFI callback, the panic cannot unwind and the process aborts immediately
/// on launch (observed as SIGABRT at `panic_cannot_unwind` in
/// `tao::app_delegate::did_finish_launching`). `tauri::async_runtime::spawn`
/// schedules onto Tauri's managed runtime so we're safe regardless of which
/// thread drove setup().
pub fn run_preflight() {
    tauri::async_runtime::spawn(async move {
        if lark_skills_present() {
            log::info!("lark preflight: skill pack already installed");
            return;
        }

        let npx = match find_bundled_npx() {
            Some(p) => p,
            None => {
                // Dev mode: no bundled node. Fall back to system npx if
                // present. If nothing is available, warn and move on — the
                // user is likely running `bash run.sh` which has its own
                // install block.
                log::info!(
                    "lark preflight: no bundled npx — skipping runtime skill install \
                     (dev mode; run.sh handles this path)"
                );
                return;
            }
        };

        install_skill_pack(&npx).await;
    });
}

/// Locate the bundled npx shim. In bundle mode this is at
/// `resources/nodejs/bin/npx`. Returns None in dev mode.
fn find_bundled_npx() -> Option<PathBuf> {
    let resources = resolve_resource_dir();
    for subdir in &["resources/nodejs/bin/npx", "nodejs/bin/npx"] {
        let p = resources.join(subdir);
        if p.exists() {
            return Some(p);
        }
    }
    None
}

async fn install_skill_pack(npx: &PathBuf) {
    log::info!(
        "lark preflight: installing Lark CLI skill pack via bundled npx (timeout {}s)",
        LARK_SKILLS_INSTALL_TIMEOUT.as_secs()
    );

    // Build PATH with bundled node bins first, so `npx` itself finds the
    // bundled node interpreter (its shebang is `#!/usr/bin/env node`).
    let path_prefix = resolve_bundled_node_bins()
        .iter()
        .map(|p| p.to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join(":");
    let parent_path = std::env::var("PATH").unwrap_or_default();
    let child_path = if path_prefix.is_empty() {
        parent_path
    } else if parent_path.is_empty() {
        path_prefix
    } else {
        format!("{}:{}", path_prefix, parent_path)
    };

    // Mirror scripts/run.sh: `HOME=$HOME npx skills add larksuite/cli -y -g`.
    // HOME is inherited; we just need to make sure the bundled node is on
    // PATH so the npx shim resolves.
    let fut = Command::new(npx)
        .args(["skills", "add", "larksuite/cli", "-y", "-g"])
        .env("PATH", &child_path)
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .status();

    match timeout(LARK_SKILLS_INSTALL_TIMEOUT, fut).await {
        Ok(Ok(status)) if status.success() => {
            log::info!("lark preflight: skill pack install OK");
        }
        Ok(Ok(status)) => {
            log::warn!(
                "lark preflight: bundled npx skills add exited {} — `lark_skill(...)` \
                 MCP tool will return 'not found' until the user re-launches with \
                 network access.",
                status
            );
        }
        Ok(Err(e)) => {
            log::warn!("lark preflight: failed to spawn bundled npx: {}", e);
        }
        Err(_) => {
            log::warn!(
                "lark preflight: bundled npx skills add hung > {}s — abandoning. \
                 Likely a slow / blocked npm registry; retry later.",
                LARK_SKILLS_INSTALL_TIMEOUT.as_secs()
            );
        }
    }
}

fn lark_skills_present() -> bool {
    // Mirror run.sh's two-location check:
    //   ~/.agents/skills/lark-shared/SKILL.md
    //   ~/.claude/skills/lark-shared/SKILL.md  (symlink created by `skills add`)
    let home = match dirs::home_dir() {
        Some(h) => h,
        None => return false,
    };
    let candidates = [
        PathBuf::from(".agents/skills/lark-shared/SKILL.md"),
        PathBuf::from(".claude/skills/lark-shared/SKILL.md"),
    ];
    candidates.iter().any(|rel| home.join(rel).exists())
}
