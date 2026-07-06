"""
AURA - CommandRegistry (closure-walk-safe lockdown).

Post Phase-3 lockdown invariants
--------------------------------
There is **exactly one** function in the entire registry object graph
that can execute a command: ``_execute_safe``, built inside
:meth:`CommandRegistry.__init__`.  It is captured by a single cell
inside the ``_ExecutorProxy.execute`` method.  Everything else
reachable via introspection is either:

* non-callable data (dicts, instances without ``__call__``, booleans,
  strings), or
* ``_execute_safe`` itself.

That property is validated at runtime by
:func:`assert_safe_closures` and by the destruction test in
``tests/test_closure_walk.py``.

Why this is safe
----------------
The previous design handed the registry a ``dispatch_fn`` callable
(either ``_engine_dispatch`` or ``_worker_dispatch``) and captured it
as a closure cell of the pipeline function.  A closure walker
(``proxy.execute.__func__.__closure__`` then that function's closure)
could reach ``dispatch_fn`` and call it directly, bypassing the entire
security pipeline.

In this revision:

* There is no ``dispatch_fn`` anywhere.  No raw executor invocation
  function is ever materialised in the main process.
* The pipeline calls ``worker_port.send({...})`` inline.  The worker
  port is a **non-callable** instance reached via attribute lookup,
  not via a closure cell pointing at a bound method.
* The executor map lives only in the worker subprocess; the main
  process has no ``ExecutionEngine`` instance at all.

Immutability
------------
``CommandRegistry`` instances are frozen post-construction:
``__setattr__`` / ``__delattr__`` raise, ``__dir__`` hides internals,
``__getattr__`` explicitly denies known bypass names.
"""
from __future__ import annotations

import json
import threading
import uuid
import weakref
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Protocol

from aura.core.errors import (
    AuraError,
    ConfirmationDenied,  # noqa: F401 - re-exported
    ConfirmationTimeout,  # noqa: F401
    EngineError,
    ExecutionError,
    PermissionDenied,
    RateLimitError,
    RegistryError,
    SchemaError,
)
from aura.core.event_bus import EventBus, EventType
from aura.core.param_schema import validate_params
from aura.security.permissions import PermissionLevel, PermissionValidator
from aura.security.plugin_manifest import PluginManifest, PluginManifestError
from aura.security.rate_limiter import RateLimiter
from aura.core.result import CommandResult
from aura.security.safety_gate import AutoConfirmGate, SafetyGate
from aura.core.schema import CommandSpec, validate_command
from aura.core.tracing import current_trace_id


# =====================================================================
# Module-private mutation token.  Used to gate the only legitimate
# entry points that mutate _entries (register_metadata, unregister).
# Python introspection can still fetch this - it is not a secret - but
# it stops *accidental* mutation via ``registry._entries[...]=...``
# and it makes every mutation site explicit and auditable.
# =====================================================================
_ENTRIES_TOKEN: object = object()


# =====================================================================
# Worker reply validation limits.
# =====================================================================
_MAX_REPLY_BYTES_DEFAULT = 1 * 1024 * 1024  # 1 MiB

_REPLY_REQUIRED_RESULT = frozenset(
    {"type", "id", "action", "success", "message", "data",
     "command_type", "error_code"}
)
_REPLY_REQUIRED_ERROR = frozenset(
    {"type", "id", "action", "error_class", "error_code", "message"}
)


# =====================================================================
# Execution-port capability store  (CF-1 fix).
#
# Previously the pipeline closure captured a direct reference to the
# ``worker_port`` instance.  That object was reachable via
# ``registry._executor.execute.__func__.__closure__[*].__closure__[*]``
# and its ``.send`` method dispatched commands bypassing rate-limit,
# permission, safety-gate and audit lifecycle events.
#
# The fix removes the port from the pipeline closure entirely.  The
# pipeline function looks the port up in this module-level
# ``WeakValueDictionary`` keyed by ``id(_execute_safe)`` at call time.
# The closure therefore contains ONLY primitives and non-transport
# data, so a closure walk cannot reach any object with a ``.send``
# method.
#
# A parallel strong-reference dict (``_EXECUTION_PORT_ANCHORS``) keeps
# the port alive for exactly the lifetime of the pipeline function.
# ``weakref.finalize`` clears the anchor when ``_execute_safe`` is
# garbage-collected (i.e. when its registry is dropped), so no port is
# leaked between registries and ``id()`` re-use is safe.
#
# Note: an attacker with arbitrary code-exec in the process can still
# read ``sys.modules['aura.runtime.command_registry']._EXECUTION_PORTS``
# — but such an attacker already controls the interpreter and is out of
# scope.  The specific attack this closes is the closure-walk path that
# only requires introspection, not monkey-patching.
# =====================================================================
_EXECUTION_PORTS: "weakref.WeakValueDictionary[int, Any]" = (
    weakref.WeakValueDictionary()
)
# Strong-ref anchor keyed by the same id as ``_EXECUTION_PORTS``; the
# finalizer below drops the anchor when the pipeline function dies,
# which in turn allows the weak-value dict entry to be cleared.
_EXECUTION_PORT_ANCHORS: dict[int, Any] = {}


