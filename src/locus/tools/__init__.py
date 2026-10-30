"""Tools primitive — tool system with ABC interface and built-in tools."""

from __future__ import annotations

from locus.tools._stubs import BaseTool, ToolExecutor, ToolSpec

# Import built-in tools to trigger @ToolRegistry.register() decorators.
# Each is wrapped in try/except so the package loads even before the
# individual tool modules are created.
try:
    import locus.tools.calculator  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.think  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.retrieval  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.llm_tool  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.file_read  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.web_search  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.weather  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.fx_macro  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.code_interpreter  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.code_interpreter_docker  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.repl  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.storage_tools  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.mcp_adapter  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.channel_tools  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.http_request  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.docker_shell_exec  # noqa: F401
    import locus.tools.shell_exec  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.memory_manage  # noqa: F401
except ImportError:
    pass
try:
    import locus.tools.user_profile_manage  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.skill_manage  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.file_write  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.apply_patch  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.git_tool  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.db_query  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.pdf_tool  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.image_tool  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.audio_tool  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.knowledge_tools  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.text_to_speech  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.digest_collect  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.scan_chunks  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.knowledge_sql  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.apple_calendar  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.knowledge_search  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.browser  # noqa: F401
    import locus.tools.browser_axtree  # noqa: F401
except ImportError:
    pass

try:
    import locus.tools.proactive_tools  # noqa: F401
except ImportError:
    pass

__all__ = ["BaseTool", "ToolExecutor", "ToolSpec"]
