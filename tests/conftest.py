import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

KEY = Fernet.generate_key().decode()
CLIENT_JSON = os.path.join(tempfile.mkdtemp(), "YTStudioCred.json")  # same shape as Cloud Console's download
with open(CLIENT_JSON, "w") as f:
    json.dump({"web": {"client_id": "cid.apps.googleusercontent.com", "client_secret": "csecret"}}, f)
os.environ.update(
    {
        "TOKEN_ENCRYPTION_KEY": KEY,
        "ALLOWED_EMAILS": "me@example.com,other@example.com",
        "GOOGLE_CLIENT_SECRETS_FILE": CLIENT_JSON,
        "BASE_URL": "http://testserver",
        "REDIS_URL": "redis://localhost:6379/15",
        "CELERY_BROKER_URL": "redis://localhost:6379/13",  # tests never touch the dev broker
        "CELERY_RESULT_BACKEND": "redis://localhost:6379/14",
        "LOG_JSON": "false",
        "LEGACY_TOKENS_DIR": "/nonexistent-ytstudio-tokens",  # never read the developer's real tokens/ folder
    }
)

from tests import fakes  # noqa: E402
from ytstudio.adapters.llm import FakeLLM  # noqa: E402
from ytstudio.adapters.prompts import FilePromptRepository  # noqa: E402
from ytstudio.adapters.storage import LocalStorage  # noqa: E402
from ytstudio.application.pipeline import InlineDispatcher  # noqa: E402
from ytstudio.cli import migrate  # noqa: E402
from ytstudio.config import ROOT, Settings, get_settings  # noqa: E402
from ytstudio.container import Container, set_container  # noqa: E402


@pytest.fixture(scope="session")
def template_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from sqlalchemy import text

    from ytstudio.adapters.persistence.db import engine_for

    path = tmp_path_factory.mktemp("db") / "template.db"
    url = f"sqlite:///{path}"
    migrate(url)
    with engine_for(url).connect() as c:  # fold the WAL into the main file so a plain copy has everything
        c.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    engine_for(url).dispose()
    return path


@pytest.fixture
def db_url(template_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    path = tmp_path / "test.db"
    shutil.copyfile(template_db, path)
    url = f"sqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


@pytest.fixture
def yt() -> fakes.FakeYouTube:
    from ytstudio.ports import ChannelInfo

    own = [
        fakes.vid(f"own{i}", "UCme", views=v, days_old=5 + i) for i, v in enumerate([5000, 900, 1000, 1100, 150, 1200])
    ]
    return fakes.FakeYouTube(
        own={"UCme": own},
        others_recent={"UCa": [1000, 1200, 800, 1100, 900], "UCb": [50_000, 60_000, 55_000, 52_000]},
        infos={
            "UCme": ChannelInfo(
                id="UCme",
                title="My Channel",
                thumb="t",
                uploads_playlist="UUme",
                description="Money tips",
                subscribers=10,
                views=100,
                videos=6,
            )
        },
    )


@pytest.fixture
def container(db_url: str, yt: fakes.FakeYouTube, tmp_path: Path) -> Container:
    s = Settings()
    dispatcher = InlineDispatcher()
    prompts = FilePromptRepository(ROOT / "prompts", ROOT / "vendor" / "youtube-agent-skill")
    c = Container(
        s,
        clock=fakes.FakeClock(),
        cache=fakes.DictCache(),
        lock=fakes.DictLock(),
        limiter=fakes.NoLimit(),
        quota=fakes.CountingQuota(),
        vault=fakes.MemVault(),
        oauth=fakes.FakeOAuth(),
        youtube=yt,
        trend_providers=[
            fakes.FakeTrends(
                "most_popular",
                [
                    fakes.vid("t1", "UCa", 9000, 3, "I tried rent hacks"),
                    fakes.vid("t2", "UCb", 60_000, 2, "Big channel normal"),
                ],
            ),
            fakes.FakeTrends(
                "niche_search", [fakes.vid("t1", "UCa", 9000, 3, "I tried rent hacks"), fakes.vid("own1", "UCme", 900)]
            ),
        ],
        llm=FakeLLM(fakes.llm_responses(), prompts=prompts),
        notifier=fakes.RecordingNotifier(),
        dispatcher=dispatcher,
        storage=LocalStorage(tmp_path / "media"),
        media={"media": fakes.FakeMedia(), "images": fakes.FakeImages(), "transcriber": fakes.FakeTranscriber()},
        prompts=prompts,
    )
    set_container(c)
    yield c
    set_container(None)


@pytest.fixture
def user_and_channel(container: Container) -> tuple[int, str]:
    user = container.accounts.owner("me@example.com")
    container.accounts.connect(user.id, fakes.OAuthToken(refresh_token="rt", scopes=["s"]))
    return user.id, "UCme"
