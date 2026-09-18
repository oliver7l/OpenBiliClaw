//! 客户端登录切换：把 TRAE SOLO CN 的登录态在账号池里**一键换人**。
//!
//! ## 为什么是「整体交换 storage.json」而不是「往里写 token」
//!
//! 登录态不是一个 token，而是一整套身份：加密的 `iCubeAuthInfo` blob（token +
//! refresh token + userId + 签发时的设备密钥对）+ 顶层的 `telemetry.devDeviceId` /
//! `telemetry.machineId`。续签要求「DeviceID 一致 **且** 用签发时那对私钥签名」
//! （否则 20403），所以**任何一个字段单独换都会破坏身份一致性**——唯一安全的做法
//! 是把上一个账号的整份 `storage.json` 换成目标账号的整份快照。
//!
//! ## 档案（profile）从哪来
//!
//! 每个账号的登录态快照存在助手数据目录 `traework_profiles/<uid>/storage.json`，
//! **与本机多开副本共享**（多开副本 = `TRAE SOLO CN 2/3/4.app` 各自的数据目录，
//! 每份 4-6GB——切换的最终目的就是不再依赖它们）。每次 `status()` / `switch_login()`
//! 都会先做一轮**自动捕获**：扫描所有 TRAE 系数据目录，解析出 uid，把比档案更新的
//! 快照存回去。所以用户**永远不需要手动「捕获」**——多开副本登录了新号、客户端自己
//! 续了签，下一轮自动捕获就会把新态收进档案。
//!
//! ## 会话为什么不会丢
//!
//! - 会话本体在云端**按账号（uid）**存储：切到 B 看不到 A 的会话，切回 A 时整批恢复；
//! - 本地缓存（`state.vscdb` 的 `local_conversation_share:<uid>:` 等键）**按 uid 命名
//!   空间隔离**，多账号在同一客户端里共存，切换互不覆盖。
//!
//! ## 安全次序（任何一步不通过，一个文件都不动）
//!
//! 退出客户端 →（不通过就中止，**绝不强杀**——IDE 里可能有未保存内容）
//! → 当前登录态回存档案 → 写入目标档案 → 清缓存 → 校验 uid → 重启客户端。

use crate::accounts::Account;
use serde::Serialize;
use std::path::{Path, PathBuf};

/// 主客户端（切换的目标载体）。多开副本（`TRAE SOLO CN 2.app` 等）只是档案来源。
const MAIN_APP: &str = "TRAE SOLO CN";

// ---------------------------------------------------------------------------
// 状态与结果结构
// ---------------------------------------------------------------------------

#[derive(Serialize, Clone)]
pub struct ProfileMeta {
    pub uid: String,
    pub phone: Option<String>,
    pub nickname: Option<String>,
    /// 档案最后来源（哪个数据目录 / 「切换前回存」）
    pub source: String,
    pub captured_at: String,
}

#[derive(Serialize, Clone)]
pub struct ClientLoginStatus {
    /// 本机是否支持切换（当前仅 macOS）
    pub supported: bool,
    pub message: String,
    /// 主客户端当前登录的 uid（解析失败 = None）
    pub current_uid: Option<String>,
    pub profiles: Vec<ProfileMeta>,
}

// ---------------------------------------------------------------------------
// 路径
// ---------------------------------------------------------------------------

#[cfg(target_os = "macos")]
fn app_support() -> Option<PathBuf> {
    dirs::home_dir().map(|h| h.join("Library").join("Application Support"))
}

/// 主客户端的 `storage.json`
#[cfg(target_os = "macos")]
fn main_storage() -> Option<PathBuf> {
    Some(
        app_support()?
            .join(MAIN_APP)
            .join("User")
            .join("globalStorage")
            .join("storage.json"),
    )
}

/// 主客户端数据目录（清理缓存用）
#[cfg(target_os = "macos")]
fn main_dir() -> Option<PathBuf> {
    app_support().map(|b| b.join(MAIN_APP))
}

/// 档案目录：与外部脚本 `twa_switch_account.py` 共用同一份
fn profiles_dir(data_dir: &Path) -> PathBuf {
    data_dir.join("traework_profiles")
}

// ---------------------------------------------------------------------------
// 扫描与档案
// ---------------------------------------------------------------------------

