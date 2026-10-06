from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from contextlib import contextmanager

from .schema import AnalysisProject, SourceDocument, utc_now


def public_project_payload(payload: dict) -> dict:
    """Drop legacy internal evaluations before a public project can be read or exported."""
    payload.get("versions", {}).pop("rating_policy", None)
    narrative = payload.get("narrative", {})
    had_rating = any(key in narrative for key in ("policy_evaluation", "legacy_company_rating"))
    for key in ("policy_evaluation", "legacy_company_rating"):
        narrative.pop(key, None)
    if had_rating:
        narrative.pop("final", None)
        narrative.pop("ai_cache", None)
    for record in narrative.get("assessment_history", []):
        record.get("versions", {}).pop("rating_policy", None)
        if "evaluations" in record:
            record.pop("evaluations", None)
            record.pop("report_narrative", None)
    return payload


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
            connection.execute("""CREATE TABLE IF NOT EXISTS news_refresh (
                project_id TEXT NOT NULL, owner_id TEXT NOT NULL, attempted_at REAL NOT NULL,
                PRIMARY KEY(project_id, owner_id))""")

    def news_refresh_remaining(self, project_id, owner_id, cooldown=3600, now=None):
        self.load(project_id, owner_id)
        with self._connect() as connection:
            row = connection.execute("SELECT attempted_at FROM news_refresh WHERE project_id=? AND owner_id=?",
                                     (project_id, owner_id)).fetchone()
        return max(0, int(cooldown - ((time.time() if now is None else now) - row[0]) + 0.999)) if row else 0

    def claim_news_refresh(self, project_id, owner_id, cooldown=3600, now=None):
        now = time.time() if now is None else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM projects WHERE project_id=? AND owner_id=?", (project_id, owner_id)).fetchone():
                raise KeyError("뉴스 대상 기업에 접근할 수 없습니다.")
            row = connection.execute("SELECT attempted_at FROM news_refresh WHERE project_id=? AND owner_id=?", (project_id, owner_id)).fetchone()
            if row and now - row[0] < cooldown:
                return False
            connection.execute("INSERT INTO news_refresh VALUES(?,?,?) ON CONFLICT(project_id,owner_id) DO UPDATE SET attempted_at=excluded.attempted_at",
                               (project_id, owner_id, now))
        return True

    def save(self, project: AnalysisProject, owner_id: str = "local-user") -> None:
        project.touch()
        payload = json.dumps(public_project_payload(project.to_dict()), ensure_ascii=False, separators=(",", ":"))
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

    def release_rejected_news_refresh(self, project_id, owner_id, attempted_at):
        """Release only this attempt after the API explicitly rejected its request."""
        with self._connect() as connection:
            connection.execute('DELETE FROM news_refresh WHERE project_id=? AND owner_id=? AND attempted_at=?',
                               (project_id, owner_id, attempted_at))

    def release_failed_news_refresh_before(self, project_id, owner_id, before):
        """Reopen a legacy failed parse without clearing a newer concurrent attempt."""
        with self._connect() as connection:
            connection.execute('DELETE FROM news_refresh WHERE project_id=? AND owner_id=? AND attempted_at<=?',
                               (project_id, owner_id, before))

    def load(self, project_id: str, owner_id: str = "local-user") -> AnalysisProject:
        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM projects WHERE project_id=? AND owner_id=?", (project_id, owner_id)).fetchone()
        if not row:
            raise KeyError("저장된 분석을 찾을 수 없거나 접근 권한이 없습니다.")
        return AnalysisProject.from_dict(public_project_payload(json.loads(row["payload_json"])))

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
        return json.dumps(public_project_payload(project.to_dict()), ensure_ascii=False, indent=2).encode("utf-8")

    def import_project_json(self, payload: bytes) -> AnalysisProject:
        project = AnalysisProject.from_dict(public_project_payload(json.loads(payload.decode("utf-8"))))
        project.updated_at = utc_now()
        return project
