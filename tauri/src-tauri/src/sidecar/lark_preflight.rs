// Lark skill pack preflight — bundle-first, npx-fallback.
//
// The `lark_skill` MCP tool resolves SKILL.md files from
// `~/.agents/skills/lark-*/`. If those directories are missing, every
// Lark-related call fails and the user sees unhelpful "SKILL.md not found"
// errors.
//
// Two iterations of this preflight:
//
// v1 (earlier): installed at runtime via `npx skills add larksuite/cli`.
//   Required working bundled npx + outbound network on first launch.
//   Both were fragile — npx was broken by Tauri bundler's symlink
//   flattening, and China users commonly couldn't reach npm registry.
//
// v2 (current): build-desktop.sh now installs the skill pack at BUILD
//   time and ships the directories inside the dmg at
//   Contents/Resources/resources/lark-skills/. At first launch we simply
//   copy them into ~/.agents/skills/. Zero network, zero npx dependency.
//   Still keep the npx fallback for dev mode (no bundle) and the odd
//   case where a build was produced without skills (WARN path in
//   build-desktop.sh).
//
// Critical for user UX: we don't block. The copy is fast (~10 ms for
// ~5 MB of markdown) but still runs on a tauri::async_runtime::spawn
// task so startup isn't gated on it.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;
use tokio::process::Command;
use tokio::time::timeout;

use crate::state::{resolve_bundled_node_bins, resolve_resource_dir};

const LARK_SKILLS_INSTALL_TIMEOUT: Duration = Duration::from_secs(180);

/// Entry point — spawn as a detached task in setup(). Non-blocking.
///
/// Using `tauri::async_runtime::spawn` (not `tokio::spawn`) because setup()
/// fires on the Cocoa main thread where no tokio runtime handle is bound;
/// a bare `tokio::spawn` there would panic inside an FFI callback and abort
/// the app at launch.
pub fn run_preflight() {
    tauri::async_runtime::spawn(async move {
        if lark_skills_present() {
            log::info!("lark preflight: skill pack already in ~/.agents/skills/");
            return;
        }

        // Primary: copy from the bundle shipped with the dmg.
        if let Some(src) = find_bundled_skills() {
            match copy_bundled_skills_to_home(&src) {
                Ok(n) if n > 0 => {
                    log::info!(
                        "lark preflight: copied {} bundled lark-* skills into ~/.agents/skills/",
                        n
                    );
                    return;
                }
                Ok(_) => {
                    log::info!(
                        "lark preflight: bundled skills dir exists but contained nothing to copy"
                    );
                }
                Err(e) => {
                    log::warn!(
                        "lark preflight: bundled skill copy failed ({}); falling back to npx",
                        e
                    );
                }
            }
        } else {
            log::info!("lark preflight: no bundled skills (dev mode or build-time install skipped)");
        }

        // Fallback: network install via bundled npx. Only reached when the
        // dmg was built without bundled skills (dev mode, or build-desktop.sh
        // hit the WARN path). Still best-effort, still graceful-degrade.
        let npx = match find_bundled_npx() {
            Some(p) => p,
            None => {
                log::info!(
                    "lark preflight: no bundled npx to fall back to — skipping. \
                     Lark features will report 'SKILL.md not found' until the user \
                     manually runs `npx skills add larksuite/cli -y -g`."
                );
                return;
            }
        };
        install_skill_pack_via_npx(&npx).await;
    });
}

fn lark_skills_present() -> bool {
    let home = match dirs::home_dir() {
        Some(h) => h,
        None => return false,
    };
    // Two locations — one is the real install dir, the other is a symlink
    // pattern the skills CLI sometimes creates for claude-code interop.
    let candidates = [
        PathBuf::from(".agents/skills/lark-shared/SKILL.md"),
        PathBuf::from(".claude/skills/lark-shared/SKILL.md"),
    ];
    candidates.iter().any(|rel| home.join(rel).exists())
}

/// Locate the bundled skill pack inside the .app Resources/ tree.
/// Returns Some only if the directory contains at least one lark-* child
/// (an empty dir from a failed build shouldn't count as "bundled").
fn find_bundled_skills() -> Option<PathBuf> {
    let resources = resolve_resource_dir();
    for subdir in &["resources/lark-skills", "lark-skills"] {
        let p = resources.join(subdir);
        if !p.is_dir() {
            continue;
        }
        let has_lark_child = std::fs::read_dir(&p)
            .ok()?
            .filter_map(|e| e.ok())
            .any(|e| e.file_name().to_string_lossy().starts_with("lark-"));
        if has_lark_child {
            return Some(p);
        }
    }
    None
}

/// Copy each `lark-*` directory from the bundle into ~/.agents/skills/.
/// Skips any skill dir that already exists so we never clobber user edits
/// or a newer version installed by other tooling.
fn copy_bundled_skills_to_home(src: &Path) -> std::io::Result<usize> {
    let home = dirs::home_dir().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "cannot locate home dir")
    })?;
    let target_root = home.join(".agents/skills");
    std::fs::create_dir_all(&target_root)?;

    let mut copied = 0;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        if !name_str.starts_with("lark-") {
            continue;
        }
        let dest = target_root.join(&name);
        if dest.exists() {
            continue; // don't clobber user's existing install
        }
        copy_dir_recursive(&entry.path(), &dest)?;
        copied += 1;
    }
    Ok(copied)
}

/// Minimal recursive copy. No external dep — keeps the Tauri binary slim.
/// Symlinks are dereferenced (canonicalize) then copied as files; this
/// matches `cp -RL` semantics and ensures the user's copy is self-contained.
fn copy_dir_recursive(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let src_path = entry.path();
        let dst_path = dst.join(entry.file_name());
        let ty = entry.file_type()?;
        if ty.is_dir() {
            copy_dir_recursive(&src_path, &dst_path)?;
        } else {
            let real_src = std::fs::canonicalize(&src_path).unwrap_or_else(|_| src_path.clone());
            std::fs::copy(&real_src, &dst_path)?;
        }
    }
    Ok(())
}

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

async fn install_skill_pack_via_npx(npx: &PathBuf) {
    log::info!(
        "lark preflight: fallback network install via bundled npx (timeout {}s)",
        LARK_SKILLS_INSTALL_TIMEOUT.as_secs()
    );

    // Prepend bundled node bin dirs so the npx bash shim can find `node`.
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

    let fut = Command::new(npx)
        .args(["skills", "add", "larksuite/cli", "-y", "-g"])
        .env("PATH", &child_path)
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .status();

    match timeout(LARK_SKILLS_INSTALL_TIMEOUT, fut).await {
        Ok(Ok(status)) if status.success() => {
            log::info!("lark preflight: npx fallback skill install OK");
        }
        Ok(Ok(status)) => {
            log::warn!(
                "lark preflight: npx fallback exited {} — skill pack not installed",
                status
            );
        }
        Ok(Err(e)) => {
            log::warn!("lark preflight: failed to spawn npx fallback: {}", e);
        }
        Err(_) => {
            log::warn!(
                "lark preflight: npx fallback hung > {}s — abandoning",
                LARK_SKILLS_INSTALL_TIMEOUT.as_secs()
            );
        }
    }
}