/// 所有 TRAE 系数据目录。**SOLO 系在前且含带后缀的多开副本**；
/// `TRAE CN` 是另一个产品（Trae CN IDE），只用来补缺、不覆盖 SOLO 快照。
#[cfg(target_os = "macos")]
fn scan_roots() -> Vec<(PathBuf, bool)> {
    let mut out: Vec<(PathBuf, bool)> = Vec::new();
    let Some(base) = app_support() else {
        return out;
    };
    // SOLO 系：精确名 + 数字后缀副本，升序保证「TRAE SOLO CN」本体最先
    if let Ok(rd) = std::fs::read_dir(&base) {
        let mut solo: Vec<PathBuf> = rd
            .flatten()
            .map(|e| e.path())
            .filter(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .map(|n| n == MAIN_APP || n.starts_with("TRAE SOLO CN "))
                    .unwrap_or(false)
            })
            .collect();
        solo.sort();
        for p in solo {
            out.push((p, true));
        }
    }
    for name in ["TRAE", "Trae TRAE", "TRAE CN"] {
        let p = base.join(name);
        if p.is_dir() {
            out.push((p, false));
        }
    }
    out
}

fn mtime(p: &Path) -> Option<std::time::SystemTime> {
    std::fs::metadata(p).and_then(|m| m.modified()).ok()
}

fn now_stamp() -> String {
    chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string()
}

/// 把某个 storage.json 存成 uid 的档案。meta 形状与外部脚本 `twa_switch_account.py`
/// 完全一致（uid/phone/nickname/source_dir/captured_at）——两个工具读写同一份档案。
fn save_profile(uid: &str, storage: &Path, source: &str, dir: &Path) -> Result<(), String> {
    let Some(acc) = crate::trae_auth::parse_storage_at(storage) else {
        return Err("登录态解析失败，未写档案".into());
    };
    let d = profiles_dir(dir).join(uid);
    std::fs::create_dir_all(&d).map_err(|e| format!("创建档案目录失败：{e}"))?;
    let tmp = d.join("storage.json.twa-tmp");
    let dst = d.join("storage.json");
    std::fs::copy(storage, &tmp).map_err(|e| format!("复制登录态失败：{e}"))?;
    std::fs::rename(&tmp, &dst).map_err(|e| format!("落盘档案失败：{e}"))?;
    let meta = serde_json::json!({
        "uid": uid,
        "phone": acc.phone,
        "nickname": acc.nickname,
        "source_dir": source,
        "captured_at": now_stamp(),
    });
    std::fs::write(
        d.join("meta.json"),
        serde_json::to_string_pretty(&meta).unwrap_or_default(),
    )
    .ok();
    Ok(())
}

/// 自动捕获：扫描所有 TRAE 系数据目录，把比档案更新的登录态收进档案。
///
/// 覆盖策略：档案缺失 → 写；非 SOLO 来源只补缺；SOLO 来源只在数据目录比档案新时
/// 覆盖（「切换前回存」的档案在切换瞬间落盘，永远不早于任何旧数据目录，天然不被旧态冲掉）。
#[cfg(target_os = "macos")]
fn refresh_profiles(data_dir: &Path) -> Vec<ProfileMeta> {
    let pdir = profiles_dir(data_dir);
    let _ = std::fs::create_dir_all(&pdir);
    for (root, is_solo) in scan_roots() {
        let sp = root.join("User").join("globalStorage").join("storage.json");
        if !sp.exists() {
            continue;
        }
        let Some(acc) = crate::trae_auth::parse_storage_at(&sp) else {
            continue;
        };
        let Some(uid) = acc.user_id.clone().filter(|s| !s.trim().is_empty()) else {
            continue;
        };
        let prof = pdir.join(&uid).join("storage.json");
        let write = if !prof.exists() {
            true
        } else if !is_solo {
            false
        } else {
            match (mtime(&sp), mtime(&prof)) {
                (Some(s), Some(p)) => s > p,
                _ => false,
            }
        };
        if write {
            let name = root.file_name().unwrap_or_default().to_string_lossy();
            let _ = save_profile(&uid, &sp, &name, data_dir);
        }
    }
    list_profiles(data_dir)
}

