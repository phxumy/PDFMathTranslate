"""Presentation helpers for persistent translation history."""

from datetime import datetime
import html
import json
from pathlib import Path

from pdf2zh.translation_history import HistoryStore
from pdf2zh.cache import cache_information, clear_translation_cache
from pdf2zh.app_update import update_manager

LABELS = {
    "running": "翻译中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已停止",
    "interrupted": "意外中断",
}
FILE_LABELS = {"original": "原文", "mono": "译文", "dual": "双语 PDF"}


def local_time(value):
    return (
        datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        if value
        else "—"
    )


def refresh_history():
    records = HistoryStore().list()
    choices = [
        (
            f"{local_time(r['started_at'])} · {LABELS.get(r['status'],r['status'])} · {r['filename']}",
            r["id"],
        )
        for r in records
    ]
    info = cache_information()
    message = f"本地记录 {len(records)} 条（最多显示最近 300 条）。翻译缓存 {info['entries']} 条，{info['bytes']/1024/1024:.1f} MB，{'可正常读取' if info['healthy'] else '检查异常'}。"
    return choices, message


def history_detail(task_id):
    if not task_id:
        return "请选择一条记录。", [], "", None
    store = HistoryStore()
    r = store.get(task_id)
    article = r.get("article", {})
    metrics = r.get("cache", {})
    esc = lambda s: html.escape(str(s or "未识别"))
    duration = int(r.get("elapsed_seconds", 0))
    lines = [
        f"### {esc(article.get('title') or r['filename'])}",
        f"**{LABELS.get(r['status'],r['status'])}** · 翻译耗时 {duration//60} 分 {duration%60:02d} 秒",
        f"作者：{esc(article.get('authors'))}　期刊：{esc(article.get('journal'))}　年份：{esc(article.get('year'))}",
        f"输入：{local_time(r.get('input_at'))}　开始：{local_time(r['started_at'])}　结束：{local_time(r.get('finished_at'))}",
    ]
    progress = r.get("progress") or {}
    if progress:
        lines.append(
            f"最后进度：原文第 {progress.get('current_page') or '—'} 页，已完成 {progress.get('completed_pages',0)}/{progress.get('total_pages',0)} 页；阶段 {esc(progress.get('phase'))}。"
        )
    if r.get("settings", {}).get("ignore_cache"):
        lines.append("本次选择了忽略缓存。")
    if r.get("settings", {}).get("engine") == "BabelDOC":
        lines.append("BabelDOC 使用自身缓存，不在此标准翻译缓存统计范围内。")
    elif "cache_check" in r:
        lines.append(
            "翻译开始时缓存自检：" + ("正常" if r["cache_check"]["healthy"] else "异常")
        )
    lines.append(
        f"缓存命中 {metrics.get('hits',0)} 次，未命中 {metrics.get('misses',0)} 次，写入 {metrics.get('writes',0)} 次，异常 {metrics.get('errors',0)} 次。"
    )
    if r.get("error"):
        lines.append("**报错信息：** " + esc(r["error"]))
    paths = store.files(task_id)
    for kind in ("original", "mono", "dual"):
        lines.append(
            f"{FILE_LABELS[kind]}："
            + (
                "可下载"
                if paths.get(kind)
                else ("已删除" if kind in r.get("files", {}) else "尚未生成")
            )
        )
    logs = "\n".join(
        f"{local_time(e['time'])} [{e['kind']}] {e['detail']}"
        for e in store.events(task_id)
    )
    export_dir = store.root / "history-exports"
    export_dir.mkdir(exist_ok=True)
    export = export_dir / f"{task_id}.json"
    export.write_text(
        json.dumps(
            {"task": r, "events": store.events(task_id)}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    return "\n\n".join(lines), [str(p) for p in paths.values() if p], logs, str(export)


def delete_task_files(task_id, kinds, confirmed):
    if not confirmed:
        raise ValueError("请先保存需要的译文，并勾选清理确认。")
    if not task_id or not kinds:
        raise ValueError("请选择记录及要删除的文件类型。")
    selected = [key for key, label in FILE_LABELS.items() if label in kinds]
    count = update_manager.maintain_storage(
        lambda: len(HistoryStore().delete_files(task_id, selected))
    )
    return f"已清理 {count} 个本地文件，历史记录保留。"


def delete_cache(confirmed):
    if not confirmed:
        raise ValueError("请先保存需要的译文，并勾选清理确认。")
    count = update_manager.maintain_storage(clear_translation_cache)
    return f"已清理 {count} 条翻译缓存；再次翻译可能需要重新请求翻译服务。"
