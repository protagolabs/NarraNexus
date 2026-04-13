# Phase 4: Tauri 2 Shell — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a Tauri 2 desktop application shell that replaces Electron. Manages Python backend processes, monitors health, provides system tray, and auto-updates.

**Architecture:** Tauri 2 app with Rust backend managing Python sidecar processes. Frontend loaded from `../frontend/dist`. IPC commands map to the PlatformBridge interface defined in Phase 3.

**Tech Stack:** Rust, Tauri 2, tokio, serde

---

## Task 1: Initialize Tauri project structure

Create the `tauri/` directory with:
- `src-tauri/Cargo.toml` — Tauri 2 dependencies
- `src-tauri/tauri.conf.json` — app config, window, bundle settings
- `src-tauri/capabilities/default.json` — permissions
- `src-tauri/src/main.rs` — entry point
- `src-tauri/src/lib.rs` — module declarations
- `src-tauri/icons/` — placeholder icons

## Task 2: App state and config management

- `src-tauri/src/state.rs` — AppState, AppConfig, ServiceDef structs
- Config loaded from file or defaults
- Mode detection (local/cloud)

## Task 3: Process manager

- `src-tauri/src/sidecar/mod.rs`
- `src-tauri/src/sidecar/process_manager.rs` — spawn/stop/restart Python processes
- `src-tauri/src/sidecar/health_monitor.rs` — TCP port checks, HTTP health checks

## Task 4: IPC commands

- `src-tauri/src/commands/mod.rs`
- `src-tauri/src/commands/service.rs` — get_service_status, start_all, stop_all, restart
- `src-tauri/src/commands/config.rs` — get_app_config, get_app_mode
- `src-tauri/src/commands/health.rs` — get_health_status, get_logs

## Task 5: System tray and auto-updater

- `src-tauri/src/tray.rs` — system tray with start/stop/quit
- `src-tauri/src/updater.rs` — auto-update check
