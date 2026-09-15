# -*- coding: utf-8 -*-
"""v1 数据库 (media_library.db) + v1 配置 (backup_v1/config_v1_backup.json)
迁移到 v2.0 新架构 (database.sqlite + config.json)。

映射规则:
- 旧 bookmarks      -> 新 devices[].roots[] (每个书签 = 一个根目录, root_id = 书签id)
- 旧 media_files(视频) -> 新 files 表 (绝对路径 -> root_id/相对路径, 按最长前缀匹配)
- 旧 actors/actor_relations/file_actors -> 新 actors(曾用名折叠进alt字段)/video_actors
- 旧 video_relations -> 新 video_links (alias图片路径 -> 所在文件夹相对路径)
- 旧 config.tags + tags_v2 -> 新 tags/file_tags 表
- 旧 crawler 配置 -> 新 crawler_config 结构
- 跨设备重复文件: 同一物理路径多个 device_id 时, 保留 config.devices 中登记的设备, 其余丢弃防重复搜索
- 图片文件不进入 files 表 (v2 运行时按文件夹实时匹配, 避免冗余)

用法: python migrate_db.py
旧库与旧配置备份只读, 不修改; 生成 database.sqlite 与新 config.json。
"""
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

from constants import DB_FILE
from db_manager import DatabaseManager

OLD_DB = "media_library.db"
OLD_CONFIG = os.path.join("backup_v1", "config_v1_backup.json")
NEW_CONFIG = "config.json"

THUMB_SIZE_MAP = {"小": "small", "中": "medium", "大": "large", "最大": "xlarge"}


def norm(p: str) -> str:
    """统一路径规范化(仅用于匹配比较): 返回 normcase 后的反斜杠形式。"""
    return os.path.normcase(os.path.normpath(p.replace("/", "\\")))


def match_prefix(path_orig: str, prefix_orig: str):
    """大小写不敏感前缀匹配, 返回 path_orig 中去掉前缀后的剩余部分(保留原始大小写)。"""
    p = path_orig.replace("/", "\\")
    pre = prefix_orig.replace("/", "\\")
    if p.upper().startswith(pre.upper() + "\\"):
        return p[len(pre) + 1:]
    if p.upper() == pre.upper():
        return ""
    return None


def to_rel(path_after_root: str) -> str:
    """转成 files.relative_path 格式: root_id/子路径 (正斜杠)。"""
    return path_after_root.replace("\\", "/").lstrip("/")


def extract_code(filename: str) -> str:
    m = re.search(r"([A-Za-z]{2,6})[-_](\d{2,5})", filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"([A-Za-z]{2,6})(\d{3,5})", os.path.splitext(filename)[0])
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return ""


