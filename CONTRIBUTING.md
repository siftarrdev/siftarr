# Contributing to Arbitratarr

Thank you for your interest in contributing to Arbitratarr! This document provides guidelines and instructions for developers.

## Development Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) - Modern Python package manager
- Docker and Docker Compose (for testing with external services)

### Initial Setup

```bash
# Clone the repository
git clone <repository-url>
cd arbitratarr

# Install dependencies using uv
uv sync

# Activate the virtual environment
source .venv/bin/activate

# Run database migrations
uv run alembic upgrade head

# Start the development server
uv run uvicorn arbitratarr.main:app --reload
```

## Development Workflow

### Running the Application

```bash
# Development mode with auto-reload
uv run uvicorn arbitratarr.main:app --reload --host 0.0.0.0 --port 8000

# Production mode
uv run uvicorn arbitratarr.main:app --host 0.0.0.0 --port 8000
```

### Code Quality Tools

We use several tools to maintain code quality. Always run these before committing:

```bash
# Format code with ruff
uv run ruff format .

# Lint code with ruff
uv run ruff check .

# Auto-fix linting issues
uv run ruff check . --fix

# Type check with ty (Astral's type checker)
uv run ty check

# Run tests with pytest
uv run pytest

# Run tests with coverage
uv run pytest --cov=app --cov-report=term-missing
```

### Type Checking

We use `ty` (Astral's type checker) for static type checking:

```bash
# Check all files
uv run ty check

# Check specific file
uv run ty check app/arbitratarr/main.py

# Show detailed errors
uv run ty check --show-error-codes
```

Configuration for `ty` is in `ty.toml`.

## Project Structure

```
arbitratarr/
├── app/
│   └── arbitratarr/
│       ├── main.py              # FastAPI application entry point
│       ├── config.py            # Configuration and environment variables
│       ├── database.py          # SQLAlchemy database setup
│       ├── models/              # Database models
│       ├── routers/             # FastAPI route handlers
│       └── services/            # Business logic services
├── tests/                       # Test files
├── alembic/                     # Database migrations
├── data/                        # Local data storage (gitignored)
├── docker-compose.yml           # Docker Compose configuration
├── Dockerfile                   # Docker image definition
├── pyproject.toml              # Project dependencies and metadata
├── ty.toml                     # Type checker configuration
└── ruff.toml                   # Linter configuration
```

## Writing Code

### Style Guidelines

- **Line length**: 100 characters (enforced by ruff)
- **Target Python version**: 3.12+
- Use type hints throughout the codebase
- Use async/await for I/O operations
- Follow PEP 8 style guidelines (enforced by ruff)

### Adding New Dependencies

```bash
# Add runtime dependency
uv add <package-name>

# Add development dependency
uv add --dev <package-name>
```

### Database Migrations

We use Alembic for database migrations:

```bash
# Create a new migration after modifying models
uv run alembic revision --autogenerate -m "description of changes"

# Run pending migrations
uv run alembic upgrade head

# Downgrade one migration
uv run alembic downgrade -1

# View current migration version
uv run alembic current
```

## Testing

### Running Tests

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_rule_engine.py

# Run with verbose output
uv run pytest -v

# Run tests matching a pattern
uv run pytest -k "test_name_pattern"
```

### Writing Tests

- Place tests in the `tests/` directory
- Use `pytest-asyncio` for async tests
- Mock external services (Overseerr, Prowlarr, qBittorrent) in unit tests

Example test structure:

```python
import pytest
from app.arbitratarr.services.rule_engine import RuleEngine

@pytest.fixture
def rule_engine():
    return RuleEngine()

@pytest.mark.asyncio
async def test_rule_evaluation(rule_engine):
    result = await rule_engine.evaluate_release(release_data)
    assert result.passed is True
```

## Docker Development

### Building and Running

```bash
# Build the Docker image
docker-compose build

# Run with Docker Compose
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Environment Variables for Development

Create a `.env` file for local development:

```bash
# Copy example file
cp .env.example .env

# Edit with your values
nano .env
```

## Submitting Changes

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Make your changes and add tests
3. Run all quality checks: `uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest`
4. Commit with descriptive messages
5. Push to your fork and create a pull request

## Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- [uv Documentation](https://docs.astral.sh/uv/)
- [ty Type Checker](https://github.com/astral-sh/ty)
- [pytest Documentation](https://docs.pytest.org/)

## Questions?

Feel free to open an issue for questions or discussion.
