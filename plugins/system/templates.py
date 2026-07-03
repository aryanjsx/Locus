"""
Project scaffolding templates — declarative stack definitions.

Each stack is a dictionary mapping relative file paths to their content.
Directories are inferred from file paths (any path containing a ``/``
will have its parent directories created automatically).

Adding a new stack:
    1. Add a function ``_<stack>_files(name)`` that returns a dict.
    2. Register it in ``STACKS``.
    3. Add a ``ParamSpec`` entry is NOT needed — the executor handles
       validation via ``STACKS.keys()``.
"""

from __future__ import annotations


STACK_CHOICES: tuple[str, ...] = ("python", "node", "react", "fastapi")

DEFAULT_STACK: str = "python"


def _common_gitignore() -> str:
    return (
        ".env\n"
        ".venv/\n"
        "node_modules/\n"
        "__pycache__/\n"
        "*.pyc\n"
        "*.egg-info/\n"
        "dist/\n"
        "build/\n"
        ".DS_Store\n"
        "Thumbs.db\n"
    )


def _python_files(name: str) -> dict[str, str]:
    return {
        "src/__init__.py": "",
        "src/main.py": (
            f'"""Entry point for {name}."""\n\n\n'
            "def main() -> None:\n"
            '    print("Hello from ' + name + '")\n\n\n'
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
        "tests/__init__.py": "",
        "tests/test_main.py": (
            "from src.main import main\n\n\n"
            "def test_main_runs(capsys):\n"
            "    main()\n"
            '    assert "Hello" in capsys.readouterr().out\n'
        ),
        "requirements.txt": "",
        "README.md": f"# {name}\n\nA Python project scaffolded by Kommy.\n",
        ".gitignore": _common_gitignore(),
    }


def _node_files(name: str) -> dict[str, str]:
    return {
        "src/index.js": (
            f'console.log("Hello from {name}");\n'
        ),
        "tests/.gitkeep": "",
        "package.json": (
            "{\n"
            f'  "name": "{name}",\n'
            '  "version": "1.0.0",\n'
            f'  "description": "{name} — scaffolded by Kommy",\n'
            '  "main": "src/index.js",\n'
            '  "scripts": {\n'
            '    "start": "node src/index.js",\n'
            '    "test": "echo \\"No tests yet\\""\n'
            "  },\n"
            '  "license": "MIT"\n'
            "}\n"
        ),
        "README.md": f"# {name}\n\nA Node.js project scaffolded by Kommy.\n",
        ".gitignore": _common_gitignore(),
    }


def _react_files(name: str) -> dict[str, str]:
    return {
        "public/index.html": (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '  <meta charset="UTF-8" />\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
            f"  <title>{name}</title>\n"
            "</head>\n"
            "<body>\n"
            '  <div id="root"></div>\n'
            "</body>\n"
            "</html>\n"
        ),
        "src/App.jsx": (
            "export default function App() {\n"
            "  return (\n"
            "    <div>\n"
            f"      <h1>{name}</h1>\n"
            "      <p>Scaffolded by Kommy.</p>\n"
            "    </div>\n"
            "  );\n"
            "}\n"
        ),
        "src/index.jsx": (
            'import { createRoot } from "react-dom/client";\n'
            'import App from "./App";\n\n'
            'createRoot(document.getElementById("root")).render(<App />);\n'
        ),
        "src/App.css": (
            "body {\n"
            "  margin: 0;\n"
            "  font-family: system-ui, sans-serif;\n"
            "}\n"
        ),
        "package.json": (
            "{\n"
            f'  "name": "{name}",\n'
            '  "version": "1.0.0",\n'
            '  "private": true,\n'
            '  "scripts": {\n'
            '    "dev": "vite",\n'
            '    "build": "vite build"\n'
            "  },\n"
            '  "dependencies": {\n'
            '    "react": "^18.2.0",\n'
            '    "react-dom": "^18.2.0"\n'
            "  },\n"
            '  "devDependencies": {\n'
            '    "@vitejs/plugin-react": "^4.0.0",\n'
            '    "vite": "^5.0.0"\n'
            "  }\n"
            "}\n"
        ),
        "README.md": f"# {name}\n\nA React project scaffolded by Kommy.\n",
        ".gitignore": _common_gitignore(),
    }


def _fastapi_files(name: str) -> dict[str, str]:
    module = name.replace("-", "_").replace(" ", "_").lower()
    return {
        f"{module}/__init__.py": "",
        f"{module}/main.py": (
            "from fastapi import FastAPI\n\n"
            "app = FastAPI()\n\n\n"
            '@app.get("/")\n'
            "async def root():\n"
            f'    return {{"message": "Hello from {name}"}}\n'
        ),
        "tests/__init__.py": "",
        "tests/test_root.py": (
            "from fastapi.testclient import TestClient\n\n"
            f"from {module}.main import app\n\n"
            "client = TestClient(app)\n\n\n"
            "def test_root():\n"
            '    response = client.get("/")\n'
            "    assert response.status_code == 200\n"
        ),
        "requirements.txt": "fastapi>=0.100.0\nuvicorn[standard]>=0.23.0\n",
        "README.md": f"# {name}\n\nA FastAPI project scaffolded by Kommy.\n",
        ".gitignore": _common_gitignore(),
    }


STACKS: dict[str, object] = {
    "python":  _python_files,
    "node":    _node_files,
    "react":   _react_files,
    "fastapi": _fastapi_files,
}


def get_template(stack: str, project_name: str) -> dict[str, str]:
    """Return ``{relative_path: content}`` for the given *stack*.

    Raises ``KeyError`` if the stack is not registered.
    """
    builder = STACKS[stack]
    return builder(project_name)


__all__ = ["STACK_CHOICES", "DEFAULT_STACK", "STACKS", "get_template"]