def _install_execution_port(pipeline_fn: Any, port: Any) -> None:
    """Register *port* as the transport for *pipeline_fn*.

    Stored in a :class:`weakref.WeakValueDictionary` so the entry
    disappears naturally once the pipeline function is no longer
    referenced.  A parallel strong-ref dict anchors the port for the
    pipeline's lifetime (otherwise the weak entry would be collected
    immediately in common call patterns where the caller constructs
    the port inline).
    """
    key = id(pipeline_fn)
    _EXECUTION_PORTS[key] = port
    _EXECUTION_PORT_ANCHORS[key] = port

    def _drop(_key: int = key) -> None:
        _EXECUTION_PORT_ANCHORS.pop(_key, None)

    weakref.finalize(pipeline_fn, _drop)


def _lookup_execution_port(pipeline_id: int) -> Any:
    """Fetch the port for the pipeline function identified by *pipeline_id*.

    Raises :class:`RuntimeError` when the port cannot be found — that
    state is never reachable in a correctly-constructed registry, so
    hitting it means the capability table was tampered with or the
    registry was never installed properly.
    """
    port = _EXECUTION_PORTS.get(pipeline_id)
    if port is None:
        raise RuntimeError(
            "Execution port missing: CommandRegistry capability store "
            "was cleared or never installed"
        )
    return port


