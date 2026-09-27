from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager

from .schema import AnalysisProject, SourceDocument, utc_now


class ProjectStore:
    def __init__(self, root: str | Path = "data"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sources_root = self.root / "sources"
        self.sources_root.mkdir(exist_ok=True)
        self.db_path = self.root / "projects.sqlite3"
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    legal_name TEXT NOT NULL,
                    country TEXT,
                    status TEXT,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_projects_owner_updated ON projects(owner_id, updated_at DESC)")

    def save(self, project: AnalysisProject, owner_id: str = "local-user") -> None:
        project.touch()
        payload = json.dumps(project.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT owner_id FROM projects WHERE project_id=?", (project.project_id,)).fetchone()
            if current and current["owner_id"] != owner_id:
                raise PermissionError("다른 사용자의 분석을 덮어쓸 수 없습니다.")
            connection.execute(
                """
                INSERT INTO projects(project_id, owner_id, title, legal_name, country, status, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    owner_id=excluded.owner_id, title=excluded.title, legal_name=excluded.legal_name,
                    country=excluded.country, status=excluded.status, updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (project.project_id, owner_id, project.title, project.entity.legal_name, project.entity.country, project.status, project.updated_at, payload),
            )

    def load(self, project_id: str, owner_id: str = "local-user") -> AnalysisProject:
        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM projects WHERE project_id=? AND owner_id=?", (project_id, owner_id)).fetchone()
        if not row:
            raise KeyError("저장된 분석을 찾을 수 없거나 접근 권한이 없습니다.")
        return AnalysisProject.from_dict(json.loads(row["payload_json"]))

    def list_projects(self, owner_id: str = "local-user") -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT project_id, title, legal_name, country, status, updated_at FROM projects WHERE owner_id=? ORDER BY updated_at DESC",
                (owner_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_source_bytes(self, project_id: str, filename: str, content: bytes, mime_type: str = "") -> tuple[SourceDocument, bool]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", project_id):
            raise ValueError("유효하지 않은 프로젝트 ID")
        digest = hashlib.sha256(content).hexdigest()
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)[:120] or "source.bin"
        target_dir = self.sources_root / project_id
        target_dir.mkdir(parents=True, exist_ok=True)
        existing = next(target_dir.glob(f"{digest}_*"), None)
        if existing:
            target = existing
            duplicate = True
        else:
            target = target_dir / f"{digest}_{safe_name}"
            target.write_bytes(content)
            duplicate = False
        source = SourceDocument(
            name=filename,
            source_type="업로드",
            local_path=str(target.relative_to(self.root)),
            mime_type=mime_type,
            sha256=digest,
            status="중복 파일 재사용" if duplicate else "원문 보존",
            note="동일 해시 원문을 재사용했습니다." if duplicate else "원문을 프로젝트 저장소에 보존했습니다.",
        )
        return source, duplicate

    def export_project_json(self, project: AnalysisProject) -> bytes:
        return json.dumps(project.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")

    def import_project_json(self, payload: bytes) -> AnalysisProject:
        project = AnalysisProject.from_dict(json.loads(payload.decode("utf-8")))
        project.updated_at = utc_now()
        return project
