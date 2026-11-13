"""Composition root: the only place that picks concrete adapters."""

import tomllib
from datetime import UTC, datetime, timedelta
from functools import cached_property
from typing import Any

from ytstudio.application import steps_daily, steps_shorts, steps_video  # noqa: F401  (registers the steps)
from ytstudio.application.pipeline import PipelineRunner
from ytstudio.application.use_cases import Accounts, Events, Profiles, Review, Scheduling, Shorts, Sync
from ytstudio.config import Settings, get_settings
from ytstudio.ports import Deps, TaskDispatcher, UnitOfWork


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class Container:
    def __init__(self, settings: Settings | None = None, **overrides: Any):
        self.s = settings or get_settings()
        self.o = overrides

    def _pick(self, name: str, build: Any) -> Any:
        return self.o[name] if name in self.o else build()

    # ----- infrastructure -----

    @cached_property
    def redis(self) -> Any:
        from ytstudio.adapters.redis_kv import client

        return self._pick("redis", lambda: client(self.s.redis_url))

    @cached_property
    def clock(self) -> Any:
        return self._pick("clock", SystemClock)

    def uow(self) -> UnitOfWork:
        from ytstudio.adapters.persistence.repos import SqlUnitOfWork

        uow: UnitOfWork = self.o["uow"]() if "uow" in self.o else SqlUnitOfWork(self.s.database_url)
        return uow

    @cached_property
    def cache(self) -> Any:
        from ytstudio.adapters.redis_kv import RedisCache

        return self._pick("cache", lambda: RedisCache(self.redis))

    @cached_property
    def lock(self) -> Any:
        from ytstudio.adapters.redis_kv import RedisLock

        return self._pick("lock", lambda: RedisLock(self.redis))

    @cached_property
    def limiter(self) -> Any:
        from ytstudio.adapters.redis_kv import RedisRateLimiter

        return self._pick("limiter", lambda: RedisRateLimiter(self.redis))

    @cached_property
    def quota(self) -> Any:
        from ytstudio.adapters.redis_kv import RedisQuotaBudget

        return self._pick(
            "quota",
            lambda: RedisQuotaBudget(self.redis, self.clock, self.s.quota_daily_limit, self.s.quota_noncritical_pct),
        )

    @cached_property
    def heartbeat(self) -> Any:
        from ytstudio.adapters.redis_kv import Heartbeat

        return self._pick("heartbeat", lambda: Heartbeat(self.redis))

    @cached_property
    def pipelines_config(self) -> dict[str, Any]:
        return tomllib.loads(self.s.pipelines_file.read_text())

    # ----- Google -----

    @cached_property
    def vault(self) -> Any:
        from ytstudio.adapters.token_vault import DbTokenVault, TokenCipher

        return self._pick("vault", lambda: DbTokenVault(self.s.database_url, TokenCipher(self.s.token_encryption_key)))

    @cached_property
    def oauth(self) -> Any:
        from ytstudio.adapters.youtube import GoogleOAuth

        return self._pick("oauth", lambda: GoogleOAuth(*self.s.google_client(), self.s.redirect_uri))

    @cached_property
    def youtube(self) -> Any:
        from ytstudio.adapters.youtube import YouTubeGateway

        def build() -> Any:
            cid, secret = self.s.google_client()
            return YouTubeGateway(
                self.vault,
                cid,
                secret,
                self.quota,
                self.cache,
                self.clock,
                on_reconnect=lambda ch: self.accounts.mark_reconnect(ch, self.events.publish),
            )

        return self._pick("youtube", build)

    @cached_property
    def trend_providers(self) -> list[Any]:
        from ytstudio.adapters.trends import CustomTopicsProvider, MostPopularProvider, NicheSearchProvider

        def build() -> list[Any]:
            per_day = self.s.searches_per_channel_per_day
            available = {
                "most_popular": lambda: MostPopularProvider(self.youtube),
                "niche_search": lambda: NicheSearchProvider(self.youtube, self.redis, self.clock, per_day),
                "custom_topics": lambda: CustomTopicsProvider(self.youtube, self.redis, self.clock, per_day),
            }
            return [available[n]() for n in self.pipelines_config["trends"]["providers"]]

        return self._pick("trend_providers", build)

    # ----- LLM, prompts, scorers -----

    @cached_property
    def prompts(self) -> Any:
        from ytstudio.adapters.prompts import FilePromptRepository

        return self._pick("prompts", lambda: FilePromptRepository(self.s.prompts_dir, self.s.vendor_dir))

    @cached_property
    def llm_config(self) -> dict[str, Any]:
        from ytstudio.adapters.llm import load_llm_config

        return load_llm_config(self.s.llm_config_file)

    @cached_property
    def llm_settings(self) -> Any:
        from ytstudio.adapters.llm_models import list_models
        from ytstudio.adapters.llm_settings import DbLLMSettings
        from ytstudio.adapters.token_vault import TokenCipher
        from ytstudio.application.llm_settings import LLMSettings

        def build() -> Any:
            defaults = {a: list(spec["models"]) for a, spec in self.llm_config["aliases"].items()}
            return LLMSettings(
                DbLLMSettings(self.s.database_url, TokenCipher(self.s.token_encryption_key)),
                defaults,
                self.cache,
                list_models,
                checker=lambda user_id, alias: self.llm.check(user_id, alias),
            )

        return self._pick("llm_settings", build)

    @cached_property
    def llm(self) -> Any:
        from ytstudio.adapters.llm import LiteLLMAdapter, build_router
        from ytstudio.adapters.redis_kv import RedisCache

        def build() -> Any:
            return LiteLLMAdapter(
                self.llm_settings.effective,
                lambda eff: build_router(self.llm_config, eff),
                self.llm_config,
                self.prompts,
                self.uow,
                RedisCache(self.redis, prefix=""),
                self.limiter,
                self.events.publish,
                self.s.llm_cache_ttl_s,
                claim_once=lambda key: bool(self.redis.set(key, "1", nx=True, ex=2 * 86400)),
            )

        return self._pick("llm", build)

    @cached_property
    def scorers(self) -> dict[str, Any]:
        from ytstudio.adapters import vendor_skills as v

        d = self.s.vendor_dir
        return self._pick(
            "scorers",
            lambda: {
                "hooks": v.VendorHookScorer(d),
                "linter": v.VendorPackageLinter(d),
                "outliers": v.VendorOutlierRanker(d),
                "chapters": v.VendorChapterBuilder(d),
                "edl": v.VendorEditDecisionList(d),
                "retention": v.VendorRetentionAnalyzer(d),
            },
        )

    # ----- media -----

    @cached_property
    def storage(self) -> Any:
        from ytstudio.adapters.storage import LocalStorage

        return self._pick("storage", lambda: LocalStorage(self.s.media_dir))

    @cached_property
    def media(self) -> Any:
        from ytstudio.adapters.media import FasterWhisperTranscriber, FFmpegMediaProcessor, PillowImageRenderer
        from ytstudio.adapters.narrator import PiperNarrator

        return self._pick(
            "media",
            lambda: {
                "media": FFmpegMediaProcessor(),
                "images": PillowImageRenderer(),
                "transcriber": FasterWhisperTranscriber(self.s.whisper_model),
                "narrator": PiperNarrator(
                    self.s.media_dir / "voices", {"en": self.s.tts_voice_en, "hi": self.s.tts_voice_hi}
                ),
            },
        )

    # ----- events -----

    @cached_property
    def events(self) -> Events:
        from ytstudio.adapters.notify import LogNotifier

        return Events(self.uow, self._pick("notifier", LogNotifier))

    # ----- application -----

    @cached_property
    def deps(self) -> Deps:
        sc, m = self.scorers, self.media
        return Deps(
            uow=self.uow,
            clock=self.clock,
            videos=self.youtube,
            analytics=self.youtube,
            trend_providers=self.trend_providers,
            llm=self.llm,
            prompts=self.prompts,
            hooks=sc["hooks"],
            linter=sc["linter"],
            outliers=sc["outliers"],
            chapters=sc["chapters"],
            edl=sc["edl"],
            retention=sc["retention"],
            transcriber=m["transcriber"],
            narrator=m["narrator"],
            media=m["media"],
            images=m["images"],
            storage=self.storage,
            publish=self.events.publish,
            settings={
                "outlier_min_multiple": self.s.outlier_min_multiple,
                "trend_providers": self.pipelines_config["trends"]["providers"],
            },
        )

    @cached_property
    def dispatcher(self) -> TaskDispatcher:
        def build() -> TaskDispatcher:
            from ytstudio.adapters.celery_dispatcher import CeleryDispatcher
            from ytstudio.interfaces.worker import celery_app

            return CeleryDispatcher(celery_app, self.s.queue_limits)

        return self._pick("dispatcher", build)

    @cached_property
    def runner(self) -> PipelineRunner:
        pipelines = {k: v["steps"] for k, v in self.pipelines_config["pipelines"].items()}
        runner = PipelineRunner(self.deps, self.dispatcher, self.lock, pipelines)
        if hasattr(self.dispatcher, "runner"):  # the inline dispatcher calls back into the runner
            self.dispatcher.runner = runner
        return runner

    @cached_property
    def accounts(self) -> Accounts:
        return Accounts(self.uow, self.oauth, self.vault, self.youtube, self.s.allowed)

    @cached_property
    def sync(self) -> Sync:
        return Sync(self.uow, self.youtube, self.youtube, self.clock)

    @cached_property
    def scheduling(self) -> Scheduling:
        return Scheduling(self.uow, self.runner, self.clock)

    @cached_property
    def profiles(self) -> Profiles:
        return Profiles(self.uow, self.youtube, self.llm)

    @cached_property
    def review(self) -> Review:
        return Review(self.uow, self.runner, self.clock, self.storage)

    @cached_property
    def shorts(self) -> Shorts:
        return Shorts(self.uow, self.runner, self.youtube, self.storage)

    # ----- health -----

    def health(self) -> dict[str, Any]:
        from sqlalchemy import text

        from ytstudio.adapters.persistence.db import engine_for

        out: dict[str, Any] = {}
        try:
            with engine_for(self.s.database_url).connect() as c:
                c.execute(text("SELECT 1"))
            out["postgres"] = {"ok": True}
        except Exception as e:
            out["postgres"] = {"ok": False, "error": type(e).__name__}
        try:
            self.redis.ping()
            out["redis"] = {"ok": True}
            last = self.heartbeat.last("tick")
            stale = last is None or self.clock.now() - last > timedelta(minutes=self.s.beat_stale_minutes)
            out["beat"] = {"ok": not stale, "last_tick": last.isoformat() if last else None}
            for q in ("io", "llm", "media"):
                seen = self.heartbeat.last(f"worker:{q}")
                out[f"worker_{q}"] = {
                    "ok": bool(seen and self.clock.now() - seen < timedelta(minutes=3)),
                    "last_seen": seen.isoformat() if seen else None,
                }
        except Exception as e:
            out["redis"] = {"ok": False, "error": type(e).__name__}
            out["beat"] = {"ok": False}
        return out


_container: Container | None = None


def get_container() -> Container:
    global _container
    if _container is None:
        _container = Container()
    return _container


def set_container(c: Container | None) -> None:
    global _container
    _container = c
