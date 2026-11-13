"""Read-only queries for pages. Every query is scoped to the owning user."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import case, func
from sqlmodel import Session, col, select

from ytstudio.adapters.persistence import tables as t
from ytstudio.adapters.persistence.db import session_for


def _row(r: Any) -> dict[str, Any]:
    return r.model_dump()


class Queries:
    def __init__(self, url: str):
        self.url = url

    def _s(self) -> Session:
        return session_for(self.url)

    def channels(self, user_id: int) -> list[dict[str, Any]]:
        with self._s() as s:
            rows = list(s.exec(select(t.Channel).where(t.Channel.user_id == user_id).order_by(t.Channel.title)))
            today = date.today()
            past = s.exec(
                select(t.Snapshot)
                .where(
                    col(t.Snapshot.channel_id).in_([r.id for r in rows]),
                    t.Snapshot.day < today,
                    t.Snapshot.day >= today - timedelta(days=60),
                )
                .order_by(col(t.Snapshot.day))
            )
            base = {p.channel_id: p for p in past}  # ordered by day, so the latest one wins
        out = []
        for r in rows:
            d = _row(r)
            d.pop("creds")  # never let token blobs reach a template
            if b := base.get(r.id):
                d["change"] = {"since": b.day, "subscribers": r.subscribers - b.subscribers, "views": r.views - b.views}
            out.append(d)
        return out

    def assets(
        self,
        user_id: int,
        channel_id: str,
        kinds: list[str],
        status: str | None = None,
        run_id: int | None = None,
        parent_id: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        q = select(t.AssetRow).where(
            t.AssetRow.user_id == user_id, t.AssetRow.channel_id == channel_id, col(t.AssetRow.kind).in_(kinds)
        )
        if status:
            q = q.where(t.AssetRow.status == status)
        if run_id is not None:
            q = q.where(t.AssetRow.run_id == run_id)
        if parent_id is not None:
            q = q.where(t.AssetRow.parent_id == parent_id)
        with self._s() as s:
            return [_row(r) for r in s.exec(q.order_by(col(t.AssetRow.id).desc()).limit(limit))]

    def asset(self, user_id: int, asset_id: int) -> dict[str, Any] | None:
        with self._s() as s:
            r = s.get(t.AssetRow, asset_id)
        return _row(r) if r and r.user_id == user_id else None

    def latest_brief(self, user_id: int, channel_id: str) -> dict[str, Any] | None:
        briefs = self.assets(user_id, channel_id, ["brief"], limit=1)
        if not briefs:
            return None
        brief = briefs[0]
        ideas = sorted(self.assets(user_id, channel_id, ["idea"], parent_id=brief["id"]), key=lambda a: a["id"])
        made = self.assets(user_id, channel_id, ["script", "package", "seo"], run_id=brief["run_id"])
        by_idea: dict[int, dict[str, Any]] = {}
        for a in sorted(made, key=lambda x: x["id"]):
            by_idea.setdefault(a["parent_id"], {})[a["kind"]] = a
        return {"brief": brief, "ideas": ideas, "made": by_idea}

    def developed(self, user_id: int, channel_id: str, idea_id: int) -> dict[str, Any]:
        rows = self.assets(user_id, channel_id, ["script", "package", "seo"], parent_id=idea_id)
        out: dict[str, Any] = {}
        for a in rows:  # newest first: keep the latest of each kind
            out.setdefault(a["kind"], a)
        return out

    def drafts(self, user_id: int, channel_id: str, limit: int = 15) -> list[dict[str, Any]]:
        """Title options, description and tags the assistant wrote for each idea, newest first."""
        seo_by_idea = {
            a["parent_id"]: a for a in reversed(self.assets(user_id, channel_id, ["seo"], limit=limit * 3))
        }  # reversed: the newest SEO of an idea wins
        out = []
        for pk in self.assets(user_id, channel_id, ["package"], limit=limit):
            if pk["status"] == "discarded":
                continue
            seo = seo_by_idea.get(pk["parent_id"], {}).get("data", {})
            out.append(
                {
                    "label": f"{pk['title']} ({pk['created_at']:%-d %b})",
                    "titles": [pair["title"] for pair in pk["data"].get("pairs", [])],
                    "description": seo.get("description", ""),
                    "tags": seo.get("tags", []),
                }
            )
        return out

    def runs(self, user_id: int, channel_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        q = select(t.PipelineRun).where(t.PipelineRun.user_id == user_id)
        if channel_id:
            q = q.where(t.PipelineRun.channel_id == channel_id)
        with self._s() as s:
            runs = list(s.exec(q.order_by(col(t.PipelineRun.id).desc()).limit(limit)))
            steps = list(
                s.exec(
                    select(t.StepRun).where(col(t.StepRun.run_id).in_([r.id for r in runs])).order_by(col(t.StepRun.id))
                )
            )
        by_run: dict[int, list[dict[str, Any]]] = {}
        for st in steps:
            by_run.setdefault(st.run_id, []).append({k: v for k, v in _row(st).items() if k != "output"})
        return [_row(r) | {"steps": by_run.get(r.id or 0, [])} for r in runs]

    def run_for_asset(self, user_id: int, asset: dict[str, Any]) -> dict[str, Any] | None:
        """The newest run that made this asset, regenerated it, or (for an idea) developed it into a script."""
        for r in self.runs(user_id, asset["channel_id"], limit=60):  # newest first
            i = r["inputs"]
            if r["id"] == asset.get("run_id") or asset["id"] in (i.get("asset_id"), i.get("idea_asset_id")):
                return self.run(user_id, r["id"])
        return None

    def latest_run(self, user_id: int, channel_id: str, pipeline: str | None = None) -> dict[str, Any] | None:
        runs = [r for r in self.runs(user_id, channel_id, limit=20) if not pipeline or r["pipeline"] == pipeline]
        return self.run(user_id, runs[0]["id"]) if runs else None

    def run(self, user_id: int, run_id: int) -> dict[str, Any] | None:
        with self._s() as s:
            r = s.get(t.PipelineRun, run_id)
            if not r or r.user_id != user_id:
                return None
            steps = [
                {k: v for k, v in _row(st).items() if k != "output"}
                for st in s.exec(select(t.StepRun).where(t.StepRun.run_id == run_id).order_by(col(t.StepRun.id)))
            ]
        planned = r.inputs.get("steps", [])
        return {
            "id": r.id,
            "status": r.status,
            "pipeline": r.pipeline,
            "error": r.error,
            "created_at": r.created_at,
            "steps": steps,
            "planned": planned or [x["step"] for x in steps],
            "progress": f"{sum(1 for x in steps if x['status'] == 'done')}/{len(planned) or len(steps)}",
        }

    def dead_letters(self, user_id: int, channel_id: str | None = None) -> list[dict[str, Any]]:
        with self._s() as s:
            owned = select(t.PipelineRun.id).where(t.PipelineRun.user_id == user_id)
            run_ids = owned.where(t.PipelineRun.channel_id == channel_id) if channel_id else owned
            rows = s.exec(
                select(t.DeadLetter)
                .where(col(t.DeadLetter.run_id).in_(run_ids), col(t.DeadLetter.resolved_at).is_(None))
                .order_by(col(t.DeadLetter.id).desc())
                .limit(100)
            )
            return [_row(r) for r in rows]

    def llm_costs(self, user_id: int) -> dict[str, Any]:
        start_today = datetime.combine(datetime.now(UTC).date(), time(0), tzinfo=UTC)
        week = start_today - timedelta(days=6)
        with self._s() as s:

            def total(since: datetime) -> float:
                return float(
                    s.exec(
                        select(func.coalesce(func.sum(t.LLMCall.cost_usd), 0.0)).where(
                            t.LLMCall.user_id == user_id, t.LLMCall.created_at >= since
                        )
                    ).one()
                )

            def grouped(column: Any) -> list[dict[str, Any]]:
                q = (
                    # more columns than sqlmodel's typed overloads cover
                    select(  # type: ignore[call-overload]
                        column,
                        func.count(),
                        func.sum(t.LLMCall.cost_usd),
                        func.sum(t.LLMCall.prompt_tokens),
                        func.sum(t.LLMCall.completion_tokens),
                        func.sum(case((col(t.LLMCall.cache_hit), 1), else_=0)),
                    )
                    .where(t.LLMCall.user_id == user_id, t.LLMCall.created_at >= week)
                    .group_by(column)
                )
                return [
                    {"key": k, "calls": n, "cost": c or 0, "in": i or 0, "out": o or 0, "cached": h or 0}
                    for k, n, c, i, o, h in s.exec(q)
                ]

            return {
                "today": total(start_today),
                "week": total(week),
                "by_alias": grouped(t.LLMCall.alias),
                "by_step": grouped(t.LLMCall.step),
                "by_model": grouped(t.LLMCall.model),
            }

    def videos(self, channel_id: str, days: int = 90) -> list[dict[str, Any]]:
        since = datetime.now(UTC) - timedelta(days=days)
        with self._s() as s:
            rows = s.exec(
                select(t.VideoRow)
                .where(t.VideoRow.channel_id == channel_id, t.VideoRow.published_at >= since)
                .order_by(col(t.VideoRow.published_at).desc())
            )
            return [_row(r) for r in rows]

    def video(self, channel_id: str, video_id: str) -> dict[str, Any] | None:
        with self._s() as s:
            r = s.get(t.VideoRow, video_id)
        return _row(r) if r and r.channel_id == channel_id else None