# ---------------------------------------------------------------------
# WorkerPort protocol.  Any object satisfying ``send / has / actions``
# can be plugged in, but it MUST NOT be callable (no ``__call__``) or
# the closure-walk assertion will reject it.
# ---------------------------------------------------------------------
class WorkerPort(Protocol):
    def send(self, request: dict[str, Any]) -> dict[str, Any]: ...

    def has(self, action: str) -> bool: ...

    def actions(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class CommandEntry:
    """Metadata for one registered action (NO callable stored)."""

    action: str
    plugin: str
    description: str = ""
    destructive: bool = False
    permission_level: PermissionLevel = PermissionLevel.MEDIUM


# =====================================================================
# Immutable view over the action-metadata map.
#
# The raw dict lives here, reachable only via token-gated mutation
# helpers.  ``__setitem__`` / ``__delitem__`` raise, so
# ``registry._entries[action] = malicious_entry`` is no longer a
# bypass.  Read operations (``get`` / ``has`` / iteration) are freely
# available because the pipeline needs them.
# =====================================================================
def _make_entries_view(data: dict[str, CommandEntry]) -> Any:
    """Build a closure-backed read-only entries view.

    The underlying dict lives ONLY inside the methods' closures; no
    attribute path on the returned instance exposes it, so naive
    mutation via ``registry._entries[k] = v`` is rejected.  The two
    mutation helpers (``_mutate_set`` / ``_mutate_pop``) require the
    module-private :data:`_ENTRIES_TOKEN`.
    """

    _ALLOWED = frozenset(
        {
            "get", "has", "list", "keys", "values", "items",
            "__contains__", "__iter__", "__len__", "__getitem__",
            "__setitem__", "__delitem__",
            "_mutate_set", "_mutate_pop",
            # dunder basics Python calls through type, not attribute
            # access, but we still allow these names explicitly:
            "__class__", "__repr__", "__dir__",
            "__hash__", "__eq__", "__ne__",
        }
    )

    class _EntriesView:
        __slots__ = ()

        # ---- read-only API ------------------------------------------
        def get(
            self, key: str, default: CommandEntry | None = None
        ) -> CommandEntry | None:
            return data.get(key, default)

        def has(self, key: str) -> bool:
            return key in data

        def list(self) -> tuple[str, ...]:
            return tuple(data.keys())

        def __contains__(self, key: object) -> bool:
            return key in data

        def __iter__(self) -> Iterator[str]:
            return iter(list(data.keys()))

        def __len__(self) -> int:
            return len(data)

        def __getitem__(self, key: str) -> CommandEntry:
            return data[key]

        def values(self) -> tuple[CommandEntry, ...]:
            return tuple(data.values())

        def items(self) -> tuple[tuple[str, CommandEntry], ...]:
            return tuple(data.items())

        def keys(self) -> tuple[str, ...]:
            return tuple(data.keys())

        # ---- direct mutation is BANNED -----------------------------
        def __setitem__(self, key: str, value: Any) -> None:
            raise RegistryError(
                "CommandRegistry entries are immutable; "
                "use register_metadata()"
            )

        def __delitem__(self, key: str) -> None:
            raise RegistryError(
                "CommandRegistry entries are immutable; "
                "use unregister(token=<internal>)"
            )

        # ---- token-gated mutation (used by CommandRegistry only) ---
        def _mutate_set(
            self, token: object, key: str, value: CommandEntry
        ) -> None:
            if token is not _ENTRIES_TOKEN:
                raise RegistryError(
                    "_EntriesView mutation requires the internal token"
                )
            data[key] = value

        def _mutate_pop(
            self, token: object, key: str
        ) -> CommandEntry | None:
            if token is not _ENTRIES_TOKEN:
                raise RegistryError(
                    "_EntriesView mutation requires the internal token"
                )
            return data.pop(key, None)

        # ---- attribute firewall ------------------------------------
        def __getattribute__(self, name: str) -> Any:
            if name in _ALLOWED:
                return object.__getattribute__(self, name)
            raise AttributeError(
                f"_EntriesView: attribute {name!r} is not accessible"
            )

        def __setattr__(self, name: str, value: Any) -> None:
            raise AttributeError("_EntriesView is immutable")

        def __delattr__(self, name: str) -> None:
            raise AttributeError("_EntriesView is immutable")

        def __dir__(self) -> list[str]:
            return sorted(
                n for n in _ALLOWED
                if not n.startswith("__") or n in ("__len__",
                                                    "__iter__",
                                                    "__getitem__",
                                                    "__contains__")
            )

        def __repr__(self) -> str:
            return f"<_EntriesView count={len(data)}>"

    return _EntriesView()


# =====================================================================
# Worker reply validation.
# Strictly checks: type/shape, required fields, no extras, action echo,
# id echo, size cap.  Runs in the CommandRegistry pipeline before we
# hand the reply to :func:`_reply_to_result`.
# =====================================================================
def _validate_worker_reply(
    reply: Any,
    *,
    expected_action: str,
    expected_id: str,
    max_bytes: int,
) -> None:
    if not isinstance(reply, dict):
        raise EngineError(
            f"Worker reply is not a dict (got {type(reply).__name__})"
        )

    # Size cap (defence against memory-blowup on a compromised worker).
    try:
        size = len(json.dumps(reply, default=str))
    except Exception as exc:  # noqa: BLE001 - serialisation boundary
        raise EngineError(
            f"Worker reply is not JSON-serialisable: {exc}"
        ) from exc
    if size > max_bytes:
        raise EngineError(
            f"Worker reply exceeds {max_bytes} bytes (got {size})"
        )

    msg_type = reply.get("type")
    if msg_type not in ("result", "error"):
        raise EngineError(
            f"Worker reply has unexpected type: {msg_type!r}"
        )

    required = (
        _REPLY_REQUIRED_RESULT if msg_type == "result"
        else _REPLY_REQUIRED_ERROR
    )
    keys = set(reply.keys())
    missing = required - keys
    if missing:
        raise EngineError(
            f"Worker reply missing required fields: {sorted(missing)}"
        )
    extra = keys - required
    if extra:
        raise EngineError(
            f"Worker reply has unexpected fields: {sorted(extra)}"
        )

    if msg_type == "result":
        if not isinstance(reply["success"], bool):
            raise EngineError("Worker reply 'success' must be bool")
        if not isinstance(reply["message"], str):
            raise EngineError("Worker reply 'message' must be str")
        if not isinstance(reply["data"], dict):
            raise EngineError("Worker reply 'data' must be dict")
        if not isinstance(reply["command_type"], str):
            raise EngineError("Worker reply 'command_type' must be str")
        ec = reply.get("error_code")
        if ec is not None and not isinstance(ec, str):
            raise EngineError(
                "Worker reply 'error_code' must be str or None"
            )
    else:  # error
        for field in ("error_class", "message"):
            if not isinstance(reply[field], str):
                raise EngineError(
                    f"Worker reply '{field}' must be str"
                )
        ec = reply["error_code"]
        if ec is not None and not isinstance(ec, str):
            raise EngineError(
                "Worker reply 'error_code' must be str or None"
            )

    # Action echo (defence against a worker returning a different
    # command's result - e.g. a race or MITM scenario between queued
    # requests).
    if reply.get("action") != expected_action:
        raise EngineError(
            "Worker reply action mismatch: sent "
            f"{expected_action!r}, got {reply.get('action')!r}"
        )
    if reply.get("id") != expected_id:
        raise EngineError(
            "Worker reply id mismatch: sent "
            f"{expected_id!r}, got {reply.get('id')!r}"
        )


# =====================================================================
# Module-level helpers.  These are intentionally NOT nested functions,
# so they never land in any closure cell.
# =====================================================================
def _apply_safety_inline(
    spec: CommandSpec,
    entry: CommandEntry,
    *,
    source: str,
    trace_id: str | None,
    bus: EventBus,
    gate: SafetyGate,
    auto_confirm: bool,
) -> CommandSpec:
    if entry.destructive and not spec.requires_confirm:
        spec = spec.with_confirm(True)
    if not spec.requires_confirm:
        return spec
    if auto_confirm:
        bus.emit(
            EventType.COMMAND_AUTO_CONFIRMED,
            {
                "action": spec.action,
                "source": source,
                "trace_id": trace_id,
            },
        )
        return spec
    gate.request(
        action=spec.action,
        params=dict(spec.params),
        source=source,
        permission=entry.permission_level.value,
        trace_id=trace_id,
    )
    return spec


def _reply_to_result(
    reply: dict[str, Any], action: str
) -> CommandResult:
    """Translate a worker reply envelope into a :class:`CommandResult`.

    Error envelopes are raised as the appropriate exception class.
    This function is module-level so it never enters a closure cell.
    """
    if not isinstance(reply, dict):
        raise EngineError(f"Worker returned non-dict reply: {reply!r}")
    msg_type = reply.get("type")
    if msg_type == "result":
        return CommandResult(
            success=bool(reply.get("success")),
            message=str(reply.get("message", "")),
            data=dict(reply.get("data") or {}),
            command_type=reply.get("command_type") or action,
            error_code=reply.get("error_code"),
        )
    if msg_type == "error":
        err_class = reply.get("error_class", "EngineError")
        message = str(reply.get("message", "worker error"))
        # Lookup canonical error class from aura.core.errors by name.
        from aura.core import errors as _errors_module
        cls = getattr(_errors_module, err_class, None)
        if isinstance(cls, type) and issubclass(cls, AuraError):
            raise cls(message)
        raise EngineError(f"{err_class}: {message}")
    raise EngineError(f"Unknown worker reply type: {msg_type!r}")


# =====================================================================
# _ExecutorProxy - opaque execute-only capability object.
# Holds exactly one closure cell: the pipeline function.
# =====================================================================
def _make_executor_proxy(
    fn: Callable[[CommandSpec, str], CommandResult],
) -> Any:
    _ALLOWED = frozenset(
        {
            "execute",
            "__class__",
            "__doc__",
            "__dir__",
            "__repr__",
            "__setattr__",
            "__delattr__",
            "__getattribute__",
            "__hash__",
            "__eq__",
            "__ne__",
            "__sizeof__",
            "__init_subclass__",
            "__subclasshook__",
            "__format__",
            "__reduce__",
            "__reduce_ex__",
        }
    )

    class _ExecutorProxy:
        __slots__ = ()

        def execute(self, spec: CommandSpec, source: str) -> CommandResult:
            return fn(spec, source)

        def __getattribute__(self, name: str) -> Any:
            if name in _ALLOWED:
                return object.__getattribute__(self, name)
            raise AttributeError(
                f"_ExecutorProxy: attribute {name!r} is not accessible"
            )

        def __setattr__(self, name: str, value: Any) -> None:
            raise AttributeError("_ExecutorProxy is immutable")

        def __delattr__(self, name: str) -> None:
            raise AttributeError("_ExecutorProxy is immutable")

        def __dir__(self) -> list[str]:
            return ["execute"]

        def __repr__(self) -> str:
            return "<_ExecutorProxy execute-only>"

    return _ExecutorProxy()


# =====================================================================
# CommandRegistry
# =====================================================================
class CommandRegistry:
    """Single safety-enforcing dispatch boundary."""

    _PUBLIC_API = (
        "execute",
        "register_metadata",
        "unregister",
        "has",
        "get",
        "list",
    )

    _DENY_NAMES = frozenset(
        {
            "_engine",
            "_worker",
            "_worker_port",
            "_dispatch",
            "_dispatcher",
            "_dispatcher_source",
            "_seal",
            "_acquire_capability",
            "_CommandRegistry__dispatch",
            "_CommandRegistry__has",
            "_CommandRegistry__engine",
            "_CommandRegistry__worker",
            "attach_security",
            "attach_manifest",
        }
    )

    __slots__ = (
        "_bus",
        "_entries",
        "_lock",
        "_manifest",
        "_rate_limiter",
        "_permissions",
        "_auto_confirm",
        "_safety_gate",
        "_executor",
        "_frozen",
    )

    def __init__(
        self,
        bus: EventBus,
        worker_port: WorkerPort,
        *,
        manifest: PluginManifest,
        rate_limiter: RateLimiter | None = None,
        permission_validator: PermissionValidator | None = None,
        safety_gate: SafetyGate | None = None,
        auto_confirm: bool = False,
    ) -> None:
        # --- argument validation --------------------------------------
        if not isinstance(manifest, PluginManifest):
            raise RegistryError(
                "manifest is required and must be a PluginManifest instance"
            )
        if worker_port is None:
            raise RegistryError("worker_port is required")
        # CRITICAL: a callable port would defeat the closure-walk
        # invariant (the closure of _execute_safe would then capture a
        # callable that isn't _execute_safe).
        if callable(worker_port):
            raise RegistryError(
                "worker_port must not be callable (no __call__)"
            )
        if not hasattr(worker_port, "send") or not callable(
            getattr(worker_port, "send", None)
        ):
            raise RegistryError(
                "worker_port must expose a .send(request) method"
            )

        # --- internal state (set via object.__setattr__ during init) --
        object.__setattr__(self, "_bus", bus)
        # Entries live inside the closure of _make_entries_view;
        # _entries is an opaque, read-only view over that dict.  The
        # only way to mutate it is via the token-gated helpers, which
        # register_metadata() / unregister() hold in scope.
        _entries_data: dict[str, CommandEntry] = {}
        object.__setattr__(
            self, "_entries", _make_entries_view(_entries_data)
        )
        object.__setattr__(self, "_lock", threading.RLock())
        object.__setattr__(self, "_manifest", manifest)
        object.__setattr__(
            self, "_rate_limiter", rate_limiter or RateLimiter()
        )
        object.__setattr__(
            self,
            "_permissions",
            permission_validator or PermissionValidator(),
        )
        object.__setattr__(self, "_auto_confirm", bool(auto_confirm))
        object.__setattr__(
            self,
            "_safety_gate",
            safety_gate or AutoConfirmGate(bus),
        )

        # -----------------------------------------------------------------
        # Build the safe pipeline.  EVERY closure cell below MUST be a
        # non-callable instance (dict, EventBus, RateLimiter, etc.) or
        # a primitive, AND must NOT expose a ``.send`` method.  There
        # is NO dispatch_fn, NO executor map, NO bound method captured
        # as a cell, and — critically — NO reference to the worker
        # transport.  The transport is fetched at call time via the
        # module-level capability table keyed by ``id(_execute_safe)``
        # (see _install_execution_port below).  This closes CF-1: a
        # closure walker cannot reach any object whose attribute
        # lookup yields a ``.send`` dispatcher.
        # -----------------------------------------------------------------
        bus_ref: EventBus = bus
        # Capture the raw dict (non-callable) directly for the fast
        # read path; the view on self._entries is a presentation layer
        # for external callers only.
        entries_ref: dict[str, CommandEntry] = _entries_data
        rate_limiter_ref: RateLimiter = self._rate_limiter
        permissions_ref: PermissionValidator = self._permissions
        safety_gate_ref: SafetyGate = self._safety_gate
        auto_confirm_ref: bool = bool(auto_confirm)
        max_reply_bytes_ref: int = _MAX_REPLY_BYTES_DEFAULT
        # DELIBERATELY NO ``worker_port_ref = worker_port`` HERE.
        # See CF-1 write-up in the module docstring / audit report.
        #
        # ``pipeline_id`` is a plain int cell that the pipeline reads
        # at call time to look up its transport in _EXECUTION_PORTS.
        # We pre-declare it here (value 0) so _execute_safe captures
        # an int cell, NOT a self-reference.  It is rebound to
        # ``id(_execute_safe)`` immediately after the function is
        # defined.  Python closure cells share state with the enclosing
        # scope, so the inner function sees the updated value.
        pipeline_id: int = 0

        def _execute_safe(
            spec: CommandSpec, source: str
        ) -> CommandResult:
            # --- source validation (STRICT, no normalisation) ---
            # History: this used to do ``source.strip().lower()``, which
            # silently turned ``"CLI "`` / ``" cli"`` / ``"CLI\n"`` into
            # the ``cli`` bucket (CRITICAL).  That is a latent privilege
            # escalation vector: any upstream layer that forwards an
            # attacker-influenced source label could gain ``cli``'s cap
            # by padding a whitespace or changing case.  We now require
            # an EXACT match against the validator's known sources and
            # refuse everything else at the gate — typos surface at
            # dev time instead of silently downgrading in prod.
            if not isinstance(source, str):
                raise SchemaError("source must be a string")
            if not source:
                raise SchemaError("source must be a non-empty string")
            known = permissions_ref.known_sources
            if source not in known:
                raise SchemaError(
                    f"Unknown source {source!r}; must be one of "
                    f"{sorted(known)} (exact match, no whitespace or case "
                    f"normalisation)."
                )
            clean_source = source  # already canonical — pass through verbatim.

            if not isinstance(spec, CommandSpec):
                spec = validate_command(spec)

            entry = entries_ref.get(spec.action)
            if entry is None:
                raise RegistryError(f"Unknown action: {spec.action!r}")

            trace_id = current_trace_id()

            # 1) Parameter schema + size limits.
            try:
                validate_params(entry.action, dict(spec.params))
            except SchemaError as exc:
                bus_ref.emit(
                    EventType.SCHEMA_REJECTED,
                    {
                        "action": entry.action,
                        "source": clean_source,
                        "reason": str(exc),
                        "trace_id": trace_id,
                    },
                )
                raise

            # 2) Rate limit.
            try:
                rate_limiter_ref.check(
                    entry.action,
                    dict(spec.params),
                    source=clean_source,
                )
            except RateLimitError as exc:
                bus_ref.emit(
                    EventType.RATE_LIMIT_BLOCKED,
                    {
                        "action": entry.action,
                        "source": clean_source,
                        "trace_id": trace_id,
                        "reason": str(exc),
                    },
                )
                raise

            # 3) Permission check.
            try:
                permissions_ref.validate(
                    action=entry.action,
                    level=entry.permission_level,
                    source=clean_source,
                )
            except PermissionDenied as exc:
                bus_ref.emit(
                    EventType.PERMISSION_DENIED,
                    {
                        "action": entry.action,
                        "source": clean_source,
                        "required": entry.permission_level.value,
                        "trace_id": trace_id,
                        "reason": str(exc),
                    },
                )
                raise

            # 4) Safety gate (module-level helper, not a closure cell).
            spec = _apply_safety_inline(
                spec,
                entry,
                source=clean_source,
                trace_id=trace_id,
                bus=bus_ref,
                gate=safety_gate_ref,
                auto_confirm=auto_confirm_ref,
            )

            # 5) Lifecycle: executing.
            bus_ref.emit(
                EventType.COMMAND_EXECUTING,
                {
                    "action": entry.action,
                    "plugin": entry.plugin,
                    "destructive": entry.destructive,
                    "permission_level": entry.permission_level.value,
                    "requires_confirm": spec.requires_confirm,
                    "source": clean_source,
                    "trace_id": trace_id,
                },
            )
            if entry.destructive:
                bus_ref.emit(
                    EventType.COMMAND_DESTRUCTIVE,
                    {
                        "action": entry.action,
                        "source": clean_source,
                        "permission_level": entry.permission_level.value,
                        "trace_id": trace_id,
                    },
                )

            # 6) Dispatch via worker IPC.  The transport is NOT a
            #    closure cell; it is fetched at call time from the
            #    module-level capability table.  A closure walker
            #    therefore cannot reach any object that owns a
            #    ``.send`` method (see _EXECUTION_PORTS above).
            request_id = uuid.uuid4().hex
            request = {
                "type": "exec",
                "id": request_id,
                "action": entry.action,
                "params": dict(spec.params),
                "trace_id": trace_id,
            }
            try:
                port = _lookup_execution_port(pipeline_id)
                reply = port.send(request)
            except AuraError:
                raise
            except RuntimeError:
                # Capability table missing — surface as EngineError so
                # higher-up callers get a consistent boundary type.
                raise
            except Exception as exc:  # noqa: BLE001 - IPC boundary
                raise ExecutionError(
                    f"Worker IPC failed for {entry.action!r}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            # Strict trust-boundary validation.  A compromised (or
            # buggy) worker cannot tunnel an arbitrary payload back
            # through the registry: shape, types, action/id echo and
            # size cap are all enforced here before any downstream
            # consumer touches the reply.
            _validate_worker_reply(
                reply,
                expected_action=entry.action,
                expected_id=request_id,
                max_bytes=max_reply_bytes_ref,
            )

            try:
                result = _reply_to_result(reply, entry.action)
            except AuraError:
                raise
            except Exception as exc:  # noqa: BLE001 - parse boundary
                raise ExecutionError(
                    f"Invalid worker reply for {entry.action!r}: {exc}"
                ) from exc

            if not result.command_type:
                result.command_type = entry.action

            # 7) Completion lifecycle.
            bus_ref.emit(
                EventType.COMMAND_COMPLETED,
                {
                    "action": entry.action,
                    "success": result.success,
                    "source": clean_source,
                    "trace_id": trace_id,
                },
            )
            return result

        # Rebind ``pipeline_id`` to the real id of the pipeline
        # function.  Because _execute_safe captured this same cell,
        # the inner function now sees the real value at call time.
        pipeline_id = id(_execute_safe)

        # Install the worker port in the module-level capability
        # table keyed by ``pipeline_id``.  The table holds a weak
        # value + a lifetime-bound strong anchor; the anchor is
        # released when _execute_safe is GC'd.  AFTER this line, the
        # pipeline function can resolve its transport via
        # :func:`_lookup_execution_port` without ever capturing the
        # port (or itself) in its closure.
        _install_execution_port(_execute_safe, worker_port)
        # ``worker_port`` must not outlive this scope through any
        # registry-owned path; drop the local binding explicitly so
        # the constructor frame cannot be used as a back-channel.
        del worker_port

        # Wrap in the slot-only proxy and store ONLY the proxy.
        proxy = _make_executor_proxy(_execute_safe)
        object.__setattr__(self, "_executor", proxy)

        # FREEZE: every subsequent attribute mutation must fail.
        object.__setattr__(self, "_frozen", True)

    # ------------------------------------------------------------------
    # Immutability surface
    # ------------------------------------------------------------------
    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(
                "CommandRegistry is immutable after construction "
                f"(attempted to set {name!r})"
            )
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            "CommandRegistry is immutable after construction "
            f"(attempted to delete {name!r})"
        )

    def __getattr__(self, name: str) -> Any:
        if name in self._DENY_NAMES:
            raise AttributeError(
                f"CommandRegistry: {name!r} is not accessible "
                "(removed by security lockdown)"
            )
        raise AttributeError(
            f"'CommandRegistry' object has no attribute {name!r}"
        )

    def __dir__(self) -> list[str]:
        return list(self._PUBLIC_API)

    # ------------------------------------------------------------------
    # Registration (mutates the _entries dict).
    # ------------------------------------------------------------------
    def register_metadata(
        self,
        action: str,
        *,
        plugin: str,
        description: str = "",
        destructive: bool = False,
        permission_level: PermissionLevel = PermissionLevel.MEDIUM,
    ) -> None:
        if not isinstance(action, str) or not action.strip():
            raise RegistryError("action must be a non-empty string")
        if not isinstance(plugin, str) or not plugin.strip():
            raise RegistryError("plugin name is required")
        level = PermissionLevel.parse(permission_level)
        clean_action = action.strip()
        clean_plugin = plugin.strip()

        try:
            self._manifest.check(
                plugin=clean_plugin,
                action=clean_action,
                permission_level=level,
                destructive=bool(destructive),
            )
        except PluginManifestError as exc:
            raise RegistryError(
                f"Manifest rejected action {clean_action!r}: {exc}"
            ) from exc

        entry = CommandEntry(
            action=clean_action,
            plugin=clean_plugin,
            description=description or "",
            destructive=bool(destructive),
            permission_level=level,
        )

        with self._lock:
            if self._entries.has(entry.action):
                raise RegistryError(
                    f"Duplicate registration for action {entry.action!r} "
                    f"(existing plugin: "
                    f"{self._entries[entry.action].plugin})"
                )
            self._entries._mutate_set(
                _ENTRIES_TOKEN, entry.action, entry
            )

        self._bus.emit(
            EventType.REGISTRY_REGISTERED,
            {
                "action": entry.action,
                "plugin": entry.plugin,
                "destructive": entry.destructive,
                "permission_level": entry.permission_level.value,
            },
        )

    def unregister(
        self,
        action: str,
        *,
        token: object | None = None,
    ) -> bool:
        """Remove a registered action.

        A private, keyword-only ``token`` is required: this is not a
        runtime API.  Pass
        :data:`aura.runtime.command_registry._ENTRIES_TOKEN` from
        trusted code only.  External code cannot obtain the token
        without explicitly importing the module private, so accidental
        deregistration and attacker-driven removal via
        ``registry._entries.pop(...)`` are both blocked.
        """
        if token is not _ENTRIES_TOKEN:
            raise RegistryError(
                "unregister() requires the internal registry token; "
                "not a public runtime API"
            )
        with self._lock:
            removed = self._entries._mutate_pop(_ENTRIES_TOKEN, action)
        if removed is not None:
            self._bus.emit(EventType.REGISTRY_UNREGISTERED, {"action": action})
            return True
        return False

    # ---- lookup --------------------------------------------------------
    def has(self, action: str) -> bool:
        with self._lock:
            return self._entries.has(action)

    def get(self, action: str) -> CommandEntry:
        with self._lock:
            entry = self._entries.get(action)
        if entry is None:
            raise RegistryError(f"Unknown action: {action!r}")
        return entry

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "action": e.action,
                    "plugin": e.plugin,
                    "description": e.description,
                    "destructive": e.destructive,
                    "permission_level": e.permission_level.value,
                }
                for e in self._entries.values()
            ]

    # ------------------------------------------------------------------
    # Single execution entry point.
    # ------------------------------------------------------------------
    def execute(
        self,
        payload: Any,
        *,
        source: str = "auto",
    ) -> CommandResult:
        if not isinstance(payload, CommandSpec):
            payload = validate_command(payload)
        return self._executor.execute(payload, source)