def main() -> int:
    if not os.path.exists(OLD_DB):
        print(f"错误: 找不到旧数据库 {OLD_DB}")
        return 1
    if not os.path.exists(OLD_CONFIG):
        print(f"错误: 找不到旧配置备份 {OLD_CONFIG}")
        return 1

    with open(OLD_CONFIG, encoding="utf-8") as f:
        old_cfg = json.load(f)

    devices_cfg = old_cfg.get("devices", {})
    current_device = old_cfg.get("current_device_name", "") or "default"
    configured_devices = set(devices_cfg.keys())

    # ---------- 1. 构建根目录表 ----------
    # roots: list of dict(orig, norm, root_id, name, device_id, root_type)
    roots = []
    for dev_id, dev in devices_cfg.items():
        for bm in dev.get("bookmarks", []):
            p = bm.get("path", "")
            if not p:
                continue
            roots.append({
                "orig": p,
                "norm": norm(p),
                "root_id": bm.get("id") or f"bm_{len(roots)}",
                "name": bm.get("display_name") or os.path.basename(p) or p,
                "device_id": dev_id,
                "root_type": "network" if p.startswith("\\\\") else "local",
            })
    # 最长前缀优先(norm 长度与 orig 长度一致, 排序键用 norm)
    roots.sort(key=lambda r: len(r["norm"]), reverse=True)

    def match_root(path_orig: str):
        for r in roots:
            rest = match_prefix(path_orig, r["orig"])
            if rest is not None:
                return r, rest
        return None, None

    # ---------- 2. 读取旧文件并解决跨设备重复 ----------
    old = sqlite3.connect(OLD_DB)
    old.row_factory = sqlite3.Row
    rows = old.execute(
        "SELECT * FROM media_files WHERE is_deleted = 0"
    ).fetchall()
    print(f"旧库有效文件总数: {len(rows)}")

    path_owner = {}       # normpath -> 选定的 (device_id, row)
    cross_dups = 0
    for row in rows:
        pn = norm(row["file_path"])
        if pn in path_owner:
            cross_dups += 1
            prev_dev, _ = path_owner[pn]
            # 已登记的优先保留 config 设备; 当前行是 config 设备则替换
            if row["device_id"] in configured_devices and prev_dev not in configured_devices:
                path_owner[pn] = (row["device_id"], row)
        else:
            path_owner[pn] = (row["device_id"], row)
    print(f"跨设备重复路径(去重丢弃): {cross_dups}")

    # 自动根: 无根匹配的路径按锚点(UNC共享/盘符)分组
    auto_groups = defaultdict(list)   # anchor_norm -> [ (device_id, row, path_orig) ]
    planned = []                      # (device_id, row, root_id, relative_path)
    unmatched = 0
    for pn, (dev_id, row) in path_owner.items():
        r, rest = match_root(row["file_path"])
        if r is None:
            p = row["file_path"].replace("/", "\\")
            if re.match(r"^[A-Za-z]:", p):
                anchor = p[:3]
            else:
                anchor = "\\".join(p.split("\\")[:3])
            auto_groups[norm(anchor)].append((dev_id, row, p, anchor))
            unmatched += 1
        else:
            rel = to_rel(rest)
            planned.append((r["device_id"], row, r["root_id"],
                            f"{r['root_id']}/{rel}" if rel else r["root_id"]))

    auto_root_ids = {}
    for i, (anchor_norm, items) in enumerate(sorted(auto_groups.items()), 1):
        rid = f"mig{i:02d}"
        auto_root_ids[anchor_norm] = rid
        anchor_orig = items[0][3]
        roots.append({
            "orig": anchor_orig, "norm": anchor_norm, "root_id": rid,
            "name": f"迁移根-{anchor_orig.lstrip(chr(92)) or anchor_orig}",
            "device_id": items[0][0],
            "root_type": "network" if anchor_orig.startswith("\\\\") else "local",
        })
        for dev_id, row, p, _ in items:
            rest = match_prefix(p, anchor_orig)
            rest = rest if rest is not None else p[len(anchor_orig):]
            rel = to_rel(rest)
            planned.append((dev_id, row, rid, f"{rid}/{rel}" if rel else rid))
    if unmatched:
        print(f"无根匹配文件(自动建根): {unmatched}, 自动根数: {len(auto_groups)}")

    # ---------- 3. 初始化新库 ----------
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
        for suf in ("-wal", "-shm"):
            if os.path.exists(DB_FILE + suf):
                os.remove(DB_FILE + suf)
    db = DatabaseManager(DB_FILE, current_device)
    db.init_tables()

    # ---------- 4. 迁移 files / custom_codes (仅视频) ----------
    file_oldid_to_rel = {}    # 旧 file_id -> (device_id, relative_path)
    seen = set()
    stats = defaultdict(int)
    batch_files = []
    batch_codes = []
    for dev_id, row, root_id, rel in planned:
        key = (dev_id, rel)
        if row["file_type"] != "video":
            stats["skip_image"] += 1
            continue
        if key in seen:                       # 同设备同相对路径去重
            stats["dup_skip"] += 1
            continue
        seen.add(key)
        dur_s = row["duration"] or 0
        title = row["video_title"] or ""
        rdate = row["release_date"] or ""
        synced = "synced" if (title or rdate or dur_s) else "pending"
        batch_files.append((
            dev_id, rel, row["file_name"] or os.path.basename(rel), title,
            f"{int(dur_s) // 60}min" if dur_s else "", rdate,
            "", synced, "",
        ))
        file_oldid_to_rel[row["file_id"]] = (dev_id, rel)
        code = (row["video_code"] or "").strip()
        if code:
            batch_codes.append((dev_id, rel, code))
        stats["video"] += 1

    db.execute("BEGIN")
    db.connection.executemany(
        "INSERT OR IGNORE INTO files "
        "(device_id, relative_path, file_name, title, duration, release_date, "
        " cover_path, sync_status, video_link) VALUES (?,?,?,?,?,?,?,?,?)",
        batch_files)
    db.connection.executemany(
        "INSERT OR IGNORE INTO custom_codes (device_id, relative_path, custom_code) "
        "VALUES (?,?,?)", batch_codes)
    db.execute("COMMIT")
    print(f"files 迁移: {stats['video']} 视频, 去重跳过 {stats['dup_skip']}, 图片不入库 {stats['skip_image']}")

    # ---------- 5. 迁移 actors / actor_relations ----------
    actor_rows = old.execute("SELECT * FROM actors").fetchall()
    alt_map = {}
    for r in old.execute(
            "SELECT actor_id, related_actor_id FROM actor_relations ORDER BY id").fetchall():
        alt_map.setdefault(r["actor_id"], r["related_actor_id"])
    actor_name = {r["actor_id"]: r["actor_name"] for r in actor_rows}
    actor_name_cn = {r["actor_id"]: r["actor_name_cn"] or "" for r in actor_rows}

    db.execute("BEGIN")
    db.connection.executemany(
        "INSERT OR IGNORE INTO actors (actor_id, japanese_name, chinese_name, initial_letter, "
        " alt_japanese_name, alt_chinese_name, profile_image, notes, video_count, "
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,0,?,?)",
        [(r["actor_id"], r["actor_name"] or "", r["actor_name_cn"] or "",
          (r["sort_char"] or "").lower()[:1],
          actor_name.get(alt_map.get(r["actor_id"]), "") if alt_map.get(r["actor_id"]) else "",
          actor_name_cn.get(alt_map.get(r["actor_id"]), "") if alt_map.get(r["actor_id"]) else "",
          r["avatar_url"] or "", "", r["created_date"], r["updated_date"])
         for r in actor_rows])
    db.execute("COMMIT")
    print(f"actors 迁移: {len(actor_rows)}")

    # ---------- 6. 迁移 file_actors -> video_actors ----------
    va_batch = []
    va_seen = set()
    for r in old.execute("SELECT * FROM file_actors ORDER BY id").fetchall():
        loc = file_oldid_to_rel.get(r["file_id"])
        if not loc:
            continue
        key = (loc[0], loc[1], r["actor_id"])
        if key in va_seen:
            continue
        va_seen.add(key)
        va_batch.append((loc[0], loc[1], r["actor_id"], 0, "cast"))
    db.execute("BEGIN")
    db.connection.executemany(
        "INSERT OR IGNORE INTO video_actors (device_id, relative_path, actor_id, "
        " actor_order, role_type) VALUES (?,?,?,?,?)", va_batch)
    # 重算 video_count
    db.connection.execute(
        "UPDATE actors SET video_count = "
        "(SELECT COUNT(*) FROM video_actors va WHERE va.actor_id = actors.actor_id)")
    db.execute("COMMIT")
    print(f"video_actors 迁移: {len(va_batch)}")

    # ---------- 7. 迁移 video_relations -> video_links ----------
    vl_batch = []
    # 全部已规划文件的 norm路径 -> (device_id, relative_path)，供关联回退查询
    path_to_rel = {norm(x[1]["file_path"]): (x[0], x[3]) for x in planned}
    for r in old.execute("SELECT * FROM video_relations").fetchall():
        p_loc = file_oldid_to_rel.get(r["primary_file_id"])
        a_loc = file_oldid_to_rel.get(r["alias_file_id"])
        if not p_loc:
            # 主视频可能未迁移(如被去重), 用旧路径直接查
            prow = old.execute("SELECT * FROM media_files WHERE file_id=?",
                               (r["primary_file_id"],)).fetchone()
            if not prow:
                continue
            p_loc = path_to_rel.get(norm(prow["file_path"]))
            if not p_loc:
                continue
        if a_loc:
            alias_folder = os.path.dirname(a_loc[1])
        else:
            arow = old.execute("SELECT * FROM media_files WHERE file_id=?",
                               (r["alias_file_id"],)).fetchone()
            a_loc_fallback = path_to_rel.get(norm(arow["file_path"])) if arow else None
            alias_folder = os.path.dirname(a_loc_fallback[1]) if a_loc_fallback else ""
        vl_batch.append((p_loc[0], p_loc[1], alias_folder.replace("\\", "/"),
                         extract_code(os.path.basename(p_loc[1]))))
    db.execute("BEGIN")
    db.connection.executemany(
        "INSERT OR IGNORE INTO video_links (device_id, primary_video_path, "
        " linked_folder_path, match_code) VALUES (?,?,?,?)", vl_batch)
    db.execute("COMMIT")
    print(f"video_links 迁移: {len(vl_batch)}")

    # ---------- 8. 迁移 actor_folders ----------
    af = old.execute("SELECT * FROM actor_folders").fetchall()
    db.connection.executemany(
        "INSERT OR IGNORE INTO actor_folders (device_id, folder_path, folder_name, "
        " is_enabled, last_scan, created_at) VALUES (?,?,?,?,?,?)",
        [(r["device_id"] or current_device, r["folder_path"], r["folder_name"],
          r["is_active"], r["last_scan"], r["created_date"]) for r in af])
    print(f"actor_folders 迁移: {len(af)}")

    # ---------- 9. 迁移标签 (config.tags + tags_v2) ----------
    tag_name_list = old_cfg.get("tags", [])
    tags_v2 = old_cfg.get("tags_v2", {})
    tag_ids = {}
    for dev_id in set(list(tags_v2.keys()) + [current_device]):
        for name in tag_name_list:
            cur = db.connection.execute(
                "INSERT OR IGNORE INTO tags (device_id, tag_name) VALUES (?,?)",
                (dev_id, name))
            row = db.connection.execute(
                "SELECT tag_id FROM tags WHERE device_id=? AND tag_name=?",
                (dev_id, name)).fetchone()
            tag_ids[(dev_id, name)] = row[0]
    ft_batch = []
    ft_seen = set()
    for dev_id, bm_map in tags_v2.items():
        for bm_id, files_map in bm_map.items():
            for rel, names in files_map.items():
                full_rel = f"{bm_id}/{str(rel).replace(chr(92), '/')}"
                # v1 标签键可能是: 视频文件路径(带/不带扩展名) / 视频文件夹路径
                targets = []
                for (t,) in db.connection.execute(
                        "SELECT relative_path FROM files WHERE device_id=?",
                        (dev_id,)).fetchall():
                    if (t == full_rel or os.path.splitext(t)[0] == full_rel
                            or t.startswith(full_rel + "/")
                            or os.path.dirname(t) == full_rel):
                        targets.append(t)
                for t in targets:
                    for name in names:
                        tid = tag_ids.get((dev_id, name))
                        if tid is None:
                            continue
                        k = (dev_id, t[0], tid)
                        if k not in ft_seen:
                            ft_seen.add(k)
                            ft_batch.append((dev_id, t[0], tid))
    db.connection.executemany(
        "INSERT OR IGNORE INTO file_tags (device_id, relative_path, tag_id) VALUES (?,?,?)",
        ft_batch)
    print(f"tags 迁移: {len(tag_name_list)} 个定义, file_tags 关联 {len(ft_batch)} 条")

    db.close()

    # ---------- 10. 生成新 config.json ----------
    old_crawler = old_cfg.get("crawler", {})
    extract_rules = []
    for i, rule in enumerate(old_crawler.get("extract_rules", []), 1):
        rt = rule.get("rule_type", "css")
        new_rule = {
            "rule_id": f"rule_{i:03d}",
            "field_id": rule.get("field_id", f"field_{i}"),
            "field_name": rule.get("field_name", ""),
            "enabled": rule.get("enabled", True),
            "rule_type": rt,
            "is_multi_value": rule.get("multiple", False),
            "selector": "", "attribute": "", "html_snippet": "",
            "placeholder": rule.get("placeholder", ""),
            "pattern": "", "group_index": 1, "code": "", "description": "",
        }
        content = rule.get("rule_content", "")
        if rt == "custom":
            new_rule["code"] = content
        elif rt == "css":
            new_rule["selector"] = content
            new_rule["attribute"] = "text" if rule.get("extract_text", True) else ""
        elif rt == "regex":
            new_rule["pattern"] = content
        elif rt == "snippet":
            snip = old_crawler.get("snippets", {}).get(new_rule["field_id"], {})
            new_rule["html_snippet"] = snip.get("snippet", content)
            new_rule["placeholder"] = snip.get("placeholder", new_rule["placeholder"])
        extract_rules.append(new_rule)

    devices_new = {}
    for dev_id, dev in devices_cfg.items():
        bm_list = dev.get("bookmarks", [])
        devices_new[dev_id] = {
            "name": dev.get("display_name", dev_id),
            "device_label": "本机" if dev_id == current_device else "",
            "roots": [
                {"root_id": bm.get("id") or f"bm_{i}", "name": bm.get("display_name") or bm.get("path", ""),
                 "path": bm.get("path", ""),
                 "root_type": "network" if bm.get("path", "").startswith("\\\\") else "local",
                 "enabled": True}
                for i, bm in enumerate(bm_list) if bm.get("path")
            ],
            "bookmarks": [
                {"bookmark_id": bm.get("id") or f"bm_{i}", "name": bm.get("display_name", ""),
                 "path": bm.get("path", ""), "root_id": bm.get("id") or f"bm_{i}",
                 "created_at": ""}
                for i, bm in enumerate(bm_list) if bm.get("path")
            ],
        }
    # 自动根所属设备补 roots
    for r in roots:
        if r["root_id"].startswith("mig"):
            devices_new.setdefault(r["device_id"], {
                "name": r["device_id"], "device_label": "", "roots": [], "bookmarks": []})
            if not any(x["root_id"] == r["root_id"] for x in devices_new[r["device_id"]]["roots"]):
                devices_new[r["device_id"]]["roots"].append({
                    "root_id": r["root_id"], "name": r["name"], "path": r["orig"],
                    "root_type": r["root_type"], "enabled": True})

    thumb_key = THUMB_SIZE_MAP.get(old_cfg.get("thumbnail_size", "中"), "medium")
    new_cfg = {
        "version": "2.0",
        "current_device": current_device,
        "settings": {
            "remember_position": old_cfg.get("remember_location", True),
            "debug_mode": old_cfg.get("debug_mode", False),
            "player": {
                "default_player": "potplayer",
                "potplayer_path": old_cfg.get("player_path", ""),
                "vlc_path": "",
                "use_embedded_vlc": False,
            },
            "gallery": {"color_scheme": "default"},
        },
        "devices": devices_new,
        "last_state": {},
        "thumbnail_size": {current_device: thumb_key},
        "crawler_config": {
            "version": "1.0",
            "url_library": {
                "urls": old_crawler.get("urls", []),
                "placeholder": old_crawler.get("placeholder", "{番号}"),
                "mode": "rotate" if old_crawler.get("crawl_mode", "round_robin") == "round_robin"
                        else old_crawler.get("crawl_mode", "rotate"),
            },
            "page_parser": {"global_css_selector": old_crawler.get("css_selector", "body"),
                            "encoding": old_crawler.get("encoding", "auto")},
            "anti_crawl": {
                "use_browser_render": old_crawler.get("render_js", False),
                "engine": old_crawler.get("render_engine", "playwright"),
                "browser": old_crawler.get("browser_type", "chromium"),
                "interval_min_ms": old_crawler.get("interval_min", 1000),
                "interval_max_ms": old_crawler.get("interval_max", 3000),
                "timeout_seconds": old_crawler.get("timeout", 10),
                "retry_count": old_crawler.get("retry_times", 3),
                "use_proxy": old_crawler.get("use_proxy", False),
                "proxy_url": old_crawler.get("proxy_url", ""),
            },
            "http_headers": old_crawler.get("headers", {}),
            "extraction_rules": extract_rules,
        },
    }
    with open(NEW_CONFIG, "w", encoding="utf-8") as f:
        json.dump(new_cfg, f, ensure_ascii=False, indent=2)

    print(f"\n新配置已写入 {NEW_CONFIG}: 设备 {len(devices_new)} 个, 根目录 "
          f"{sum(len(d['roots']) for d in devices_new.values())} 个")
    print("迁移完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