/// 列出档案（目录为准，meta 缺失也能列出 uid）。meta 兼容两种来源：
/// 本模块写 `source_dir`，外部脚本亦然；旧版可能只有 `source`。
#[cfg(target_os = "macos")]
fn list_profiles(data_dir: &Path) -> Vec<ProfileMeta> {
    let pdir = profiles_dir(data_dir);
    let mut out = Vec::new();
    let Ok(rd) = std::fs::read_dir(&pdir) else {
        return out;
    };
    let mut dirs: Vec<PathBuf> = rd.flatten().map(|e| e.path()).collect();
    dirs.sort();
    for d in dirs {
        if !d.is_dir() {
            continue;
        }
        let uid = d.file_name().unwrap_or_default().to_string_lossy().to_string();
        if !d.join("storage.json").exists() {
            continue;
        }
        let meta: Option<serde_json::Value> = std::fs::read_to_string(d.join("meta.json"))
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok());
        let gs = |keys: &[&str]| -> Option<String> {
            meta.as_ref().and_then(|v| {
                keys.iter()
                    .find_map(|k| v.get(*k).and_then(|x| x.as_str()))
                    .map(|s| s.to_string())
            })
        };
        out.push(ProfileMeta {
            uid,
            phone: gs(&["phone"]),
            nickname: gs(&["nickname"]),
            source: gs(&["source_dir", "source"]).unwrap_or_default(),
            captured_at: gs(&["captured_at"]).unwrap_or_default(),
        });
    }
    out
}

// ---------------------------------------------------------------------------
// 进程控制（只针对主客户端，绝不碰多开副本与其它 Trae 应用）
// ---------------------------------------------------------------------------

/// 主客户端主进程是否在跑。pgrep 模式锚定 `.app/Contents/MacOS`——
/// `TRAE SOLO CN 2.app` 的路径是 `… CN 2.app/…`，不包含 `…CN.app/…`，不会误匹配。
#[cfg(target_os = "macos")]
fn main_client_running() -> bool {
    let pattern = format!("{MAIN_APP}.app/Contents/MacOS");
    std::process::Command::new("pgrep")
        .args(["-f", &pattern])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

#[cfg(target_os = "macos")]
fn main_client_pids() -> Vec<u32> {
    let pattern = format!("{MAIN_APP}.app/Contents/MacOS");
    std::process::Command::new("pgrep")
        .args(["-f", &pattern])
        .output()
        .ok()
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .split_whitespace()
                .filter_map(|s| s.parse::<u32>().ok())
                .collect()
        })
        .unwrap_or_default()
}

