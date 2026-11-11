"""Entry point: `uv run main.py` for local development. Production runs uvicorn/celery from docker-compose."""

from ytstudio.interfaces.web.app import create_app

app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
