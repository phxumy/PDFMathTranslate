"""Ordered PDF queue bookkeeping without starting provider requests."""

import hashlib
import html
import json
from pathlib import Path


def queue_files(value):
    values = value if isinstance(value, (list, tuple)) else [value]
    return [str(path) for path in values if path]


def queue_signature(files, settings):
    identities = []
    for value in files:
        path = Path(value)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError(f"PDF 文件不存在或格式不正确：{path.name}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        identities.append((path.name, digest.hexdigest()))
    return hashlib.sha256(
        json.dumps([identities, settings], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def queue_summary(names, statuses, durations=None):
    completed = sum(status == "已完成" for status in statuses)
    rows = [
        f"**PDF 队列** · 已完成 {completed}/{len(names)} 个文件",
        "",
        "| 顺序 | PDF | 状态 |" + (" 翻译耗时 |" if durations is not None else ""),
        "| --- | --- | --- |" + (" --- |" if durations is not None else ""),
    ]
    for index, (name, status) in enumerate(zip(names, statuses, strict=True), 1):
        label = html.escape(name).replace("|", "&#124;").replace("\n", " ")
        duration = durations[index - 1] if durations is not None else None
        elapsed = (
            "—"
            if duration is None
            else f"{int(duration)//60} 分 {int(duration)%60:02d} 秒"
        )
        rows.append(
            f"| {index} | <code>{label}</code> | {status} |"
            + (f" {elapsed} |" if durations is not None else "")
        )
    return "\n".join(rows)