/// 优雅退出主客户端：AppleScript quit → SIGTERM（Electron hot-exit 会保存现场）。
/// **绝不 SIGKILL**。失败返回 Err，调用方必须中止（一个文件都不能动）。
#[cfg(target_os = "macos")]
fn quit_main_client() -> Result<(), String> {
    if !main_client_running() {
        return Ok(());
    }
    let _ = std::process::Command::new("osascript")
        .arg("-e")
        .arg(format!("tell application {MAIN_APP:?} to quit"))
        .output();
    for _ in 0..20 {
        if !main_client_running() {
            return Ok(());
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
    // osascript 可能被 TCC 拦（-10004），降级 SIGTERM：VSCode 系 hot-exit 会保留未保存输入
    for pid in main_client_pids() {
        let _ = std::process::Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .output();
    }
    for _ in 0..20 {
        if !main_client_running() {
            return Ok(());
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
    Err("主客户端未能在限时内退出（可能有未保存对话框）——已中止，未改动任何文件。请手动保存并退出 TRAE SOLO CN 后重试。".into())
}

// ---------------------------------------------------------------------------
// 缓存清理（traehop 同款清单：切号后可能残留旧账号态的文件/键）
// ---------------------------------------------------------------------------

#[cfg(target_os = "macos")]
fn clear_stale_cache() -> Vec<String> {
    let mut removed = Vec::new();
    if let Some(d) = main_dir() {
        for rel in [
            "User/globalStorage/state.vscdb.backup",
            "Cookies-journal",
            "Network/Cookies-journal",
        ] {
            let p = d.join(rel);
            if p.exists() && std::fs::remove_file(&p).is_ok() {
                removed.push(rel.to_string());
            }
        }
    }
    removed
}

/// 防写进来的档案带着上一个账号的 usertag（指向旧账号的标记，traehop 实测需删）
#[cfg(target_os = "macos")]
fn strip_usertag(storage: &Path) -> bool {
    let Ok(text) = std::fs::read_to_string(storage) else {
        return false;
    };
    let Ok(mut v) = serde_json::from_str::<serde_json::Value>(&text) else {
        return false;
    };
    let Some(obj) = v.as_object_mut() else {
        return false;
    };
    if obj.remove("iCubeAuthInfo://usertag").is_none() {
        return false;
    }
    if let Ok(t) = serde_json::to_string_pretty(&v) {
        return std::fs::write(storage, t).is_ok();
    }
    false
}

// ---------------------------------------------------------------------------
// 对外入口
// ---------------------------------------------------------------------------

/// 当前客户端登录状态（只读 + 顺带自动捕获一轮档案）
pub fn status(data_dir: &Path) -> ClientLoginStatus {
    #[cfg(target_os = "macos")]
    {
        let current_uid = main_storage()
            .and_then(|p| crate::trae_auth::parse_storage_at(&p))
            .and_then(|a| a.user_id);
        let profiles = refresh_profiles(data_dir);
        ClientLoginStatus {
            supported: true,
            message: "就绪".into(),
            current_uid,
            profiles,
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = data_dir;
        ClientLoginStatus {
            supported: false,
            message: "登录切换当前仅支持 macOS（Windows 多开方式不同，暂未适配）".into(),
            current_uid: None,
            profiles: Vec::new(),
        }
    }
}

/// 切换主客户端登录到账号池里的某个账号。完成后返回最新状态。
pub fn switch_login(data_dir: &Path, account: &Account) -> Result<ClientLoginStatus, String> {
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (data_dir, account);
        return Err("登录切换当前仅支持 macOS".into());
    }
    #[cfg(target_os = "macos")]
    {
        let Some(target_uid) = account.user_id.as_deref().map(str::trim).filter(|s| !s.is_empty())
        else {
            return Err("该账号缺 userId，无法定位它的登录态档案。".into());
        };
        let Some(main) = main_storage() else {
            return Err("无法定位主客户端登录态".into());
        };
        if !main.exists() {
            return Err(format!("主客户端登录态不存在：{}", main.display()));
        }
        let cur_uid = crate::trae_auth::parse_storage_at(&main).and_then(|a| a.user_id);
        if cur_uid.as_deref() == Some(target_uid) {
            return Ok(ClientLoginStatus {
                message: format!("客户端已登录该账号（{}），无需切换。", account.name),
                ..status(data_dir)
            });
        }

        // ① 先确保目标档案在（自动捕获一轮；还没有就是真没有）
        let _ = refresh_profiles(data_dir);
        let prof = profiles_dir(data_dir).join(target_uid).join("storage.json");
        if !prof.exists() {
            return Err(format!(
                "找不到 uid={target_uid} 的登录态档案。请确认这个账号在某个 TRAE SOLO CN（含多开副本）里登录过；浏览器登录的账号不会自动有客户端登录态。"
            ));
        }

        // ② 退出主客户端（失败即中止，一个文件都不动）
        quit_main_client()?;

        // ③ 当前登录态回存档案（下次切回它时是最新 token）
        if let Some(cur) = &cur_uid {
            let _ = save_profile(cur, &main, "切换前回存", data_dir);
        }

        // ④ 写入目标档案 + 清理旧账号残留
        let tmp = main.with_extension("json.twa-tmp");
        std::fs::copy(&prof, &tmp).map_err(|e| format!("读取目标档案失败：{e}"))?;
        std::fs::rename(&tmp, &main).map_err(|e| format!("写入登录态失败：{e}"))?;
        strip_usertag(&main);
        let cleared = clear_stale_cache();

        // ⑤ 校验（fail-closed：写错了绝不把客户端拉起来）
        let got = crate::trae_auth::parse_storage_at(&main).and_then(|a| a.user_id);
        if got.as_deref() != Some(target_uid) {
            return Err(format!(
                "写入后校验失败：登录态 uid = {:?}，不是目标 {target_uid}。已中止（客户端未重启），可重试一次。",
                got
            ));
        }

        // ⑥ 重启
        let relaunched = crate::endpoint::relaunch();
        let label = account
            .phone
            .clone()
            .unwrap_or_else(|| account.name.clone());
        let msg = if relaunched {
            format!(
                "已切换客户端登录到 {label}{}；该账号的云端会话登录后自动恢复。",
                if cleared.is_empty() {
                    String::new()
                } else {
                    format!("（已清理残留缓存 {} 项）", cleared.len())
                }
            )
        } else {
            format!("登录态已切换到 {label}，但重启客户端失败——请手动打开 {MAIN_APP}。")
        };
        Ok(ClientLoginStatus {
            message: msg,
            ..status(data_dir)
        })
    }
}