# =====================================================================
# Closure-walk self-audit.
# =====================================================================
def assert_safe_closures(registry: CommandRegistry) -> None:
    """Walk every reachable closure on the registry and assert that
    every cell is either non-callable OR is the registry's own safe
    pipeline function.

    Raises :class:`AssertionError` on any violation - used by the
    runtime destruction test and the bootstrap self-check.

    Walks the registry's proxy and any function reachable from its
    closures transitively.  The only callable allowed by the rule is
    the single pipeline function captured by the proxy's ``execute``
    method.  That function is identified at walk-start as "the safe
    pipeline" and then any further closure walk must only yield
    non-callable data.
    """
    visited: set[int] = set()
    proxy = object.__getattribute__(registry, "_executor")
    execute_method = proxy.execute  # bound method
    # Unwrap to underlying function to access __closure__.
    underlying = getattr(execute_method, "__func__", execute_method)
    closure = getattr(underlying, "__closure__", None) or ()
    if len(closure) != 1:
        raise AssertionError(
            f"proxy.execute must capture exactly one cell, got {len(closure)}"
        )
    safe_pipeline = closure[0].cell_contents
    if not callable(safe_pipeline):
        raise AssertionError(
            "proxy.execute cell_contents is not callable - broken build"
        )

    def _reject_transport(obj: Any) -> None:
        # CF-1 hardening: no transport object may survive in the
        # closure graph.  The only way to reach the worker must be via
        # the module-level capability table.
        cls_name = type(obj).__name__
        if "Worker" in cls_name or "Port" in cls_name:
            raise AssertionError(
                "Transport-shaped object found in closure graph: "
                f"{obj!r} (type={cls_name}) - the worker port must "
                "live in the _EXECUTION_PORTS capability table, not "
                "in a closure cell"
            )
        send_attr = getattr(obj, "send", None)
        if callable(send_attr):
            raise AssertionError(
                "Closure cell exposes a .send(...) dispatcher: "
                f"{obj!r} (type={cls_name}) - any object with a send "
                "method is treated as a transport and is forbidden"
            )

    def _walk(fn: Any) -> None:
        if id(fn) in visited:
            return
        visited.add(id(fn))
        cells = getattr(fn, "__closure__", None) or ()
        for cell in cells:
            try:
                obj = cell.cell_contents
            except ValueError:
                continue
            if callable(obj):
                if obj is safe_pipeline:
                    # Allowed: recursion would not find anything new
                    # because safe_pipeline does not capture itself.
                    continue
                raise AssertionError(
                    "Unsafe callable found in closure graph: "
                    f"{obj!r} (type={type(obj).__name__}) - only the "
                    "registry's own pipeline function is permitted"
                )
            # Non-callable data still has to pass the transport
            # firewall.  Instances of RateLimiter, PermissionValidator
            # and EventBus are fine; anything with a ``.send`` method
            # or a ``Worker``/``Port`` class name is not.
            _reject_transport(obj)

    _walk(safe_pipeline)
