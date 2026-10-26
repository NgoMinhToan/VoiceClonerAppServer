from pathlib import Path
import yaml

def test_docker_compose_structure():
    compose_path = Path("VoiceClonerAppServer/docker-compose.yml")
    if not compose_path.exists():
        compose_path = Path("docker-compose.yml")
    assert compose_path.exists()
    with open(compose_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    services = data.get("services", {})
    assert "db" in services
    assert "redis" in services
    assert "appserver" in services

    app_env = services["appserver"].get("environment", {})
    env_str = str(app_env)
    assert "DATABASE_URL" in env_str
    assert "REDIS_URL" in env_str

def test_env_example_contains_only_db_and_redis():
    env_path = Path("VoiceClonerAppServer/.env.example")
    if not env_path.exists():
        env_path = Path(".env.example")
    assert env_path.exists()
    env_example = env_path.read_text(encoding="utf-8")
    assert "DATABASE_URL" in env_example or "POSTGRES_USER" in env_example
    assert "REDIS_URL" in env_example

