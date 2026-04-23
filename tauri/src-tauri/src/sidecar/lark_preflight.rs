// Preflight for Lark/Feishu optional integration.
//
// Mirrors the `check_deps` flow in `run.sh`:
//   1. If `lark-cli` binary is missing on PATH → `npm install -g @larksuite/cli`
//   2. If `~/.agents/skills/lark-shared/SKILL.md` is missing (and no
//      `~/.claude/skills/lark-shared/SKILL.md` symlink) → `npx skills add
//      larksuite/cli -y -g`
//
// Everything is "best effort": failures log a warning but never block the app.
// If the user has no `node`/`npm` on PATH we skip entirely — Lark features
// degrade gracefully (the `lark_skill` MCP tool returns "not found" and the
// Agent falls back to `<domain> +<cmd> --help`).
//
// Iron rule #7 alignment: keep this in lockstep with the lark install block
// in scripts/run.sh. If you change timeouts / the install command there, fix
// them here too.

use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;
use tokio::process::Command;
use tokio::time::timeout;

const LARK_CLI_INSTALL_TIMEOUT: Duration = Duration::from_secs(120);
const LARK_SKILLS_INSTALL_TIMEOUT: Duration = Duration::from_secs(180);

/// Entry point — spawn as a detached task in setup(). Non-blocking.
pub fn run_preflight() {
    tokio::spawn(async move {
        if !command_exists("npm").await {
            log::warn!(
                "lark preflight: `npm` not on PATH — skipping lark-cli install. \
                 Lark/Feishu features will be disabled. Install Node.js from \
                 https://nodejs.org/ and relaunch to enable them."
            );
            return;
        }

        install_lark_cli_if_missing().await;
        install_lark_skills_if_missing().await;
    });
}

async fn command_exists(cmd: &str) -> bool {
    // `command -v` returns 0 iff the command resolves on PATH. Pipe output to
    // /dev/null — we only care about the exit status.
    Command::new("sh")
        .arg("-c")
        .arg(format!("command -v {}", cmd))
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await
        .map(|s| s.success())
        .unwrap_or(false)
}

async fn install_lark_cli_if_missing() {
    if command_exists("lark-cli").await {
        log::info!("lark preflight: lark-cli already installed");
        return;
    }

    log::info!(
        "lark preflight: lark-cli not found — running `npm install -g @larksuite/cli` (timeout {}s)",
        LARK_CLI_INSTALL_TIMEOUT.as_secs()
    );

    let fut = Command::new("npm")
        .args(["install", "-g", "@larksuite/cli"])
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .status();

    match timeout(LARK_CLI_INSTALL_TIMEOUT, fut).await {
        Ok(Ok(status)) if status.success() => {
            log::info!("lark preflight: lark-cli install OK");
        }
        Ok(Ok(status)) => {
            log::warn!(
                "lark preflight: npm install exited {} — Lark features will be disabled. \
                 Common fixes: change registry (`npm config set registry \
                 https://registry.npmmirror.com`), fix permissions (use nvm or sudo), \
                 or check network to registry.npmjs.org.",
                status
            );
        }
        Ok(Err(e)) => {
            log::warn!("lark preflight: failed to spawn npm: {}", e);
        }
        Err(_) => {
            log::warn!(
                "lark preflight: npm install hung > {}s — abandoning. Retry manually: \
                 `npm install -g @larksuite/cli`",
                LARK_CLI_INSTALL_TIMEOUT.as_secs()
            );
        }
    }
}

async fn install_lark_skills_if_missing() {
    if lark_skills_present() {
        log::info!("lark preflight: lark-shared skill pack already installed");
        return;
    }

    log::info!(
        "lark preflight: installing Lark CLI Skills via `npx skills add larksuite/cli -y -g` (timeout {}s)",
        LARK_SKILLS_INSTALL_TIMEOUT.as_secs()
    );

    // Mirror run.sh: `HOME=$HOME npx skills add larksuite/cli -y -g`.
    // HOME is already inherited from our env, so no need to re-export.
    let fut = Command::new("npx")
        .args(["skills", "add", "larksuite/cli", "-y", "-g"])
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .status();

    match timeout(LARK_SKILLS_INSTALL_TIMEOUT, fut).await {
        Ok(Ok(status)) if status.success() => {
            log::info!("lark preflight: skill pack install OK");
        }
        Ok(Ok(status)) => {
            log::warn!(
                "lark preflight: npx skills add exited {} — `lark_skill(...)` MCP tool \
                 will return 'not found'. Retry manually: \
                 `HOME=$HOME npx skills add larksuite/cli -y -g`",
                status
            );
        }
        Ok(Err(e)) => {
            log::warn!("lark preflight: failed to spawn npx: {}", e);
        }
        Err(_) => {
            log::warn!(
                "lark preflight: npx skills add hung > {}s — abandoning.",
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
